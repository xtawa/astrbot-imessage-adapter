import json
from pathlib import Path
from urllib.parse import quote, urlsplit

from astrbot.api.event import MessageChain
from astrbot.api.message_components import (
    At,
    AtAll,
    Face,
    File,
    Image,
    Json,
    Location,
    Music,
    Node,
    Nodes,
    Plain,
    Record,
    Reply,
    Video,
)
from astrbot.api.platform import AstrBotMessage, Group, MessageMember, MessageType

from .config import Config
from .media import MediaStore, mime_type
from .route import Route


def split_text(text: str, limit: int) -> list[str]:
    parts = []
    while len(text) > limit:
        split = text.rfind("\n", limit // 2, limit)
        split = split + 1 if split >= 0 else limit
        parts.append(text[:split])
        text = text[split:]
    if text:
        parts.append(text)
    return parts


def convert_incoming(
    raw: dict, config: Config, media: MediaStore, unique_session: bool = False
) -> AstrBotMessage | None:
    if raw.get("direction") != "inbound" or not raw.get("sender_id"):
        return None
    route = Route(raw["chat_id"], raw["phone"], raw["kind"])
    if route.kind == "group" and not config.allow_groups:
        return None
    if config.allowed_senders and raw["sender_id"] not in config.allowed_senders:
        return None
    if config.allowed_chats and route.chat_id not in config.allowed_chats:
        return None
    message = AstrBotMessage()
    message.type = (
        MessageType.GROUP_MESSAGE
        if route.kind == "group"
        else MessageType.FRIEND_MESSAGE
    )
    message.self_id = (
        route.phone if route.phone != "shared" else config.bot_number or "shared"
    )
    message.session_id = Route(
        route.chat_id,
        route.phone,
        route.kind,
        raw["sender_id"] if unique_session and route.kind == "group" else "",
    ).encode()
    message.message_id = raw["id"]
    message.timestamp = int(raw["timestamp"])
    message.sender = MessageMember(
        user_id=raw["sender_id"], nickname=raw.get("sender_name") or raw["sender_id"]
    )
    if route.kind == "group":
        message.group = Group(
            group_id=route.encode(), group_name=raw.get("group_name") or None
        )
    message.raw_message = raw
    message.message = []
    text = []
    for part in raw.get("parts", []):
        kind = part["type"]
        if kind == "text":
            value = part.get("text", "")
            if value:
                message.message.append(Plain(value))
                text.append(value)
        elif kind == "mention":
            message.message.append(At(qq=part["sender_id"], name=part.get("text", "")))
            text.append(part.get("text", ""))
        elif kind == "reply":
            message.message.append(
                Reply(
                    id=part["target_id"],
                    sender_id=part.get("sender_id", ""),
                    message_str=part.get("text", ""),
                )
            )
        elif kind == "reaction" and config.receive_reactions:
            value = f"[回应 {part.get('target_id', '')}] {part.get('emoji', '')}"
            message.message.append(Plain(value))
            text.append(value)
        elif kind in ("attachment", "voice"):
            try:
                path = media.incoming_path(part["path"])
                mime = part.get("mime_type", "application/octet-stream")
                if kind == "voice" or mime.startswith("audio/"):
                    message.message.append(Record.fromFileSystem(path))
                elif mime.startswith("image/"):
                    message.message.append(Image.fromFileSystem(path))
                elif mime.startswith("video/"):
                    message.message.append(Video.fromFileSystem(path))
                else:
                    message.message.append(
                        File(name=part.get("name", Path(path).name), file=path)
                    )
            except (OSError, ValueError, KeyError):
                value = f"[附件不可用: {part.get('name', 'attachment')}]"
                message.message.append(Plain(value))
                text.append(value)
    if not any(not isinstance(part, Reply) for part in message.message):
        return None
    message.message_str = "".join(text)
    return message


def flatten_chain(components, depth=0):
    if depth > 8:
        raise ValueError("Forwarded message nesting exceeds 8 levels")
    for component in components:
        if isinstance(component, Node):
            if component.name:
                yield Plain(f"{component.name}:\n")
            yield from flatten_chain(component.content, depth + 1)
            yield Plain("\n")
        elif isinstance(component, Nodes):
            yield from flatten_chain(component.nodes, depth + 1)
        else:
            yield component


async def convert_outgoing(
    chain: MessageChain, config: Config, media: MediaStore
) -> tuple[list[dict], str]:
    parts = []
    buffer = ""
    reply_id = ""
    use_markdown = (
        chain.use_markdown_ if chain.use_markdown_ is not None else config.markdown
    )

    def flush():
        nonlocal buffer
        for chunk in split_text(buffer, config.max_text_length):
            parts.append(
                {
                    "type": "text",
                    "text": chunk,
                    "markdown": use_markdown,
                    "link_preview": config.link_preview,
                }
            )
        buffer = ""

    for component in flatten_chain(chain.chain):
        if isinstance(component, Reply):
            if reply_id and reply_id != str(component.id):
                raise ValueError("A message chain cannot reply to multiple messages")
            reply_id = str(component.id)
        elif isinstance(component, Plain):
            buffer += component.text
        elif isinstance(component, AtAll):
            buffer += "@所有人 "
        elif isinstance(component, At):
            buffer += f"@{component.name or component.qq} "
        elif isinstance(component, Face):
            buffer += f"[表情:{component.id}]"
        elif isinstance(component, Json):
            buffer += json.dumps(component.data, ensure_ascii=False)
        elif isinstance(component, Location):
            buffer += f"{component.title or '位置'} https://maps.apple.com/?ll={component.lat},{component.lon}&q={quote(component.title or '')}"
        elif isinstance(component, Music):
            buffer += "\n".join(
                x for x in (component.title, component.url or component.audio) if x
            )
        elif isinstance(component, Image | Record | Video | File):
            flush()
            if isinstance(component, File):
                source = component.file_ or component.url
                name = (
                    component.name
                    or Path(urlsplit(source).path).name
                    or "attachment.bin"
                )
            else:
                source = component.path or component.url or component.file
                default = (
                    "voice.wav"
                    if isinstance(component, Record)
                    else "video.mp4"
                    if isinstance(component, Video)
                    else "image.png"
                )
                name = (
                    Path(urlsplit(source or "").path).name
                    if not (source or "").startswith(("base64://", "data:"))
                    else ""
                )
                name = name or default
            kind = "voice" if isinstance(component, Record) else "attachment"
            path, name = await media.resolve(source or "", name)
            parts.append(
                {
                    "type": kind,
                    "path": path,
                    "name": name,
                    "mime_type": mime_type(name, kind),
                }
            )
        else:
            raise ValueError(f"Unsupported iMessage component: {component.type}")
    flush()
    if len(parts) > 100:
        raise ValueError(
            "A message chain exceeds 100 iMessage parts; split it before sending"
        )
    return parts, reply_id

from dataclasses import replace

import pytest
from astrbot.api.event import MessageChain
from astrbot.api.message_components import (
    At,
    AtAll,
    File,
    Image,
    Node,
    Nodes,
    Plain,
    Record,
    Reply,
    Video,
)
from astrbot.api.platform import MessageType
from astrbot.core.platform.message_session import MessageSession
from photon_plugin.imessage.converter import (
    convert_incoming,
    convert_outgoing,
    split_text,
)
from photon_plugin.imessage.route import Route


def test_real_private_message(config, media, inbound):
    message = convert_incoming(inbound, config, media)
    assert message.type == MessageType.FRIEND_MESSAGE
    assert message.self_id == inbound["phone"]
    assert message.sender.user_id == inbound["sender_id"]
    assert message.message_str == "你好"
    session = MessageSession.from_str(
        f"test_imessage:FriendMessage:{message.session_id}"
    )
    assert Route.decode(session.session_id).payload() == {
        "chat_id": inbound["chat_id"],
        "phone": inbound["phone"],
        "kind": "dm",
    }


def test_group_unique_sessions_keep_transport(config, media, inbound):
    inbound.update(kind="group", chat_id="iMessage;+;chat123", group_name="Friends")
    first = convert_incoming(inbound, config, media, unique_session=True)
    inbound["sender_id"] = "+15550000003"
    second = convert_incoming(inbound, config, media, unique_session=True)
    assert first.session_id != second.session_id
    assert first.group_id == second.group_id
    assert (
        Route.decode(first.session_id).payload()
        == Route.decode(second.session_id).payload()
    )
    assert first.group.group_name == "Friends"


@pytest.mark.parametrize("changes", [{"direction": "outbound"}, {"sender_id": ""}])
def test_self_and_actorless_messages_skipped(config, media, inbound, changes):
    inbound.update(changes)
    assert convert_incoming(inbound, config, media) is None


def test_shared_identity_and_multi_line_identity(config, media, inbound):
    config = replace(config, bot_number="+15550000009")
    assert convert_incoming(inbound, config, media).self_id == "+15550000002"
    inbound["phone"] = "shared"
    assert convert_incoming(inbound, config, media).self_id == config.bot_number


def test_incoming_reply_mention_and_reaction(config, media, inbound):
    inbound["parts"] = [
        {"type": "reply", "target_id": "old", "text": "old text", "sender_id": "peer"},
        {"type": "mention", "sender_id": inbound["phone"], "text": "@bot"},
        {"type": "text", "text": " hi"},
    ]
    message = convert_incoming(inbound, config, media)
    assert isinstance(message.message[0], Reply)
    assert message.message[0].message_str == "old text"
    assert isinstance(message.message[1], At)
    assert message.message_str == "@bot hi"
    inbound["parts"] = [{"type": "reaction", "target_id": "old", "emoji": "👍"}]
    assert convert_incoming(inbound, config, media) is None
    assert (
        "👍"
        in convert_incoming(
            inbound, replace(config, receive_reactions=True), media
        ).message_str
    )


@pytest.mark.parametrize(
    "kind,mime,component",
    [
        ("attachment", "image/png", Image),
        ("voice", "audio/mp4", Record),
        ("attachment", "audio/mpeg", Record),
        ("attachment", "video/mp4", Video),
        ("attachment", "application/pdf", File),
    ],
)
def test_incoming_media_components(config, media, inbound, kind, mime, component):
    path = media.root / "content.bin"
    path.write_bytes(b"test-media")
    inbound["parts"] = [
        {"type": kind, "path": str(path), "mime_type": mime, "name": "original.bin"}
    ]
    message = convert_incoming(inbound, config, media)
    assert isinstance(message.message[0], component)
    if component is File:
        assert message.message[0].name == "original.bin"


def test_incoming_unsafe_media_falls_back(config, media, inbound, tmp_path):
    outside = tmp_path / "private.txt"
    outside.write_text("private")
    inbound["parts"] = [
        {"type": "attachment", "path": str(outside), "name": "file.txt"}
    ]
    assert "附件不可用" in convert_incoming(inbound, config, media).message_str


@pytest.mark.parametrize(
    "option,value",
    [("allowed_senders", ("different",)), ("allowed_chats", ("different",))],
)
def test_inbound_allowlists(config, media, inbound, option, value):
    assert convert_incoming(inbound, replace(config, **{option: value}), media) is None


def test_disable_groups(config, media, inbound):
    inbound["kind"] = "group"
    assert convert_incoming(inbound, replace(config, allow_groups=False), media) is None


@pytest.mark.parametrize(
    "text", ["a" * 151, "你好😀" * 80, "a" * 65 + "\n" + "b" * 110, "", "\n\n  "]
)
def test_split_preserves_characters(text):
    parts = split_text(text, 100)
    assert "".join(parts) == text
    assert all(len(x) <= 100 for x in parts)


async def test_outgoing_mixed_chain_preserves_order(config, media, tmp_path):
    image = tmp_path / "photo.png"
    audio = tmp_path / "voice.mp3"
    video = tmp_path / "video.mp4"
    document = tmp_path / "a.pdf"
    for path in (image, audio, video, document):
        path.write_bytes(b"test")
    chain = MessageChain(
        [
            Reply(id="quoted"),
            Plain("A"),
            Plain("B"),
            Image.fromFileSystem(str(image)),
            Plain("C"),
            Record.fromFileSystem(str(audio)),
            Video.fromFileSystem(str(video)),
            File(name="report.pdf", file=str(document)),
        ]
    )
    parts, reply = await convert_outgoing(chain, config, media)
    assert reply == "quoted"
    assert [part["type"] for part in parts] == [
        "text",
        "attachment",
        "text",
        "voice",
        "attachment",
        "attachment",
    ]
    assert parts[0]["text"] == "AB" and parts[2]["text"] == "C"
    assert parts[3]["mime_type"] == "audio/mpeg"
    assert parts[-1]["name"] == "report.pdf"


async def test_outgoing_forward_and_markdown_override(config, media):
    chain = MessageChain(
        [
            Nodes(nodes=[Node(name="Alice", content=[Plain("hello")])]),
            At(qq="123", name="Bob"),
            AtAll(),
        ]
    )
    chain.use_markdown_ = False
    parts, _ = await convert_outgoing(chain, config, media)
    assert parts[0]["text"] == "Alice:\nhello\n@Bob @所有人 "
    assert parts[0]["markdown"] is False


async def test_two_reply_targets_rejected(config, media):
    with pytest.raises(ValueError, match="multiple"):
        await convert_outgoing(
            MessageChain([Reply(id="a"), Reply(id="b"), Plain("hi")]), config, media
        )


@pytest.mark.parametrize("value", ["+15551234567", "im1_invalid", "im1_", "im1_WzFd"])
def test_unknown_session_is_never_guessed(value):
    with pytest.raises(ValueError):
        Route.decode(value)

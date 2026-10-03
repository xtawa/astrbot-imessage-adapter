from collections.abc import AsyncGenerator
from contextlib import suppress

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.platform import Group, MessageMember

from .route import Route


class IMessageEvent(AstrMessageEvent):
    def __init__(self, message_str, message_obj, platform_meta, session_id, adapter):
        super().__init__(message_str, message_obj, platform_meta, session_id)
        self.adapter = adapter
        # AstrBot may rewrite session_id for per-user group conversations later.
        # Keep the transport route fixed for replies to this original event.
        self.route = Route.decode(message_obj.session_id)
        self.last_sent_ids: list[str] = []

    async def send(self, message: MessageChain):
        target = (
            self.message_obj.message_id if self.adapter.options.reply_to_message else ""
        )
        self.last_sent_ids = await self.adapter.send_chain(self.route, message, target)
        await super().send(message)

    async def send_typing(self):
        if self.adapter.options.typing_indicator:
            try:
                await self.adapter.client.typing(self.route, True)
            except Exception:
                logger.warning("[iMessage] Typing indicator failed")

    async def stop_typing(self):
        if self.adapter.options.typing_indicator:
            with suppress(Exception):
                await self.adapter.client.typing(self.route, False)

    async def send_streaming(
        self, generator: AsyncGenerator[MessageChain, None], use_fallback: bool = False
    ):
        # Apple's edits have time/count limits; buffer deltas into a normal chain.
        # Preserve chain metadata and component order and stop typing on failure.
        merged = None
        await self.send_typing()
        try:
            async for delta in generator:
                if merged is None:
                    merged = delta.derive(list(delta.chain))
                else:
                    merged.chain.extend(delta.chain)
            if merged and merged.chain:
                await self.send(merged)
        finally:
            await self.stop_typing()

    async def react(self, emoji: str):
        return await self.adapter.client.react(
            self.route, self.message_obj.message_id, emoji
        )

    async def mark_read(self):
        await self.adapter.client.mark_read(self.route, self.message_obj.message_id)

    async def edit_message(self, message_id: str, text: str):
        await self.adapter.client.edit(self.route, message_id, text)

    async def unsend_message(self, message_id: str):
        await self.adapter.client.unsend(self.route, message_id)

    async def get_group(self, group_id: str | None = None, **kwargs):
        if self.route.kind != "group" and not group_id:
            return None
        route = Route.decode(group_id) if group_id else self.route
        result = await self.adapter.client.group_info(route)
        members = [
            MessageMember(user_id=user["id"], nickname=user.get("name") or user["id"])
            for user in result["members"]
        ]
        return Group(
            group_id=route.encode(),
            group_name=result.get("name"),
            members=members,
            member_count=len(members),
        )

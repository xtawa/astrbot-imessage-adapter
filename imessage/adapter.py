import asyncio
import hashlib
import json
from contextlib import suppress
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.platform import Platform, PlatformMetadata, register_platform_adapter
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.platform.platform import PlatformStatus
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .client import BridgeError, PhotonClient
from .config import DEFAULT_CONFIG, Config
from .converter import convert_incoming, convert_outgoing
from .event import IMessageEvent
from .media import MediaStore
from .route import Route
from .store import SeenStore


@register_platform_adapter(
    "photon_imessage",
    "iMessage via Photon Spectrum",
    default_config_tmpl=DEFAULT_CONFIG.copy(),
    adapter_display_name="iMessage (Photon)",
    support_streaming_message=False,
    config_metadata=json.loads(
        (Path(__file__).parent.parent / "config_metadata.json").read_text(
            encoding="utf-8"
        )
    ),
)
class IMessageAdapter(Platform):
    def __init__(
        self, platform_config: dict, platform_settings: dict, event_queue: asyncio.Queue
    ):
        super().__init__(platform_config, event_queue)
        self.settings = platform_settings
        self.options = Config.parse(platform_config)
        key = hashlib.sha256(self.options.id.encode()).hexdigest()[:16]
        root = (
            Path(get_astrbot_data_path()) / "plugin_data/astrbot_imessage_adapter" / key
        )
        self.media = MediaStore(
            root / "media",
            self.options.max_attachment_mb * 1024 * 1024,
            self.options.media_retention_hours,
            self.options.media_cache_mb * 1024 * 1024,
        )
        self.seen = SeenStore(root / "deliveries.sqlite3")
        self.client = PhotonClient(self.options, self.media.root)
        self.incoming: asyncio.Queue = asyncio.Queue(maxsize=128)
        self.stopped = asyncio.Event()
        self.tasks: set[asyncio.Task] = set()
        self.metadata = PlatformMetadata(
            name="photon_imessage",
            description="iMessage via Photon Spectrum",
            id=self.options.id,
            adapter_display_name="iMessage (Photon)",
            support_streaming_message=False,
        )
        self._warned_shared_identity = False

    def meta(self):
        return self.metadata

    def get_client(self) -> PhotonClient:
        return self.client

    def get_stats(self):
        stats = super().get_stats()
        stats["photon"] = {"connected": self.client.connected, **self.client.stats}
        return stats

    def _connected(self):
        self.clear_errors()
        self.status = PlatformStatus.RUNNING

    async def run(self):
        self.status = PlatformStatus.PENDING
        try:
            await self.client.prepare()
            self.media.prune()
            consumer = asyncio.create_task(self._consume())
            maintenance = asyncio.create_task(self._maintain())
            self.tasks.update((consumer, maintenance))
            delay = 1
            while not self.stopped.is_set():
                try:
                    await self.client.run(self.incoming.put, self._connected)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if self.stopped.is_set():
                        break
                    reason = (
                        str(exc)
                        if isinstance(exc, BridgeError)
                        else "Photon bridge connection failed"
                    )
                    self.record_error(reason)
                    self._errors[:] = self._errors[-20:]
                    logger.warning("[iMessage] %s; reconnecting in %ss", reason, delay)
                    self.client.stats["restarts"] += 1
                    try:
                        await asyncio.wait_for(self.stopped.wait(), delay)
                    except TimeoutError:
                        pass
                    delay = min(delay * 2, 60)
        finally:
            await self.client.stop()
            for task in tuple(self.tasks):
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task
            self.tasks.clear()
            self.seen.close()
            self.status = PlatformStatus.STOPPED

    async def terminate(self):
        self.stopped.set()
        await self.client.stop()

    async def _maintain(self):
        while not self.stopped.is_set():
            await asyncio.sleep(300)
            self.media.prune()
            self.seen.prune()

    async def _consume(self):
        while True:
            raw = await self.incoming.get()
            try:
                await self.handle_message(raw)
            except Exception:
                logger.warning("[iMessage] Invalid inbound message was skipped")
            finally:
                self.incoming.task_done()

    async def handle_message(self, raw: dict):
        key = json.dumps(
            [raw.get("phone"), raw.get("chat_id"), raw.get("id")], separators=(",", ":")
        )
        if self.seen.contains(key):
            return
        message = convert_incoming(
            raw, self.options, self.media, self.settings.get("unique_session", False)
        )
        if not message:
            return
        if message.self_id == "shared" and not self._warned_shared_identity:
            logger.warning(
                "[iMessage] Set bot_number to the Photon assigned bot number for shared-line mentions and identity"
            )
            self._warned_shared_identity = True
        route = Route.decode(message.session_id)
        event = IMessageEvent(
            message.message_str, message, self.meta(), message.session_id, self
        )
        # Mark as seen only once the AstrBot queue has accepted it.
        await self._event_queue.put(event)
        self.seen.add(key)
        if self.options.mark_read:
            task = asyncio.create_task(self._read_receipt(route, message.message_id))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    async def _read_receipt(self, route: Route, message_id: str):
        try:
            await self.client.mark_read(route, message_id)
        except Exception:
            logger.warning(
                "[iMessage] Could not mark chat read; message handling continues"
            )

    async def send_chain(
        self, route: Route, chain: MessageChain, reply_id: str = ""
    ) -> list[str]:
        if route.kind == "group" and not self.options.allow_groups:
            raise ValueError("Group messages are disabled")
        if (
            self.options.allowed_chats
            and route.chat_id not in self.options.allowed_chats
        ):
            raise ValueError("Chat is not in allowed_chats")
        parts, explicit_reply = await convert_outgoing(chain, self.options, self.media)
        if not parts:
            return []
        return await self.client.send(route, parts, reply_id=explicit_reply or reply_id)

    async def send_by_session(
        self, session: MessageSession, message_chain: MessageChain
    ):
        if session.platform_name != self.options.id:
            raise ValueError("Session belongs to a different platform instance")
        await self.send_chain(Route.decode(session.session_id), message_chain)
        await super().send_by_session(session, message_chain)

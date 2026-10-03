import asyncio
import hashlib
import json
import os
import shutil
import sys
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path

from astrbot.api import logger
from filelock import AsyncFileLock

from .config import Config
from .route import Route


class BridgeError(RuntimeError):
    def __init__(self, message: str, sent_ids: list[str] | None = None):
        super().__init__(message)
        self.sent_ids = sent_ids or []


class PhotonClient:
    """Private stdio RPC. Each platform instance owns one child process."""

    def __init__(self, config: Config, media_dir: Path):
        self.config = config
        self.media_dir = media_dir
        self.sidecar = Path(__file__).resolve().parent.parent / "sidecar"
        self.process: asyncio.subprocess.Process | None = None
        self.ready = asyncio.Event()
        self.connected = False
        self.pending: dict[int, asyncio.Future] = {}
        self.write_lock = asyncio.Lock()
        self.counter = 0
        self.reader_task: asyncio.Task | None = None
        self.stderr_task: asyncio.Task | None = None
        self.stats = {"received": 0, "sent": 0, "restarts": 0}

    async def prepare(self):
        if self.config.auto_install:
            async with AsyncFileLock(self.sidecar / ".install.lock", timeout=600):
                await self._prepare()
        else:
            await self._prepare()

    async def _prepare(self):
        node = shutil.which(self.config.node_path)
        if not node:
            raise BridgeError(
                "Node.js 22+ is required; install Node or configure node_path"
            )
        files = [
            self.sidecar / "package.json",
            self.sidecar / "package-lock.json",
            *sorted((self.sidecar / "src").glob("*.ts")),
            self.sidecar / "tsconfig.json",
        ]
        digest = hashlib.sha256(b"".join(p.read_bytes() for p in files)).hexdigest()
        marker = self.sidecar / ".installed"
        if (
            (self.sidecar / "node_modules/@spectrum-ts/core").is_dir()
            and (self.sidecar / "dist/index.js").exists()
            and marker.exists()
            and marker.read_text() == digest
        ):
            return
        if not self.config.auto_install:
            # Manual installs need no private marker, but do require both artifacts.
            if (self.sidecar / "node_modules/@spectrum-ts/core").is_dir() and (
                self.sidecar / "dist/index.js"
            ).exists():
                return
            raise BridgeError(
                "Run `cd sidecar && npm ci && npm run build`, or enable auto_install"
            )
        logger.info(
            "[iMessage] Installing locked Photon dependencies and compiling bridge"
        )
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(self.sidecar.parent / "scripts/setup_bridge.py"),
            "--node",
            node,
            cwd=self.sidecar.parent,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout=600)
        except BaseException:
            process.kill()
            await process.wait()
            raise
        if process.returncode:
            # Package-manager output can contain proxy credentials; don't echo it.
            raise BridgeError(
                "Bridge setup failed; run scripts/setup_bridge.py manually for diagnostics"
            )
        marker.write_text(digest)

    async def run(
        self,
        on_message: Callable[[dict], Awaitable[None]],
        on_ready: Callable[[], None] | None = None,
    ):
        env = os.environ.copy()
        env.update(
            {
                "PHOTON_PROJECT_ID": self.config.project_id,
                "PHOTON_PROJECT_SECRET": self.config.project_secret,
                "PHOTON_MEDIA_DIR": str(self.media_dir),
                "PHOTON_MAX_BYTES": str(self.config.max_attachment_mb * 1024 * 1024),
                "PHOTON_CACHE_BYTES": str(self.config.media_cache_mb * 1024 * 1024),
            }
        )
        self.ready.clear()
        self.process = await asyncio.create_subprocess_exec(
            self.config.node_path,
            str(self.sidecar / "dist/index.js"),
            cwd=self.sidecar,
            env=env,
            limit=1024 * 1024,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self.reader_task = asyncio.create_task(self._read(on_message))
        self.stderr_task = asyncio.create_task(self._stderr())
        try:
            await asyncio.wait_for(self.ready.wait(), self.config.startup_timeout)
            if not self.connected:
                raise BridgeError(
                    "Photon could not initialize; check project credentials and network"
                )
            if on_ready:
                on_ready()
            logger.info(
                "[iMessage] Photon bridge initialized; awaiting inbound messages"
            )
            await self.reader_task
            raise BridgeError("Photon bridge disconnected")
        finally:
            await self.stop()

    async def _read(self, on_message):
        try:
            while line := await self.process.stdout.readline():
                data = json.loads(line)
                if data.get("event") == "ready":
                    if data.get("protocol") != 1:
                        raise BridgeError("Incompatible Photon bridge protocol")
                    self.connected = True
                    self.ready.set()
                elif data.get("event") == "message":
                    self.stats["received"] += 1
                    await on_message(data["message"])
                elif "id" in data:
                    future = self.pending.get(data["id"])
                    if future and not future.done():
                        if data.get("error"):
                            future.set_exception(
                                BridgeError(data["error"], data.get("sent_ids"))
                            )
                        else:
                            future.set_result(data.get("result"))
                elif data.get("event") == "warning":
                    logger.warning("[iMessage] %s", data.get("code", "bridge_warning"))
        finally:
            self.connected = False
            self.ready.set()
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(
                        BridgeError(
                            "Photon disconnected; send result may be unknown, do not retry blindly"
                        )
                    )

    async def _stderr(self):
        # Never print SDK diagnostics containing credentials or message contents.
        count = 0
        while await self.process.stderr.readline():
            if count == 0:
                logger.warning(
                    "[iMessage] Bridge emitted diagnostics; check connectivity and runtime installation"
                )
            count += 1

    async def rpc(self, action: str, **params):
        if (
            not self.connected
            or not self.process
            or self.process.returncode is not None
        ):
            raise BridgeError("Photon is not connected")
        self.counter += 1
        request_id = self.counter
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            payload = (
                json.dumps(
                    {"id": request_id, "action": action, **params}, ensure_ascii=False
                ).encode()
                + b"\n"
            )
            async with self.write_lock:
                self.process.stdin.write(payload)
                await self.process.stdin.drain()
            return await asyncio.wait_for(future, self.config.rpc_timeout)
        except TimeoutError as exc:
            raise BridgeError(
                "Photon operation timed out; delivery may have succeeded, automatic resend is disabled"
            ) from exc
        finally:
            self.pending.pop(request_id, None)

    async def send(
        self, route: Route, parts: list[dict], reply_id: str = "", effect: str = ""
    ) -> list[str]:
        result = await self.rpc(
            "send", route=route.payload(), parts=parts, reply_id=reply_id, effect=effect
        )
        self.stats["sent"] += len(result)
        return result

    async def react(self, route: Route, message_id: str, emoji: str) -> str | None:
        return await self.rpc(
            "react", route=route.payload(), message_id=message_id, emoji=emoji
        )

    async def edit(self, route: Route, message_id: str, text: str):
        await self.rpc("edit", route=route.payload(), message_id=message_id, text=text)

    async def unsend(self, route: Route, message_id: str):
        await self.rpc("unsend", route=route.payload(), message_id=message_id)

    async def mark_read(self, route: Route, message_id: str):
        await self.rpc("read", route=route.payload(), message_id=message_id)

    async def typing(self, route: Route, active: bool):
        await self.rpc("typing", route=route.payload(), active=active)

    async def group_info(self, route: Route) -> dict:
        return await self.rpc("group_info", route=route.payload())

    async def rename_group(self, route: Route, name: str):
        await self.rpc("rename", route=route.payload(), name=name)

    async def group_members(
        self, route: Route, members: list[str], remove: bool = False
    ):
        await self.rpc(
            "remove_members" if remove else "add_members",
            route=route.payload(),
            members=members,
        )

    async def leave_group(self, route: Route):
        await self.rpc("leave", route=route.payload())

    async def create_chat(self, addresses: list[str], phone: str = "") -> Route:
        result = await self.rpc("create_chat", addresses=addresses, phone=phone)
        return Route(**result)

    async def send_poll(
        self, route: Route, title: str, options: list[str]
    ) -> list[str]:
        return await self.send(
            route, [{"type": "poll", "title": title, "options": options}]
        )

    async def send_contact(self, route: Route, vcard: str) -> list[str]:
        return await self.send(route, [{"type": "contact", "vcard": vcard}])

    async def send_app_card(
        self, route: Route, url: str, caption: str, subcaption: str = ""
    ) -> list[str]:
        part = {"type": "app", "url": url, "caption": caption}
        if subcaption:
            part["subcaption"] = subcaption
        return await self.send(route, [part])

    async def share_contact_card(self, route: Route):
        await self.rpc("share_contact", route=route.payload())

    async def set_group_avatar(
        self, route: Route, path: str = "clear", mime_type: str = ""
    ):
        await self.rpc(
            "avatar",
            route=route.payload(),
            path=path,
            **({"mime_type": mime_type} if mime_type else {}),
        )

    async def set_background(
        self, route: Route, path: str = "clear", mime_type: str = ""
    ):
        await self.rpc(
            "background",
            route=route.payload(),
            path=path,
            **({"mime_type": mime_type} if mime_type else {}),
        )

    async def stop(self):
        self.connected = False
        process = self.process
        if process and process.returncode is None:
            if process.stdin:
                process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except TimeoutError:
                process.kill()
                await process.wait()
        for task in (self.reader_task, self.stderr_task):
            if task and task is not asyncio.current_task():
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task
        self.process = None

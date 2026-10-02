import asyncio
import base64
import mimetypes
import os
import re
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname

import aiohttp


class MediaStore:
    def __init__(
        self, root: Path, max_bytes: int, retention_hours: int, cache_bytes: int
    ):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self.retention = retention_hours * 3600
        self.cache_bytes = cache_bytes

    def prune(self):
        cutoff = time.time() - self.retention
        for path in self.root.iterdir():
            if (
                path.is_file()
                and not path.is_symlink()
                and path.stat().st_mtime < cutoff
            ):
                path.unlink(missing_ok=True)

    def check_capacity(self, size: int):
        if size > self.max_bytes:
            raise ValueError("Attachment exceeds max_attachment_mb")
        used = sum(p.stat().st_size for p in self.root.iterdir() if p.is_file())
        if used + size > self.cache_bytes:
            raise ValueError(
                "Attachment cache is full; wait for expiry or increase media_cache_mb"
            )

    def incoming_path(self, value: str) -> str:
        path = Path(value).resolve(strict=True)
        if path.parent != self.root or not path.is_file():
            raise ValueError("Attachment path is outside the iMessage cache")
        if path.stat().st_size > self.max_bytes:
            raise ValueError("Attachment exceeds max_attachment_mb")
        return str(path)

    def target(self, name: str) -> Path:
        # Incoming names are display labels only; never become filesystem paths.
        extension = Path(name.replace("\\", "/")).suffix
        if not re.fullmatch(r"\.[A-Za-z0-9]{1,10}", extension):
            extension = ".bin"
        return self.root / (uuid.uuid4().hex + extension)

    async def resolve(self, source: str, name: str) -> tuple[str, str]:
        if not source:
            raise ValueError("Media component has no source")
        if source.startswith("data:"):
            header, separator, encoded = source.partition(",")
            if not separator or not header.endswith(";base64"):
                raise ValueError("Only base64 data URIs are supported")
            content_type = header[5:-7]
            extension = mimetypes.guess_extension(content_type)
            if extension:
                name = Path(name).stem + extension
            source = "base64://" + encoded
        if source.startswith("base64://"):
            encoded = source[9:]
            if len(encoded) > 4 * ((self.max_bytes + 2) // 3):
                raise ValueError("Attachment exceeds max_attachment_mb")
            data = base64.b64decode(encoded, validate=True)
            self.check_capacity(len(data))
            target = self.target(name)
            await asyncio.to_thread(target.write_bytes, data)
            os.chmod(target, 0o600)
            return str(target), name
        if source.startswith(("http://", "https://")):
            target = self.target(name)
            size = 0
            self.prune()
            try:
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=60)
                ) as session:
                    async with session.get(source) as response:
                        response.raise_for_status()
                        if not mimetypes.guess_type(name)[0] and (
                            extension := mimetypes.guess_extension(
                                response.content_type
                            )
                        ):
                            name += extension
                        if response.content_length:
                            self.check_capacity(response.content_length)
                        with target.open("xb") as output:
                            async for block in response.content.iter_chunked(65536):
                                size += len(block)
                                if size > self.max_bytes:
                                    raise ValueError(
                                        "Attachment exceeds max_attachment_mb"
                                    )
                                # target is already included in the current cache usage.
                                self.check_capacity(len(block))
                                output.write(block)
                os.chmod(target, 0o600)
                return str(target), name
            except BaseException:
                target.unlink(missing_ok=True)
                raise
        if source.startswith("file://"):
            url = urlsplit(source)
            if url.netloc not in ("", "localhost"):
                raise ValueError("Remote file URIs are unsupported")
            source = url2pathname(url.path)
        path = Path(source).resolve(strict=True)
        if not path.is_file() or path.stat().st_size > self.max_bytes:
            raise ValueError(
                "Media source must be a regular file within max_attachment_mb"
            )
        return str(path), name


def mime_type(name: str, kind: str) -> str:
    guessed = mimetypes.guess_type(name)[0]
    if kind == "voice":
        if guessed and guessed.startswith("audio/"):
            return guessed
        raise ValueError("Voice requires a known audio format (e.g. .m4a, .wav, .mp3)")
    return guessed or "application/octet-stream"

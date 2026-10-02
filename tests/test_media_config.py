import base64
import os
import time

import pytest
from aiohttp import web
from photon_plugin.imessage.config import Config
from photon_plugin.imessage.media import MediaStore
from photon_plugin.imessage.store import SeenStore


def test_config_env_and_secret_redaction(monkeypatch):
    monkeypatch.setenv("PHOTON_PROJECT_ID", "project-test")
    monkeypatch.setenv("PHOTON_PROJECT_SECRET", "secret-test")
    config = Config.parse({})
    assert config.project_secret == "secret-test"
    assert "secret-test" not in repr(config) and "project-test" not in repr(config)


@pytest.mark.parametrize(
    "setting,value",
    [
        ("mark_read", "false"),
        ("max_attachment_mb", -1),
        ("max_text_length", 0),
        ("rpc_timeout", True),
        ("allowed_senders", "all"),
    ],
)
def test_config_rejects_invalid_values(setting, value):
    with pytest.raises(ValueError):
        Config.parse({"project_id": "x", "project_secret": "x", setting: value})


async def test_base64_limits_and_file_uri(media, tmp_path):
    payload = base64.b64encode(b"hello").decode()
    path, name = await media.resolve("base64://" + payload, "../../image.png")
    assert media.incoming_path(path) == path
    assert (media.root / os.path.basename(path)).read_bytes() == b"hello"
    local = tmp_path / "a%20 file.txt"
    local.write_bytes(b"local")
    resolved, _ = await media.resolve(local.as_uri(), local.name)
    assert resolved == str(local.resolve())
    with pytest.raises(ValueError):
        await media.resolve("base64://not-valid!", "x.png")


async def test_oversized_base64_and_local_file(tmp_path):
    media = MediaStore(tmp_path / "media", 4, 1, 100)
    with pytest.raises(ValueError):
        await media.resolve("base64://" + base64.b64encode(b"12345").decode(), "x.bin")
    path = tmp_path / "large.bin"
    path.write_bytes(b"12345")
    with pytest.raises(ValueError):
        await media.resolve(str(path), path.name)


async def test_data_uri_retains_mime_extension(media):
    encoded = base64.b64encode(b"jpeg-data").decode()
    path, name = await media.resolve("data:image/jpeg;base64," + encoded, "image.png")
    assert name.endswith((".jpg", ".jpe", ".jpeg"))
    assert open(path, "rb").read() == b"jpeg-data"
    with pytest.raises(ValueError, match="base64"):
        await media.resolve("data:text/plain,unsupported", "file.txt")


async def test_bounded_http_download_and_partial_cleanup(tmp_path):
    async def handler(request):
        return web.Response(body=b"123456789")

    app = web.Application()
    app.router.add_get("/file", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        small = MediaStore(tmp_path / "small", 4, 1, 100)
        with pytest.raises(ValueError):
            await small.resolve(f"http://127.0.0.1:{port}/file", "x.bin")
        assert list(small.root.iterdir()) == []
        large = MediaStore(tmp_path / "large", 100, 1, 1000)
        path, _ = await large.resolve(f"http://127.0.0.1:{port}/file", "x.bin")
        assert open(path, "rb").read() == b"123456789"
    finally:
        await runner.cleanup()


def test_cache_retention_and_full_cache(media):
    old = media.root / "old.bin"
    current = media.root / "new.bin"
    old.write_bytes(b"old")
    current.write_bytes(b"new")
    timestamp = time.time() - 25 * 3600
    os.utime(old, (timestamp, timestamp))
    media.prune()
    assert not old.exists() and current.exists()
    media.cache_bytes = 3
    with pytest.raises(ValueError, match="full"):
        media.check_capacity(1)


def test_seen_store_survives_restart(tmp_path):
    path = tmp_path / "seen.sqlite3"
    store = SeenStore(path)
    assert not store.contains("one")
    store.add("one")
    store.close()
    store = SeenStore(path)
    assert store.contains("one")
    store.close()

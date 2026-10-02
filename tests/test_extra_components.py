import json
from unittest.mock import AsyncMock

import pytest
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Json, Location, Music, Plain, Record
from photon_plugin.imessage.client import PhotonClient
from photon_plugin.imessage.converter import convert_outgoing
from photon_plugin.imessage.route import Route


async def test_json_location_and_music_fallback(config, media):
    chain = MessageChain(
        [
            Json(data={"title": "示例"}),
            Plain("\n"),
            Location(lat=31.2, lon=121.5, title="上海"),
            Plain("\n"),
            Music(url="https://example.com/music", title="歌曲"),
        ]
    )
    parts, _ = await convert_outgoing(chain, config, media)
    assert json.dumps({"title": "示例"}, ensure_ascii=False) in parts[0]["text"]
    assert "maps.apple.com/?ll=31.2,121.5" in parts[0]["text"]
    assert "https://example.com/music" in parts[0]["text"]


async def test_native_extensions_do_not_send_null_mime(config, tmp_path):
    client = PhotonClient(config, tmp_path)
    client.rpc = AsyncMock()
    route = Route("iMessage;+;group", "+15550000002", "group")
    await client.set_group_avatar(route)
    assert "mime_type" not in client.rpc.call_args.kwargs
    await client.set_background(route, "photo.jpg", "image/jpeg")
    assert client.rpc.call_args.kwargs["mime_type"] == "image/jpeg"


async def test_manual_prepare_does_not_create_install_lock(config, tmp_path):
    from dataclasses import replace

    client = PhotonClient(replace(config, auto_install=False), tmp_path)
    client._prepare = AsyncMock()
    client.sidecar = tmp_path
    await client.prepare()
    client._prepare.assert_awaited_once()
    assert not (tmp_path / ".install.lock").exists()


async def test_unknown_voice_format_is_explicit(config, media, tmp_path):
    path = tmp_path / "voice.unknownformat"
    path.write_bytes(b"unknown")
    with pytest.raises(ValueError, match="audio format"):
        await convert_outgoing(
            MessageChain([Record.fromFileSystem(str(path))]), config, media
        )

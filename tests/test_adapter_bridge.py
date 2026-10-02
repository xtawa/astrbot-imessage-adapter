import asyncio
import importlib
import sys
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain
from astrbot.core.platform.message_session import MessageSession
from photon_plugin.imessage.adapter import IMessageAdapter
from photon_plugin.imessage.client import BridgeError, PhotonClient
from photon_plugin.imessage.route import Route


@pytest.fixture
def adapter(config):
    queue = asyncio.Queue()
    values = {
        "id": config.id,
        "project_id": config.project_id,
        "project_secret": config.project_secret,
        "mark_read": False,
    }
    instance = IMessageAdapter(values, {}, queue)
    instance.client = AsyncMock()
    instance.client.send.return_value = ["sent-1"]
    yield instance
    instance.seen.close()


def test_plugin_registers_actual_astrbot_platform():
    importlib.import_module("photon_plugin.main")
    from astrbot.core.platform.register import platform_cls_map

    assert platform_cls_map["photon_imessage"] is IMessageAdapter


async def test_dedup_event_and_proactive_route(adapter, inbound):
    inbound["id"] = "adapter-test-one"
    await adapter.handle_message(inbound)
    await adapter.handle_message(inbound)
    assert adapter._event_queue.qsize() == 1
    event = await adapter._event_queue.get()
    assert event.get_sender_id() == inbound["sender_id"]
    assert event.get_platform_id() == adapter.options.id
    await event.send(MessageChain([Plain("hello")]))
    assert event.last_sent_ids == ["sent-1"]
    route = adapter.client.send.call_args.args[0]
    assert route.chat_id == inbound["chat_id"] and route.phone == inbound["phone"]
    session = MessageSession.from_str(event.unified_msg_origin)
    await adapter.send_by_session(session, MessageChain([Plain("reminder")]))
    assert adapter.client.send.await_count == 2


async def test_dedup_same_message_on_different_lines(adapter, inbound):
    inbound["id"] = "adapter-test-lines"
    await adapter.handle_message(inbound)
    inbound = dict(inbound, phone="+15550000004")
    await adapter.handle_message(inbound)
    assert adapter._event_queue.qsize() == 2


async def test_read_failure_does_not_drop_event(adapter, inbound):
    adapter.options = replace(adapter.options, mark_read=True)
    adapter.client.mark_read.side_effect = BridgeError("read failed")
    inbound["id"] = "adapter-test-read"
    await adapter.handle_message(inbound)
    await asyncio.gather(*adapter.tasks)
    assert adapter._event_queue.qsize() == 1


async def test_streaming_metadata_order_and_typing_cleanup(adapter, inbound):
    inbound["id"] = "adapter-test-stream"
    await adapter.handle_message(inbound)
    event = await adapter._event_queue.get()

    async def stream():
        first = MessageChain([Plain("hello ")])
        first.use_markdown_ = False
        yield first
        yield MessageChain([Plain("world")])

    await event.send_streaming(stream())
    parts = adapter.client.send.call_args.args[1]
    assert parts[0]["text"] == "hello world" and parts[0]["markdown"] is False
    assert adapter.client.typing.await_args_list[0].args[1] is True
    assert adapter.client.typing.await_args_list[-1].args[1] is False

    async def broken():
        yield MessageChain([Plain("partial")])
        raise RuntimeError("generator failure")

    with pytest.raises(RuntimeError):
        await event.send_streaming(broken())
    assert adapter.client.send.await_count == 1
    assert adapter.client.typing.await_args_list[-1].args[1] is False


async def test_foreign_platform_session_rejected(adapter):
    from astrbot.api.platform import MessageType

    session = MessageSession(
        "foreign", MessageType.FRIEND_MESSAGE, Route("chat", "phone", "dm").encode()
    )
    with pytest.raises(ValueError, match="different"):
        await adapter.send_by_session(session, MessageChain([Plain("hi")]))
    adapter.client.send.assert_not_awaited()


async def test_failed_send_never_retries(adapter, inbound):
    inbound["id"] = "adapter-test-failure"
    await adapter.handle_message(inbound)
    event = await adapter._event_queue.get()
    adapter.client.send.side_effect = BridgeError("timeout")
    with pytest.raises(BridgeError):
        await event.send(MessageChain([Plain("hi")]))
    assert adapter.client.send.await_count == 1


async def test_event_native_reaction_and_group_info(adapter, inbound):
    inbound.update(id="adapter-test-group", kind="group", chat_id="iMessage;+;group")
    await adapter.handle_message(inbound)
    event = await adapter._event_queue.get()
    await event.react("❤️")
    adapter.client.react.assert_awaited_once_with(event.route, inbound["id"], "❤️")
    adapter.client.group_info.return_value = {
        "name": "Friends",
        "members": [{"id": "+15550000003"}],
    }
    group = await event.get_group()
    assert group.group_name == "Friends" and group.member_count == 1


async def test_stdio_process_rpc_and_disconnect(config, tmp_path):
    sidecar = tmp_path / "bridge"
    (sidecar / "dist").mkdir(parents=True)
    # A real child process exercises pipe framing, concurrent IDs and shutdown.
    (sidecar / "dist/index.js").write_text(
        """import json, sys
print(json.dumps({"event":"ready","protocol":1}), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    if request["action"] == "disconnect": break
    if request["action"] == "fail":
        print(json.dumps({"id":request["id"],"error":"partial","sent_ids":["delivered"]}), flush=True)
    else:
        print(json.dumps({"id":request["id"],"result":request["action"]}), flush=True)
""",
        encoding="utf-8",
    )
    client = PhotonClient(replace(config, node_path=sys.executable), tmp_path)
    client.sidecar = sidecar
    task = asyncio.create_task(client.run(AsyncMock()))
    try:
        await asyncio.wait_for(client.ready.wait(), 10)
        assert client.connected
        results = await asyncio.gather(client.rpc("one"), client.rpc("two"))
        assert results == ["one", "two"]
        with pytest.raises(BridgeError) as error:
            await client.rpc("fail")
        assert error.value.sent_ids == ["delivered"]
        with pytest.raises(BridgeError):
            await client.rpc("disconnect")
        with pytest.raises(BridgeError):
            await task
        assert not client.connected and not client.pending
    finally:
        await client.stop()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

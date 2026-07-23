"""Tier 2 plugin websocket + push_result (PROTOCOL.md §10).

Drives the REAL `/cprb/ws` route through aiohttp's test client — handshake,
supersede-close-4000, slot hygiene — and exercises `push_result`'s
worker-thread contract against the test's own running loop
(``asyncio.to_thread`` stands in for ComfyUI's prompt worker; calling it
directly from the test coroutine stands in for the deadlock-guard case).

Module-state hygiene: routes.py holds the single plugin slot
(`_connection`) and the captured loop (`_loop`) at module level, exactly
like the real server; the autouse fixture below resets both around every
test so ordering never matters.
"""

from __future__ import annotations

import asyncio
import gc
import sys
import time
import warnings
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cprb import routes as cprb_routes
from cprb.context import BridgeContext
from cprb.routes import build_routes
from cprb.version import __version__


@pytest.fixture(autouse=True)
def _reset_plugin_slot():
    """§10.1's single slot must never leak between tests, whatever the order."""
    cprb_routes._connection = None
    cprb_routes._loop = None
    yield
    cprb_routes._connection = None
    cprb_routes._loop = None


@pytest.fixture
async def client(context: BridgeContext, aiohttp_client):
    app = web.Application()
    app.add_routes(build_routes(context))
    return await aiohttp_client(app)


async def _wait_until(predicate, timeout: float = 2.0) -> None:
    """Poll *predicate* across event-loop turns (the server handler and the
    test share one loop, so yielding IS how its work gets to run)."""
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached within the time bound")
        await asyncio.sleep(0.005)


async def _handshake(ws) -> dict:
    """hello → hello_ack → ready, waited until the server marks it ready."""
    await ws.send_json({"type": "hello", "plugin_version": "0.0.0-test"})
    ack = await ws.receive_json()
    await ws.send_json({"type": "ready"})
    await _wait_until(
        lambda: cprb_routes._connection is not None and cprb_routes._connection.ready
    )
    return ack


# ------------------------------------------------------------ §10.2 handshake


async def test_hello_gets_hello_ack_with_the_server_version(client) -> None:
    async with client.ws_connect("/cprb/ws") as ws:
        await ws.send_json({"type": "hello", "plugin_version": "9.9.9-test"})
        ack = await ws.receive_json()
        assert ack == {"type": "hello_ack", "server_version": __version__}
        connection = cprb_routes._connection
        assert connection is not None
        assert connection.plugin_version == "9.9.9-test"
        assert connection.ready is False  # ready only after the ready message


async def test_ready_marks_the_connection_ready(client) -> None:
    async with client.ws_connect("/cprb/ws") as ws:
        ack = await _handshake(ws)
        assert ack["type"] == "hello_ack"
        assert cprb_routes._connection.ready is True


async def test_unknown_pong_and_non_json_frames_are_tolerated(client) -> None:
    """§10.2: never a disconnect over a bad/unknown message — the handler
    must still be alive to answer a hello afterwards."""
    async with client.ws_connect("/cprb/ws") as ws:
        await ws.send_str("this is not json")
        await ws.send_json({"type": "mystery_from_the_future"})
        await ws.send_json({"type": "pong"})
        await ws.send_json({"type": "hello", "plugin_version": "x"})
        ack = await ws.receive_json()
        assert ack["type"] == "hello_ack"


# ------------------------------------------- §10.1 single slot / supersede


async def test_second_connection_supersedes_the_first_with_close_4000(client) -> None:
    ws1 = await client.ws_connect("/cprb/ws")
    await _wait_until(lambda: cprb_routes._connection is not None)
    first_connection = cprb_routes._connection

    ws2 = await client.ws_connect("/cprb/ws")
    msg = await ws1.receive()
    assert msg.type == aiohttp.WSMsgType.CLOSE
    assert msg.data == 4000
    assert msg.extra == "replaced by a new connection"

    # The replacement owns the slot and still fully works after ws1's own
    # handler has unwound — its cleanup must NOT clobber the new connection.
    await _handshake(ws2)
    assert cprb_routes._connection is not None
    assert cprb_routes._connection is not first_connection
    assert cprb_routes._connection.ready is True

    await ws2.close()
    await _wait_until(lambda: cprb_routes._connection is None)


async def test_disconnect_clears_the_slot(client) -> None:
    ws = await client.ws_connect("/cprb/ws")
    await _wait_until(lambda: cprb_routes._connection is not None)
    await ws.close()
    await _wait_until(lambda: cprb_routes._connection is None)


# ------------------------------------------------------- §10.4 export_ready


async def test_export_ready_relays_payload_minus_type_via_send_event(
    client, context: BridgeContext
) -> None:
    events: list[tuple[str, dict]] = []
    context.send_event = lambda event, payload: events.append((event, payload))
    async with client.ws_connect("/cprb/ws") as ws:
        await ws.send_json({"type": "hello", "plugin_version": "x"})
        await ws.receive_json()
        await ws.send_json({"type": "export_ready", "path": "C:/frame.png", "job": 7})
        await _wait_until(lambda: bool(events))
    assert events == [("cprb.export_ready", {"path": "C:/frame.png", "job": 7})]


async def test_export_ready_without_send_event_is_accepted_not_fatal(client) -> None:
    # The conftest context has send_event=None (the bare construction §10.4
    # promises keeps working); the handler must log-and-continue.
    async with client.ws_connect("/cprb/ws") as ws:
        await ws.send_json({"type": "export_ready", "path": "C:/frame.png"})
        await ws.send_json({"type": "hello", "plugin_version": "x"})
        ack = await ws.receive_json()
        assert ack["type"] == "hello_ack"


# ------------------------------------------------------- §10.3 push_result


def test_push_result_is_false_with_no_connection() -> None:
    assert (
        cprb_routes.push_result(path="/x.mp4", label="", bin_name="ComfyUI Results") is False
    )


async def test_push_result_is_false_before_ready(client) -> None:
    async with client.ws_connect("/cprb/ws") as ws:
        await ws.send_json({"type": "hello", "plugin_version": "x"})
        await ws.receive_json()
        cprb_routes._loop = asyncio.get_running_loop()
        assert cprb_routes.push_result(path="/x.mp4", label="", bin_name="B") is False


async def test_push_result_is_false_without_a_captured_loop(client) -> None:
    async with client.ws_connect("/cprb/ws") as ws:
        await _handshake(ws)
        assert cprb_routes._loop is None  # register() never ran in tests
        assert cprb_routes.push_result(path="/x.mp4", label="", bin_name="B") is False


async def test_push_result_sends_the_full_pr_result_schema(client) -> None:
    async with client.ws_connect("/cprb/ws") as ws:
        await _handshake(ws)
        cprb_routes._loop = asyncio.get_running_loop()

        before = time.time()
        # ComfyUI calls push_result from the prompt worker thread; to_thread
        # reproduces exactly that (a non-loop thread blocking on the send).
        result = await asyncio.to_thread(
            cprb_routes.push_result,
            path="/renders/shot-audio.mp4",
            label="Shot 1",
            bin_name="ComfyUI Results",
        )
        assert result is True

        msg = await ws.receive_json()
        assert msg["type"] == "pr_result"
        assert msg["path"] == "/renders/shot-audio.mp4"
        assert msg["label"] == "Shot 1"
        assert msg["bin_name"] == "ComfyUI Results"
        # §10.3: ALWAYS present, empty/False until later node versions add
        # the widgets — the plugin skips absent/empty values.
        assert msg["color_label"] == ""
        assert msg["insert_at_playhead"] is False
        assert before <= msg["sent_ts"] <= time.time()


async def test_push_result_carries_explicit_color_label_and_playhead(client) -> None:
    async with client.ws_connect("/cprb/ws") as ws:
        await _handshake(ws)
        cprb_routes._loop = asyncio.get_running_loop()
        result = await asyncio.to_thread(
            lambda: cprb_routes.push_result(
                path="/renders/out.mp4",
                label="",
                bin_name="B",
                color_label="Violet",
                insert_at_playhead=True,
            )
        )
        assert result is True
        msg = await ws.receive_json()
        assert msg["color_label"] == "Violet"
        assert msg["insert_at_playhead"] is True


async def test_push_result_refuses_to_block_the_loop_thread(client) -> None:
    """The deadlock guard (§10.3): called ON the loop's own thread — as this
    test coroutine is — it must return False promptly, never hang."""
    async with client.ws_connect("/cprb/ws") as ws:
        await _handshake(ws)
        cprb_routes._loop = asyncio.get_running_loop()
        started = time.monotonic()
        assert cprb_routes.push_result(path="/x.mp4", label="", bin_name="B") is False
        assert time.monotonic() - started < 1.0  # refused, not timed out


async def test_push_result_is_false_when_the_loop_is_unusable(client) -> None:
    """The RuntimeError arm: a captured loop that can no longer schedule work
    (closed here; stopped/wedged in real life) fails the ONE push, silently."""
    async with client.ws_connect("/cprb/ws") as ws:
        await _handshake(ws)
        dead_loop = asyncio.new_event_loop()
        dead_loop.close()
        cprb_routes._loop = dead_loop
        with warnings.catch_warnings():
            # The send_json coroutine is created, then abandoned when the
            # closed loop refuses it; its "never awaited" RuntimeWarning
            # fires at GC. It dies inside push_result itself (nothing
            # retains it — see push_result's own str(exc) logging note), so
            # suppressing here plus a belt-and-braces collect keeps it out
            # of every test's output.
            warnings.simplefilter("ignore", RuntimeWarning)
            result = await asyncio.to_thread(
                cprb_routes.push_result, path="/x.mp4", label="", bin_name="B"
            )
            gc.collect()
        assert result is False

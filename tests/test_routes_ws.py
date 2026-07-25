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
import os
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


async def test_hello_gets_hello_ack_with_the_server_version(
    client, context: BridgeContext
) -> None:
    async with client.ws_connect("/cprb/ws") as ws:
        await ws.send_json({"type": "hello", "plugin_version": "9.9.9-test"})
        ack = await ws.receive_json()
        # §11 added `frames_dir` to this message; §10.2's own two fields are
        # unchanged, and the field set is asserted exactly so a future
        # addition has to come through PROTOCOL.md first.
        assert ack == {
            "type": "hello_ack",
            "server_version": __version__,
            "frames_dir": str(context.input_dir / cprb_routes.FRAMES_DIRNAME),
        }
        connection = cprb_routes._connection
        assert connection is not None
        assert connection.plugin_version == "9.9.9-test"
        assert connection.ready is False  # ready only after the ready message


# ------------------------------------------------------ §11 hello_ack.frames_dir


async def test_hello_ack_frames_dir_is_absolute_and_created(
    client, context: BridgeContext
) -> None:
    """§11: the plugin must be able to write there the moment it handshakes."""
    frames_dir = context.input_dir / cprb_routes.FRAMES_DIRNAME
    assert not frames_dir.exists()  # nothing pre-created it

    async with client.ws_connect("/cprb/ws") as ws:
        await ws.send_json({"type": "hello", "plugin_version": "x"})
        ack = await ws.receive_json()

    reported = Path(ack["frames_dir"])
    assert reported.is_absolute()
    assert reported == frames_dir
    assert reported.is_dir()  # CREATED on demand, not merely named


async def test_hello_ack_frames_dir_survives_an_existing_directory(
    client, context: BridgeContext
) -> None:
    """A second handshake (reconnect) must not fail on the folder already existing."""
    frames_dir = context.input_dir / cprb_routes.FRAMES_DIRNAME
    frames_dir.mkdir(parents=True)
    (frames_dir / "frame_earlier.png").write_bytes(b"keep me")

    async with client.ws_connect("/cprb/ws") as ws:
        await ws.send_json({"type": "hello", "plugin_version": "x"})
        ack = await ws.receive_json()

    assert ack["frames_dir"] == str(frames_dir)
    assert (frames_dir / "frame_earlier.png").exists()  # exist_ok, never clobbered


async def test_hello_ack_still_answers_when_the_frames_dir_cannot_be_created(
    client, context: BridgeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read-only input dir must not break the handshake — §11's log-and-report."""

    def _boom(*_args, **_kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(Path, "mkdir", _boom)
    async with client.ws_connect("/cprb/ws") as ws:
        await ws.send_json({"type": "hello", "plugin_version": "x"})
        ack = await ws.receive_json()

    assert ack["type"] == "hello_ack"
    # The path is still reported so the failure is diagnosable downstream
    # (the panel's export error and PremiereFrameSource both name it).
    assert ack["frames_dir"] == str(context.input_dir / cprb_routes.FRAMES_DIRNAME)


def test_resolve_frames_dir_does_not_create_it(context: BridgeContext) -> None:
    """Asking where the folder is must not be the thing that creates it."""
    resolved = cprb_routes.resolve_frames_dir(context)

    assert resolved == context.input_dir / cprb_routes.FRAMES_DIRNAME
    assert not resolved.exists()
    assert cprb_routes.ensure_frames_dir(context).is_dir()  # ensure_ is the creator


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
    client, context: BridgeContext, tmp_path: Path
) -> None:
    frame = tmp_path / "frame.png"
    frame.write_bytes(b"x")
    events: list[tuple[str, dict]] = []
    context.send_event = lambda event, payload: events.append((event, payload))
    async with client.ws_connect("/cprb/ws") as ws:
        await ws.send_json({"type": "hello", "plugin_version": "x"})
        await ws.receive_json()
        await ws.send_json(
            {"type": "export_ready", "kind": "frame", "path": str(frame), "job": 7}
        )
        await _wait_until(lambda: bool(events))
    # Everything the plugin sent, minus `type`, plus §11.2's two verification
    # fields. Unknown extras (`job`) ride along untouched — §8's additive rule.
    assert events == [
        (
            "cprb.export_ready",
            {
                "kind": "frame",
                "path": str(frame),
                "job": 7,
                "path_exists": True,
                "resolved_path": str(frame),
            },
        )
    ]


# ------------------------------------- §11.2 server-side existence check


async def test_export_ready_reports_a_frame_that_was_never_written(
    client, context: BridgeContext, tmp_path: Path
) -> None:
    """The failure the panel CANNOT detect on its own (§11.7).

    `exportSequenceFrame` is documented to return `true` and sometimes write
    nothing, and the panel's `localFileSystem: "request"` manifest cannot stat
    an arbitrary path to check. This hop is the only one that both sees the
    message and can look at the disk, so a missing file must be named here —
    otherwise the frontend toasts "press Run when ready" for a file that does
    not exist.
    """
    events: list[tuple[str, dict]] = []
    context.send_event = lambda event, payload: events.append((event, payload))
    missing = tmp_path / "never_written.png"
    async with client.ws_connect("/cprb/ws") as ws:
        await ws.send_json({"type": "hello", "plugin_version": "x"})
        await ws.receive_json()
        await ws.send_json({"type": "export_ready", "kind": "frame", "path": str(missing)})
        await _wait_until(lambda: bool(events))

    payload = events[0][1]
    assert payload["path_exists"] is False
    assert "resolved_path" not in payload  # nothing to resolve to


async def test_export_ready_resolves_premieres_doubled_extension(
    client, context: BridgeContext, tmp_path: Path
) -> None:
    """Pre-26.2.2 wrote `foo.png` as `foo.png.png` (§11.7)."""
    doubled = tmp_path / "shot.png.png"
    doubled.write_bytes(b"x")
    events: list[tuple[str, dict]] = []
    context.send_event = lambda event, payload: events.append((event, payload))
    async with client.ws_connect("/cprb/ws") as ws:
        await ws.send_json({"type": "hello", "plugin_version": "x"})
        await ws.receive_json()
        await ws.send_json(
            {"type": "export_ready", "kind": "frame", "path": str(tmp_path / "shot.png")}
        )
        await _wait_until(lambda: bool(events))

    payload = events[0][1]
    assert payload["path_exists"] is True
    assert payload["resolved_path"] == str(doubled)
    assert payload["path"] == str(tmp_path / "shot.png")  # never rewritten


async def test_export_ready_checks_a_clip_path_literally(
    client, context: BridgeContext, tmp_path: Path
) -> None:
    """A clip's path is Premiere's OWN media file — no doubled-name fallback.

    The doubled-extension defect belongs to `exportSequenceFrame` alone, so
    guessing at alternate names for a media file would be inventing facts.
    """
    events: list[tuple[str, dict]] = []
    context.send_event = lambda event, payload: events.append((event, payload))
    media = tmp_path / "footage.mov"
    async with client.ws_connect("/cprb/ws") as ws:
        await ws.send_json({"type": "hello", "plugin_version": "x"})
        await ws.receive_json()
        await ws.send_json({"type": "export_ready", "kind": "clip", "path": str(media)})
        await _wait_until(lambda: bool(events))
    assert events[0][1] == {"kind": "clip", "path": str(media), "path_exists": False}

    events.clear()
    media.write_bytes(b"x")
    async with client.ws_connect("/cprb/ws") as ws:
        await ws.send_json({"type": "hello", "plugin_version": "x"})
        await ws.receive_json()
        await ws.send_json({"type": "export_ready", "kind": "clip", "path": str(media)})
        await _wait_until(lambda: bool(events))
    # No resolved_path for a clip: the path is taken literally either way.
    assert events[0][1] == {"kind": "clip", "path": str(media), "path_exists": True}


async def test_export_ready_with_no_path_is_reported_as_missing(
    client, context: BridgeContext
) -> None:
    events: list[tuple[str, dict]] = []
    context.send_event = lambda event, payload: events.append((event, payload))
    async with client.ws_connect("/cprb/ws") as ws:
        await ws.send_json({"type": "hello", "plugin_version": "x"})
        await ws.receive_json()
        await ws.send_json({"type": "export_ready", "kind": "frame", "path": ""})
        await _wait_until(lambda: bool(events))
    assert events[0][1]["path_exists"] is False


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


# ------------------------------------ §11.1 frames_dir override + retention


def test_frames_dir_honours_an_absolute_env_override(
    context: BridgeContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The escape hatch for an input dir Premiere cannot write to (§11.1).

    The default lives under ComfyUI's input dir, which on a real install is
    often a NAS/UNC share — and the plugin correctly refuses to invent a path,
    so without this the Frame button would be unfixable short of moving
    ComfyUI's whole input directory.
    """
    elsewhere = tmp_path / "local frames"
    monkeypatch.setenv(cprb_routes.FRAMES_DIR_ENV, str(elsewhere))

    assert cprb_routes.resolve_frames_dir(context) == elsewhere
    assert cprb_routes.ensure_frames_dir(context).is_dir()


def test_frames_dir_ignores_a_relative_override_rather_than_raising(
    context: BridgeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same posture as §3.2's output_dir: ignored with a log, never an error."""
    monkeypatch.setenv(cprb_routes.FRAMES_DIR_ENV, "somewhere/relative")

    assert cprb_routes.resolve_frames_dir(context) == (
        context.input_dir / cprb_routes.FRAMES_DIRNAME
    )


def test_frames_dir_ignores_an_empty_override(
    context: BridgeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(cprb_routes.FRAMES_DIR_ENV, "   ")

    assert cprb_routes.resolve_frames_dir(context) == (
        context.input_dir / cprb_routes.FRAMES_DIRNAME
    )


def test_ensure_frames_dir_keeps_only_the_newest_exports(context: BridgeContext) -> None:
    """Unique names per export (§11.7) mean this folder never self-limits.

    Every click writes a fresh full-resolution PNG — 10-25 MB at 4K — into
    ComfyUI's input tree, forever. Retention is a contract (§11.1), so it is
    asserted rather than left to whoever reads the folder later.
    """
    frames = cprb_routes.ensure_frames_dir(context)
    keep = 3
    for index in range(keep + 4):
        frame = frames / f"f{index}.png"
        frame.write_bytes(b"x")
        os.utime(frame, (1_000_000 + index, 1_000_000 + index))
    other = frames / "notes.txt"
    other.write_bytes(b"not a frame")

    removed = cprb_routes._prune_frames_dir(frames, keep=keep)

    assert removed == 4
    survivors = sorted(p.name for p in frames.glob("*.png"))
    assert survivors == ["f4.png", "f5.png", "f6.png"]  # the newest by mtime
    assert other.exists()  # only *.png is ours to prune


def test_prune_is_a_no_op_below_the_threshold(context: BridgeContext) -> None:
    frames = cprb_routes.ensure_frames_dir(context)
    (frames / "only.png").write_bytes(b"x")

    assert cprb_routes._prune_frames_dir(frames, keep=200) == 0
    assert (frames / "only.png").exists()


async def test_hello_does_not_block_the_event_loop_on_a_slow_share(
    client, context: BridgeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§11.1: the frames-dir prep runs off-loop and bounded.

    A UNC share that is asleep makes `mkdir` block for the SMB timeout. On the
    loop that stalls ComfyUI ENTIRELY — every HTTP request, the frontend
    websocket, queue progress — and presents as "ComfyUI froze when I connected
    the Premiere panel". Proven here by making the blocking work outlast the
    timeout and asserting the handshake still answers with the resolved path.
    """
    monkeypatch.setattr(cprb_routes, "_FRAMES_DIR_TIMEOUT_SECONDS", 0.05)

    def _slow(_context):
        time.sleep(0.5)  # blocking, exactly like a sleeping share
        return cprb_routes.resolve_frames_dir(_context)

    monkeypatch.setattr(cprb_routes, "ensure_frames_dir", _slow)

    ticks = 0

    async def _heartbeat():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    beat = asyncio.create_task(_heartbeat())
    try:
        async with client.ws_connect("/cprb/ws") as ws:
            await ws.send_json({"type": "hello", "plugin_version": "x"})
            ack = await ws.receive_json()
    finally:
        beat.cancel()

    assert ack["frames_dir"] == str(context.input_dir / cprb_routes.FRAMES_DIRNAME)
    # The loop kept running while the blocking call was in flight — the whole
    # point. On the loop, `ticks` would be 0.
    assert ticks > 0

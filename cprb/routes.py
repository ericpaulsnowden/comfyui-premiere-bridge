"""HTTP routes (PROTOCOL.md §7) + the Tier 2 plugin websocket (PROTOCOL.md §10).

Tier 1's routes are all in service of the frontend's file affordances: the
version/config rows the panel reads, a server-filesystem browser feeding the
`Browse…` picker, an OS-file-manager reveal, and the resolver that answers
"where would this sequence name write?" without the frontend re-implementing
:func:`~cprb.context.sanitize_name`.

Everything that touches the SERVER's filesystem is loopback-only (§7.1): a
browser on another machine can still type paths and run the nodes, it just
can't browse or reveal folders on the host. Handlers close over the injected
:class:`~cprb.context.BridgeContext`, so tests build an
``aiohttp.web.Application`` from :func:`build_routes` directly — no ComfyUI.

Tier 2 (§10) adds ``GET /cprb/ws`` — the Premiere UXP panel's websocket,
sibling of comfyui-photoshop-bridge's ``/cpsb/ws`` — plus
:func:`push_result`, the worker-thread-safe entry point
``PremiereSendResult`` uses to hand a finished file to the connected plugin.
One plugin at a time (:data:`_connection`); a new connection supersedes the
old with close code 4000, exactly like cpsb.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import ipaddress
import json
import logging
import os
import string
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any

import aiohttp
from aiohttp import web

from .context import BridgeContext
from .version import __version__

logger = logging.getLogger("cprb")

#: PROTOCOL.md §7.2 — the picker's default extension allowlist (Load
#: Premiere Timeline reads Premiere's Final Cut Pro XML export).
DEFAULT_EXTENSIONS = (".xml",)

#: ../../STANDARD-fs-browse.md's `dir` sentinel meaning "the virtual top
#: level": this pack's own default output directory (labeled), "Home", and
#: every drive on Windows / every `/Volumes` mount on macOS.
ROOTS = "ROOTS"

#: STANDARD-fs-browse.md's locality policy for THIS pack, as an explicit,
#: documented, build-time flag (not a request-time param -- flipping this via
#: a query string would let any caller downgrade their own security posture).
#: `True` here is cprb's pre-existing posture (PROTOCOL.md §7.1: everything
#: that touches the SERVER's filesystem is loopback-only) -- porting to the
#: shared contract must never silently flip a pack's posture. Contrast cpsb's
#: own (deliberately `False`) flag for the same route shape.
FS_LIST_LOCAL_ONLY: bool = True

#: STANDARD-fs-browse.md ROOTS listing label for this pack's own default
#: fs/list directory (`context.output_dir` -- ComfyUI's own output dir, not a
#: cprb-owned folder; PROTOCOL.md §7.2).
_FS_LIST_DEFAULT_DIR_LABEL = "ComfyUI Output"

#: Same for the user's home directory (always present, regardless of platform).
_FS_LIST_HOME_LABEL = "Home"

#: Cap on combined `dirs` + `files` entries returned by one `/cprb/fs/list`
#: listing (root or directory) -- so a directory with an enormous number of
#: children can't turn one request into a multi-megabyte response. Counts
#: only entries actually emitted -- a huge pile of hidden dotfiles or
#: extension-filtered-out files never consumes a slot.
_FS_LIST_MAX_ENTRIES = 500


def request_is_loopback(request: web.Request) -> bool:
    """True when *request* comes from this machine (PROTOCOL.md §7.1).

    A forwarded request (``X-Forwarded-For``) is never loopback — the proxy
    hop hides the real origin, so it gets the restricted tier. ``remote``
    being absent (unix sockets, aiohttp test clients) counts as loopback:
    both mean "not a foreign machine".
    """
    if "X-Forwarded-For" in request.headers:
        return False
    remote = request.remote
    if remote is None:
        return True
    try:
        return ipaddress.ip_address(remote).is_loopback
    except ValueError:
        return False


def error_response(status: int, message: str) -> web.Response:
    return web.json_response({"error": message}, status=status)


# --------------------------------------------------- fs/list: ROOTS & drives
#
# 2026-07-19 fix: the §7.2 picker could reach the top of C:\ but no further
# — drive roots reported `parent: null`, which reads as "nothing above
# here" and traps the user instead of offering a way to another drive or a
# NAS/UNC path. The pieces below are their own functions (rather than
# inlined in the handler) specifically so tests can monkeypatch the
# Windows-only bits from macOS/Linux CI — real drive enumeration and real
# `os.name` are both unavailable there.


def _is_windows() -> bool:
    """True on a real Windows host (PROTOCOL.md §7.2's drive-letter world).

    A seam, not a bare ``os.name`` check inlined in the handler, so tests
    can monkeypatch it to exercise the Windows branches (drive enumeration,
    drive-root → "ROOTS" parent) without depending on the dev/CI machine's
    own platform.
    """
    return os.name == "nt"


def _list_windows_drives() -> list[str]:
    """Every existing drive's root, e.g. ``["C:\\\\", "D:\\\\"]``.

    Backs the ``dir="ROOTS"`` sentinel's platform tail on Windows
    (:func:`_platform_root_entries`). Probes A-Z with ``Path.exists()`` —
    factored out to its own function so tests replace it wholesale instead of
    needing real drives to exist.
    """
    return [
        f"{letter}:\\" for letter in string.ascii_uppercase if Path(f"{letter}:\\").exists()
    ]


def _list_macos_volumes() -> list[str]:
    """Every mounted volume under ``/Volumes`` on a macOS (or other POSIX) host.

    Backs the ``dir="ROOTS"`` sentinel's platform tail on POSIX
    (:func:`_platform_root_entries`) -- ``/Volumes`` always contains at least
    a symlink back to the boot volume (e.g. ``Macintosh HD``) plus one entry
    per externally-mounted disk/network share, exactly the set a user reaches
    for when they mean "browse by volume" the way Finder's own sidebar does.
    Hidden entries are skipped (same convention the directory-listing branch
    of ``get_fs_list`` uses) and a stat failure on any one entry is skipped
    rather than aborting the whole root listing. Factored out to its own
    function, like :func:`_list_windows_drives`, so tests replace it wholesale.
    """
    volumes_dir = Path("/Volumes")
    if not volumes_dir.is_dir():
        return []
    try:
        entries = sorted(volumes_dir.iterdir(), key=lambda p: p.name.casefold())
    except OSError:
        return []
    volumes = []
    for entry in entries:
        if entry.name.startswith("."):
            continue
        try:
            is_dir = entry.is_dir()
        except OSError:
            continue
        if is_dir:
            volumes.append(str(entry))
    return volumes


def _fs_entry(name: str, path: Path) -> dict[str, str]:
    """A labeled, directly-navigable ROOTS entry: ``{"name", "path"}``.

    STANDARD-fs-browse.md's general contract is names-only (the client joins
    ``dir``+``sep``+``name`` for a REAL directory listing), but a ROOTS entry
    (this pack's default output dir, "Home", a `/Volumes` mount, a Windows
    drive) has no single parent directory to join against -- each one is
    independently rooted, so the server hands back its actual absolute path
    directly. A deliberate, documented, additive extension of the base
    schema: any consumer that only reads ``name`` still gets a sensible label.
    """
    return {"name": name, "path": str(path)}


def _platform_root_entries(windows: bool) -> list[dict[str, str]]:
    """STANDARD-fs-browse.md ROOTS listing's platform-specific tail.

    Every existing drive letter on Windows (:func:`_list_windows_drives`,
    labeled by its short drive-letter form, e.g. ``"C:"``), or every mounted
    ``/Volumes`` entry on macOS/other POSIX (:func:`_list_macos_volumes`,
    labeled by its bare volume name, e.g. ``"Macintosh HD"``).
    """
    if windows:
        return [_fs_entry(raw.rstrip("\\"), Path(raw)) for raw in _list_windows_drives()]
    return [_fs_entry(Path(raw).name, Path(raw)) for raw in _list_macos_volumes()]


def _fs_list_roots(context: BridgeContext, *, windows: bool) -> list[dict[str, str]]:
    """The top-level entries for ``dir="ROOTS"`` (STANDARD-fs-browse.md).

    Always: this pack's own default fs/list directory (labeled
    :data:`_FS_LIST_DEFAULT_DIR_LABEL`) and the user's home directory, then
    :func:`_platform_root_entries`'s platform-specific tail -- the standard's
    exact ROOTS ordering ("the pack's default dir first (labeled) ... 'Home',
    then platform roots"). 2026-07-19: previously POSIX's ``ROOTS`` resolved
    straight to a real listing of ``/``; this labeled-roots shape (already
    used on Windows) now applies uniformly on every platform.
    """
    roots = [
        _fs_entry(_FS_LIST_DEFAULT_DIR_LABEL, context.output_dir.resolve()),
        _fs_entry(_FS_LIST_HOME_LABEL, Path.home().resolve()),
    ]
    roots.extend(_platform_root_entries(windows))
    return roots


def _is_unc_share_root(directory: Path) -> bool:
    """True when *directory* is a UNC share root (``\\\\server\\share``).

    Re-parsed with ``PureWindowsPath`` rather than trusting the ambient
    ``Path`` flavor, so this is exercisable on any platform: a share root's
    "drive" is the ``\\\\server\\share`` string itself (no drive LETTER),
    which is exactly what distinguishes it from a real drive root like
    ``C:\\`` — the two need different ``parent`` answers (see below).
    """
    drive = PureWindowsPath(str(directory)).drive
    return bool(drive) and not (len(drive) == 2 and drive[1] == ":")


def _fs_root_parent(directory: Path, *, windows: bool) -> str | None:
    """PROTOCOL.md §7.2 ``parent`` for a *directory* that IS a filesystem root.

    - Windows drive root (``C:\\``): climbs to the drive list (``"ROOTS"``)
      — the fix. Today this reported ``null`` and trapped the user.
    - UNC share root (``\\\\server\\share``): reports ``null`` even on
      Windows — there is no portable way to enumerate a server's other
      shares, so there is nothing to climb to.
    - POSIX root (``/``): reports ``null`` — it has no sibling to climb to.
    """
    if windows and not _is_unc_share_root(directory):
        return ROOTS
    return None


def _reveal_folder(path: Path) -> None:
    """Open *path* in the OS file manager, without blocking the server.

    Split out as a module-level function so tests monkeypatch it rather
    than spawning real Explorer/Finder windows.
    """
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    elif sys.platform == "win32":
        os.startfile(str(path))
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _parse_extensions(raw: str) -> tuple[str, ...]:
    """PROTOCOL.md §7.2 ``ext``: a comma-separated, case-insensitive allowlist.

    Entries may be given with or without the leading dot. An empty/blank
    value means "the default allowlist", never "everything" — the picker
    exists to find timeline XML, and a stray ``ext=`` shouldn't silently
    turn it into a general file browser.
    """
    parts = [part.strip().lower() for part in (raw or "").split(",")]
    cleaned = tuple(f".{p.lstrip('.')}" for p in parts if p.strip(". "))
    return cleaned or DEFAULT_EXTENSIONS


# ------------------------------------------------ Tier 2: plugin websocket (§10)
#
# The Premiere UXP panel connects to `GET /cprb/ws` (spike-proven: SPIKES.md
# S6-A, plain ws:// to localhost from inside Premiere 26.3 on the PC). M1's
# surface is deliberately minimal — hello/hello_ack/ready, then server-pushed
# `pr_result` messages — a subset of cpsb's proven `/cpsb/ws` design, minus
# what cprb doesn't need yet (no keepalive loop, no chunked uploads, no
# local_mode: PROTOCOL.md §10.1 is SAME-MACHINE-ONLY, so there is no remote
# posture to negotiate).

#: Bound on waiting for a `pr_result` push to be scheduled and sent on
#: ComfyUI's event loop (PROTOCOL.md §10.3). `push_result` is called from the
#: prompt worker thread mid-`execute`; a wedged plugin connection or a loop
#: that stops pumping now fails this ONE push (the node falls back to its
#: "import manually" summary line) instead of hanging `prompt_worker` — and
#: therefore ComfyUI's entire prompt queue, which processes one item at a
#: time — forever. Same rationale as cpsb's `_TIER2_SEND_TIMEOUT_SECONDS`.
_PUSH_SEND_TIMEOUT_SECONDS = 5.0

#: How long the handshake waits for the frames directory to be prepared
#: before answering anyway (PROTOCOL.md §11.1). Generous for a local disk,
#: short enough that an unreachable share costs a pause rather than the
#: connection.
_FRAMES_DIR_TIMEOUT_SECONDS = 5.0

#: How long §11.2's server-side existence check may take before the relay
#: goes out unverified. Must exceed `_FRAME_POLL_BUDGET_SECONDS` (the poll
#: below) with headroom; on a dead share the whole thing is abandoned and
#: the payload goes out with no verdict rather than holding up the message.
_EXPORT_STAT_TIMEOUT_SECONDS = 5.0

#: PROTOCOL.md §11: the folder the Premiere plugin writes its frame exports
#: into, reported to the plugin as ``hello_ack.frames_dir``. It lives under
#: ComfyUI's INPUT directory (not the output tree) on purpose: these files
#: are material coming IN to a graph, and ComfyUI can always read its own
#: input dir. Sibling naming to §2's ``premiere_timelines`` and §10.5's
#: ``premiere_results``, both of which are output-side.
FRAMES_DIRNAME = "premiere_frames"

#: Environment override for :func:`resolve_frames_dir` (PROTOCOL.md §11.1).
#: The default lives under ComfyUI's input dir, which on a real install is
#: often a NAS/UNC share — and whether Premiere's exporter can write a PNG
#: over SMB is not something this pack can prove for every setup. Without an
#: escape hatch a share that refuses the write bricks "Frame → ComfyUI"
#: outright, since the plugin correctly refuses to invent a path. Same
#: precedent as §3.2's ``output_dir`` override on ``PremiereSaveTimeline``:
#: an ABSOLUTE path is honored, anything else is ignored with a log line.
FRAMES_DIR_ENV = "CPRB_FRAMES_DIR"

#: How many exported stills :func:`ensure_frames_dir` keeps (PROTOCOL.md
#: §11.1). Every "Frame → ComfyUI" click writes a NEW uniquely-named still at
#: full sequence resolution — unique names are required (reusing one races
#: with Premiere still writing the previous file, §11.7), which is exactly
#: what stops the folder ever self-limiting. A 4K PNG is 10-25 MB, so an
#: afternoon of testing is otherwise a gigabyte of orphans in ComfyUI's input
#: tree. 200 is far more than any session revisits.
FRAMES_KEEP_NEWEST = 200


def resolve_frames_dir(context: BridgeContext) -> Path:
    """Where the panel writes its frame exports — computed, NOT created.

    ``$CPRB_FRAMES_DIR`` when it is set to an ABSOLUTE path, else
    ``<input_dir>/premiere_frames/``. A non-absolute override is ignored with
    a log line rather than raising — same posture as
    ``PremiereSaveTimeline``'s ``output_dir`` (§3.2), which is the pack's own
    precedent for "redirect this somewhere the host can actually write".

    Split from :func:`ensure_frames_dir` for the same reason
    :meth:`~cprb.context.BridgeContext.resolve_timeline_dir` is split from
    ``timeline_dir``: asking where the folder is must not be the thing that
    creates it.
    """
    override = os.environ.get(FRAMES_DIR_ENV, "").strip()
    if override:
        candidate = Path(override)
        if candidate.is_absolute():
            return candidate
        logger.warning(
            "cprb: ignoring %s=%r — it must be an ABSOLUTE path; using %s instead",
            FRAMES_DIR_ENV,
            override,
            context.input_dir / FRAMES_DIRNAME,
        )
    return context.input_dir / FRAMES_DIRNAME


def _prune_frames_dir(directory: Path, keep: int = FRAMES_KEEP_NEWEST) -> int:
    """Delete all but the *keep* newest ``*.png`` in *directory*.

    Returns how many files were removed. Every failure is swallowed: this is
    housekeeping on the way to answering a handshake, and a share that won't
    let us stat or unlink must not cost the user their connection.
    """
    try:
        stamped = []
        for entry in directory.glob("*.png"):
            try:
                stamped.append((entry.stat().st_mtime_ns, entry))
            except OSError:
                continue
    except OSError:
        return 0
    if len(stamped) <= keep:
        return 0
    stamped.sort(key=lambda pair: pair[0], reverse=True)
    removed = 0
    for _mtime, entry in stamped[keep:]:
        try:
            entry.unlink()
            removed += 1
        except OSError:
            continue
    if removed:
        logger.info(
            "cprb: pruned %d old frame export(s) from %s (keeping the %d newest)",
            removed,
            directory,
            keep,
        )
    return removed


def ensure_frames_dir(context: BridgeContext) -> Path:
    """:func:`resolve_frames_dir`, created and pruned (PROTOCOL.md §11.1).

    The plugin must be able to write into this folder the instant it
    finishes handshaking -- Premiere's ``Exporter.exportSequenceFrame`` does
    NOT create its output directory and simply reports failure if it is
    missing, and the plugin (``localFileSystem: "request"``) cannot create
    one for a path the user never picked in a folder dialog. So the server
    creates it, on demand, at ``hello`` (:func:`_handle_plugin_message`).

    A creation failure is logged and NOT raised: the handshake matters more
    than the folder, and reporting the resolved path anyway keeps the
    failure diagnosable (the plugin's own export error will name it, and
    ``PremiereFrameSource.VALIDATE_INPUTS`` says the file is missing) rather
    than silently handing the plugin nowhere to write.

    BLOCKING (``mkdir``/``glob``/``unlink``), so callers on the event loop
    must hand this to a thread — the default folder can be a UNC share, and
    a sleeping share would otherwise stall all of ComfyUI for the SMB
    timeout. :func:`_handle_plugin_message` does exactly that.
    """
    directory = resolve_frames_dir(context)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.exception(
            "cprb: could not create the Premiere frames directory %s — "
            "frame exports from the panel will fail until it exists",
            directory,
        )
        return directory
    _prune_frames_dir(directory)
    return directory


@dataclass
class PluginConnection:
    """State for the single active Premiere plugin websocket (PROTOCOL.md §10.1)."""

    ws: web.WebSocketResponse
    ready: bool = False
    plugin_version: str | None = None


#: The single plugin slot (§10.1: one Premiere panel per server; a second
#: connection supersedes the first). Module-level, like every other piece of
#: cprb state that must be reachable from BOTH the aiohttp handlers and the
#: nodes' worker-thread calls; tests reset it between cases.
_connection: PluginConnection | None = None

#: ComfyUI's own running event loop (`PromptServer.instance.loop`), captured
#: by :func:`register` so :func:`push_result` can reach the loop-bound
#: websocket from the prompt worker thread. `None` outside ComfyUI; tests
#: that exercise `push_result` install their own running loop here.
_loop: asyncio.AbstractEventLoop | None = None


def _running_on_push_loop(loop: asyncio.AbstractEventLoop) -> bool:
    """Whether THIS thread is already inside *loop*'s own run loop.

    Not the same question as "is any loop running on this thread": the
    actual hazard is identity — ``run_coroutine_threadsafe(..., loop)
    .result()`` only deadlocks if this thread is the one already driving
    *loop* itself (scheduling work onto a loop, then blocking the very
    thread that must run it). Current ComfyUI executes nodes on a worker
    thread, never the loop's own (verified in cpsb, whose
    ``_running_on_state_loop`` this ports); but that is a fact about one
    host's execution model, not a guarantee, so :func:`push_result` checks
    rather than assumes.
    """
    try:
        return asyncio.get_running_loop() is loop
    except RuntimeError:
        return False


#: How long :func:`_stat_export_path` keeps polling for a frame that
#: Premiere claims to have exported, and how often. OBSERVED LIVE
#: (owner, 2026-07-29): `exportSequenceFrame` returned ``true``, the panel
#: relayed instantly, the server statted instantly — and the file landed a
#: beat LATER, so a perfectly good export was announced as "nothing was
#: written" and (worse) auto-run was suppressed, since a missing file never
#: auto-runs. §11.7 documents exactly this: "it can return before the write
#: completes". A single stat therefore races Premiere's own disk flush; a
#: short poll is the fix. 3 s is far beyond any observed write latency and
#: still comfortably inside `_EXPORT_STAT_TIMEOUT_SECONDS`'s off-loop bound.
_FRAME_POLL_BUDGET_SECONDS = 3.0
_FRAME_POLL_INTERVAL_SECONDS = 0.15


def _stat_export_path(kind: Any, raw_path: Any) -> dict[str, Any]:
    """Blocking half of :func:`_verify_export_path`.

    A clip is one ``stat`` — its path is Premiere's own already-existing
    media file, so absence means offline media, not latency. A frame is a
    POLL (:data:`_FRAME_POLL_BUDGET_SECONDS`): Premiere reports success
    before the bytes land, so the first look is often one beat too early.
    Runs on a worker thread (``asyncio.to_thread``), so the sleeps below
    never touch the event loop.
    """
    from .nodes_source import resolve_frame_path

    text = str(raw_path or "").strip()
    if not text:
        return {"path_exists": False}
    if kind == "frame":
        # Only a frame gets the doubled-extension fallback: that defect is
        # `exportSequenceFrame`'s alone (§11.7).
        started = time.monotonic()
        while True:
            resolved = resolve_frame_path(text)
            if resolved is not None:
                waited = time.monotonic() - started
                if waited > _FRAME_POLL_INTERVAL_SECONDS:
                    # The race actually happened — say so, because this line
                    # is the evidence that the poll is earning its keep.
                    logger.info(
                        "cprb: frame %s appeared %.2fs after Premiere reported it "
                        "(the documented late write, §11.7)",
                        resolved.name,
                        waited,
                    )
                return {"path_exists": True, "resolved_path": str(resolved)}
            if time.monotonic() - started >= _FRAME_POLL_BUDGET_SECONDS:
                return {"path_exists": False}
            time.sleep(_FRAME_POLL_INTERVAL_SECONDS)
    try:
        exists = Path(text).is_file()
    except OSError:
        exists = False
    return {"path_exists": exists}


async def _verify_export_path(payload: dict[str, Any]) -> dict[str, Any]:
    """§11.2's server-side existence check: ``path_exists`` (+ ``resolved_path``).

    This is the ONLY hop that both sees the message and can look at the disk.
    The panel cannot: ``localFileSystem: "request"`` grants access only to
    entries the user picked in a dialog, so its own probe answers "cannot
    tell" for a path under ComfyUI's input dir — which makes its whole
    retry-under-a-fresh-name ladder unreachable on a real install. Meanwhile
    ``exportSequenceFrame`` is documented to return ``true`` and sometimes
    write nothing (§11.7). Without this check the panel says "sent", the
    frontend toasts "press Run when ready", and the user only learns at Run
    time — from advice ("click Frame → ComfyUI again") that would loop
    forever, because nothing upstream can detect the failure.

    Two additive fields, never a rewrite of what the plugin sent, so §11.3's
    relay stays verbatim in spirit: the frontend chooses whether to use them.
    Off the loop and bounded, for :func:`ensure_frames_dir`'s reasons — a
    ``stat`` against a dead share must not stall ComfyUI. On timeout the
    fields are simply absent, and the frontend treats an absent
    ``path_exists`` as "unverified" rather than "missing".
    """
    kind = payload.get("kind")
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_stat_export_path, kind, payload.get("path")),
            timeout=_EXPORT_STAT_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.warning(
            "cprb: checking whether %r exists took longer than %.0fs — relaying it "
            "unverified",
            payload.get("path"),
            _EXPORT_STAT_TIMEOUT_SECONDS,
        )
        return {}
    except OSError:
        logger.exception("cprb: could not check whether %r exists", payload.get("path"))
        return {}


async def _handle_plugin_message(
    context: BridgeContext, connection: PluginConnection, raw: str
) -> None:
    """Dispatch one inbound plugin frame (PROTOCOL.md §10.2 / §10.4).

    Same tolerant posture as cpsb's `_handle_plugin_message`: a non-JSON
    frame or an unknown ``type`` is logged and ignored, never a reason to
    drop the connection — the plugin and server must stay pairable across
    version skew (§8's additive-only rule applies to this surface too).
    """
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("cprb: plugin sent a non-JSON frame, ignoring")
        return
    msg_type = msg.get("type") if isinstance(msg, dict) else None

    if msg_type == "hello":
        connection.plugin_version = msg.get("plugin_version")
        # PROTOCOL.md §11 adds ONE additive field to §10.2's hello_ack:
        # `frames_dir`, the absolute folder the plugin exports frames into,
        # created here on demand so it is writable the moment the plugin
        # needs it. Additive per §8 — an M1 plugin that ignores the field
        # keeps working unchanged.
        #
        # OFF THE EVENT LOOP, and bounded: ensure_frames_dir does real disk
        # work (mkdir + a prune pass) and the default folder lives under
        # ComfyUI's input dir, which on a real install is often a NAS/UNC
        # share. A blocking mkdir against a sleeping share would stall the
        # whole aiohttp loop for the SMB timeout — every HTTP request, the
        # frontend websocket, queue progress — and present as "ComfyUI froze
        # when I connected the Premiere panel". So: a thread, a timeout, and
        # the resolved path reported either way (§11.1's log-and-report).
        frames_dir = resolve_frames_dir(context)
        try:
            frames_dir = await asyncio.wait_for(
                asyncio.to_thread(ensure_frames_dir, context),
                timeout=_FRAMES_DIR_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.warning(
                "cprb: preparing the Premiere frames directory %s took longer than %.0fs "
                "(a slow or unreachable share?) — answering the handshake anyway; set %s "
                "to a local absolute path to redirect frame exports",
                frames_dir,
                _FRAMES_DIR_TIMEOUT_SECONDS,
                FRAMES_DIR_ENV,
            )
        logger.info("cprb: Premiere frames directory is %s", frames_dir)
        await connection.ws.send_json(
            {
                "type": "hello_ack",
                "server_version": __version__,
                "frames_dir": str(frames_dir),
            }
        )
    elif msg_type == "ready":
        connection.ready = True
        logger.info(
            "cprb: plugin ready (plugin_version=%s)", connection.plugin_version
        )
    elif msg_type == "pong":
        # Accepted so a future keepalive loop (§10.2's noted hardening item)
        # slots in without a plugin-side change; M1 sends no pings.
        pass
    elif msg_type == "export_ready":
        # §10.4 accepted this message in M1; §11 fixes its payload schema and
        # ships the consuming frontend listener (`web/cprb/premiere_source.js`,
        # which writes the payload into `PremiereFrameSource` /
        # `PremiereClipSource`'s hidden widgets):
        #
        #   kind    "frame" | "clip"
        #   path    absolute — the exported still, or (kind=clip) the clip's
        #           OWN media file: no export, no re-encode
        #   label   human name (sequence or clip) for toasts/logs
        #   frame   ticks (string) + seconds (number) it came from
        #   clip    start_seconds + end_seconds (the clip's in/out in its
        #           SOURCE media)
        #
        # The relay is VERBATIM (everything except `type`) and stays
        # schema-agnostic on purpose: §8's additive rule means a later
        # plugin may send fields this server has never heard of, and the
        # frontend — not this hop — is what decides which of them it uses.
        # This hop adds exactly TWO fields of its own (§11.2) and rewrites
        # nothing.
        payload = {key: value for key, value in msg.items() if key != "type"}
        payload.update(await _verify_export_path(payload))
        logger.info("cprb: plugin export_ready: %s", payload)
        if payload.get("path_exists") is False:
            logger.warning(
                "cprb: the panel reported a %r export at %r but nothing is on disk "
                "there — Premiere's exporter can report success without writing "
                "(PROTOCOL.md §11.7)",
                payload.get("kind"),
                payload.get("path"),
            )
        if context.send_event is None:
            # Only reachable in tests and in a torn-down server: __init__.py
            # always wires send_event. Said out loud because the panel has
            # already told the user "sent" by this point.
            logger.warning(
                "cprb: no frontend event sink — export_ready was received but cannot "
                "reach any ComfyUI tab; the file is on disk at %r",
                payload.get("path"),
            )
        else:
            context.send_event("cprb.export_ready", payload)
    else:
        logger.debug("cprb: ignoring unknown plugin message type: %r", msg_type)


def push_result(
    *,
    path: str,
    label: str,
    bin_name: str,
    color_label: str = "",
    insert_at_playhead: bool = False,
) -> bool:
    """Send one `pr_result` message to the connected plugin (PROTOCOL.md §10.3).

    Called by ``PremiereSendResult.execute`` on ComfyUI's prompt worker
    thread; the plugin websocket lives on ComfyUI's event loop
    (:data:`_loop`), so the send crosses threads via
    ``asyncio.run_coroutine_threadsafe``, bounded to
    :data:`_PUSH_SEND_TIMEOUT_SECONDS` — cpsb's `_send_tier2_open` pattern.

    Every §10.3 field is always present in the message — ``color_label``/
    ``insert_at_playhead`` stay ``""``/``False`` until later node versions
    add their widgets; the plugin skips absent/empty ones — so the plugin
    never needs per-field feature detection.

    Returns:
        ``True`` when the message was handed to the socket within the
        bound. ``False`` — NEVER an exception; the caller's fallback is a
        human "import manually" summary line, not error handling — when no
        plugin is connected/ready, no loop is available, the send timed
        out, or the socket failed mid-send.
    """
    connection = _connection
    loop = _loop
    if connection is None or not connection.ready or loop is None:
        return False
    if _running_on_push_loop(loop):
        # Blocking the loop's own thread on work scheduled onto that same
        # loop deadlocks for real (see _running_on_push_loop). Refuse — the
        # node's "import manually" fallback is strictly better than a hang.
        logger.warning(
            "cprb: push_result called from ComfyUI's own event-loop thread; "
            "refusing the cross-thread wait (it would deadlock) — import manually"
        )
        return False

    message = {
        "type": "pr_result",
        "path": path,
        "label": label,
        "bin_name": bin_name,
        "color_label": color_label,
        "insert_at_playhead": insert_at_playhead,
        "sent_ts": time.time(),
    }
    future: concurrent.futures.Future[None] | None = None
    try:
        future = asyncio.run_coroutine_threadsafe(connection.ws.send_json(message), loop)
        future.result(timeout=_PUSH_SEND_TIMEOUT_SECONDS)
    except (concurrent.futures.TimeoutError, RuntimeError) as exc:
        if future is not None:
            future.cancel()  # Best-effort; harmless if already done/failed.
        # str(exc), not exc: a retained record (pytest caplog, log
        # aggregators) holding the exception would pin its traceback — and
        # through it the abandoned send_json coroutine — long past this call.
        logger.error(
            "cprb: pr_result push for %s did not send within %.0fs (%s) — import manually",
            path,
            _PUSH_SEND_TIMEOUT_SECONDS,
            str(exc),
        )
        return False
    except Exception:
        # E.g. ConnectionResetError from a socket that died after `ready`.
        # push_result NEVER raises into a running workflow (§10.3).
        logger.exception("cprb: pr_result push for %s failed — import manually", path)
        return False
    logger.info("cprb: pushed pr_result for %s (bin %r)", path, bin_name)
    return True


def _register_all(context: BridgeContext, routes: web.RouteTableDef) -> None:
    @routes.get("/cprb/version")
    async def get_version(_request: web.Request) -> web.Response:
        return web.json_response({"version": __version__})

    @routes.get("/cprb/config")
    async def get_config(request: web.Request) -> web.Response:
        return web.json_response(
            {
                "is_local": request_is_loopback(request),
                "output_dir": str(context.output_dir),
                "input_dir": str(context.input_dir),
            }
        )

    @routes.get("/cprb/fs/list")
    async def get_fs_list(request: web.Request) -> web.Response:
        """STANDARD-fs-browse.md's shared cross-plugin contract. Gated by
        :data:`FS_LIST_LOCAL_ONLY` (``True`` for cprb -- PROTOCOL.md §7.1).
        The loopback check runs before ROOTS/absolute-path handling below:
        ROOTS is an exception to the absolute-path requirement, never to
        this one.

        Query params:
            dir: Optional. Empty/omitted resolves to this pack's own default
                directory (``context.output_dir``); the literal ``"ROOTS"``
                (:data:`ROOTS`) returns the virtual top-level listing
                (:func:`_fs_list_roots`). Any other value MUST be an absolute
                path naming an existing, listable directory.
            ext: Optional, comma-separated, case-insensitive
                (:func:`_parse_extensions`) -- defaults to
                :data:`DEFAULT_EXTENSIONS`.

        Returns 200 with ``{"dir", "parent", "sep", "dirs", "files",
        "truncated"}`` (STANDARD-fs-browse.md) -- names-only ``dirs``/
        ``files`` entries for a real directory listing (the client joins
        with ``dir``+``sep``); ROOTS entries additionally carry ``path``
        (:func:`_fs_entry`). 403 when :data:`FS_LIST_LOCAL_ONLY` and the
        caller isn't loopback; 400 for a relative/non-existent/non-directory
        ``dir``.
        """
        if FS_LIST_LOCAL_ONLY and not request_is_loopback(request):
            return error_response(403, "file browsing is host-machine-only — PROTOCOL.md §7.1")
        raw = (request.query.get("dir") or "").strip()
        windows = _is_windows()
        if raw == ROOTS:
            return web.json_response(
                {
                    "dir": ROOTS,
                    "parent": None,
                    "sep": os.sep,
                    "dirs": _fs_list_roots(context, windows=windows),
                    "files": [],
                    "truncated": False,
                }
            )
        directory = Path(raw) if raw else context.output_dir
        if not directory.is_absolute():
            return error_response(400, f"dir must be an absolute path (got {raw!r})")
        extensions = _parse_extensions(request.query.get("ext", ""))
        try:
            entries = sorted(directory.iterdir(), key=lambda p: p.name.casefold())
        except OSError as exc:
            return error_response(400, f"could not list {directory}: {exc}")

        dirs: list[dict[str, str]] = []
        files: list[dict[str, object]] = []
        count = 0
        truncated = False
        for entry in entries:
            if entry.name.startswith("."):
                continue
            try:
                is_dir = entry.is_dir()
            except OSError:
                continue
            if is_dir:
                if count >= _FS_LIST_MAX_ENTRIES:
                    truncated = True
                    break
                dirs.append({"name": entry.name})
                count += 1
                continue
            if entry.suffix.lower() not in extensions:
                continue
            try:
                stat_result = entry.stat()
            except OSError:
                continue
            if count >= _FS_LIST_MAX_ENTRIES:
                truncated = True
                break
            files.append(
                {"name": entry.name, "size": stat_result.st_size, "mtime": stat_result.st_mtime}
            )
            count += 1

        at_root = directory.parent == directory
        parent = _fs_root_parent(directory, windows=windows) if at_root else str(directory.parent)
        return web.json_response(
            {
                "dir": str(directory),
                "parent": parent,
                "sep": os.sep,
                "dirs": dirs,
                "files": files,
                "truncated": truncated,
            }
        )

    @routes.post("/cprb/open_folder")
    async def post_open_folder(request: web.Request) -> web.Response:
        if not request_is_loopback(request):
            return error_response(
                403, "revealing folders is host-machine-only — PROTOCOL.md §7.1"
            )
        try:
            body = await request.json()
        except Exception:  # malformed body is a client error
            return error_response(400, "body must be JSON")
        raw = str((body or {}).get("path") or "").strip()
        if not raw:
            return error_response(400, "path is required")
        path = Path(raw)
        folder = path if path.is_dir() else path.parent
        if not folder.is_dir():
            return error_response(404, f"no such folder: {folder}")
        try:
            _reveal_folder(folder)
        except Exception as exc:
            logger.exception("cprb: could not reveal %s", folder)
            return error_response(500, f"could not open the folder: {exc}")
        return web.json_response({"ok": True})

    @routes.get("/cprb/ws")
    async def websocket_route(request: web.Request) -> web.WebSocketResponse:
        """PROTOCOL.md §10.1: the Premiere plugin's websocket.

        One plugin at a time: a new connection closes any previous one with
        code 4000 ("replaced by a new connection" — the plugin treats 4000
        as "another panel took over", cpsb's exact supersede convention),
        then installs itself as :data:`_connection`. On ANY exit the slot is
        cleared only if it still points at THIS connection — a superseded
        socket's late cleanup must never clobber its replacement.
        """
        global _connection
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        previous = _connection
        if previous is not None:
            await previous.ws.close(code=4000, message=b"replaced by a new connection")

        connection = PluginConnection(ws=ws)
        _connection = connection
        logger.info("cprb: plugin websocket connected")
        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await _handle_plugin_message(context, connection, msg.data)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.warning("cprb: plugin websocket error: %s", ws.exception())
        finally:
            if _connection is connection:
                _connection = None
                logger.info("cprb: plugin websocket disconnected")

        return ws

    @routes.get("/cprb/timeline_dir")
    async def get_timeline_dir(request: web.Request) -> web.Response:
        """PROTOCOL.md §7.2/§3.2: ``output_dir``-aware. Passing the SAME
        (possibly empty) ``output_dir`` the node itself will run with makes
        this resolve the IDENTICAL effective folder
        ``PremiereSaveTimeline.execute`` writes to -- an omitted/blank
        ``output_dir`` behaves exactly as before this param existed. Never
        400s on a non-absolute ``output_dir``: `resolve_timeline_dir` falls
        back to the default base on its own (the same clean-rejection rule
        the node itself warns about; this route has no UI summary to put a
        warning in, so it just quietly resolves what the node WOULD
        actually do).
        """
        sequence_name = request.query.get("sequence_name", "")
        output_dir = request.query.get("output_dir", "")
        directory = context.resolve_timeline_dir(sequence_name, output_dir)
        return web.json_response({"dir": str(directory), "exists": directory.is_dir()})


def build_routes(context: BridgeContext) -> web.RouteTableDef:
    """Every cprb route on a fresh table — the tests' entry point."""
    routes = web.RouteTableDef()
    _register_all(context, routes)
    return routes


def register(context: BridgeContext) -> None:
    """Attach routes to the running ComfyUI server (called from
    ``__init__.py``; the only function in the pack that touches PromptServer).

    Registers onto ``PromptServer.instance.routes`` — NOT directly onto the
    aiohttp app — because ComfyUI mirrors exactly that table under the
    ``/api`` prefix at startup (server.py: "Prefix every route with /api"),
    and the frontend's `fetchApi` calls `/api/cprb/...`.

    Also captures ComfyUI's own event loop into :data:`_loop` (PROTOCOL.md
    §10.3): the plugin websocket is bound to that loop, and
    :func:`push_result` — called from the prompt worker thread — needs it
    for its ``run_coroutine_threadsafe`` hop.
    """
    global _loop
    from server import PromptServer  # ComfyUI's module; import only inside ComfyUI

    _register_all(context, PromptServer.instance.routes)
    _loop = PromptServer.instance.loop
    logger.info("cprb: routes registered")

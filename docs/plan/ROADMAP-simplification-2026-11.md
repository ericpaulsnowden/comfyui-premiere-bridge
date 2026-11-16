# Simplification audit, November 2026

An audit of this pack for code that can be removed or simplified without
changing behaviour, and for copied logic that has drifted from its twin in the
sibling packs (`comfyui-epsnodes`, `comfyui-photoshop-bridge`). The audit ran
at v0.16.4. Each item below lists its evidence so it can be re-checked.

Status words: **Shipped** (in `main`), **Open** (agreed, not built),
**Needs Premiere** (only the owner can confirm it, in real Premiere),
**Owner decision** (a choice, not a defect), **Leave** (looked at, kept on
purpose).

## Shipped

| Item | Evidence | Version |
| --- | --- | --- |
| One copy of `nodeClassOf`, `findWidget`, `toast`, `basename` in `web/cprb/api.js` | Were declared in three files; the `nodes.js` copy of `nodeClassOf` had lost the `node.type` fallback | v0.16.5 |
| Dead `countVia` variable removed from `insertAtPlayhead` | Assigned three times, never read | v0.16.5 |
| Comments that called settled questions "unproven" now cite the SPIKES result | `export_source.js` (file readers, `getFrameSize`, linked A/V), `import_recipe.js` (insert, ingest guard, property setter), `timeline_write.py` (UNC pathurl); S6 box ticked in `SPIKES.md` | v0.16.5 |
| Frontend modules are loaded by a test (`tests/test_frontend_modules_js.py`) | No test imported `web/cprb/*.js` together before | v0.16.5 |

## Open: drift that changes behaviour

| Item | Evidence |
| --- | --- |
| Folder browser: `..` from `/` should return to the roots list on macOS and Linux | `_fs_root_parent` returns `null` for `/`; EPSNodes fixed the same dead end on 2026-07-26 |
| Saving over an existing file should work on shares that refuse `os.replace` | Only EPSNodes handles `FileExistsError` from gvfs/FUSE mounts |

## Needs Premiere: settled-spike leftovers in the panel

`docs/SPIKES.md` records each of these as never needed or unreachable on a real
install. Removing them is safe by that record, but the panel can only be run in
Premiere, so each needs one manual check, and `docs/PROTOCOL.md` describes
several of them and must change in the same commit.

| Code | Lines | SPIKES | Manual check |
| --- | --- | --- | --- |
| Panel-side file probe and retry ladder: `cprbNoteFsProbe`, `cprbLooksMissing`, `cprbFileExists`, `cprbLocateExport` in `export_source.js` | ~165 | S7-b: "returns `unknown` on every real install and the retry ladder below it is unreachable" | Frame → ComfyUI still delivers the frame |
| `getIsSelected()` sweep fallback in `export_source.js` | ~75 | S7-c: "was never needed" | Clip → ComfyUI with a clip selected, and with nothing selected |
| `getSettings()` frame-size fallback and prototype dump | ~31 | S7-b: "were never needed" | Same click as the first; log shows the size via `getFrameSize()` |
| Export shape A2 (trailing separator) | ~15 | S7-b: "never reached and can be considered dead weight" | Same click as the first |
| Direct-assignment property setter in `stampProperties` | ~20 | 2026-07-25: `createSetValueAction` is read-back verified | Log reads "tagged via createSetValueAction" |

## Open, low risk

- **One display-name reader.** `folderDisplayName` (`import_recipe.js`) and
  `cprbDisplayName` (`export_source.js`) are the same three-step ladder. Move
  one to `helpers.js`. About 40 lines. Needs the "second Send reuses the same
  bin" check in Premiere.
- **One error-to-text helper in the panel.** `describeError`, `cprbErrMsg` and
  an inline `error && error.message ? …` (13 uses) do the same job.
- **Parameters nobody passes.** `_as_float(default=)`, `frontTruncate(maxChars)`;
  `_prune_frames_dir(keep=)` is passed only by tests.

## Owner decision

- **ping/pong.** The panel answers `ping` and the server accepts `pong`, but
  the server never sends `ping`. Kept for a future keepalive
  (`PROTOCOL.md` §10). About 11 lines.
- **Exporter probe.** `cprbLogExporterProbe` logs `typeof`/arity, which SPIKES
  calls meaningless for native bindings; PROTOCOL names it as deliberate.
- **Locality guard and a missing remote address.** This pack and EPSNodes treat
  a request with no `remote` as local; the Photoshop bridge treats it as not
  local and also rejects `X-Real-IP`. One answer should apply to all three.
- **Listing timeouts.** This pack and the Photoshop bridge bound folder
  listings at 5 s; EPSNodes does not. EPSNodes lists Linux mount points as
  roots; this pack does not.

## Leave

- `migrateSegmentSocketNames`, `LEGACY_COLOR_LABEL_NONE` and the legacy frame
  name pattern exist for workflows and files already saved on disk.
- The server-side doubled-extension guard (SPIKES: "the guard stays").
- The 2-argument `importFiles` path, still needed when there is no bin.
- Node class ids (`PremiereGetShot` and the rest) are frozen by PROTOCOL §8.

## Copies shared with the sibling packs

The three packs install separately, so they cannot import from each other.
These files are copies; a fix in one does not reach the others unless it is
ported by hand.

| Area | Here | State at the audit |
| --- | --- | --- |
| Folder browse backend | `cprb/routes.py` `_fs_*` helpers | Seven helpers identical to EPSNodes; root handling had drifted (fixed above) |
| Locality guard | `cprb/routes.py` `request_is_loopback` | Identical to both EPSNodes copies |
| `scripts/check_version_sync.py` | same path | Byte-identical in all three |
| `scripts/bump_version.py` | same path | Differs in names only |
| Panel `connection.js` | `premiere_plugin/connection.js` | Ported from the Photoshop panel; this copy has no test that loads it |

A drift check (a script that diffs the designated copies across the three
repos) was proposed in August 2026 as `tools/check_drift.py` and has not been
built. It is the smallest thing that would have caught the root-handling drift.

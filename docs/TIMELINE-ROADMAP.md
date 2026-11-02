# Timeline editor: status and roadmap

Frontend: `studio/static/timeline.js` (class `TakeTimelineEditor`) + `studio/static/studio.css`.
Backend: `studio/timeline.py` (validate, save, render), `studio/beats.py`, endpoints in `studio/app.py`
(`/api/s/{sid}/e/{eid}/timeline[/source|/waveform|/thumb|/beats|/export]`).
Tests: `tests/test_timeline.py`, `tests/test_beats.py`, `tests/test_timeline_browser.py`.
User docs: `STUDIO.md` ("Editing in Takes", shortcuts, Beats, Markers, Render range).

## Status

| Phase | What | State |
| --- | --- | --- |
| Pro basics | timecode, frame step, snapping, Fit, ctrl-wheel zoom, shortcuts, track mutes | done |
| A | multi-select, copy/paste, slip, roll, clip filmstrips | done |
| B | beat detection (numpy), beat grid, beat snapping, Cut to beat, half/double time | done |
| C | markers, In/Out range, **Render range** previews | done |
| D1 | per-clip fade in / fade out (video + audio) | done |
| D2 | crossfades between clips | done |
| E | titles / captions overlay | done |
| F1 | extra audio lanes | done |
| F2 | video overlay lane | not started |
| G1 | clip speed (0.25-4x, pitch-preserving audio) | done |
| G2 | reframe (crop / position / scale) | not started |

Run all tests: `PATH=$PWD/.runtime/ffmpeg/usr/bin:$PATH LD_LIBRARY_PATH=$PWD/.runtime/ffmpeg/usr/lib/x86_64-linux-gnu PLAYWRIGHT_BROWSERS_PATH=/tmp/reelwright-browsers .venv/bin/python -m pytest -q tests`
Expected: everything passes except 3 errors in `tests/test_studio_e2e.py` (missing
`video_director_agent/i2v_template.json`, unrelated to the editor).

## How a new saved field must be added (checklist)

The save step keeps only fields it knows, so a field that is not in `validate()` is silently dropped.
For every new field in the edit (`clips[]`, `music`, or top level):

1. `timeline.default_edit` and `timeline.view` (`setdefault`) so old projects load with defaults.
2. `timeline.validate`: type checks (`number()`, `flag()` helpers), ranges, atomic rejection (400).
3. `timeline.retime`: anything measured in frames must scale when the project fps changes.
4. `timeline.slice_clips`: range previews slice clips; decide what each field does for a partial clip.
5. `timeline.render`: apply it in the right place (see "Render pipeline").
6. Client: `changeEdit` operations that create clips (`split`, `trimTo`, `duplicate`, `paste`,
   `planBeatCuts`, `addTake`) must copy / reset the field sensibly.
7. Tests: validate (good + bad), retime, a real-ffmpeg render test, a browser test, and docs.

## Render pipeline (timeline.render)

Per clip: extract `NNNN_source.mp4` (scale/pad, `fps=..:start_time=0`, exact frame count, audio padded),
optional LatentSync lip-sync (cached by source/in/frames/video/voice), then `NNNN_clip.mp4` with the
clip volume. Final: one `filter_complex` concat of all clips (+ amix with the single music track), alimiter,
then a frame-count check (`Timeline export failed its frame-count check` = an off-by-one somewhere).
Range previews (`window=(first,last)`) slice the clips, skip lip-sync, seek the prepared music, and are
stored in `outputs.timeline_previews` (never `outputs.final`).
Put per-clip picture/audio effects in the **final filter graph**, not in the cached per-clip files, so they
do not invalidate the lip-sync cache.

## Remaining phases (design notes)

### D1. Fades (small)
Per clip `fade_in`, `fade_out` (whole frames, `fade_in + fade_out <= frames`).
Render in the final graph: video `fade=t=in:s=0:n=N` / `fade=t=out:s=frames-N:n=N`; audio `afade` with
`st`/`d` in seconds. Slices (`slice_clips`): keep a fade only when its edge is inside the range.
Client: clamp after every edit (a trim can shorten a clip below its fades); split gives the fade-out to
the right half and keeps the fade-in on the left.

### D2. Crossfades (medium, risky)
A crossfade overlaps two clips, so the timeline gets shorter than the sum of clip lengths. Needs a model
decision first: store `transition: {type, frames}` on the *incoming* clip and define timeline position as
`sum(frames) - sum(transition frames)`. Everything that sums clip frames (total, `start()`, `cuts()`,
beat alignment, snapping, markers, range, lip-sync positions, frame-count verification) must use that.
Render with `xfade` + `acrossfade`. Do not allow a transition on a lip-sync clip's overlapped part.
Consider doing this only after D1, with a feature flag in settings.

### E. Titles and captions (medium)
`titles: [{id, text, start_frame, frames, x, y, size, color, align, box}]`, validated like markers
(max count, max text length). Render: draw each title to a transparent PNG with Pillow and overlay it with
`overlay=enable='between(n,a,b)'` in the final graph (portable: no ffmpeg font/fontconfig dependency).
Font: ship an OFL/permissive TTF in `studio/static/fonts/` (the repo only has `.woff2`, which Pillow cannot
read); keep the browser preview and the PNG renderer using the same family and the same size/position maths
(positions in 0..1 of the frame, sizes as a fraction of frame height) or preview and export will differ.
UI: a title lane or markers-style flags, an inspector panel, drag to move/resize in time.
Range previews: include titles that overlap the window, shifted by `-first`.

### F. More tracks (large)
Step 1, extra audio lanes (voice-over, effects): generalise `music` into `audio: [{id, file, ...}]` while
keeping `music` as the lip-sync guide track; render with `amix` of N prepared tracks; UI lanes under MUSIC
with per-lane mute/solo/volume; waveforms use `/timeline/waveform` (needs the file allowed in
`source_info(audio=True)`).
Step 2, a video overlay lane (picture-in-picture, B-roll): clips with `track: 1`, `start_frame` (they are not
rippled), `overlay` position/scale/opacity; render with `overlay`. This breaks the "one ripple track"
assumption in many client functions (`start()`, `cuts()`, `remove()`, drag, snapping): introduce a
`track` argument everywhere instead of special cases.

### G. Speed and reframe (medium-high)
Per clip `speed` (0.25..4): timeline `frames` stay the length on the timeline, source consumed is
`frames * speed`; `validate` "extends beyond source" and `sourceFrames`/slip/roll bounds/filmstrip times must
use `frames * speed`. Render: `setpts=PTS/speed` + `atempo` chain (each factor 0.5..2). Forbid on lip-sync clips.
Reframe: `crop {x, y, scale}` for 9:16 <-> 16:9; apply in the per-clip extraction (before lip-sync) or in the
final graph (then lip-sync sees the uncropped picture).

### Smaller ideas
- Drag handles for fades on the clip corners; clip waveforms (needs a video-audio waveform endpoint).
- Delete / clean up range previews; EDL markers (Resolve format unverified, so currently only a copyable list).
- Play the range in a loop; J/K/L shuttle; multi-clip group drag (reorder a selection as a block).
- Auto-cut proposal that arranges takes to the beat (today Cut to beat only moves and splits existing clips).

## Lessons learned (save yourself the debugging)

- `pointer capture` on `.edit-canvas` redirects the `click`/`dblclick` to the canvas: handle a stationary press
  in `dragEnd` (see the marker branch), and never re-render an element between pointerdown and pointerup.
- Re-rendering (`tracks()`/`draw()`) destroys the focused element: call `refocus()` after clicks, drags, shortcuts,
  and after closing a dialog, or the next shortcut goes nowhere.
- A source take can be 24 fps while the timeline is 30: any trim between source frames needs `fps=..:start_time=0`.
- `App` is a top-level `const` (not on `window`); tests use `App.timelineEditor` inside `page.evaluate`.
- Playwright: scroll a target into view before reading its box (`scroll_into_view_if_needed`) and wait for the
  waveform (`_open_editor`) before acting, or a single late redraw detaches the element. `modifiers=[...]` for clicks.
- Never `pkill -f "<text in your own command>"`: it kills your own shell. Find servers by port with `ss -ltnp`.
- `STUDIO_DATA=/path` runs a second server on a copy of the project (try risky edits there first).
- Beat detector: bass weighting + tempo folding (70-165) fixed double-time errors; the editor's half/double
  buttons cover the rest. Calibration constant `ONSET_OFFSET` was measured on synthetic click tracks.

## Soft reload

- **Soft reload (no page rebuild)**: in the Frames, Takes and Animatic tabs `reload()` now calls `App.soft()` (set in
  `viewEpisode`): it re-fetches the episode, mutates `ctx.e` and `ctx.e.shots` *in place* (handlers captured those arrays),
  repaints only the cards whose JSON changed (`repaintCards`, cards carry `data-card="<shot id>"`), the header
  (`paintHead`), gate buttons (`data-label`), the frames status line (`framesStatus`), re-inits bulk selection, and calls
  `App.timelineEditor.draw()`. The timeline editor, its undo history, scroll position, open panels and untouched cards
  (and their videos) survive. If shots were added/removed it falls back to `route()`. `route()` itself now also restores
  scroll position and open `<details>` when it re-renders the page you are already on (Character, Location, ...).
  **When you add a new card type or tab section that depends on episode data, give it a `ctx.repaint` or it will not update.**
  Tests: `test_pick_lock_delete_and_approve_update_in_place...`, `test_frames_tab_picks_update...`,
  `test_a_page_that_must_rebuild_keeps...` (the first fails on the old code).

## Progress log
(Append one line per finished phase: what changed, tests added, anything left over.)
- D1 fades: `fade_in`/`fade_out` per clip (frames); validated, retimed, applied in the final filter graph (lip-sync cache unaffected); clamped client-side after every edit; split gives the fade-out to the right half; range previews keep a fade only at a true clip edge. Inspector fields (single + group) and a triangle overlay on the clip. +11 tests (backend render checks picture and audio ramps). Left over: drag handles on the clip corners (see Smaller ideas).
- E titles: `titles[]` (text, start_frame, frames, x, y, size, color, box) validated (max 200), retimed, shifted into range previews; drawn with Pillow (`studio/titles.py`, bundled `DejaVuSans-Bold.ttf`) and overlaid in the final graph with `overlay=enable='between(n,a,b)'`. Editor: TITLES lane, add/edit dialog, live preview over the monitor (same font and maths), Delete-key priority marker > title > clips. +36 tests (real renders prove the text shows exactly in its frames, also in range previews). Left over: drag/resize titles on the lane, fade/animate titles, per-title font choice, title templates, subtitles import (.srt).
- G1 speed: per-clip `speed` (0.25..4, default 1; old projects load as 1). `validate` counts `round(frames*speed)` source frames and rejects speed != 1 on lip-sync clips; `slice_clips` advances `source_in` by `offset*speed`; render adds `setpts=PTS/speed` + an `atempo` chain (`timeline.atempo`) in the per-clip extraction (lip-sync cache unaffected because lip-sync clips are always 1x). Client: `speed()/usedFrames()/fitFrames()` helpers used by right-trim, left-trim, slip, roll, Cut to beat, split, trimTo, addTake, thumbnails and the monitor (`playbackRate`); inspector Speed field changes the timeline length and keeps the source range; badge shows `N×`. +19 backend tests (frame-exact at 0.25/0.5/1.5/2/3x, 24 fps take, range preview equals full export, audio length) and 1 browser test. Baseline now: 196 passed in Chromium. Left over: speed ramps, reverse, a drag handle for speed, EDL events still log timeline duration only.
- F1 audio lanes: `audio: [{id, file, lane, source_in, start_frame, frames, volume, muted, loop, fade_in, fade_out}]` (max 8; old projects load as `[]`). `parse_audio` validates like music (trim within the recording unless looping, fades fit), `retime` scales position/length/fades, `source_info(audio=True)` also allows files of saved lanes, new `POST /timeline/audio` stores an upload in `audio_assets` without touching `music`. Render: `prepare_music(..., name='laneK')` per unmuted lane (now also applies fades), one `-i` each (seeked like music for range previews), `amix` of clips + music + lanes before the limiter. Client: lane rows under MUSIC with `LANE_HEIGHT`, label rows (`.lbl-lane`) with M buttons, waveforms via `/timeline/waveform`, one `Audio` element per active lane (`syncLanes`), drag with snapping (`mode 'lane'`), inspector section with per-lane fields. +7 backend tests (frequency checks at the right times, overlap adds up, mute, fades, range preview, fps change, validation) and 1 browser test. Left over: per-lane solo, edge-trim handles and vertical lane drag, Delete key on a lane clip, lane clip fade preview in the monitor, deleting unused uploaded files.
- D2 crossfades: `transition` (int frames, default 0) on the *incoming* clip = overlap with the clip before it. Timeline length/positions come from ONE helper in each language (`timeline.clip_starts` / JS `clipStarts`); all sums of clip frames were replaced (render, lip-sync voice offsets, range previews, export total in app.py, editor total/start/cuts/snap/reorder/beat cuts). Validation: whole frames, none on the first clip, <= half of the shorter neighbour, none next to a lip-sync clip. Render: when any transition exists the clips are chained with `xfade` + `acrossfade` (`settb` makes the time bases equal; hard cuts in the chain use a 2-input `concat`); no transitions keeps the original single `concat`. `slice_clips` keeps the part of a crossfade inside the range (a range that starts inside one gets a shorter blend than the full export). Client: inspector field, wedge on the incoming clip, `clampTransitions` after every edit, split/duplicate/paste/beat-split pieces get 0, Cut to beat aligns the middle of the overlap and refuses moves that would invalidate it. Deviations from the plan: stored as an integer `transition` (not `{type, frames}`; only a dissolve exists) and no settings feature flag, because the tests prove it and old projects are unaffected. +9 backend tests (blended pixels, frame count, audio, chain of fades and cuts, positions of lanes/titles, range previews, fps change, rules) and 2 browser tests. Left over: other transition types (wipes), monitor preview of the blend, fade handles, crossfade across a lip-sync clip.
- Tap counting: browsers drop `dblclick` when a click redraws the element, so double actions on clips, markers and titles use `tap()` (two quick taps). Found because "double-click a clip" (phase A) had silently never worked.

## Next steps for the phases not built yet (execution plan)

Baseline when this was written: `pytest -q tests` = 167 passed + the 3 unrelated e2e errors. Keep it green after each step.
Each phase below is a vertical slice: backend + tests, then UI + browser test, then docs + the log above.
Recommended order (value over risk): **~~G1 speed~~ (done) -> ~~F1 audio lanes~~ (done) -> ~~D2 crossfades~~ (done) -> F2 overlay lane -> G2 reframe.**

### G1. Clip speed (DONE, see the progress log)
1. `timeline.py`: clip field `speed` (float 0.25..4, default 1). `validate`: source needed = `frames * speed` frames,
   so the "extends beyond its source" check, and `source_info` duration checks, use `round(frames * speed)`.
   `default_edit`/`view` default 1; `retime` unchanged (timeline frames stay timeline frames);
   forbid `speed != 1` together with `lipsync` (400 with a clear message); `slice_clips`: `source_in += (lo-start)/fps * speed`
   and `frames` stay timeline frames.
2. `render`: per-clip extraction reads `frames * speed / fps` seconds of source, then `setpts=PTS/speed` (video) and an
   `atempo` chain (each factor within 0.5..2.0) (audio); output length must still be exactly `frames`. Check the 24 fps
   case again (`fps=..:start_time=0`).
3. Client: every place that assumes 1 timeline frame = 1 source frame: `sourceFrames()` comparisons in `dragMove`
   (left/right/slip/roll bounds), `planBeatCuts` roll bounds, `split()`/`trimTo()` (`source_in += offset/fps * speed`),
   `thumbs()` sample times (`* speed`), inspector Source in/out fields and `change()` for them, `addTake`.
4. Tests: validate (good/bad, speed + lipsync), render at 0.5x, 2x and 1.5x on a source whose frames encode their index
   (like `test_real_source_frame_numbers_survive_split_and_trim`) asserting the exact source frames shown and the frame count;
   preview slice with speed; browser test for the inspector field, split keeping content continuous, roll bounds.
5. Docs: STUDIO.md "Speed". Risk: off-by-one source frames; the frame-count check will catch them.

### F1. Extra audio lanes (DONE, see the progress log)
1. Schema: `audio: [{id, file, lane, source_in, start_frame, frames, volume, muted, loop, fade_in, fade_out}]` (max 8);
   keep `music` as the lip-sync guide. `source_info(audio=True)` must also allow files uploaded for lanes
   (`episode['audio_assets']` already exists; add an upload endpoint modelled on `/music`).
2. Render: `prepare_music` generalised per track (position = `adelay`), `amix` of N inputs (`normalize=0`) before the limiter;
   range previews seek each prepared track like the music track.
3. Client: lanes under MUSIC (reuse the music clip drawing/dragging with `data-audio-id`), per-lane M/S buttons, inspector;
   snapping to lane edges; waveforms via `/timeline/waveform`.
4. Tests: validate/retime/slice, render with two tracks (frequency peaks at the right times), browser (add, move, mute, delete).

### D2. Crossfades (DONE, see the progress log)
1. Decide and document the model first (see D2 above): `transition: {frames}` on the incoming clip, timeline length =
   sum(frames) - sum(transition frames). Write one helper (`edit_total`, `clip_starts`) used by backend AND client;
   replace every ad-hoc `sum(c.frames)` / `start()` / `cuts()` / `total` use.
2. Render: `xfade` (video) + `acrossfade` (audio) between consecutive per-clip files; verify frame count = new total.
3. Rules: no transition on/against a lip-sync clip; transition <= half of the shorter neighbour; roll/trim/slip clamp to it.
4. Tests: lengths, exact frames inside the overlap (picture blends), markers/range/titles keep their timeline positions,
   Cut to beat still aligns the cut point (centre of the overlap). Feature-flag it in settings until proven.

### F2. Video overlay lane (large, do last)
Clips with `track: 1`, free `start_frame` (not rippled), `overlay: {x, y, scale, opacity}`; render with `overlay` and
`enable`; introduce a `track` argument in client `start()`, `cuts()`, `remove()`, drag, snapping, selection.
Split into: (a) data model + render + tests, (b) lane UI read-only, (c) editing (move/trim), (d) overlay inspector.

### G2. Reframe (medium)
`crop: {x, y, scale}` per clip for 9:16 <-> 16:9; apply in the final graph (`crop`+`scale`) so lip-sync is unaffected;
preview in the monitor with CSS `object-position`/`transform`; tests compare cropped pixel regions.

### Small items that fit between phases
Fade handles on clip corners; delete/clean range previews; title drag on the lane; `.srt` titles import;
EDL markers once the Resolve format is verified; clip waveforms; J/K/L shuttle; group drag of a selection.

### Starting prompt for the next session
"Read docs/TIMELINE-ROADMAP.md and STUDIO.md, run the test suite for a baseline, then implement phase F2 (video overlay lane)
following the 'How a new saved field must be added' checklist. Try risky edits on a copy of studio_data
(STUDIO_DATA=...) first. Update the Progress log and STUDIO.md when done."
- Lip-sync quality: no better free local model found (LatentSync 1.6 remains the best dedicated video-to-video option for a 24 GB card; MuseTalk is faster but 256 px, InfiniteTalk regenerates the performance). A/B on 3 project clips: guidance 2.0 beats 1.5 on SyncNet (LSE-C +33..72%, LSE-D lower) at equal time. New `lipsync` setting (steps 20, guidance 2.0, seed 1247) via `settings.lipsync_params()`, used by both lip-sync call sites and part of the render cache key. The A/B scripts were throwaway (SyncNet weights come from `ByteDance/LatentSync-1.6/auxiliary/`). Untested ideas: KeySync (occlusion/microphone handling; license unconfirmed), MuseTalk as a fast-preview engine.

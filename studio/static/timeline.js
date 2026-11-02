// Takes editor: the saved edit is independent of its source takes.
"use strict";
// A clip's `transition` is a crossfade with the clip before it: they overlap, so the timeline is shorter than the
// sum of the clip lengths. Mirror of timeline.clip_starts in the backend: never add up c.frames by hand.
const clipStarts = clips => { const starts = []; let end = 0; clips.forEach((c, i) => { const begin = end - (i ? c.transition || 0 : 0); starts.push(begin); end = begin + c.frames; }); return {starts, total: end}; };
const editClone = value => JSON.parse(JSON.stringify(value));
const LANE_HEIGHT = 40, MAX_LANES = 8;
const MARKER_COLORS = ["red", "orange", "yellow", "green", "blue", "purple"];
const markerId = () => `mk_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
const editId = () => `clip_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 9)}`;

class TakeTimelineEditor {
  constructor(root, ctx, data, generate) {
    this.root = root; this.ctx = ctx; this.generate = generate;
    this.edit = data.edit; this.sources = data.sources; this.selection = new Set(); this.anchor = null; this.clipboard = [];
    this.markerSel = null; this.titleSel = null; this.tool = 'select'; this.beatsOn = store.get('rw-beats') === '1'; this.beatData = null; this.beatMode = 'normal'; this.beatLoading = false; this.beatCache = null;
    this.selected = this.edit.clips[0]?.id;
    this.frame = 0; this.zoom = 48; this.history = []; this.future = []; this.serial = 0;
    this.dirty = false; this.playing = false; this.destroyed = false; this.snap = store.get('rw-snap') !== '0'; this.snapAt = null; this.scrubbing = null;
    this.api = ctx.base + '/timeline';
    root.innerHTML = `<section class="take-editor" aria-label="Video timeline editor" tabindex="0">
      <div class="edit-heading"><div><h2>Takes editor</h2><span class="edit-status muted small" data-edit-status data-state="saved" role="status" aria-live="polite">Timeline ready · source takes retained</span></div>
        <div class="row"><button data-edit="focus" title="Full-screen editor (Esc to exit)">Expand editor</button><button data-edit="inspector" title="Show or hide the clip and music settings">Inspector</button><button data-edit="undo" aria-label="Undo timeline edit" title="Undo (Ctrl+Z)">Undo</button><button data-edit="redo" aria-label="Redo timeline edit" title="Redo (Ctrl+Shift+Z)">Redo</button>
        <button data-edit="save" title="Edits also autosave">Save now</button><button data-edit="export" class="primary">Render timeline</button></div></div>
      <div class="edit-stage"><div class="edit-monitor"><video data-edit-video playsinline preload="metadata"></video><div class="edit-title-preview" aria-hidden="true"></div><span data-edit-empty>Add a take from the library below</span>
        <div class="edit-transport"><button data-edit="play" aria-label="Play timeline" title="Play / pause (Space)">▶</button><button data-edit="start" aria-label="Go to timeline start" title="Go to start (Home)">↤</button>
        <button data-edit="prev-cut" aria-label="Previous cut" title="Previous cut (↑)">⏮</button><button data-edit="step-back" aria-label="Back one frame" title="Back one frame (←)">◂</button><button data-edit="step-fwd" aria-label="Forward one frame" title="Forward one frame (→)">▸</button><button data-edit="next-cut" aria-label="Next cut" title="Next cut (↓)">⏭</button>
        <output class="edit-tc" data-edit-tc aria-label="Timecode">00:00:00:00</output><output data-edit-time>00:00.000</output><span class="grow"></span></div>
        <input data-edit-scrub type="range" min="0" step="1" value="0" aria-label="Timeline playhead"></div>
        <aside class="edit-inspector" data-edit-inspector></aside></div>
      <div class="edit-tools"><span class="edit-sel" data-edit-sel>No clip selected</span>
        <div class="tb-group"><button data-edit="split" title="Split at playhead (S)">Split <span class="kbd">S</span></button><button data-edit="trim-start" title="Trim start to playhead (Q)">⇤ Trim start <span class="kbd">Q</span></button><button data-edit="trim-end" title="Trim end to playhead (W)">Trim end ⇥ <span class="kbd">W</span></button><button data-edit="duplicate" title="Duplicate (Ctrl+D)">Duplicate</button></div>
        <div class="tb-group"><button data-edit="earlier" aria-label="Move earlier" title="Move earlier (Alt+←)">← Earlier</button><button data-edit="later" aria-label="Move later" title="Move later (Alt+→)">Later →</button><button data-edit="delete" class="danger" title="Delete and close the gap (Delete)">Delete</button></div>
        <span class="grow"></span>
        <div class="tb-group" role="group" aria-label="Edit tool"><button data-edit="tool-select" aria-pressed="true" title="Select and trim (V)">Select</button><button data-edit="tool-slip" aria-pressed="false" title="Slip: drag a clip to choose which part of the take it shows (Y)">Slip</button><button data-edit="tool-roll" aria-pressed="false" title="Roll: drag a cut to move it without changing the total length (R)">Roll</button></div></div>
      <div class="edit-tools edit-view">
        <div class="tb-group" role="group" aria-label="Beats"><button data-edit="beats" aria-pressed="false" title="Show the beat grid and snap to it (B)" disabled>Beats</button><button data-edit="beat-half" aria-label="Half the beat rate" title="Half-time: use every other beat" disabled>½×</button><button data-edit="beat-double" aria-label="Double the beat rate" title="Double-time: add a beat between each pair" disabled>2×</button>
          <button data-edit="beat-prev" title="Previous beat (,)" disabled>◂ Beat</button><button data-edit="beat-next" title="Next beat (.)" disabled>Beat ▸</button><button data-edit="beat-cut" title="Move cuts onto the beat, or cut every few beats" disabled>Cut to beat…</button></div>
        <span class="grow"></span>
        <div class="tb-group"><button data-edit="snap" aria-pressed="true" title="Snap to clip edges, music, beats and the playhead (N). Hold Alt while dragging to bypass.">Snap</button>
        <button data-edit="zoom-out" aria-label="Zoom out" title="Zoom out (−)">−</button><input data-edit-zoom type="range" min="4" max="400" step="1" value="48" aria-label="Timeline zoom"><button data-edit="zoom-in" aria-label="Zoom in" title="Zoom in (+)">+</button><button data-edit="zoom-fit" title="Fit whole timeline (\\)">Fit</button></div></div>
      <div class="edit-tools edit-marks">
        <div class="tb-group" role="group" aria-label="Markers"><button data-edit="marker-add" title="Add a marker at the playhead, or remove the one under it (Shift+M)">⚑ Marker <span class="kbd">⇧M</span></button><button data-edit="markers" title="Name, colour, jump to and list markers">Markers (0)</button><button data-edit="title-add" title="Add on-screen text at the playhead">T Title</button><button data-edit="titles" title="Edit all titles">Titles (0)</button></div>
        <span class="grow"></span>
        <div class="tb-group" role="group" aria-label="Render range"><button data-edit="range-in" title="Start the range at the playhead (I)">In <span class="kbd">I</span></button><button data-edit="range-out" title="End the range at the playhead (O)">Out <span class="kbd">O</span></button><button data-edit="range-clear" title="Clear the range (Alt+X)" disabled>Clear</button><button data-edit="export-range" title="Render only this range as a quick preview. Lip-sync is skipped, and your current final film is not replaced." disabled>Render range</button><span class="range-info" data-edit-range-info></span></div></div>
      <div class="edit-tracks"><div class="edit-labels"><div>TIME</div><div><span>VIDEO</span><button class="trk-btn" data-edit="track-video-mute" aria-pressed="false" aria-label="Mute all clip audio" title="Mute / unmute all clip audio">M</button></div><div><span>MUSIC</span><button class="trk-btn" data-edit="track-music-mute" aria-pressed="false" aria-label="Mute music" title="Mute / unmute music">M</button></div><div class="lbl-titles"><span>TITLES</span></div></div>
        <div class="edit-scroll"><div class="edit-canvas"><div class="edit-ruler"></div><div class="edit-video-track"></div><div class="edit-music-track"></div><div class="edit-audio-track"></div><div class="edit-title-track"></div><div class="edit-beatgrid" aria-hidden="true"></div><div class="edit-rangeview" hidden><i class="dim a" aria-hidden="true"></i><i class="dim b" aria-hidden="true"></i><i class="bar" aria-hidden="true"></i><b class="handle in" data-range-handle="in" role="slider" aria-label="Range start" title="Drag the range start"></b><b class="handle out" data-range-handle="out" role="slider" aria-label="Range end" title="Drag the range end"></b></div><div class="edit-markers"></div>
          <div class="edit-snap-line" hidden aria-hidden="true"></div><div class="edit-playhead" aria-hidden="true"></div></div></div></div>
      <div class="edit-footer row"><label class="btn file-pick">Add music / voice<input data-edit-audio type="file" accept="audio/*,.mp3,.wav,.m4a,.flac,.ogg,.aac" aria-label="Add timeline music"></label>
        <label class="btn file-pick" title="Voice-over or sound effects on their own lane (up to 8)">Add audio lane<input data-edit-lane type="file" accept="audio/*,.mp3,.wav,.m4a,.flac,.ogg,.aac" aria-label="Add audio lane"></label>
        <button data-edit="mute-all">Mute all takes</button><button data-edit="shot-lengths">Use shot lengths</button><button data-edit="sync">Choose lip-sync clips</button>
        <span class="grow"></span><button data-edit="reload" title="Throw away unsaved edits and reload the saved timeline">Discard unsaved edits</button><button data-edit="picked" title="Rebuild the timeline from the takes picked in the library">Reset to picked takes</button></div>
      <p class="muted small edit-help">Drag a clip’s grip (⠿) to reorder, or its edges to trim. <span class="kbd">Shift</span>/<span class="kbd">Ctrl</span>-click selects several clips. Split and delete ripple the video track. Edits autosave. <span class="kbd">Space</span> play · <span class="kbd">Delete</span> remove clip · <span class="kbd">Ctrl</span> <span class="kbd">Z</span> undo</p>
      <div class="edit-results"></div>
      <audio data-edit-music preload="metadata"></audio></section>`;
    this.video = $('[data-edit-video]', root); this.audio = $('[data-edit-music]', root);
    this.video.onloadedmetadata = () => this.syncMedia(true);
    this.audio.onloadedmetadata = () => this.syncMedia(true);
    root.onclick = event => { event.stopPropagation(); if (!event.target.closest('input, select, textarea, button, summary, a, label')) $('.take-editor', root).focus({preventScroll: true}); this.click(event).catch(error => this.error(error)).finally(() => this.refocus()); };
    root.onchange = event => { event.stopPropagation(); this.change(event).catch(error => this.error(error)); };
    $('[data-edit-scrub]', root).oninput = event => this.seek(+event.target.value);
    $('[data-edit-zoom]', root).oninput = event => this.setZoom(+event.target.value);
    $('.edit-scroll', root).addEventListener('wheel', event => { if (!(event.ctrlKey || event.metaKey)) return; event.preventDefault(); this.setZoom(this.zoom * (event.deltaY < 0 ? 1.15 : 1 / 1.15), event.clientX); }, {passive: false});
    this.canvasEl = $('.edit-canvas', root);
    const canvas = $('.edit-canvas', root);
    canvas.onpointerdown = event => this.dragStart(event);
    canvas.onpointermove = event => this.dragMove(event);
    canvas.onpointerup = event => this.dragEnd(event);
    canvas.onpointercancel = event => this.dragEnd(event, true);
    root.onkeydown = event => {
      if (event.key === 'Escape') { $('.take-editor', root).classList.remove('focused'); document.body.classList.remove('timeline-focus'); $('[data-edit="focus"]', root).textContent = 'Expand editor'; }
      if (/INPUT|SELECT|TEXTAREA/.test(event.target.tagName)) return;
      this.key(event);
      this.refocus();  // an edit can redraw the focused clip away; keep the shortcuts working
    };
    $('.edit-scroll', root).addEventListener('scroll', () => { if (this.thumbRaf) return; this.thumbRaf = requestAnimationFrame(() => { this.thumbRaf = 0; if (!this.destroyed && this.thumbsStale()) this.tracks(); }); }, {passive: true});
    this.monitorObserver = new ResizeObserver(() => this.titlePreview()); this.monitorObserver.observe($('.edit-monitor', root));
    this.laneEls = new Map(); this.laneWaves = {}; this.laneWavePending = new Set();
    this.draw(); this.seek(0); this.loadWaveform(); this.ensureBeats();
    const versions = this.ctx.e.outputs?.timeline_versions || [], previews = this.ctx.e.outputs?.timeline_previews || [];
    const span = v => v.window ? `${(v.window[0] / (v.video?.fps || this.fps)).toFixed(2)}–${(v.window[1] / (v.video?.fps || this.fps)).toFixed(2)} s` : '';
    const rows = list => list.slice().reverse().map(v => `<div class="row"><span class="grow small">${esc(v.created)} · ${v.window ? `range ${span(v)} · ` : ''}${Number(v.runtime).toFixed(3)} s · edit ${v.revision}${v.lipsync ? ' · lip-sync' : ''}${v.lipsync_skipped ? ' · lip-sync skipped' : ''}</span><a class="btn" href="${media(v.file)}" target="_blank" rel="noopener">Watch</a><a class="btn" href="${media(v.file)}" download>Download MP4</a></div>`).join('');
    $('.edit-results', root).innerHTML = (versions.length ? `<details><summary>Rendered timelines (${versions.length})</summary>${rows(versions)}</details>` : '') + (previews.length ? `<details><summary>Range previews (${previews.length})</summary>${rows(previews)}</details>` : '');
  }
  get fps() { return this.edit.fps; }
  get total() { return clipStarts(this.edit.clips).total; }
  get selected() { return this._primary; }
  set selected(id) { this._primary = id; this.anchor = id; this.markerSel = null; this.titleSel = null; this.selection = new Set(id ? [id] : []); }
  get titles() { return this.edit.titles || []; }
  get markers() { return this.edit.markers || []; }
  get range() { return this.edit.range || null; }
  get clip() { return this.edit.clips.find(c => c.id === this.selected); }
  selectedClips() { return this.edit.clips.filter(c => this.selection.has(c.id)); }
  normalizeSelection() {
    const ids = new Set(this.edit.clips.map(c => c.id));
    for (const id of [...this.selection]) if (!ids.has(id)) this.selection.delete(id);
    if (!ids.has(this._primary)) this._primary = [...this.selection][0];
    if (this._primary && !this.selection.has(this._primary)) this.selection.add(this._primary);
  }
  pick(id, event = {}) {
    const clips = this.edit.clips;
    if (event.shiftKey && this.anchor && clips.some(c => c.id === this.anchor)) {
      const a = clips.findIndex(c => c.id === this.anchor), b = clips.findIndex(c => c.id === id);
      this.selection = new Set(clips.slice(Math.min(a, b), Math.max(a, b) + 1).map(c => c.id)); this._primary = id;
    } else if (event.ctrlKey || event.metaKey) {
      if (this.selection.has(id) && this.selection.size > 1) { this.selection.delete(id); if (this._primary === id) this._primary = [...this.selection].pop(); }
      else { this.selection.add(id); this._primary = id; }
      this.anchor = id;
    } else this.selected = id;
    this.markerSel = null; this.titleSel = null;
  }
  selectAll() { this.selection = new Set(this.edit.clips.map(c => c.id)); this._primary = this.selection.has(this._primary) ? this._primary : this.edit.clips[0]?.id; this.anchor = this._primary; this.draw(); }
  start(id) { const i = this.edit.clips.findIndex(c => c.id === id); return i < 0 ? 0 : clipStarts(this.edit.clips).starts[i]; }
  seconds(frame) { return (frame / this.fps).toFixed(3); }
  label(c) { return c.shot_id || 'Video clip'; }
  sourceFrames(c) { return Math.round((this.sources[c.source]?.video_duration || c.source_in + c.frames / this.fps) * this.fps); }
  audioEnd() { return Math.max(0, ...(this.edit.audio || []).map(t => t.start_frame + t.frames)); }
  speed(c) { return c.speed || 1; }
  // source frames used by `frames` timeline frames of clip c, and the most timeline frames `source` frames can fill
  usedFrames(c, frames = c.frames) { return Math.round(frames * this.speed(c)); }
  fitFrames(c, source) { return Math.max(0, Math.floor(source / this.speed(c) + 1e-9)); }
  eligible(c) { return this.speed(c) === 1 && this.ctx.e.shots.find(sh => sh.id === c.shot_id)?.characters?.length === 1; }
  status(text, state) { const el = $('[data-edit-status]', this.root); if (!el) return; el.textContent = text; el.dataset.state = state; }
  error(error) { this.status(error.message || String(error), 'error'); }
  changeEdit(fn) {
    this.pause(); const before = editClone(this.edit); fn();
    if (JSON.stringify(before) === JSON.stringify(this.edit)) return;
    this.history.push(before); if (this.history.length > 50) this.history.shift(); this.future = [];
    this.changed();
  }
  clampTransitions() {  // no crossfade on the first clip, next to lip-sync, or longer than half of the shorter neighbour
    this.edit.clips.forEach((c, i) => {
      const prev = this.edit.clips[i - 1];
      c.transition = !prev || c.lipsync || prev.lipsync ? 0 : Math.max(0, Math.min(Math.round(c.transition || 0), Math.floor(Math.min(c.frames, prev.frames) / 2)));
    });
  }
  clampFades() {
    this.clampTransitions();
    for (const c of this.edit.clips) {
      c.fade_in = Math.max(0, Math.min(c.frames, Math.round(c.fade_in || 0)));
      c.fade_out = Math.max(0, Math.min(c.frames - c.fade_in, Math.round(c.fade_out || 0)));
    }
  }
  changed() {
    this.clampFades(); this.serial++; this.dirty = true; App.dirty = true;
    this.status('Saving timeline…', 'saving');
    this.frame = Math.min(this.frame, Math.max(0, this.total - 1));
    this.draw(); this.syncMedia(true);
    clearTimeout(this.saveTimer); this.saveTimer = setTimeout(() => this.flush().catch(error => this.error(error)), 650);
  }
  async flush() {
    clearTimeout(this.saveTimer);
    if (this.saving) await this.saving;
    if (!this.dirty && this.edit.revision > 0) return;
    const serial = this.serial;
    this.saving = PUT(this.api, editClone(this.edit)).then(data => {
      this.edit.revision = data.edit.revision; Object.assign(this.sources, data.sources);
      if (this.serial === serial) this.dirty = false;
      App.dirty = this.dirty;
      if (this.root.isConnected) this.dirty ? this.status('Saving timeline…', 'saving') : this.status(`Saved · ${this.seconds(this.total)} s · revision ${this.edit.revision}`, 'saved');
    }).finally(() => { this.saving = null; });
    await this.saving;
    if (this.dirty) await this.flush();
  }
  restore(redo = false) {
    const from = redo ? this.future : this.history, to = redo ? this.history : this.future;
    if (!from.length) return;
    this.pause(); to.push(editClone(this.edit)); const revision = this.edit.revision;
    this.edit = from.pop(); this.edit.revision = revision;
    if (!this.clip) this.selected = this.edit.clips[0]?.id;
    this.changed();
  }
  draw() {
    this.tracks(); this.inspector(); this.position();
    this.normalizeSelection();
    const hasClip = !!this.clip, many = this.selection.size > 1;
    for (const action of ['delete', 'split', 'duplicate', 'earlier', 'later', 'trim-start', 'trim-end']) $(`[data-edit="${action}"]`, this.root).disabled = !hasClip;
    const picked = this.selectedClips();
    $('[data-edit-sel]', this.root).textContent = !hasClip ? 'No clip selected' : many ? `Selected: ${picked.length} clips · ${this.seconds(picked.reduce((n, c) => n + c.frames, 0))}s` : `Selected: ${this.label(this.clip)} · ${this.seconds(this.clip.frames)}s`;
    for (const tool of ['select', 'slip', 'roll']) $(`[data-edit="tool-${tool}"]`, this.root).setAttribute('aria-pressed', String(this.tool === tool));
    $('.edit-canvas', this.root).dataset.tool = this.tool;
    this.ensureBeats();
    this.beatButtons();
    this.markButtons();
    const snapBtn = $('[data-edit="snap"]', this.root); snapBtn.setAttribute('aria-pressed', String(this.snap));
    const vm = $('[data-edit="track-video-mute"]', this.root), mm = $('[data-edit="track-music-mute"]', this.root);
    vm.disabled = !this.edit.clips.length; vm.setAttribute('aria-pressed', String(!!this.edit.clips.length && this.edit.clips.every(c => c.muted)));
    mm.disabled = !this.edit.music; mm.setAttribute('aria-pressed', String(!!this.edit.music?.muted));
    $('[data-edit="undo"]', this.root).disabled = !this.history.length;
    $('[data-edit="redo"]', this.root).disabled = !this.future.length;
    $('[data-edit="export"]', this.root).disabled = !this.total;
    $('[data-edit="play"]', this.root).disabled = !this.total;
    $('[data-edit-empty]', this.root).hidden = !!this.total;
    $('[data-edit="mute-all"]', this.root).textContent = this.edit.clips.length && this.edit.clips.every(c => c.muted) ? 'Unmute all takes' : 'Mute all takes';
  }
  tracks() {
    const music = this.edit.music;
    const end = Math.max(this.total, music ? music.start_frame + music.frames : 0, this.audioEnd(), this.fps);
    const width = Math.max(260, end / this.fps * this.zoom + 30);
    const canvas = $('.edit-canvas', this.root); canvas.style.width = width + 'px';
    const secs = end / this.fps, nice = [.1, .2, .5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600];
    const major = nice.find(n => n * this.zoom >= 72) || 600;
    const minorStep = [5, 2].map(k => major / k).find(s => s * this.zoom >= 6) || major;
    const ticks = [];
    for (let i = 0; i * major <= secs + 1e-6; i++) {
      const s = Math.round(i * major * 1000) / 1000, m = Math.floor(s / 60), r = s % 60;
      ticks.push(`<span style="left:${s * this.zoom}px">${m}:${major < 1 ? r.toFixed(1).padStart(4, '0') : String(Math.floor(r)).padStart(2, '0')}</span>`);
    }
    const ruler = $('.edit-ruler', this.root); ruler.innerHTML = ticks.join('');
    ruler.style.backgroundImage = `repeating-linear-gradient(90deg, #47566b 0 1px, transparent 1px ${minorStep * this.zoom}px)`;
    const scroller = $('.edit-scroll', this.root); this.canvasWidth = width;
    this.thumbWindow = {from: scroller.scrollLeft - 500, to: scroller.scrollLeft + scroller.clientWidth + 500};
    const placed = clipStarts(this.edit.clips).starts;
    $('.edit-video-track', this.root).innerHTML = this.edit.clips.map((c, index) => {
      const left = placed[index] / this.fps * this.zoom;
      const sh = this.ctx.e.shots.find(sh => sh.id === c.shot_id);
      const poster = sh && selFrame(sh)?.file;
      const px = Math.max(3, c.frames / this.fps * this.zoom), size = px < 46 ? 'tiny' : px < 96 ? 'narrow' : '';
      const shortName = px < 96 ? this.label(c).replace(/^(S\d+)_SH(\d+)$/, '$1·$2') : this.label(c);
      return `<button type="button" class="edit-clip ${size} ${this.selection.has(c.id) ? 'selected' : ''} ${c.id === this.selected ? 'primary' : ''} ${c.muted ? 'muted' : ''}" aria-pressed="${this.selection.has(c.id)}" data-clip="${esc(c.id)}" style="left:${left}px;width:${px}px" title="${esc(this.label(c))} · ${this.seconds(c.frames)}s${c.muted ? ' · muted' : ''}${c.lipsync ? ' · lip-sync' : ''}${c.transition ? ` · crossfade ${this.seconds(c.transition)}s` : ''}" aria-label="Timeline clip ${esc(this.label(c))}">
        ${poster ? `<img src="${media(poster)}" alt="" draggable="false">` : ''}${this.thumbs(c, left, px)}<span class="edit-clip-name">${esc(shortName)}</span>
        <span class="edit-clip-meta">${this.seconds(c.frames)}s${this.speed(c) !== 1 ? ` · ${+this.speed(c).toFixed(2)}×` : ''}</span><span class="edit-badges" aria-hidden="true">${c.muted ? '<i>M</i>' : ''}${c.lipsync ? '<i class="ls">LS</i>' : ''}</span>
        ${c.fade_in ? `<i class="edit-fade in" style="width:${Math.min(px, c.fade_in / this.fps * this.zoom)}px" aria-hidden="true"></i>` : ''}${c.fade_out ? `<i class="edit-fade out" style="width:${Math.min(px, c.fade_out / this.fps * this.zoom)}px" aria-hidden="true"></i>` : ''}${c.transition ? `<i class="edit-xfade" style="width:${Math.min(px, c.transition / this.fps * this.zoom)}px" aria-hidden="true"></i>` : ''}<span class="edit-grip" data-grip="move" aria-label="Drag to reorder">⠿</span><span class="edit-edge left" data-edge="left"></span><span class="edit-edge right" data-edge="right"></span></button>`;
    }).join('');
    this.drawBeats(); this.drawMarks(); this.drawTitles();
    const waveform = music && this.waveform?.file === music.file ? this.waveSvg(music, this.waveform) : '';
    $('.edit-music-track', this.root).innerHTML = music ? `<button class="edit-music-clip ${music.muted ? 'muted' : ''}" data-music-move style="left:${music.start_frame / this.fps * this.zoom}px;width:${Math.max(8, music.frames / this.fps * this.zoom)}px" aria-label="Music timeline clip">${waveform}<span>${esc(music.file.split('/').pop())}${music.muted ? ' · muted' : ''}</span></button>` : '<span class="edit-track-empty">Add a song or voice recording</span>';
    this.laneTracks();
  }
  waveSvg(t, {peaks, duration}) {
    const points = [], remaining = Math.max(.001, duration - t.source_in);
    for (let x = 0; x <= 400; x++) {
      let time = x / 400 * t.frames / this.fps;
      if (t.loop) time %= remaining;
      points.push([x, (peaks[Math.floor((t.source_in + time) / duration * peaks.length)] || 0) * 18]);
    }
    return `<svg viewBox="0 0 400 40" preserveAspectRatio="none" aria-hidden="true"><polygon points="${points.map(([x, h]) => `${x},${20-h}`).join(' ')} ${points.reverse().map(([x, h]) => `${x},${20+h}`).join(' ')}"/></svg>`;
  }
  laneTracks() {
    const lanes = this.edit.audio || [], labels = $('.edit-labels', this.root);
    $$('.lbl-lane', labels).forEach(el => el.remove());
    const before = $('.lbl-titles', labels);
    lanes.forEach((t, i) => before.insertAdjacentHTML('beforebegin', `<div class="lbl-lane"><span>A${i + 1}</span><button class="trk-btn" data-edit="lane-mute" data-lane="${esc(t.id)}" aria-pressed="${!!t.muted}" aria-label="Mute audio lane ${i + 1}" title="Mute / unmute this lane">M</button></div>`));
    $('.edit-audio-track', this.root).style.height = lanes.length * LANE_HEIGHT + 'px';
    $('.edit-audio-track', this.root).innerHTML = lanes.map((t, i) => {
      const wave = this.laneWaves[t.file], name = t.file.split('/').pop();
      const fadeW = f => Math.min(t.frames, f) / this.fps * this.zoom;
      return `<div class="edit-lane" style="top:${i * LANE_HEIGHT}px"><button type="button" class="edit-lane-clip ${t.muted ? 'muted' : ''}" data-lane-move="${esc(t.id)}" style="left:${t.start_frame / this.fps * this.zoom}px;width:${Math.max(8, t.frames / this.fps * this.zoom)}px" title="${esc(name)} · ${this.seconds(t.frames)}s" aria-label="Audio lane ${i + 1} clip ${esc(name)}">${wave ? this.waveSvg(t, wave) : ''}<span>${esc(name)}${t.muted ? ' · muted' : ''}</span>${t.fade_in ? `<i class="edit-fade in" style="width:${fadeW(t.fade_in)}px" aria-hidden="true"></i>` : ''}${t.fade_out ? `<i class="edit-fade out" style="width:${fadeW(t.fade_out)}px" aria-hidden="true"></i>` : ''}</button></div>`;
    }).join('');
    this.ensureLaneWaves();
  }
  async ensureLaneWaves() {
    for (const t of this.edit.audio || []) {
      if (this.laneWaves[t.file] || this.laneWavePending.has(t.file) || this.sources[t.file]?.missing) continue;
      this.laneWavePending.add(t.file);
      try {
        const data = await GET(this.api + '/waveform?file=' + encodeURIComponent(t.file));
        if (this.destroyed) return; this.laneWaves[t.file] = data; this.tracks();
      } catch { /* the lane still plays without a waveform */ } finally { this.laneWavePending.delete(t.file); }
    }
  }
  inspector() {
    const c = this.clip, m = this.edit.music, picked = this.selectedClips();
    if (picked.length > 1) {
      const all = key => picked.every(x => x[key]), some = key => picked.some(x => x[key]);
      const volume = picked.reduce((n, x) => n + x.volume, 0) / picked.length, eligible = picked.every(x => this.eligible(x));
      $('[data-edit-inspector]', this.root).innerHTML = `<h3>${picked.length} clips selected</h3>
        <p class="muted small">${this.seconds(picked.reduce((n, x) => n + x.frames, 0))}s in total. Changes apply to every selected clip.</p>
        <label class="row"><input data-edit-muted type="checkbox" ${all('muted') ? 'checked' : ''} ${some('muted') && !all('muted') ? 'data-mixed="true"' : ''}>Mute clip audio${some('muted') && !all('muted') ? ' (mixed)' : ''}</label>
        <label>Clip volume<input data-edit-volume type="range" min="0" max="1" step=".05" value="${volume}" aria-label="Clip volume"></label>
        ${this.fadeFields(picked)}
        <label class="row"><input data-edit-lipsync type="checkbox" ${all('lipsync') ? 'checked' : ''} ${eligible ? '' : 'disabled'}>Lip-sync these clips to music</label>
        ${eligible ? '' : '<p class="muted small">Lip-sync needs single-character shots.</p>'}
        <p class="muted small">Copy <span class="kbd">Ctrl</span> <span class="kbd">C</span>, paste <span class="kbd">Ctrl</span> <span class="kbd">V</span>, duplicate <span class="kbd">Ctrl</span> <span class="kbd">D</span>, delete <span class="kbd">Delete</span>.</p>
        <h3>Music / voice</h3>${this.musicInspector()}${this.laneInspector()}`;
      const mixed = $('[data-mixed]', this.root); if (mixed) mixed.indeterminate = true;
      return;
    }
    const sh = this.ctx.e.shots.find(sh => sh.id === c?.shot_id);
    const takes = sh?.takes.items || [];
    $('[data-edit-inspector]', this.root).innerHTML = `<h3>${c ? esc(this.label(c)) : 'Clip settings'}</h3>
      ${c ? `<label>Take<select data-edit-take aria-label="Timeline source take">${!takes.some(t => t.file === c.source) ? `<option value="${esc(c.source)}">Current rendered clip</option>` : ''}${takes.map((t, i) => `<option value="${esc(t.file)}" ${t.file === c.source ? 'selected' : ''}>Take ${i+1} · ${esc(t.model || t.engine || 'video')}</option>`).join('')}</select></label>
      <div class="edit-field-pair"><label>Source in (s)<input data-edit-in type="number" min="0" step="${1/this.fps}" value="${this.seconds(Math.round(c.source_in*this.fps))}" aria-label="Clip source in"></label>
      <label>Source out (s)<input data-edit-out type="number" min="0" step="${1/this.fps}" value="${this.seconds(Math.round(c.source_in*this.fps)+this.usedFrames(c))}" aria-label="Clip source out"></label></div>
      <label>Speed (×)<input data-edit-speed type="number" min="0.25" max="4" step=".05" value="${this.speed(c)}" ${c.lipsync ? 'disabled title="Turn lip-sync off to change the speed"' : ''} aria-label="Clip speed"></label>
      <label class="row"><input data-edit-muted type="checkbox" ${c.muted ? 'checked' : ''}>Mute clip audio</label>
      <label>Clip volume<input data-edit-volume type="range" min="0" max="1" step=".05" value="${c.volume}" aria-label="Clip volume"></label>
      ${this.fadeFields([c])}
      ${this.crossfadeField(c)}
      <label class="row"><input data-edit-lipsync type="checkbox" ${c.lipsync ? 'checked' : ''} ${this.eligible(c) ? '' : 'disabled'}>Lip-sync this clip to music</label>
      <label>Generate with<select data-edit-model aria-label="Timeline generation model">${$('#take-model')?.innerHTML || ''}</select></label>
      <button data-edit="generate" ${sh ? '' : 'disabled'}>Generate take for this shot</button>` : '<p class="muted small">Select a clip or add a take below.</p>'}
      <h3>Music / voice</h3>${this.musicInspector()}${this.laneInspector()}`;
    const model = $('[data-edit-model]', this.root); if (model) model.value = $('#take-model').value;
  }
  crossfadeField(c) {
    const i = this.edit.clips.indexOf(c), prev = this.edit.clips[i - 1];
    const reason = !prev ? 'The first clip has no clip before it' : c.lipsync || prev.lipsync ? 'Lip-sync clips cannot be crossfaded' : '';
    const most = prev ? Math.floor(Math.min(c.frames, prev.frames) / 2) / this.fps : 0;
    return `<label>Crossfade from previous clip (s)<input data-edit-xfade type="number" min="0" max="${most.toFixed(3)}" step=".1" value="${((c.transition || 0) / this.fps).toFixed(2)}" ${reason ? `disabled title="${reason}"` : ''} aria-label="Crossfade from the previous clip, seconds"></label>
      <p class="muted small">${reason || 'The two clips overlap, so the timeline gets shorter. The monitor shows a cut; the export blends.'}</p>`;
  }
  fadeFields(clips) {
    const same = key => clips.every(c => c[key] === clips[0][key]) ? (clips[0][key] / this.fps).toFixed(2) : '';
    const longest = (Math.min(...clips.map(c => c.frames)) / this.fps).toFixed(2);
    return `<div class="edit-field-pair"><label>Fade in (s)<input data-edit-fade="in" type="number" min="0" max="${longest}" step=".1" value="${same('fade_in')}" placeholder="mixed" aria-label="Fade in, seconds"></label>
      <label>Fade out (s)<input data-edit-fade="out" type="number" min="0" max="${longest}" step=".1" value="${same('fade_out')}" placeholder="mixed" aria-label="Fade out, seconds"></label></div>`;
  }
  laneInspector() {
    const lanes = this.edit.audio || [], sec = f => (f / this.fps).toFixed(2);
    return `<h3>Audio lanes (${lanes.length}/${MAX_LANES})</h3>` + (lanes.length ? lanes.map((t, i) => `<fieldset class="edit-lane-box" data-lane-box="${esc(t.id)}"><legend>A${i + 1} · ${esc(t.file.split('/').pop())}</legend>
      <div class="edit-field-pair"><label>Source in (s)<input data-lane-field="in" data-lane="${esc(t.id)}" type="number" min="0" step=".1" value="${t.source_in}" aria-label="Lane ${i + 1} source in"></label>
      <label>Timeline start (s)<input data-lane-field="start" data-lane="${esc(t.id)}" type="number" min="0" step=".1" value="${this.seconds(t.start_frame)}" aria-label="Lane ${i + 1} timeline start"></label></div>
      <label>Length (s)<input data-lane-field="length" data-lane="${esc(t.id)}" type="number" min="${1/this.fps}" step=".1" value="${this.seconds(t.frames)}" aria-label="Lane ${i + 1} length"></label>
      <div class="edit-field-pair"><label>Fade in (s)<input data-lane-field="fade_in" data-lane="${esc(t.id)}" type="number" min="0" step=".1" value="${sec(t.fade_in || 0)}" aria-label="Lane ${i + 1} fade in"></label>
      <label>Fade out (s)<input data-lane-field="fade_out" data-lane="${esc(t.id)}" type="number" min="0" step=".1" value="${sec(t.fade_out || 0)}" aria-label="Lane ${i + 1} fade out"></label></div>
      <label>Volume<input data-lane-field="volume" data-lane="${esc(t.id)}" type="range" min="0" max="1" step=".05" value="${t.volume}" aria-label="Lane ${i + 1} volume"></label>
      <label class="row"><input data-lane-field="muted" data-lane="${esc(t.id)}" type="checkbox" ${t.muted ? 'checked' : ''}>Mute</label>
      <label class="row"><input data-lane-field="loop" data-lane="${esc(t.id)}" type="checkbox" ${t.loop ? 'checked' : ''}>Loop</label>
      <button data-edit="lane-remove" data-lane="${esc(t.id)}" class="danger">Remove this lane</button></fieldset>`).join('') : '<p class="muted small">Use "Add audio lane" for voice-over or sound effects. Music stays the lip-sync guide.</p>');
  }
  laneChange(t) {
    const lane = (this.edit.audio || []).find(x => x.id === t.dataset.lane); if (!lane) return;
    const field = t.dataset.laneField, box = t.closest('[data-lane-box]'), value = f => box.querySelector(`[data-lane-field="${f}"]`).value;
    const duration = this.sources[lane.file]?.duration || 0;
    if (['in', 'start', 'length'].includes(field)) {
      const begin = Number(value('in')), start = Math.round(Number(value('start')) * this.fps), frames = Math.round(Number(value('length')) * this.fps);
      const available = Math.round((duration - begin) * this.fps);
      if (![begin, start, frames].every(Number.isFinite) || begin < 0 || start < 0 || frames < 1 || available < 1 || !lane.loop && frames > available) { this.inspector(); return toast('Choose audio timing within the recording, or enable looping.', true); }
      return this.changeEdit(() => { Object.assign(lane, {source_in: Math.round(begin * this.fps) / this.fps, start_frame: start, frames}); this.clampLane(lane); });
    }
    if (field === 'fade_in' || field === 'fade_out') {
      const frames = Math.max(0, Math.round(Number(t.value) * this.fps));
      if (!Number.isFinite(frames)) return this.inspector();
      return this.changeEdit(() => { lane[field] = Math.min(frames, lane.frames); const other = field === 'fade_in' ? 'fade_out' : 'fade_in'; lane[other] = Math.min(lane[other] || 0, lane.frames - lane[field]); });
    }
    if (field === 'volume') return this.changeEdit(() => { lane.volume = +t.value; });
    if (field === 'muted') return this.changeEdit(() => { lane.muted = t.checked; });
    if (field === 'loop') return this.changeEdit(() => { lane.loop = t.checked; if (!lane.loop) lane.frames = Math.max(1, Math.min(lane.frames, Math.round((duration - lane.source_in) * this.fps))); this.clampLane(lane); });
  }
  clampLane(t) { t.fade_in = Math.max(0, Math.min(t.frames, Math.round(t.fade_in || 0))); t.fade_out = Math.max(0, Math.min(t.frames - t.fade_in, Math.round(t.fade_out || 0))); }
  async uploadLane(input) {
    const file = input.files[0]; if (!file) return;
    if ((this.edit.audio || []).length >= MAX_LANES) { input.value = ''; return toast(`At most ${MAX_LANES} audio lanes.`, true); }
    App.interactionBusy = true;
    try {
      const fd = new FormData(); fd.append('file', file);
      const saved = await FORM(this.api + '/audio', fd);
      if (this.destroyed) return;
      const info = await GET(this.api + '/source?audio=true&file=' + encodeURIComponent(saved.file));
      if (this.destroyed) return;
      this.sources[saved.file] = info;
      this.changeEdit(() => {
        this.edit.audio = this.edit.audio || [];
        this.edit.audio.push({id: editId(), file: saved.file, lane: this.edit.audio.length, source_in: 0, start_frame: this.frame, frames: Math.max(1, Math.round(info.duration * this.fps)), volume: 1, muted: false, loop: false, fade_in: 0, fade_out: 0});
      });
    } catch (error) { toast(error.message || 'Could not add that audio file.', true); } finally { input.value = ''; App.interactionBusy = false; }
  }
  musicInspector() {
    const m = this.edit.music;
    return `${m ? `<div class="edit-field-pair"><label>Source in (s)<input data-music-in type="number" min="0" step=".1" value="${m.source_in}" aria-label="Music source in"></label>
      <label>Timeline start (s)<input data-music-start type="number" min="0" step=".1" value="${this.seconds(m.start_frame)}" aria-label="Music timeline start"></label></div>
      <label>Length (s)<input data-music-length type="number" min="${1/this.fps}" step=".1" value="${this.seconds(m.frames)}" aria-label="Music duration"></label>
      <label class="row"><input data-music-muted type="checkbox" ${m.muted ? 'checked' : ''}>Mute music</label>
      <label>Music volume<input data-music-volume type="range" min="0" max="1" step=".05" value="${m.volume}" aria-label="Timeline music volume"></label>
      <label class="row"><input data-music-loop type="checkbox" ${m.loop ? 'checked' : ''}>Loop music</label>
      <label class="row"><input data-music-isolate type="checkbox" ${m.isolate_vocals ? 'checked' : ''}>Isolate vocals for lip-sync</label>
      ${this.beatSummary()}<button data-edit="remove-music">Remove music track</button>` : '<p class="muted small">Add audio to preview, mix or lip-sync.</p>'}`;
  }
  position() {
    $('[data-edit-time]', this.root).textContent = `${this.seconds(this.frame)} / ${this.seconds(this.total)} s`;
    const slider = $('[data-edit-scrub]', this.root); slider.max = Math.max(0, this.total - 1); slider.value = this.frame;
    $('.edit-playhead', this.root).style.left = this.frame / this.fps * this.zoom + 'px';
    $('[data-edit-tc]', this.root).textContent = this.tc(this.frame);
    this.titlePreview();
  }
  seek(frame) {
    this.frame = Math.max(0, Math.min(Math.round(frame), Math.max(0, this.total - 1)));
    if (this.playing) this.clock = performance.now() - this.frame / this.fps * 1000;
    this.position(); this.syncMedia(true);
  }
  syncLanes(force) {
    const ids = new Set();
    for (const t of this.edit.audio || []) {
      ids.add(t.id);
      let el = this.laneEls.get(t.id);
      if (t.muted || this.frame < t.start_frame || this.frame >= t.start_frame + t.frames) { el?.pause(); continue; }
      if (!el) { el = new Audio(); el.preload = 'metadata'; el.onloadedmetadata = () => this.syncMedia(true); this.laneEls.set(t.id, el); }
      const src = media(t.file);
      if (el.dataset.source !== src) { el.dataset.source = src; el.src = src; }
      let offset = (this.frame - t.start_frame) / this.fps;
      const remaining = (this.sources[t.file]?.duration || 0) - t.source_in;
      if (t.loop && remaining > 0) offset %= remaining;
      if (el.readyState >= 1 && (force || Math.abs(el.currentTime - (t.source_in + offset)) > .16)) el.currentTime = t.source_in + offset;
      el.volume = Math.min(1, t.volume);
      if (this.playing && el.paused) el.play().catch(() => {});
    }
    for (const [id, el] of this.laneEls) if (!ids.has(id)) { el.pause(); el.removeAttribute('src'); this.laneEls.delete(id); }
  }
  syncMedia(force = false) {
    if (this.destroyed) return;
    this.syncLanes(force);
    let start = 0, clip;
    const where = clipStarts(this.edit.clips).starts;  // in a crossfade the monitor shows the incoming clip (the export blends)
    for (let i = this.edit.clips.length - 1; i >= 0; i--) { if (this.frame >= where[i] && this.frame < where[i] + this.edit.clips[i].frames) { clip = this.edit.clips[i]; start = where[i]; break; } }
    if (!clip) { this.video.pause(); this.video.removeAttribute('src'); return; }
    const src = media(clip.source);
    if (this.video.dataset.source !== src) { this.video.dataset.source = src; this.video.src = src; }
    const target = clip.source_in + (this.frame - start) / this.fps * this.speed(clip);
    if (this.video.playbackRate !== this.speed(clip)) this.video.playbackRate = this.speed(clip);
    if (this.video.readyState >= 1 && (force || Math.abs(this.video.currentTime - target) > .16)) this.video.currentTime = target;
    this.video.muted = clip.muted; this.video.volume = Math.min(1, clip.volume);
    if (this.playing && this.video.paused && !this.videoStarting) {
      this.videoStarting = true; this.video.play().catch(error => { if (error.name !== 'AbortError') { this.pause(); toast('Tap Play again to start the preview.', true); } }).finally(() => { this.videoStarting = false; });
    }
    const m = this.edit.music;
    const active = m && !m.muted && this.frame >= m.start_frame && this.frame < m.start_frame + m.frames;
    if (!active) { this.audio.pause(); return; }
    const audioSrc = media(m.file);
    if (this.audio.dataset.source !== audioSrc) { this.audio.dataset.source = audioSrc; this.audio.src = audioSrc; }
    let offset = (this.frame - m.start_frame) / this.fps;
    const remaining = (this.sources[m.file]?.duration || 0) - m.source_in;
    if (m.loop && remaining > 0) offset %= remaining;
    const audioTime = m.source_in + offset;
    if (this.audio.readyState >= 1 && (force || Math.abs(this.audio.currentTime - audioTime) > .16)) this.audio.currentTime = audioTime;
    this.audio.volume = Math.min(1, m.volume);
    if (this.playing && this.audio.paused && !this.audioStarting) {
      this.audioStarting = true; this.audio.play().catch(error => { if (error.name !== 'AbortError') { this.pause(); toast('Audio could not play. Check the uploaded recording.', true); } }).finally(() => { this.audioStarting = false; });
    }
  }
  togglePlay() {
    if (this.playing) return this.pause();
    if (!this.total) return;
    if (this.frame >= this.total - 1) this.frame = 0;
    this.playing = true; App.interactionBusy = true; this.clock = performance.now() - this.frame / this.fps * 1000;
    $('[data-edit="play"]', this.root).textContent = '❚❚';
    const tick = () => {
      if (!this.playing || this.destroyed) return;
      const frame = Math.floor((performance.now() - this.clock) / 1000 * this.fps);
      if (frame >= this.total) { this.frame = this.total - 1; this.pause(); this.position(); return; }
      this.frame = frame; this.position(); this.ensureVisible(); this.syncMedia(); this.animation = requestAnimationFrame(tick);
    };
    this.syncMedia(true); this.animation = requestAnimationFrame(tick);
  }
  pause() {
    this.playing = false; App.interactionBusy = false; cancelAnimationFrame(this.animation);
    this.video.pause(); this.audio.pause(); this.laneEls?.forEach(el => el.pause());
    const button = $('[data-edit="play"]', this.root); if (button) button.textContent = '▶';
  }
  remove() {
    const doomed = new Set(this.selection), first = this.edit.clips.findIndex(c => doomed.has(c.id));
    if (first < 0) return;
    this.changeEdit(() => { this.edit.clips = this.edit.clips.filter(c => !doomed.has(c.id)); this.selected = this.edit.clips[Math.min(first, this.edit.clips.length - 1)]?.id; });
  }
  async addTake(shotId, index, replace = false) {
    const sh = this.ctx.e.shots.find(sh => sh.id === shotId), take = sh?.takes.items[index]; if (!take) return;
    const info = await GET(this.api + '/source?file=' + encodeURIComponent(take.file)); this.sources[take.file] = info;
    const maximum = Math.round(info.video_duration * this.fps);
    this.changeEdit(() => {
      if (replace && this.clip) Object.assign(this.clip, {shot_id: shotId, source: take.file, source_in: 0, frames: Math.min(this.clip.frames, this.fitFrames(this.clip, maximum)), lipsync: false});
      else { const clip = {id: editId(), shot_id: shotId, source: take.file, source_in: 0, frames: maximum, muted: false, volume: 1, lipsync: false, speed: 1, transition: 0}; this.edit.clips.push(clip); this.selected = clip.id; this.frame = this.start(clip.id); }
    });
  }
  /** Counts quick consecutive taps on the same item (a redraw between clicks stops browsers sending dblclick). */
  tap(key) {
    const now = performance.now(), count = this.tapKey === key && now - this.tapAt < 450 ? this.tapCount + 1 : 1;
    this.tapKey = key; this.tapAt = now; this.tapCount = count;
    return count;
  }
  refocus() { if (this.destroyed || !this.root.isConnected) return; const a = document.activeElement; if (!a || a === document.body || !this.root.contains(a)) $('.take-editor', this.root)?.focus({preventScroll: true}); }
  tc(frame) { const f = Math.max(1, Math.round(this.fps)), s = Math.floor(frame / f), p = n => String(n).padStart(2, '0'); return `${p(Math.floor(s / 3600))}:${p(Math.floor(s / 60) % 60)}:${p(s % 60)}:${p(frame % f)}`; }
  cuts() {  // the edit points: the end of a hard cut, the middle of a crossfade
    const {starts, total} = clipStarts(this.edit.clips), out = [0];
    this.edit.clips.slice(1).forEach((c, i) => out.push(starts[i + 1] + Math.floor((c.transition || 0) / 2)));
    out.push(total); return out;
  }
  ensureVisible() {
    const scroll = $('.edit-scroll', this.root), x = this.frame / this.fps * this.zoom;
    if (x < scroll.scrollLeft + 24 || x > scroll.scrollLeft + scroll.clientWidth - 48) scroll.scrollLeft = Math.max(0, x - scroll.clientWidth * .3);
  }
  setZoom(zoom, anchorX) {
    zoom = Math.max(4, Math.min(400, zoom));
    const scroll = $('.edit-scroll', this.root), box = scroll.getBoundingClientRect();
    const ax = anchorX == null ? box.width / 2 : anchorX - box.left, time = (scroll.scrollLeft + ax) / this.zoom;
    this.zoom = zoom; $('[data-edit-zoom]', this.root).value = Math.round(zoom);
    this.tracks(); this.position();
    const was = scroll.scrollLeft; scroll.scrollLeft = Math.max(0, time * zoom - ax);
    if (Math.abs(scroll.scrollLeft - was) > 1) { this.tracks(); this.position(); }  // thumbnails follow the new view in the same pass
  }
  fit() {
    const scroll = $('.edit-scroll', this.root), music = this.edit.music;
    const end = Math.max(this.total, music ? music.start_frame + music.frames : 0, this.audioEnd(), this.fps) / this.fps;
    this.setZoom((scroll.clientWidth - 40) / end); scroll.scrollLeft = 0;
  }
  step(frames) { this.pause(); this.seek(this.frame + frames); this.ensureVisible(); }
  jumpCut(direction) {
    this.pause(); const cuts = this.cuts();
    const target = direction > 0 ? cuts.find(f => f > this.frame) : [...cuts].reverse().find(f => f < this.frame);
    this.seek(target ?? (direction > 0 ? this.total - 1 : 0)); this.ensureVisible();
  }
  split() {
    const c = this.clip; if (!c) return;
    const offset = this.frame - this.start(c.id);
    if (offset <= 0 || offset >= c.frames) return toast('Place the playhead inside the selected clip.', true);
    this.changeEdit(() => { const right = {...c, id: editId(), source_in: c.source_in + offset / this.fps * this.speed(c), frames: c.frames - offset, fade_in: 0, transition: 0}; c.frames = offset; c.fade_out = 0; this.edit.clips.splice(this.edit.clips.indexOf(c) + 1, 0, right); this.selected = right.id; });
  }
  trimTo(startSide) {
    const c = this.clip; if (!c) return;
    const offset = this.frame - this.start(c.id);
    if (offset <= 0 || offset >= c.frames) return toast('Place the playhead inside the selected clip.', true);
    this.changeEdit(() => { if (startSide) { c.source_in += offset / this.fps * this.speed(c); c.frames -= offset; } else c.frames = offset; });
  }
  duplicate() {
    const picked = this.selectedClips(); if (!picked.length) return;
    this.changeEdit(() => {
      const copies = picked.map(c => ({...c, id: editId(), transition: 0})), at = this.edit.clips.indexOf(picked.at(-1)) + 1;
      this.edit.clips.splice(at, 0, ...copies); this.selection = new Set(copies.map(c => c.id)); this._primary = copies.at(-1).id; this.anchor = copies[0].id;
    });
  }
  moveClip(direction) {
    const picked = this.selectedClips(); if (!picked.length) return;
    const index = picked.map(c => this.edit.clips.indexOf(c));
    if (index.at(-1) - index[0] !== picked.length - 1) return toast('Select neighbouring clips to move them together.', true);
    this.changeEdit(() => {
      const clips = this.edit.clips; clips.splice(index[0], picked.length);
      clips.splice(Math.max(0, Math.min(clips.length, index[0] + direction)), 0, ...picked);
      this.frame = this.start(this.selected);
    });
  }
  copySelection(cut = false) {
    const picked = this.selectedClips(); if (!picked.length) return;
    this.clipboard = editClone(picked); toast(`${cut ? 'Cut' : 'Copied'} ${pl(picked.length, 'clip')}.`);
    if (cut) this.remove();
  }
  paste() {
    if (!this.clipboard.length) return toast('Copy some clips first (Ctrl+C).', true);
    this.changeEdit(() => {
      const copies = this.clipboard.map(c => ({...editClone(c), id: editId(), transition: 0, lipsync: this.edit.music ? c.lipsync : false}));
      const last = this.selectedClips().at(-1), at = last ? this.edit.clips.indexOf(last) + 1 : this.edit.clips.length;
      this.edit.clips.splice(at, 0, ...copies); this.selection = new Set(copies.map(c => c.id)); this._primary = copies.at(-1).id; this.anchor = copies[0].id;
    });
  }
  thumbs(c, left, px) {
    const info = this.sources[c.source], window = this.thumbWindow;
    if (!info || info.missing || !info.video_duration || !window || left + px < window.from || left > window.to) return '';
    const tile = Math.max(24, Math.round(80 * (info.width && info.height ? info.width / info.height : 9 / 16)));
    const count = Math.max(1, Math.min(14, Math.ceil(px / tile))), last = Math.max(0, info.video_duration - .05);
    const tiles = Array.from({length: count}, (_, k) => {
      const seconds = Math.min(last, c.source_in + (k + .5) * (px / count) / this.zoom * this.speed(c));
      const url = `${this.api}/thumb?file=${encodeURIComponent(c.source).replace(/'/g, '%27')}&t=${seconds.toFixed(2)}&h=120`;
      return `<i style="background-image:url('${url}')"></i>`;
    });
    return `<span class="edit-thumbs" aria-hidden="true">${tiles.join('')}</span>`;
  }
  thumbsStale() {
    const w = this.thumbWindow, scroll = $('.edit-scroll', this.root); if (!w) return true;
    const left = scroll.scrollLeft, right = left + scroll.clientWidth;
    return (left - 150 < w.from && w.from > 0) || (right + 150 > w.to && w.to < this.canvasWidth);
  }
  // ── beats ──
  ratio() { return {half: .5, double: 2, normal: 1}[this.beatMode]; }
  ensureBeats() {
    const m = this.edit.music;
    if (!m) { this.beatData = null; return; }
    if (!this.beatsOn || this.beatLoading || this.beatData?.file === m.file || this.beatFailed === m.file) return;
    const file = m.file; this.beatLoading = true; this.status('Finding the beat…', 'saving');
    GET(`${this.api}/beats?file=${encodeURIComponent(file)}`).then(data => {
      if (this.destroyed) return;
      this.beatData = {...data, file}; this.beatCache = null;
      this.status(data.beats.length ? `Beat grid ready · ${Math.round(data.tempo)} BPM` : 'No clear beat found in this recording', 'saved');
    }).catch(() => { this.beatFailed = file; this.beatsOn = false; if (!this.destroyed) this.status('Could not find the beat', 'error'); })
      .finally(() => { this.beatLoading = false; if (!this.destroyed) this.draw(); });
  }
  toggleBeats() {
    if (!this.edit.music) return toast('Add music first, then turn on Beats.', true);
    this.beatsOn = !this.beatsOn; store.set('rw-beats', this.beatsOn ? '1' : '0'); this.beatFailed = null; this.draw();
  }
  setBeatMode(mode) { this.beatMode = this.beatMode === mode ? 'normal' : mode; this.beatCache = null; this.draw(); }
  beatTimes() {
    const d = this.beatData; if (!d?.beats.length) return {times: [], bars: new Set()};
    const confident = d.bar_confidence >= 1.15 && d.bars.length, bars = new Set(confident ? d.bars.map(i => d.beats[i]) : []);
    if (this.beatMode === 'half') {
      const parity = confident ? d.bars[0] % 2 : d.strength.reduce((n, s, i) => n + (i % 2 ? -s : s), 0) >= 0 ? 0 : 1;
      return {times: d.beats.filter((_, i) => i % 2 === parity), bars};
    }
    if (this.beatMode === 'double') {
      const times = []; d.beats.forEach((t, i) => { times.push(t); if (i + 1 < d.beats.length) times.push((t + d.beats[i + 1]) / 2); });
      return {times, bars};
    }
    return {times: d.beats, bars};
  }
  beatFrames() {
    const m = this.edit.music, d = this.beatData;
    if (!this.beatsOn || !m || !d || d.file !== m.file) return [];
    const key = [d.file, this.beatMode, m.start_frame, m.source_in, m.frames, m.loop, this.fps].join('|');
    if (this.beatCache?.key === key) return this.beatCache.frames;
    const {times, bars} = this.beatTimes(), end = m.start_frame + m.frames, out = [];
    const remaining = Math.max(.001, (this.sources[m.file]?.duration || d.duration) - m.source_in);
    for (let lap = 0; lap < (m.loop ? 400 : 1); lap++) {
      for (const time of times) {
        const rel = time - m.source_in; if (rel < 0 || rel >= remaining) continue;
        const frame = Math.round((m.start_frame / this.fps + rel + lap * remaining) * this.fps);
        if (frame < end) out.push({frame, bar: bars.has(time)});
      }
      if (m.start_frame + Math.round((lap + 1) * remaining * this.fps) >= end) break;
    }
    out.sort((a, b) => a.frame - b.frame);
    this.beatCache = {key, frames: out};
    return out;
  }
  drawBeats() {
    const grid = $('.edit-beatgrid', this.root), beats = this.beatFrames();
    const gap = this.beatData?.tempo ? 60 / (this.beatData.tempo * this.ratio()) * this.zoom : 0;
    grid.innerHTML = beats.filter(b => gap >= 7 || b.bar).map(b => `<i class="${b.bar ? 'bar' : ''}" style="left:${b.frame / this.fps * this.zoom}px"></i>`).join('');
  }
  beatButtons() {
    const m = this.edit.music, on = !!m && this.beatsOn, d = this.beatData && this.beatData.file === m?.file ? this.beatData : null;
    const button = $('[data-edit="beats"]', this.root); button.disabled = !m; button.setAttribute('aria-pressed', String(on));
    button.textContent = on && this.beatLoading ? 'Finding beat…' : on && d?.tempo ? `Beats · ${Math.round(d.tempo * this.ratio())} BPM` : 'Beats';
    const ready = on && this.beatFrames().length > 0;
    for (const name of ['beat-half', 'beat-double', 'beat-prev', 'beat-next', 'beat-cut']) $(`[data-edit="${name}"]`, this.root).disabled = !ready;
    $('[data-edit="beat-half"]', this.root).setAttribute('aria-pressed', String(this.beatMode === 'half'));
    $('[data-edit="beat-double"]', this.root).setAttribute('aria-pressed', String(this.beatMode === 'double'));
  }
  beatSummary() {
    const m = this.edit.music, d = this.beatData;
    if (!m) return '';
    if (this.beatLoading) return '<p class="muted small">Finding the beat…</p>';
    if (!this.beatsOn || !d || d.file !== m.file) return '<p class="muted small">Turn on <b>Beats</b> to find the tempo and show the beat grid.</p>';
    if (!d.beats.length) return '<p class="muted small">No clear beat found in this recording.</p>';
    const mode = this.beatMode === 'normal' ? '' : ` · ${this.beatMode}-time`;
    return `<p class="small"><b>${Math.round(d.tempo * this.ratio() * 10) / 10} BPM</b> · ${this.beatTimes().times.length} beats${mode}${d.bar_confidence >= 1.15 ? ' · bars marked' : ''}</p>`;
  }
  jumpBeat(direction) {
    const frames = this.beatFrames().map(b => b.frame); if (!frames.length) return;
    this.pause(); const target = direction > 0 ? frames.find(f => f > this.frame) : [...frames].reverse().find(f => f < this.frame);
    if (target != null) { this.seek(target); this.ensureVisible(); }
  }
  /** Plan (without applying) how to move cuts onto beats and/or add cuts on the beat grid. */
  planBeatCuts({align, tolerance, every, scope}) {
    const fps = this.fps, edit = editClone(this.edit), beats = this.beatFrames(), frames = beats.map(b => b.frame);
    const minFrames = Math.max(2, Math.round(.2 * fps)), inScope = new Set(scope === 'selected' ? this.selection : edit.clips.map(c => c.id));
    const result = {edit, aligned: 0, already: 0, stuck: 0, added: 0};
    const nearestBeat = pos => frames.reduce((best, f) => best == null || Math.abs(f - pos) < Math.abs(best - pos) ? f : best, null);
    if (align) {
      for (let i = 0; i + 1 < edit.clips.length; i++) {
        const left = edit.clips[i], right = edit.clips[i + 1];
        if (!inScope.has(left.id) && !inScope.has(right.id)) continue;
        const cut = clipStarts(edit.clips).starts[i + 1] + Math.floor((right.transition || 0) / 2), beat = nearestBeat(cut);
        if (beat == null) continue;
        const shift = beat - cut;
        if (shift === 0) { result.already++; continue; }
        const low = Math.max(minFrames - left.frames, -this.fitFrames(right, Math.round(right.source_in * fps)));
        const high = Math.min(right.frames - minFrames, this.fitFrames(left, this.sourceFrames(left) - Math.round(left.source_in * fps)) - left.frames);
        const t = right.transition || 0;  // the crossfade has to stay valid for both clips after the move
        if ((tolerance && Math.abs(shift) > Math.round(tolerance * fps)) || shift < low || shift > high || t * 2 > Math.min(left.frames + shift, right.frames - shift)) { result.stuck++; continue; }
        left.frames += shift; right.source_in = (Math.round(right.source_in * fps) + Math.round(shift * this.speed(right))) / fps; right.frames -= shift; result.aligned++;
      }
    }
    if (every > 0 && frames.length) {
      const firstBar = beats.findIndex(b => b.bar), anchor = firstBar >= 0 ? firstBar % every : 0;
      const grid = frames.filter((_, i) => i >= anchor && (i - anchor) % every === 0);
      const clips = [], where = clipStarts(edit.clips).starts;
      for (const [ci, c] of edit.clips.entries()) {
        const start = where[ci], end = start + c.frames, cuts = [];
        if (inScope.has(c.id)) for (const g of grid) if (g >= start + minFrames && g <= end - minFrames && g - (cuts.at(-1) ?? start) >= minFrames) cuts.push(g);
        if (cuts.length && end - cuts.at(-1) < minFrames) cuts.pop();
        const bounds = [start, ...cuts, end];
        const fadeOut = c.fade_out || 0;
        for (let k = 0; k + 1 < bounds.length; k++) {
          const lastPart = k + 2 === bounds.length;
          clips.push(k === 0 ? Object.assign(c, {frames: bounds[1] - bounds[0], fade_out: lastPart ? fadeOut : 0}) : {...c, id: editId(), transition: 0, source_in: c.source_in + (bounds[k] - start) / fps * this.speed(c), frames: bounds[k + 1] - bounds[k], fade_in: 0, fade_out: lastPart ? fadeOut : 0});
        }
        result.added += cuts.length;
      }
      edit.clips = clips;
    }
    return result;
  }
  beatDialog() {
    if (!this.beatFrames().length) return toast('Turn on Beats first, with music on the timeline.', true);
    const some = this.selection.size > 1, d = this.beatData;
    modal(`<h2 style="margin-top:0">Cut to the beat</h2>
      <p class="muted small">${Math.round(d.tempo * this.ratio() * 10) / 10} BPM. Nothing changes until you apply, and it is one undo step.</p>
      <label class="row"><input type="checkbox" id="bc-align" checked> Move existing cuts onto the nearest beat</label>
      <label class="row bc-indent">…only if the cut is within <select id="bc-tol"><option value="0.15">0.15 s</option><option value="0.3" selected>0.3 s</option><option value="0">any distance</option></select></label>
      <label class="row"><input type="checkbox" id="bc-split"> Add a cut every <select id="bc-every"><option value="1">beat</option><option value="2">2 beats</option><option value="4" selected>4 beats (one bar)</option><option value="8">8 beats (two bars)</option></select></label>
      <fieldset class="bc-scope"><legend>Apply to</legend>
        <label class="row"><input type="radio" name="bc-scope" value="all" ${some ? '' : 'checked'}> The whole timeline</label>
        <label class="row"><input type="radio" name="bc-scope" value="selected" ${some ? 'checked' : ''} ${this.selection.size ? '' : 'disabled'}> Selected clips (${this.selection.size})</label></fieldset>
      <p id="bc-preview" class="small" role="status"></p>
      <div class="row" style="justify-content:flex-end"><button id="bc-cancel">Cancel</button><button class="primary" id="bc-apply">Apply</button></div>`);
    const options = () => ({align: $('#bc-align').checked, tolerance: +$('#bc-tol').value, every: $('#bc-split').checked ? +$('#bc-every').value : 0, scope: $('input[name=bc-scope]:checked').value});
    const summary = plan => [plan.aligned && `${pl(plan.aligned, 'cut')} moved onto the beat`, plan.already && `${plan.already} already on a beat`,
      plan.added && `${pl(plan.added, 'new cut')} added`, plan.stuck && `${plan.stuck} can’t move (too far, or not enough footage)`].filter(Boolean).join(' · ') || 'Nothing to change with these options.';
    const refresh = () => { const plan = this.planBeatCuts(options()); $('#bc-preview').textContent = summary(plan); $('#bc-apply').disabled = !plan.aligned && !plan.added; };
    $('#modal-body').oninput = refresh; refresh();
    $('#bc-cancel').onclick = () => { closeModal(); this.refocus(); };
    $('#bc-apply').onclick = () => {
      const plan = this.planBeatCuts(options()); closeModal();
      this.changeEdit(() => { this.edit = plan.edit; }); toast(summary(plan)); this.refocus();
    };
  }
  // ── titles ──
  drawTitles() {
    const px = f => f / this.fps * this.zoom;
    $('.edit-title-track', this.root).innerHTML = this.titles.length ? this.titles.map(t => `<button type="button" class="edit-title ${t.id === this.titleSel ? 'sel' : ''}" data-title="${esc(t.id)}" style="left:${px(t.start_frame)}px;width:${Math.max(6, px(t.frames))}px" title="${esc(t.text)} · ${this.tc(t.start_frame)}" aria-label="Title ${esc(t.text)} at ${this.tc(t.start_frame)}"><span>${esc(t.text.replace(/\n/g, ' '))}</span></button>`).join('') : '<span class="edit-track-empty">Add on-screen text with “Title”</span>';
  }
  titlePreview() {
    const layer = $('.edit-title-preview', this.root); if (!layer) return;
    const active = this.titles.filter(t => this.frame >= t.start_frame && this.frame < t.start_frame + t.frames);
    if (!active.length) { if (layer.firstChild) layer.innerHTML = ''; return; }
    const v = this.video.getBoundingClientRect(), host = layer.parentElement.getBoundingClientRect(), dims = this.ctx.e._settings?.video || {width: 9, height: 16};
    const scale = Math.min(v.width / dims.width, v.height / dims.height), w = dims.width * scale, h = dims.height * scale;
    Object.assign(layer.style, {left: v.left - host.left + (v.width - w) / 2 + 'px', top: v.top - host.top + (v.height - h) / 2 + 'px', width: w + 'px', height: h + 'px'});
    layer.innerHTML = active.map(t => `<div class="ttl ${t.box ? 'box' : ''}" style="left:${t.x * 100}%;top:${t.y * 100}%;font-size:${t.size * h}px;color:${esc(t.color)}">${esc(t.text)}</div>`).join('');
  }
  addTitle() {
    const total = this.total; if (!total) return toast('Add clips to the timeline first.', true);
    const title = {id: markerId().replace('mk_', 'tt_'), text: 'Title', start_frame: this.frame, frames: Math.max(1, Math.min(Math.round(3 * this.fps), total - this.frame)), x: .5, y: .85, size: .06, color: '#ffffff', box: true};
    this.changeEdit(() => { this.edit.titles = [...this.titles, title]; this.titleSel = title.id; this.markerSel = null; });
    this.titlesDialog(title.id);
  }
  removeTitle(id) { this.changeEdit(() => { this.edit.titles = this.titles.filter(t => t.id !== id); if (this.titleSel === id) this.titleSel = null; }); }
  titlesDialog(focusId) {
    modal(`<h2 style="margin-top:0">Titles</h2><p class="muted small">On-screen text for part of the timeline. Position and size are relative to the frame, so the export matches the preview.</p><div id="tt-list" class="tt-list"></div>
      <div class="row" style="margin-top:12px"><button id="tt-add">Add at playhead</button><span class="grow"></span><button class="primary" id="tt-done">Done</button></div>`);
    const render = () => {
      $('#tt-list').innerHTML = this.titles.length ? this.titles.map(t => `<div class="tt-card" data-tt="${esc(t.id)}"><textarea data-tt-text rows="2" maxlength="200" aria-label="Title text">${esc(t.text)}</textarea>
        <div class="tt-grid"><label>Start (s)<input data-tt-start type="number" min="0" step=".1" value="${(t.start_frame / this.fps).toFixed(2)}"></label>
        <label>Length (s)<input data-tt-length type="number" min=".1" step=".1" value="${(t.frames / this.fps).toFixed(2)}"></label>
        <label>Across (%)<input data-tt-x type="number" min="0" max="100" step="1" value="${Math.round(t.x * 100)}"></label>
        <label>Down (%)<input data-tt-y type="number" min="0" max="100" step="1" value="${Math.round(t.y * 100)}"></label>
        <label>Size (% of height)<input data-tt-size type="number" min="2" max="30" step=".5" value="${+(t.size * 100).toFixed(1)}"></label>
        <label>Colour<input data-tt-color type="color" value="${esc(t.color)}"></label>
        <label class="row"><input data-tt-box type="checkbox" ${t.box ? 'checked' : ''}> Dark box</label><button class="danger" data-tt-del>Delete</button></div></div>`).join('') : '<p class="muted">No titles yet.</p>';
    };
    const rowOf = el => this.titles.find(t => t.id === el.closest('[data-tt]')?.dataset.tt), list = $('#tt-list');
    list.onchange = event => {
      const el = event.target, found = rowOf(el); if (!found) return;
      const n = Number(el.value), fps = this.fps, clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
      const set = (key, value) => this.changeEdit(() => { rowOf(el)[key] = value; });
      if (el.matches('[data-tt-text]')) { if (!el.value.trim()) { toast('A title needs some text.', true); return render(); } set('text', el.value.trim().slice(0, 200)); }
      else if (el.matches('[data-tt-start]')) { if (!Number.isFinite(n) || n < 0) return render(); set('start_frame', Math.round(n * fps)); }
      else if (el.matches('[data-tt-length]')) { if (!Number.isFinite(n) || n * fps < 1) return render(); set('frames', Math.max(1, Math.round(n * fps))); }
      else if (el.matches('[data-tt-x]')) { if (!Number.isFinite(n)) return render(); set('x', clamp(n / 100, 0, 1)); }
      else if (el.matches('[data-tt-y]')) { if (!Number.isFinite(n)) return render(); set('y', clamp(n / 100, 0, 1)); }
      else if (el.matches('[data-tt-size]')) { if (!Number.isFinite(n)) return render(); set('size', clamp(n / 100, .02, .3)); }
      else if (el.matches('[data-tt-color]')) set('color', el.value.toLowerCase());
      else if (el.matches('[data-tt-box]')) set('box', el.checked);
    };
    list.onclick = event => { if (event.target.closest('[data-tt-del]') && rowOf(event.target)) { this.removeTitle(rowOf(event.target).id); render(); } };
    $('#tt-add').onclick = () => { const total = this.total; if (!total) return; const title = {id: markerId().replace('mk_', 'tt_'), text: 'Title', start_frame: this.frame, frames: Math.max(1, Math.min(Math.round(3 * this.fps), total - this.frame)), x: .5, y: .85, size: .06, color: '#ffffff', box: true}; this.changeEdit(() => { this.edit.titles = [...this.titles, title]; this.titleSel = title.id; }); render(); };
    $('#tt-done').onclick = () => { closeModal(); this.refocus(); };
    render();
    if (focusId) { const field = $(`[data-tt="${CSS.escape(focusId)}"] [data-tt-text]`); field?.focus(); field?.select(); }
  }
  // ── markers and the render range ──
  drawMarks() {
    const px = f => f / this.fps * this.zoom;
    $('.edit-markers', this.root).innerHTML = this.markers.map(m => `<button type="button" class="edit-marker c-${m.color} ${m.id === this.markerSel ? 'sel' : ''}" data-marker="${esc(m.id)}" style="left:${px(m.frame)}px" title="${esc(m.label || 'Marker')} · ${this.tc(m.frame)}" aria-label="Marker ${esc(m.label)} at ${this.tc(m.frame)}"><span>${esc(m.label)}</span></button>`).join('');
    const r = this.range, view = $('.edit-rangeview', this.root);
    view.hidden = !r; if (!r) return;
    const a = px(r.start), b = px(r.end);
    $('.dim.a', view).style.width = a + 'px'; $('.dim.b', view).style.left = b + 'px';
    Object.assign($('.bar', view).style, {left: a + 'px', width: Math.max(2, b - a) + 'px'});
    $('.handle.in', view).style.left = a + 'px'; $('.handle.out', view).style.left = b + 'px';
  }
  markButtons() {
    const r = this.range, end = r ? Math.min(r.end, this.total) : 0;
    $('[data-edit="markers"]', this.root).textContent = `Markers (${this.markers.length})`;
    $('[data-edit="titles"]', this.root).textContent = `Titles (${this.titles.length})`;
    $('[data-edit="range-clear"]', this.root).disabled = !r;
    $('[data-edit="export-range"]', this.root).disabled = !r || end <= r.start;
    $('[data-edit-range-info]', this.root).textContent = !r ? '' : end <= r.start ? 'range is past the end' : `${(r.start / this.fps).toFixed(2)} → ${(end / this.fps).toFixed(2)} s · ${((end - r.start) / this.fps).toFixed(2)} s long`;
  }
  addMarker(toggle = true) {
    const here = this.markers.find(m => m.frame === this.frame);
    if (here) return toggle ? this.removeMarker(here.id) : toast('There is already a marker at the playhead.', true);
    const marker = {id: markerId(), frame: this.frame, label: `Marker ${this.markers.length + 1}`, color: 'blue'};
    this.changeEdit(() => { this.edit.markers = [...this.markers, marker]; this.markerSel = marker.id; });
  }
  removeMarker(id) { this.changeEdit(() => { this.edit.markers = this.markers.filter(m => m.id !== id); if (this.markerSel === id) this.markerSel = null; }); }
  jumpMarker(direction) {
    const frames = [...new Set(this.markers.map(m => m.frame))].sort((a, b) => a - b);
    if (!frames.length) return toast('No markers yet. Press Shift+M to add one.', true);
    const target = direction > 0 ? frames.find(f => f > this.frame) : [...frames].reverse().find(f => f < this.frame);
    if (target == null) return;
    this.pause(); this.seek(target); this.markerSel = this.markers.find(m => m.frame === target)?.id ?? null; this.ensureVisible(); this.draw();
  }
  markersDialog(focusId) {
    modal(`<h2 style="margin-top:0">Markers</h2><div id="mk-list" class="mk-list"></div>
      <details id="mk-text-wrap"><summary>Plain-text list</summary><textarea id="mk-text" readonly rows="6" aria-label="Marker list as text"></textarea></details>
      <div class="row" style="margin-top:14px"><button id="mk-add">Add at playhead</button><button id="mk-copy">Copy list</button><span class="grow"></span><button class="primary" id="mk-done">Done</button></div>`);
    const text = () => { $('#mk-text').value = this.markers.map(m => `${this.tc(m.frame)}  ${m.label || 'Marker'}`).join('\n'); };
    const render = () => {
      $('#mk-list').innerHTML = this.markers.length ? this.markers.map(m => `<div class="mk-row" data-mk="${esc(m.id)}"><button class="link" data-mk-go title="Go to this marker"><code>${this.tc(m.frame)}</code></button><input data-mk-label value="${esc(m.label)}" maxlength="80" placeholder="Name" aria-label="Marker name"><select data-mk-color aria-label="Marker colour">${MARKER_COLORS.map(c => `<option ${c === m.color ? 'selected' : ''}>${c}</option>`).join('')}</select><button class="danger" data-mk-del aria-label="Delete marker" title="Delete marker">✕</button></div>`).join('')
        : '<p class="muted">No markers yet. Press <span class="kbd">Shift</span> <span class="kbd">M</span> at the playhead, or use “Add at playhead”.</p>';
      text();
    };
    const rowOf = el => this.markers.find(m => m.id === el.closest('[data-mk]')?.dataset.mk);
    const list = $('#mk-list');
    list.onchange = event => {
      if (!rowOf(event.target)) return;
      if (event.target.matches('[data-mk-label]')) { this.changeEdit(() => { rowOf(event.target).label = event.target.value.trim().slice(0, 80); }); text(); }
      if (event.target.matches('[data-mk-color]')) { this.changeEdit(() => { rowOf(event.target).color = event.target.value; }); render(); }
    };
    list.onclick = event => {
      const m = rowOf(event.target); if (!m) return;
      if (event.target.closest('[data-mk-del]')) { this.removeMarker(m.id); render(); }
      if (event.target.closest('[data-mk-go]')) { closeModal(); this.pause(); this.seek(m.frame); this.markerSel = m.id; this.ensureVisible(); this.draw(); this.refocus(); }
    };
    $('#mk-add').onclick = () => { this.addMarker(false); render(); };
    $('#mk-copy').onclick = async () => {
      try { await navigator.clipboard.writeText($('#mk-text').value); toast('Marker list copied.'); }
      catch { $('#mk-text-wrap').open = true; $('#mk-text').select(); toast('Select and copy the text below (copying needs a secure connection).'); }
    };
    $('#mk-done').onclick = () => { closeModal(); this.refocus(); };
    render();
    if (focusId) { const input = $(`[data-mk="${CSS.escape(focusId)}"] [data-mk-label]`); input?.focus(); input?.select(); }
  }
  setRange(side) {
    const f = this.frame, total = this.total, r = this.range;
    const start = side === 'in' ? f : (r && r.start < (f === total - 1 ? total : f) ? r.start : 0);
    const end = side === 'in' ? (r && r.end > f ? r.end : total) : (f === total - 1 ? total : f);
    if (end <= start) return toast('The range needs at least one frame.', true);
    this.changeEdit(() => { this.edit.range = {start, end}; });
  }
  clearRange() { if (this.range) this.changeEdit(() => { this.edit.range = null; }); }
  async renderRange() {
    const r = this.range; if (!r) return;
    const end = Math.min(r.end, this.total);
    if (end <= r.start) return toast('The range is past the end of the timeline.', true);
    this.pause(); await this.flush();
    return startJob(this.api + '/export', {range: [r.start, end]});
  }
  key(event) {
    const k = event.key, low = k.toLowerCase(), mod = event.ctrlKey || event.metaKey, go = fn => { event.preventDefault(); fn(); };
    const button = event.target.closest('button, summary, a');
    if (mod) {
      if (low === 'z') return go(() => this.restore(event.shiftKey));
      if (low === 'y') return go(() => this.restore(true));
      if (low === 's') return go(() => this.flush().catch(error => this.error(error)));
      if (low === 'd' && this.clip) return go(() => this.duplicate());
      if (low === 'a') return go(() => this.selectAll());
      if (low === 'c') return go(() => this.copySelection());
      if (low === 'x') return go(() => this.copySelection(true));
      if (low === 'v') return go(() => this.paste());
      return;
    }
    if (event.altKey) {
      if (k === 'ArrowUp') return go(() => this.jumpMarker(-1));
      if (k === 'ArrowDown') return go(() => this.jumpMarker(1));
      if (low === 'x') return go(() => this.clearRange());
      if (k === 'ArrowLeft' && this.clip) return go(() => this.moveClip(-1));
      if (k === 'ArrowRight' && this.clip) return go(() => this.moveClip(1));
      return;
    }
    if (event.code === 'Space') { if (button) return; return go(() => this.togglePlay()); }
    if (k === 'Delete' || k === 'Backspace') return go(() => this.markerSel ? this.removeMarker(this.markerSel) : this.titleSel ? this.removeTitle(this.titleSel) : this.remove());
    if (k === 'ArrowLeft') return go(() => this.step(event.shiftKey ? -Math.round(this.fps) : -1));
    if (k === 'ArrowRight') return go(() => this.step(event.shiftKey ? Math.round(this.fps) : 1));
    if (k === 'ArrowUp') return go(() => this.jumpCut(-1));
    if (k === 'ArrowDown') return go(() => this.jumpCut(1));
    if (k === 'Home') return go(() => { this.pause(); this.seek(0); this.ensureVisible(); });
    if (k === 'End') return go(() => { this.pause(); this.seek(this.total - 1); this.ensureVisible(); });
    if (low === 'k') return go(() => this.pause());
    if (low === 'l') return go(() => { if (!this.playing) this.togglePlay(); });
    if (low === 's') return go(() => this.split());
    if (low === 'q') return go(() => this.trimTo(true));
    if (low === 'w') return go(() => this.trimTo(false));
    if (low === 'm' && event.shiftKey) return go(() => this.addMarker());
    if (low === 'i') return go(() => this.setRange('in'));
    if (low === 'o') return go(() => this.setRange('out'));
    if (low === 'm' && this.clip) return go(() => { const picked = this.selectedClips(), mute = picked.some(c => !c.muted); this.changeEdit(() => picked.forEach(c => { c.muted = mute; })); });
    if (low === 'v') return go(() => { this.tool = 'select'; this.draw(); });
    if (low === 'y') return go(() => { this.tool = 'slip'; this.draw(); });
    if (low === 'r') return go(() => { this.tool = 'roll'; this.draw(); });
    if (low === 'b') return go(() => this.toggleBeats());
    if (k === ',') return go(() => this.jumpBeat(-1));
    if (k === '.') return go(() => this.jumpBeat(1));
    if (low === 'n') return go(() => { this.snap = !this.snap; store.set('rw-snap', this.snap ? '1' : '0'); this.draw(); });
    if (k === '+' || k === '=') return go(() => this.setZoom(this.zoom * 1.4));
    if (k === '-' || k === '_') return go(() => this.setZoom(this.zoom / 1.4));
    if (k === '\\') return go(() => this.fit());
  }
  snapTargets(d) {
    const out = new Set([0, this.frame]), before = d.before, music = before.music;
    const global = ['music', 'lane', 'marker', 'range-in', 'range-out'].includes(d.mode);
    const {starts, total} = clipStarts(before.clips), at = before.clips.findIndex(c => c.id === d.id), own = at < 0 ? null : starts[at];
    before.clips.forEach((c, i) => { if (global || (own != null && starts[i] + c.frames <= own)) { out.add(starts[i]); out.add(starts[i] + c.frames); } });
    if (d.mode !== 'music' && d.mode !== 'lane') for (const b of this.beatFrames()) out.add(b.frame);
    if (global) out.add(total);
    if (d.mode !== 'music' && music) { out.add(music.start_frame); out.add(music.start_frame + music.frames); }
    for (const t of before.audio || []) if (!(d.mode === 'lane' && t.id === d.laneId)) { out.add(t.start_frame); out.add(t.start_frame + t.frames); }
    for (const m of before.markers || []) if (!(d.mode === 'marker' && m.id === d.id)) out.add(m.frame);
    return [...out];
  }
  nearest(frame, targets) {
    let best = null;
    for (const t of targets) { const gap = Math.abs(t - frame) * this.zoom / this.fps; if (gap <= 8 && (best == null || gap < best.gap)) best = {frame: t, gap}; }
    return best?.frame ?? null;
  }
  showSnap() {
    const line = $('.edit-snap-line', this.root); if (!line) return;
    line.hidden = this.snapAt == null; if (this.snapAt != null) line.style.left = this.snapAt / this.fps * this.zoom + 'px';
  }
  scrubTo(event) {
    let frame = Math.round((event.clientX - $('.edit-canvas', this.root).getBoundingClientRect().left) / this.zoom * this.fps);
    if (this.snap && !event.altKey) { const hit = this.nearest(frame, [...this.beatFrames().map(b => b.frame), ...this.cuts(), ...this.markers.map(m => m.frame)]); this.snapAt = hit; if (hit != null) frame = hit; this.showSnap(); }
    this.seek(frame);
  }
  async click(event) {
    if (performance.now() < (this.suppressClickUntil || 0) && event.target.closest('.edit-canvas')) return;
    const markerEl = event.target.closest('[data-marker]');
    if (markerEl) { const m = this.markers.find(x => x.id === markerEl.dataset.marker); if (m) { this.pause(); this.markerSel = m.id; this.seek(m.frame); this.draw(); } return; }
    const titleEl = event.target.closest('[data-title]');
    if (titleEl) { const tt = this.titles.find(x => x.id === titleEl.dataset.title); if (tt) { const again = this.tap('title:' + tt.id) === 2; this.pause(); this.titleSel = tt.id; this.markerSel = null; this.seek(tt.start_frame); this.draw(); if (again) this.titlesDialog(tt.id); } return; }
    if (event.target.closest('[data-range-handle]')) return;
    const action = event.target.closest('[data-edit]')?.dataset.edit;
    const c = this.clip;
    if (action === 'focus') { const panel = $('.take-editor', this.root); const focus = !panel.classList.contains('focused'); panel.classList.toggle('focused', focus); document.body.classList.toggle('timeline-focus', focus); $('[data-edit="focus"]', this.root).textContent = focus ? 'Exit expanded view' : 'Expand editor'; return; }
    if (action === 'inspector') { const visible = getComputedStyle($('.edit-inspector', this.root)).display !== 'none'; const panel = $('.take-editor', this.root); panel.classList.toggle('inspector-hidden', visible); panel.classList.toggle('inspector-open', !visible); return; }
    if (action === 'play') return this.togglePlay();
    if (action === 'start') return this.seek(0);
    if (action === 'save') return this.flush();
    if (action === 'undo' || action === 'redo') return this.restore(action === 'redo');
    if (action === 'delete') return this.remove();
    if (action === 'split' && c) return this.split();
    if (action === 'trim-start' || action === 'trim-end') return this.trimTo(action === 'trim-start');
    if (action === 'step-back' || action === 'step-fwd') { this.step(action === 'step-fwd' ? 1 : -1); return; }
    if (action === 'prev-cut' || action === 'next-cut') return this.jumpCut(action === 'next-cut' ? 1 : -1);
    if (action?.startsWith('tool-')) { this.tool = action.slice(5); this.draw(); return; }
    if (action === 'title-add') return this.addTitle();
    if (action === 'titles') return this.titlesDialog();
    if (action === 'marker-add') return this.addMarker();
    if (action === 'markers') return this.markersDialog();
    if (action === 'range-in') return this.setRange('in');
    if (action === 'range-out') return this.setRange('out');
    if (action === 'range-clear') return this.clearRange();
    if (action === 'export-range') return this.renderRange();
    if (action === 'beats') return this.toggleBeats();
    if (action === 'beat-half' || action === 'beat-double') return this.setBeatMode(action.slice(5));
    if (action === 'beat-prev' || action === 'beat-next') return this.jumpBeat(action === 'beat-next' ? 1 : -1);
    if (action === 'beat-cut') return this.beatDialog();
    if (action === 'snap') { this.snap = !this.snap; store.set('rw-snap', this.snap ? '1' : '0'); this.draw(); return; }
    if (action === 'zoom-in') return this.setZoom(this.zoom * 1.4);
    if (action === 'zoom-out') return this.setZoom(this.zoom / 1.4);
    if (action === 'zoom-fit') return this.fit();
    if (action === 'track-video-mute') return this.changeEdit(() => { const muted = !this.edit.clips.every(c => c.muted); this.edit.clips.forEach(c => { c.muted = muted; }); });
    if (action === 'track-music-mute' && this.edit.music) return this.changeEdit(() => { this.edit.music.muted = !this.edit.music.muted; });
    if (action === 'lane-mute' || action === 'lane-remove') {
      const id = event.target.closest('[data-lane]')?.dataset.lane, lane = (this.edit.audio || []).find(x => x.id === id); if (!lane) return;
      return this.changeEdit(() => {
        if (action === 'lane-mute') lane.muted = !lane.muted;
        else { this.edit.audio = this.edit.audio.filter(x => x !== lane); this.edit.audio.forEach((x, i) => { x.lane = i; }); }
      });
    }
    if (action === 'duplicate' && c) return this.duplicate();
    if ((action === 'earlier' || action === 'later') && c) return this.moveClip(action === 'earlier' ? -1 : 1);
    if (action === 'mute-all') return this.changeEdit(() => { const muted = !this.edit.clips.every(c => c.muted); this.edit.clips.forEach(c => { c.muted = muted; }); });
    if (action === 'shot-lengths') return this.changeEdit(() => this.edit.clips.forEach(c => { const sh = this.ctx.e.shots.find(sh => sh.id === c.shot_id); if (sh) c.frames = Math.min(c.frames, Math.max(1, Math.round(sh.duration_s * this.fps))); }));
    if (action === 'remove-music') return this.changeEdit(() => { this.edit.music = null; this.edit.clips.forEach(c => { c.lipsync = false; }); });
    if (action === 'sync') return this.syncDialog();
    if (action === 'generate' && c?.shot_id) { await this.flush(); return this.generate(c.shot_id); }
    if (action === 'export') { this.pause(); await this.flush(); return startJob(this.api + '/export', {}); }
    if (action === 'reload' || action === 'picked') {
      if (!await ask(action === 'picked' ? 'Replace the edit with the currently picked takes? Your source files are retained.' : 'Reload the saved timeline and discard unsaved edits?', 'Continue')) return;
      const data = await GET(this.api + (action === 'picked' ? '?selected_takes=true' : ''));
      if (action === 'reload') { this.pause(); this.edit = data.edit; this.sources = data.sources; this.dirty = false; App.dirty = false; this.selected = this.edit.clips[0]?.id; this.draw(); this.seek(0); }
      else this.changeEdit(() => { const revision = this.edit.revision; this.edit = data.edit; this.edit.revision = revision; Object.assign(this.sources, data.sources); this.selected = this.edit.clips[0]?.id; this.frame = 0; });
      return;
    }
    const button = event.target.closest('[data-clip]');
    const again = button && !event.shiftKey && !event.ctrlKey && !event.metaKey && this.tap('clip:' + button.dataset.clip) === 2;
    if (button) { this.pick(button.dataset.clip, event); this.draw(); }
    if (event.target.closest('.edit-canvas') && !event.target.closest('[data-edge], [data-grip]')) {
      const x = event.clientX - $('.edit-canvas', this.root).getBoundingClientRect().left;
      this.seek(Math.round(x / this.zoom * this.fps));
    }
    if (again) { this.pause(); this.seek(this.start(button.dataset.clip)); this.ensureVisible(); }
  }
  async change(event) {
    const t = event.target, c = this.clip, m = this.edit.music;
    if (t.matches('[data-lane-field]')) return this.laneChange(t);
    if (t.matches('[data-edit-lane]')) return this.uploadLane(t);
    if (t.matches('[data-edit-model]')) { $('#take-model').value = t.value; return; }
    if (t.matches('[data-edit-audio]')) return this.uploadMusic(t);
    if (t.matches('[data-edit-take]') && c) {
      const sh = this.ctx.e.shots.find(sh => sh.id === c.shot_id);
      return this.addTake(sh.id, sh.takes.items.findIndex(take => take.file === t.value), true);
    }
    if (t.matches('[data-edit-in], [data-edit-out]') && c) {
      const begin = Math.round(Number($('[data-edit-in]', this.root).value) * this.fps), end = Math.round(Number($('[data-edit-out]', this.root).value) * this.fps);
      if (!Number.isFinite(begin + end) || begin < 0 || end <= begin || end > this.sourceFrames(c)) { this.inspector(); return toast('Choose a trim within the source video.', true); }
      return this.changeEdit(() => { c.source_in = begin / this.fps; c.frames = Math.max(1, Math.round((end - begin) / this.speed(c))); });
    }
    if (t.matches('[data-edit-speed]') && c) {
      const speed = Number(t.value);
      if (!Number.isFinite(speed) || speed < .25 || speed > 4) { this.inspector(); return toast('Speed must be between 0.25 and 4.', true); }
      const picked = this.selectedClips().filter(x => !x.lipsync);
      return this.changeEdit(() => { for (const x of picked) {
        const used = this.usedFrames(x);  // the clip keeps its source range, so it gets shorter / longer on the timeline
        x.speed = speed;
        x.frames = Math.max(1, Math.min(this.fitFrames(x, this.sourceFrames(x) - Math.round(x.source_in * this.fps)), Math.round(used / speed)));
      } });
    }
    if (t.matches('[data-edit-xfade]') && c) {
      const frames = Math.round(Number(t.value) * this.fps);
      if (!Number.isFinite(frames) || frames < 0) return this.inspector();
      return this.changeEdit(() => { c.transition = frames; });  // clampTransitions limits it to what the neighbours allow
    }
    if (t.matches('[data-edit-muted]') && c) return this.changeEdit(() => { for (const x of this.selectedClips()) x.muted = t.checked; });
    if (t.matches('[data-edit-fade]') && c) {
      if (t.value.trim() === '' || !Number.isFinite(Number(t.value))) return this.inspector();
      const frames = Math.max(0, Math.round(Number(t.value) * this.fps)), side = t.dataset.editFade;
      return this.changeEdit(() => { for (const x of this.selectedClips()) {
        if (side === 'in') { x.fade_in = Math.min(frames, x.frames); x.fade_out = Math.min(x.fade_out || 0, x.frames - x.fade_in); }
        else { x.fade_out = Math.min(frames, x.frames); x.fade_in = Math.min(x.fade_in || 0, x.frames - x.fade_out); }
      } });
    }
    if (t.matches('[data-edit-volume]') && c) return this.changeEdit(() => { for (const x of this.selectedClips()) x.volume = +t.value; });
    if (t.matches('[data-edit-lipsync]') && c) {
      if (t.checked && !m) { t.checked = false; return toast('Add music or a voice recording first.', true); }
      return this.changeEdit(() => { for (const x of this.selectedClips()) { x.lipsync = t.checked && this.eligible(x); if (x.lipsync) x.muted = true; } });
    }
    if (m && t.matches('[data-music-in], [data-music-start], [data-music-length]')) {
      const begin = Number($('[data-music-in]', this.root).value), start = Math.round(Number($('[data-music-start]', this.root).value) * this.fps), frames = Math.round(Number($('[data-music-length]', this.root).value) * this.fps);
      const available = Math.round(((this.sources[m.file]?.duration || 0) - begin) * this.fps);
      if (![begin, start, frames].every(Number.isFinite) || begin < 0 || start < 0 || frames < 1 || available < 1 || !m.loop && frames > available) { this.inspector(); return toast('Choose music timing within the recording, or enable looping.', true); }
      return this.changeEdit(() => Object.assign(m, {source_in: Math.round(begin * this.fps) / this.fps, start_frame: start, frames}));
    }
    for (const [attribute, key] of [['muted', 'muted'], ['volume', 'volume'], ['loop', 'loop'], ['isolate', 'isolate_vocals']]) {
      if (m && t.matches(`[data-music-${attribute}]`)) return this.changeEdit(() => { m[key] = attribute === 'volume' ? +t.value : t.checked; if (attribute === 'loop' && !m.loop) m.frames = Math.min(m.frames, Math.round(((this.sources[m.file]?.duration || 0) - m.source_in) * this.fps)); });
    }
  }
  async uploadMusic(input) {
    const file = input.files[0]; if (!file) return;
    App.interactionBusy = true;
    try {
      const fd = new FormData(); fd.append('file', file); fd.append('volume', '1'); fd.append('duck', 'false');
      const saved = await FORM(this.ctx.base + '/music', fd);
      if (this.destroyed) return;
      const info = await GET(this.api + '/source?audio=true&file=' + encodeURIComponent(saved.file));
      if (this.destroyed) return;
      this.sources[saved.file] = info;
      this.changeEdit(() => {
        this.edit.music = {file: saved.file, source_in: 0, start_frame: 0, frames: Math.round(info.duration * this.fps), volume: 1, muted: false, loop: false, isolate_vocals: true};
        this.edit.clips.forEach(c => { c.lipsync = false; });
      });
      this.loadWaveform();
      const choice = ask('Music added. Choose which timeline clips should lip-sync to its vocals?', 'Choose clips'); $('#ask-no').textContent = 'Not now';
      if (await choice) this.syncDialog();
    } finally { input.value = ''; App.interactionBusy = false; }
  }
  syncDialog() {
    if (!this.edit.music) return toast('Add a music or voice track first.', true);
    const clips = this.edit.clips.filter(c => this.eligible(c));
    if (!clips.length) return toast('Add clips from single-character shots first.', true);
    modal(`<h2>Lip-sync timeline clips</h2><p>Checked clips use their exact timeline position in the song. Their take audio will be muted. Your full music mix stays in the export.</p>
      <div style="max-height:40vh;overflow:auto">${clips.map(c => { const sh = this.ctx.e.shots.find(sh => sh.id === c.shot_id); return `<label class="row"><input type="checkbox" data-sync-id="${esc(c.id)}" ${c.lipsync ? 'checked' : ''}>${esc(this.label(c))} · ${esc(charName(this.ctx, sh.characters[0].id))}</label>`; }).join('')}</div>
      <div class="row"><button id="edit-sync-all">Select all</button><button id="edit-sync-none">Clear</button><span class="grow"></span><button id="edit-sync-apply" class="primary">Apply to timeline</button></div>`);
    $('#edit-sync-all').onclick = () => $$('[data-sync-id]').forEach(t => { t.checked = true; });
    $('#edit-sync-none').onclick = () => $$('[data-sync-id]').forEach(t => { t.checked = false; });
    $('#edit-sync-apply').onclick = () => { const checked = new Set($$('[data-sync-id]:checked').map(t => t.dataset.syncId)); this.changeEdit(() => this.edit.clips.forEach(c => { c.lipsync = checked.has(c.id); if (c.lipsync) c.muted = true; })); closeModal(); };
  }
  async loadWaveform() {
    const file = this.edit.music?.file; if (!file || this.sources[file]?.missing) return;
    try { const data = await GET(this.api + '/waveform?file=' + encodeURIComponent(file)); if (!this.destroyed && this.edit.music?.file === file) { this.waveform = {...data, file}; this.tracks(); this.position(); } } catch { /* audio playback still works without a waveform */ }
  }
  dragStart(event) {
    const markerEl = event.target.closest('[data-marker]'), handle = event.target.closest('[data-range-handle]');
    if (markerEl || handle) {
      event.preventDefault(); this.pause(); App.interactionBusy = true;
      if (markerEl) this.markerSel = markerEl.dataset.marker;
      this.drag = {pointer: event.pointerId, x: event.clientX, before: editClone(this.edit), mode: markerEl ? 'marker' : `range-${handle.dataset.rangeHandle}`, id: markerEl?.dataset.marker, rightId: null};
      $('.edit-canvas', this.root).setPointerCapture(event.pointerId); $('.take-editor', this.root).focus({preventScroll: true}); return;
    }
    if (event.target.closest('.edit-ruler')) {
      event.preventDefault(); this.pause(); App.interactionBusy = true; this.scrubbing = {pointer: event.pointerId};
      $('.edit-canvas', this.root).setPointerCapture(event.pointerId); this.scrubTo(event); $('.take-editor', this.root).focus({preventScroll: true}); return;
    }
    const edge = event.target.closest('[data-edge]'), grip = event.target.closest('[data-grip]'), music = event.target.closest('[data-music-move]'), lane = event.target.closest('[data-lane-move]');
    const body = this.tool === 'slip' && !edge && !grip && !music && !lane ? event.target.closest('[data-clip]') : null;
    if (!edge && !grip && !music && !lane && !body) return;
    event.preventDefault(); this.pause(); App.interactionBusy = true;
    const button = event.target.closest('[data-clip]'); if (button) this.selected = button.dataset.clip;
    let mode = lane ? 'lane' : music ? 'music' : body ? 'slip' : edge?.dataset.edge || 'move', id = this.selected, rightId = null;
    if (this.tool === 'roll' && edge && button) {
      const i = this.edit.clips.findIndex(c => c.id === button.dataset.clip), left = edge.dataset.edge === 'right' ? i : i - 1;
      if (i >= 0 && left >= 0 && left + 1 < this.edit.clips.length) { mode = 'roll'; id = this.edit.clips[left].id; rightId = this.edit.clips[left + 1].id; }
    }
    this.drag = {pointer: event.pointerId, x: event.clientX, before: editClone(this.edit), mode, id, rightId, laneId: lane?.dataset.laneMove};
    $('.edit-canvas', this.root).setPointerCapture(event.pointerId);
  }
  dragMove(event) {
    if (this.scrubbing) { if (event.pointerId === this.scrubbing.pointer) this.scrubTo(event); return; }
    const d = this.drag; if (!d || event.pointerId !== d.pointer) return;
    let delta = Math.round((event.clientX - d.x) / this.zoom * this.fps);
    this.edit = editClone(d.before);
    const c = this.edit.clips.find(c => c.id === d.id);
    const snapping = this.snap && !event.altKey && !['move', 'slip'].includes(d.mode), targets = snapping ? this.snapTargets(d) : [];
    this.snapAt = null;
    if (d.mode === 'music') {
      let begin = Math.max(0, d.before.music.start_frame + delta);
      if (snapping) {
        const frames = d.before.music.frames, a = this.nearest(begin, targets), b = this.nearest(begin + frames, targets);
        if (a != null && (b == null || Math.abs(a - begin) <= Math.abs(b - begin - frames))) { begin = a; this.snapAt = a; }
        else if (b != null) { begin = b - frames; this.snapAt = b; }
      }
      this.edit.music.start_frame = Math.max(0, begin);
    }
    else if (d.mode === 'lane') {
      const t = d.before.audio.find(x => x.id === d.laneId), now = this.edit.audio.find(x => x.id === d.laneId);
      let begin = Math.max(0, t.start_frame + delta);
      if (snapping) {
        const a = this.nearest(begin, targets), b = this.nearest(begin + t.frames, targets);
        if (a != null && (b == null || Math.abs(a - begin) <= Math.abs(b - begin - t.frames))) { begin = a; this.snapAt = a; }
        else if (b != null) { begin = b - t.frames; this.snapAt = b; }
      }
      now.start_frame = Math.max(0, begin);
    }
    else if (d.mode === 'left') { const origin = this.start(c.id), hit = snapping ? this.nearest(origin + delta, targets) : null; if (hit != null) { delta = hit - origin; this.snapAt = hit; } const offset = Math.max(-this.fitFrames(c, Math.round(c.source_in * this.fps)), Math.min(c.frames - 1, delta)); c.source_in = Math.max(0, c.source_in + offset / this.fps * this.speed(c)); c.frames -= offset; }
    else if (d.mode === 'right') { const end = this.start(c.id) + c.frames, hit = snapping ? this.nearest(end + delta, targets) : null; if (hit != null) { delta = hit - end; this.snapAt = hit; } c.frames = Math.max(1, Math.min(this.fitFrames(c, this.sourceFrames(c) - Math.round(c.source_in * this.fps)), c.frames + delta)); }
    else if (d.mode === 'marker') {
      const marker = this.edit.markers.find(x => x.id === d.id), want = Math.max(0, d.before.markers.find(x => x.id === d.id).frame + delta);
      const hit = snapping ? this.nearest(want, targets) : null;
      marker.frame = hit ?? want; this.snapAt = hit;
    }
    else if (d.mode === 'range-in' || d.mode === 'range-out') {
      const r = this.edit.range, starting = d.mode === 'range-in', want = (starting ? r.start : r.end) + delta;
      const hit = snapping ? this.nearest(want, targets) : null, f = hit ?? want;
      if (starting) r.start = Math.max(0, Math.min(r.end - 1, f)); else r.end = Math.max(r.start + 1, Math.min(Math.max(this.total, r.start + 1), f));
      this.snapAt = hit != null && (starting ? r.start : r.end) === hit ? hit : null;
    }
    else if (d.mode === 'slip') {
      const base = Math.round(d.before.clips.find(x => x.id === d.id).source_in * this.fps), room = Math.max(0, this.sourceFrames(c) - this.usedFrames(c));
      c.source_in = Math.max(0, Math.min(room, base - Math.round(delta * this.speed(c)))) / this.fps;
    }
    else if (d.mode === 'roll') {
      const right = this.edit.clips.find(x => x.id === d.rightId), fps = this.fps, cut = this.start(c.id) + c.frames, hit = snapping ? this.nearest(cut + delta, targets) : null;
      if (hit != null) { delta = hit - cut; this.snapAt = hit; }
      const low = Math.max(1 - c.frames, -this.fitFrames(right, Math.round(right.source_in * fps))), high = Math.min(right.frames - 1, this.fitFrames(c, this.sourceFrames(c) - Math.round(c.source_in * fps)) - c.frames);
      const shift = Math.max(low, Math.min(high, delta)); if (shift !== delta) this.snapAt = null;
      c.frames += shift; right.source_in = (Math.round(right.source_in * fps) + Math.round(shift * this.speed(right))) / fps; right.frames -= shift;
    }
    else {
      const center = this.start(c.id) + c.frames / 2 + delta; let nearest = 0, distance = Infinity;
      const where = clipStarts(this.edit.clips).starts;
      this.edit.clips.forEach((clip, i) => { const gap = Math.abs(center - where[i] - clip.frames / 2); if (gap < distance) { distance = gap; nearest = i; } });
      this.edit.clips.splice(this.edit.clips.indexOf(c), 1); this.edit.clips.splice(nearest, 0, c);
    }
    this.tracks(); this.position(); this.showSnap();
  }
  dragEnd(event, cancelled = false) {
    if (this.scrubbing) { this.scrubbing = null; App.interactionBusy = false; this.snapAt = null; this.showSnap(); this.suppressClickUntil = performance.now() + 200; return; }
    const d = this.drag; if (!d) return; this.drag = null; App.interactionBusy = false; this.snapAt = null; this.showSnap();
    if (cancelled) { this.edit = d.before; this.draw(); this.refocus(); return; }
    this.suppressClickUntil = Math.abs(event.clientX - d.x) > 3 ? performance.now() + 200 : 0;
    if (d.mode === 'marker' && Math.abs(event.clientX - d.x) <= 3) {  // pointer capture sends the click to the canvas, so a plain press on a flag is handled here
      const m = this.markers.find(x => x.id === d.id); this.suppressClickUntil = performance.now() + 200;
      const now = performance.now(), count = this.lastTap?.id === d.id && now - this.lastTap.at < 450 ? this.lastTap.count + 1 : 1;
      this.lastTap = {id: d.id, at: now, count};  // like a native double-click: exactly two quick taps
      if (m) { this.markerSel = m.id; this.titleSel = null; this.seek(m.frame); this.draw(); if (count === 2) { this.markersDialog(m.id); return; } }
      this.refocus(); return;
    }
    if (JSON.stringify(d.before) !== JSON.stringify(this.edit)) { this.history.push(d.before); this.future = []; this.changed(); }
    else if (d.mode !== 'marker' && !d.mode.startsWith('range-')) this.draw();  // an untouched flag or handle must keep its element, or the click is lost
    this.refocus();
  }
  destroy() {
    this.pause(); this.destroyed = true; clearTimeout(this.saveTimer); cancelAnimationFrame(this.thumbRaf); this.monitorObserver?.disconnect();
    document.body.classList.remove('timeline-focus');
    this.video.removeAttribute('src'); this.audio.removeAttribute('src'); this.video.load(); this.audio.load();
    this.laneEls.forEach(el => { el.pause(); el.removeAttribute('src'); }); this.laneEls.clear();
  }
}

async function mountTakeTimeline(root, ctx, generate) {
  const data = await GET(ctx.base + '/timeline');
  const editor = new TakeTimelineEditor(root, ctx, data, generate);
  App.timelineEditor = editor;
  return editor;
}

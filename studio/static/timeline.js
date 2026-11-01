// Takes editor: the saved edit is independent of its source takes.
"use strict";
const editClone = value => JSON.parse(JSON.stringify(value));
const editId = () => `clip_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 9)}`;

class TakeTimelineEditor {
  constructor(root, ctx, data, generate) {
    this.root = root; this.ctx = ctx; this.generate = generate;
    this.edit = data.edit; this.sources = data.sources; this.selected = this.edit.clips[0]?.id;
    this.frame = 0; this.zoom = 48; this.history = []; this.future = []; this.serial = 0;
    this.dirty = false; this.playing = false; this.destroyed = false;
    this.api = ctx.base + '/timeline';
    root.innerHTML = `<section class="take-editor" aria-label="Video timeline editor" tabindex="0">
      <div class="edit-heading"><div><h2>Takes editor</h2><span class="muted small" data-edit-status>Timeline ready · source takes retained</span></div>
        <div class="row"><button data-edit="focus">Expand editor</button><button data-edit="inspector">Settings</button><button data-edit="undo" aria-label="Undo timeline edit">Undo</button><button data-edit="redo" aria-label="Redo timeline edit">Redo</button>
        <button data-edit="save">Save</button><button data-edit="export" class="primary">Render timeline</button></div></div>
      <div class="edit-stage"><div class="edit-monitor"><video data-edit-video playsinline preload="metadata"></video><span data-edit-empty>Add a take from the library below</span>
        <div class="edit-transport"><button data-edit="play" aria-label="Play timeline">▶</button><button data-edit="start" aria-label="Go to timeline start">↤</button>
        <output data-edit-time>00:00.000</output><span class="grow"></span><span class="small">Preview</span></div>
        <input data-edit-scrub type="range" min="0" step="1" value="0" aria-label="Timeline playhead"></div>
        <aside class="edit-inspector" data-edit-inspector></aside></div>
      <div class="edit-tools"><button data-edit="split">Split at playhead</button><button data-edit="duplicate">Duplicate clip</button>
        <button data-edit="earlier" aria-label="Move clip earlier">← Earlier</button><button data-edit="later" aria-label="Move clip later">Later →</button>
        <button data-edit="delete" class="danger">Delete clip</button><span class="grow"></span>
        <label class="row small">Zoom<input data-edit-zoom type="range" min="16" max="160" step="4" value="48" aria-label="Timeline zoom"></label></div>
      <div class="edit-tracks"><div class="edit-labels"><div>TIME</div><div>VIDEO</div><div>MUSIC</div></div>
        <div class="edit-scroll"><div class="edit-canvas"><div class="edit-ruler"></div><div class="edit-video-track"></div><div class="edit-music-track"></div>
          <div class="edit-playhead" aria-hidden="true"></div></div></div></div>
      <div class="edit-footer row"><label class="btn file-pick">Add music / voice<input data-edit-audio type="file" accept="audio/*,.mp3,.wav,.m4a,.flac,.ogg,.aac" aria-label="Add timeline music"></label>
        <button data-edit="mute-all">Mute all takes</button><button data-edit="shot-lengths">Use shot lengths</button><button data-edit="sync">Choose lip-sync clips</button>
        <span class="grow"></span><button data-edit="reload">Reload saved edit</button><button data-edit="picked">Use picked takes</button></div>
      <p class="muted small edit-help">Drag a clip’s grip to reorder, or its edges to trim. Split and delete ripple the video track. Edits autosave; Undo restores edits in this session. Add or replace clips from the take library below.</p>
      <div class="edit-results"></div>
      <audio data-edit-music preload="metadata"></audio></section>`;
    this.video = $('[data-edit-video]', root); this.audio = $('[data-edit-music]', root);
    this.video.onloadedmetadata = () => this.syncMedia(true);
    this.audio.onloadedmetadata = () => this.syncMedia(true);
    root.onclick = event => { event.stopPropagation(); this.click(event).catch(error => this.error(error)); };
    root.onchange = event => { event.stopPropagation(); this.change(event).catch(error => this.error(error)); };
    $('[data-edit-scrub]', root).oninput = event => this.seek(+event.target.value);
    $('[data-edit-zoom]', root).oninput = event => { this.zoom = +event.target.value; this.tracks(); this.position(); };
    const canvas = $('.edit-canvas', root);
    canvas.onpointerdown = event => this.dragStart(event);
    canvas.onpointermove = event => this.dragMove(event);
    canvas.onpointerup = event => this.dragEnd(event);
    canvas.onpointercancel = event => this.dragEnd(event, true);
    root.onkeydown = event => {
      if (event.key === 'Escape') { $('.take-editor', root).classList.remove('focused'); document.body.classList.remove('timeline-focus'); $('[data-edit="focus"]', root).textContent = 'Expand editor'; }
      if (/INPUT|SELECT|TEXTAREA/.test(event.target.tagName)) return;
      if (event.code === 'Space') { event.preventDefault(); this.togglePlay(); }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') { event.preventDefault(); this.restore(event.shiftKey); }
      if (event.key === 'Delete' || event.key === 'Backspace') { event.preventDefault(); this.remove(); }
    };
    this.draw(); this.seek(0); this.loadWaveform();
    const versions = this.ctx.e.outputs?.timeline_versions || [];
    if (versions.length) $('.edit-results', root).innerHTML = `<details><summary>Rendered timelines (${versions.length})</summary>${versions.slice().reverse().map(v => `<div class="row"><span class="grow small">${esc(v.created)} · ${Number(v.runtime).toFixed(3)} s · edit ${v.revision}${v.lipsync ? ' · lip-sync' : ''}</span><a class="btn" href="${media(v.file)}" target="_blank" rel="noopener">Watch</a><a class="btn" href="${media(v.file)}" download>Download MP4</a></div>`).join('')}</details>`;
  }
  get fps() { return this.edit.fps; }
  get total() { return this.edit.clips.reduce((n, c) => n + c.frames, 0); }
  get clip() { return this.edit.clips.find(c => c.id === this.selected); }
  start(id) { let f = 0; for (const c of this.edit.clips) { if (c.id === id) return f; f += c.frames; } return 0; }
  seconds(frame) { return (frame / this.fps).toFixed(3); }
  label(c) { return c.shot_id || 'Video clip'; }
  sourceFrames(c) { return Math.round((this.sources[c.source]?.video_duration || c.source_in + c.frames / this.fps) * this.fps); }
  eligible(c) { return this.ctx.e.shots.find(sh => sh.id === c.shot_id)?.characters?.length === 1; }
  error(error) { $('[data-edit-status]', this.root).textContent = error.message || String(error); }
  changeEdit(fn) {
    this.pause(); const before = editClone(this.edit); fn();
    if (JSON.stringify(before) === JSON.stringify(this.edit)) return;
    this.history.push(before); if (this.history.length > 50) this.history.shift(); this.future = [];
    this.changed();
  }
  changed() {
    this.serial++; this.dirty = true; App.dirty = true;
    $('[data-edit-status]', this.root).textContent = 'Saving timeline…';
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
      if (this.root.isConnected) $('[data-edit-status]', this.root).textContent = this.dirty ? 'Saving timeline…' : `Saved · ${this.seconds(this.total)} s · revision ${this.edit.revision}`;
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
    const hasClip = !!this.clip;
    for (const action of ['delete', 'split', 'duplicate', 'earlier', 'later']) $(`[data-edit="${action}"]`, this.root).disabled = !hasClip;
    $('[data-edit="undo"]', this.root).disabled = !this.history.length;
    $('[data-edit="redo"]', this.root).disabled = !this.future.length;
    $('[data-edit="export"]', this.root).disabled = !this.total;
    $('[data-edit="play"]', this.root).disabled = !this.total;
    $('[data-edit-empty]', this.root).hidden = !!this.total;
    $('[data-edit="mute-all"]', this.root).textContent = this.edit.clips.length && this.edit.clips.every(c => c.muted) ? 'Unmute all takes' : 'Mute all takes';
  }
  tracks() {
    const music = this.edit.music;
    const end = Math.max(this.total, music ? music.start_frame + music.frames : 0, this.fps);
    const width = Math.max(560, end / this.fps * this.zoom + 30);
    const canvas = $('.edit-canvas', this.root); canvas.style.width = width + 'px';
    const ticks = []; const interval = end / this.fps > 120 ? 10 : end / this.fps > 45 ? 5 : 1;
    for (let t = 0; t <= end / this.fps; t += interval) ticks.push(`<span style="left:${t * this.zoom}px">${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}</span>`);
    $('.edit-ruler', this.root).innerHTML = ticks.join('');
    let position = 0;
    $('.edit-video-track', this.root).innerHTML = this.edit.clips.map(c => {
      const left = position / this.fps * this.zoom; position += c.frames;
      const sh = this.ctx.e.shots.find(sh => sh.id === c.shot_id);
      const poster = sh && selFrame(sh)?.file;
      return `<button type="button" class="edit-clip ${c.id === this.selected ? 'selected' : ''} ${c.muted ? 'muted' : ''}" data-clip="${esc(c.id)}" style="left:${left}px;width:${Math.max(3, c.frames / this.fps * this.zoom)}px" aria-label="Timeline clip ${esc(this.label(c))}">
        ${poster ? `<img src="${media(poster)}" alt="" draggable="false">` : ''}<span class="edit-clip-name">${esc(this.label(c))}</span>
        <span class="edit-clip-meta">${this.seconds(c.frames)}s ${c.muted ? '· muted' : ''} ${c.lipsync ? '· lip-sync' : ''}</span>
        <span class="edit-grip" data-grip="move" aria-label="Drag to reorder">⠿</span><span class="edit-edge left" data-edge="left"></span><span class="edit-edge right" data-edge="right"></span></button>`;
    }).join('');
    let waveform = '';
    if (music && this.waveform?.file === music.file) {
      const points = [], {peaks, duration} = this.waveform;
      const remaining = Math.max(.001, duration - music.source_in);
      for (let x = 0; x <= 400; x++) {
        let t = x / 400 * music.frames / this.fps;
        if (music.loop) t %= remaining;
        const index = Math.floor((music.source_in + t) / duration * peaks.length);
        points.push([x, (peaks[index] || 0) * 18]);
      }
      waveform = `<svg viewBox="0 0 400 40" preserveAspectRatio="none" aria-hidden="true"><polygon points="${points.map(([x, h]) => `${x},${20-h}`).join(' ')} ${points.reverse().map(([x, h]) => `${x},${20+h}`).join(' ')}"/></svg>`;
    }
    $('.edit-music-track', this.root).innerHTML = music ? `<button class="edit-music-clip ${music.muted ? 'muted' : ''}" data-music-move style="left:${music.start_frame / this.fps * this.zoom}px;width:${Math.max(8, music.frames / this.fps * this.zoom)}px" aria-label="Music timeline clip">${waveform}<span>${esc(music.file.split('/').pop())}${music.muted ? ' · muted' : ''}</span></button>` : '<span class="edit-track-empty">Add a song or voice recording</span>';
  }
  inspector() {
    const c = this.clip, m = this.edit.music;
    const sh = this.ctx.e.shots.find(sh => sh.id === c?.shot_id);
    const takes = sh?.takes.items || [];
    $('[data-edit-inspector]', this.root).innerHTML = `<h3>${c ? esc(this.label(c)) : 'Clip settings'}</h3>
      ${c ? `<label>Take<select data-edit-take aria-label="Timeline source take">${!takes.some(t => t.file === c.source) ? `<option value="${esc(c.source)}">Current rendered clip</option>` : ''}${takes.map((t, i) => `<option value="${esc(t.file)}" ${t.file === c.source ? 'selected' : ''}>Take ${i+1} · ${esc(t.model || t.engine || 'video')}</option>`).join('')}</select></label>
      <div class="edit-field-pair"><label>Source in (s)<input data-edit-in type="number" min="0" step="${1/this.fps}" value="${this.seconds(Math.round(c.source_in*this.fps))}" aria-label="Clip source in"></label>
      <label>Source out (s)<input data-edit-out type="number" min="0" step="${1/this.fps}" value="${this.seconds(Math.round(c.source_in*this.fps)+c.frames)}" aria-label="Clip source out"></label></div>
      <label class="row"><input data-edit-muted type="checkbox" ${c.muted ? 'checked' : ''}>Mute clip audio</label>
      <label>Clip volume<input data-edit-volume type="range" min="0" max="1" step=".05" value="${c.volume}" aria-label="Clip volume"></label>
      <label class="row"><input data-edit-lipsync type="checkbox" ${c.lipsync ? 'checked' : ''} ${this.eligible(c) ? '' : 'disabled'}>Lip-sync this clip to music</label>
      <label>Generate with<select data-edit-model aria-label="Timeline generation model">${$('#take-model')?.innerHTML || ''}</select></label>
      <button data-edit="generate" ${sh ? '' : 'disabled'}>Generate take for this shot</button>` : '<p class="muted small">Select a clip or add a take below.</p>'}
      <h3>Music / voice</h3>${m ? `<div class="edit-field-pair"><label>Source in (s)<input data-music-in type="number" min="0" step=".1" value="${m.source_in}" aria-label="Music source in"></label>
      <label>Timeline start (s)<input data-music-start type="number" min="0" step=".1" value="${this.seconds(m.start_frame)}" aria-label="Music timeline start"></label></div>
      <label>Length (s)<input data-music-length type="number" min="${1/this.fps}" step=".1" value="${this.seconds(m.frames)}" aria-label="Music duration"></label>
      <label class="row"><input data-music-muted type="checkbox" ${m.muted ? 'checked' : ''}>Mute music</label>
      <label>Music volume<input data-music-volume type="range" min="0" max="1" step=".05" value="${m.volume}" aria-label="Timeline music volume"></label>
      <label class="row"><input data-music-loop type="checkbox" ${m.loop ? 'checked' : ''}>Loop music</label>
      <label class="row"><input data-music-isolate type="checkbox" ${m.isolate_vocals ? 'checked' : ''}>Isolate vocals for lip-sync</label>
      <button data-edit="remove-music">Remove music track</button>` : '<p class="muted small">Add audio to preview, mix or lip-sync.</p>'}`;
    const model = $('[data-edit-model]', this.root); if (model) model.value = $('#take-model').value;
  }
  position() {
    $('[data-edit-time]', this.root).textContent = `${this.seconds(this.frame)} / ${this.seconds(this.total)} s`;
    const slider = $('[data-edit-scrub]', this.root); slider.max = Math.max(0, this.total - 1); slider.value = this.frame;
    $('.edit-playhead', this.root).style.left = this.frame / this.fps * this.zoom + 'px';
  }
  seek(frame) {
    this.frame = Math.max(0, Math.min(Math.round(frame), Math.max(0, this.total - 1)));
    if (this.playing) this.clock = performance.now() - this.frame / this.fps * 1000;
    this.position(); this.syncMedia(true);
  }
  syncMedia(force = false) {
    if (this.destroyed) return;
    let start = 0, clip;
    for (const c of this.edit.clips) { if (this.frame < start + c.frames) { clip = c; break; } start += c.frames; }
    if (!clip) { this.video.pause(); this.video.removeAttribute('src'); return; }
    const src = media(clip.source);
    if (this.video.dataset.source !== src) { this.video.dataset.source = src; this.video.src = src; }
    const target = clip.source_in + (this.frame - start) / this.fps;
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
      this.frame = frame; this.position(); this.syncMedia(); this.animation = requestAnimationFrame(tick);
    };
    this.syncMedia(true); this.animation = requestAnimationFrame(tick);
  }
  pause() {
    this.playing = false; App.interactionBusy = false; cancelAnimationFrame(this.animation);
    this.video.pause(); this.audio.pause();
    const button = $('[data-edit="play"]', this.root); if (button) button.textContent = '▶';
  }
  remove() {
    const index = this.edit.clips.findIndex(c => c.id === this.selected); if (index < 0) return;
    this.changeEdit(() => { this.edit.clips.splice(index, 1); this.selected = this.edit.clips[Math.min(index, this.edit.clips.length - 1)]?.id; });
  }
  async addTake(shotId, index, replace = false) {
    const sh = this.ctx.e.shots.find(sh => sh.id === shotId), take = sh?.takes.items[index]; if (!take) return;
    const info = await GET(this.api + '/source?file=' + encodeURIComponent(take.file)); this.sources[take.file] = info;
    const maximum = Math.round(info.video_duration * this.fps);
    this.changeEdit(() => {
      if (replace && this.clip) Object.assign(this.clip, {shot_id: shotId, source: take.file, source_in: 0, frames: Math.min(this.clip.frames, maximum), lipsync: false});
      else { const clip = {id: editId(), shot_id: shotId, source: take.file, source_in: 0, frames: maximum, muted: false, volume: 1, lipsync: false}; this.edit.clips.push(clip); this.selected = clip.id; this.frame = this.start(clip.id); }
    });
  }
  async click(event) {
    if (performance.now() < (this.suppressClickUntil || 0) && event.target.closest('.edit-canvas')) return;
    const action = event.target.closest('[data-edit]')?.dataset.edit;
    const c = this.clip;
    if (action === 'focus') { const panel = $('.take-editor', this.root); const focus = !panel.classList.contains('focused'); panel.classList.toggle('focused', focus); document.body.classList.toggle('timeline-focus', focus); $('[data-edit="focus"]', this.root).textContent = focus ? 'Exit expanded view' : 'Expand editor'; return; }
    if (action === 'inspector') { const visible = getComputedStyle($('.edit-inspector', this.root)).display !== 'none'; const panel = $('.take-editor', this.root); panel.classList.toggle('inspector-hidden', visible); panel.classList.toggle('inspector-open', !visible); return; }
    if (action === 'play') return this.togglePlay();
    if (action === 'start') return this.seek(0);
    if (action === 'save') return this.flush();
    if (action === 'undo' || action === 'redo') return this.restore(action === 'redo');
    if (action === 'delete') return this.remove();
    if (action === 'split' && c) {
      const offset = this.frame - this.start(c.id);
      if (offset <= 0 || offset >= c.frames) return toast('Place the playhead inside the selected clip.', true);
      return this.changeEdit(() => { const right = {...c, id: editId(), source_in: c.source_in + offset / this.fps, frames: c.frames - offset}; c.frames = offset; this.edit.clips.splice(this.edit.clips.indexOf(c) + 1, 0, right); this.selected = right.id; });
    }
    if (action === 'duplicate' && c) return this.changeEdit(() => { const duplicate = {...c, id: editId()}; this.edit.clips.splice(this.edit.clips.indexOf(c) + 1, 0, duplicate); this.selected = duplicate.id; });
    if ((action === 'earlier' || action === 'later') && c) return this.changeEdit(() => { const i = this.edit.clips.indexOf(c), j = Math.max(0, Math.min(this.edit.clips.length - 1, i + (action === 'earlier' ? -1 : 1))); this.edit.clips.splice(i, 1); this.edit.clips.splice(j, 0, c); this.frame = this.start(c.id); });
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
    if (button) { this.selected = button.dataset.clip; this.draw(); }
    if (event.target.closest('.edit-canvas') && !event.target.closest('[data-edge], [data-grip]')) {
      const x = event.clientX - $('.edit-canvas', this.root).getBoundingClientRect().left;
      this.seek(Math.round(x / this.zoom * this.fps));
    }
  }
  async change(event) {
    const t = event.target, c = this.clip, m = this.edit.music;
    if (t.matches('[data-edit-model]')) { $('#take-model').value = t.value; return; }
    if (t.matches('[data-edit-audio]')) return this.uploadMusic(t);
    if (t.matches('[data-edit-take]') && c) {
      const sh = this.ctx.e.shots.find(sh => sh.id === c.shot_id);
      return this.addTake(sh.id, sh.takes.items.findIndex(take => take.file === t.value), true);
    }
    if (t.matches('[data-edit-in], [data-edit-out]') && c) {
      const begin = Math.round(Number($('[data-edit-in]', this.root).value) * this.fps), end = Math.round(Number($('[data-edit-out]', this.root).value) * this.fps);
      if (!Number.isFinite(begin + end) || begin < 0 || end <= begin || end > this.sourceFrames(c)) { this.inspector(); return toast('Choose a trim within the source video.', true); }
      return this.changeEdit(() => { c.source_in = begin / this.fps; c.frames = end - begin; });
    }
    if (t.matches('[data-edit-muted]') && c) return this.changeEdit(() => { c.muted = t.checked; });
    if (t.matches('[data-edit-volume]') && c) return this.changeEdit(() => { c.volume = +t.value; });
    if (t.matches('[data-edit-lipsync]') && c) {
      if (t.checked && !m) { t.checked = false; return toast('Add music or a voice recording first.', true); }
      return this.changeEdit(() => { c.lipsync = t.checked; if (t.checked) c.muted = true; });
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
    const edge = event.target.closest('[data-edge]'), grip = event.target.closest('[data-grip]'), music = event.target.closest('[data-music-move]');
    if (!edge && !grip && !music) return;
    event.preventDefault(); this.pause(); App.interactionBusy = true;
    const button = event.target.closest('[data-clip]'); if (button) this.selected = button.dataset.clip;
    this.drag = {pointer: event.pointerId, x: event.clientX, before: editClone(this.edit), mode: music ? 'music' : edge?.dataset.edge || 'move', id: this.selected};
    $('.edit-canvas', this.root).setPointerCapture(event.pointerId);
  }
  dragMove(event) {
    const d = this.drag; if (!d || event.pointerId !== d.pointer) return;
    const delta = Math.round((event.clientX - d.x) / this.zoom * this.fps);
    this.edit = editClone(d.before);
    const c = this.edit.clips.find(c => c.id === d.id);
    if (d.mode === 'music') this.edit.music.start_frame = Math.max(0, d.before.music.start_frame + delta);
    else if (d.mode === 'left') { const offset = Math.max(-Math.round(c.source_in * this.fps), Math.min(c.frames - 1, delta)); c.source_in += offset / this.fps; c.frames -= offset; }
    else if (d.mode === 'right') c.frames = Math.max(1, Math.min(this.sourceFrames(c) - Math.round(c.source_in * this.fps), c.frames + delta));
    else {
      const center = this.start(c.id) + c.frames / 2 + delta; let position = 0, nearest = 0, distance = Infinity;
      this.edit.clips.forEach((clip, i) => { const gap = Math.abs(center - position - clip.frames / 2); if (gap < distance) { distance = gap; nearest = i; } position += clip.frames; });
      this.edit.clips.splice(this.edit.clips.indexOf(c), 1); this.edit.clips.splice(nearest, 0, c);
    }
    this.tracks(); this.position();
  }
  dragEnd(event, cancelled = false) {
    const d = this.drag; if (!d) return; this.drag = null; App.interactionBusy = false;
    if (cancelled) { this.edit = d.before; this.draw(); return; }
    this.suppressClickUntil = Math.abs(event.clientX - d.x) > 3 ? performance.now() + 200 : 0;
    if (JSON.stringify(d.before) !== JSON.stringify(this.edit)) { this.history.push(d.before); this.future = []; this.changed(); }
    else this.draw();
  }
  destroy() {
    this.pause(); this.destroyed = true; clearTimeout(this.saveTimer);
    document.body.classList.remove('timeline-focus');
    this.video.removeAttribute('src'); this.audio.removeAttribute('src'); this.video.load(); this.audio.load();
  }
}

async function mountTakeTimeline(root, ctx, generate) {
  const data = await GET(ctx.base + '/timeline');
  const editor = new TakeTimelineEditor(root, ctx, data, generate);
  App.timelineEditor = editor;
  return editor;
}

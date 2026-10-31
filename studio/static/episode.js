// Reelwright - episode views: story, shots, frames, animatic, takes, cut.
"use strict";

const TABS = [["story", "1. Story", null], ["shots", "2. Shot list", "shots"], ["frames", "3. First frames", "frames"],
  ["animatic", "4. Animatic", "animatic"], ["takes", "5. Takes", "takes"], ["cut", "6. Cut", null]];
const FRAMINGS = ["extreme wide", "wide", "full", "medium wide", "medium", "medium close-up", "close-up", "extreme close-up", "over-the-shoulder", "two-shot", "insert"];

async function viewEpisode(view, sid, eid, tab) {
  const [d, e] = await Promise.all([getSeries(sid), GET(`/api/s/${sid}/e/${eid}`)]);
  const ctx = { sid, eid, d, e, base: `/api/s/${sid}/e/${eid}`, chars: d.bible.characters, locs: d.bible.locations };
  crumbs([[d.series.name, `#/s/${sid}`], [e.title, null]]);
  const g = e.gates || {};
  const done = { story: e.shots.length > 0 || (e.scenes || []).length > 0, shots: !!g.shots, frames: !!g.frames, animatic: !!g.animatic, takes: !!g.takes, cut: !!(e.outputs && e.outputs.final) };
  const nDone = Object.values(done).filter(Boolean).length;
  view.innerHTML = `
    <div class="page-head"><div class="grow"><h1>${esc(e.title)}</h1>
      <div class="row sub"><span class="chip">${e.shots.length} shots</span><span class="chip">runtime ${fmtSec(e._runtime)}</span><span class="chip">est. render ${fmtSec(e._estimate_s)} at ${e._settings.takes_per_shot} takes/shot</span><span class="chip ${nDone === 6 ? "good" : "accent"}">${nDone}/6 stages done</span></div></div></div>
    <div class="tabs">${TABS.map(([k, label, gate], i) => `<a href="#/s/${sid}/e/${eid}/${k}" class="${k === tab ? "on" : ""} ${done[k] ? "done" : ""}"><span class="n">${done[k] ? ic("check") : i + 1}</span><span>${label.replace(/^\d+\.\s*/, "")}</span>${gate && g[gate] ? `<span class="gate chip good">approved</span>` : ""}</a>`).join("")}</div>
    <div class="progress"><div style="width:${Math.round((nDone / 6) * 100)}%"></div></div>
    <div id="tab"></div>`;
  const deleteEpisode = document.createElement("button"); deleteEpisode.textContent = "Delete episode"; deleteEpisode.className = "danger";
  $(".page-head", view).append(deleteEpisode);
  deleteEpisode.onclick = async () => {
    if (await ask(`Delete episode ${e.title}? Files are kept in a .deleted folder.`, "Delete", true)) {
      await api("DELETE", ctx.base); location.hash = `#/s/${sid}`;
    }
  };
  const el = $("#tab");
  const fn = { story: tabStory, shots: tabShots, frames: tabFrames, animatic: tabAnimatic, takes: tabTakes, cut: tabCut }[tab] || tabStory;
  await fn(el, ctx);
  if (tab === "frames" || tab === "takes") {
    bulkSelect(el, "[data-rm]", async targets => {
      const items = {};
      for (const t of targets) (items[t.dataset.shot] ||= []).push(+t.dataset.rm);
      await POST(`${ctx.base}/delete_assets`, {kind: tab === "frames" ? "frame" : "take", items});
    }, reload, tab === "frames" ? "frames" : "takes");
  }
}

const reload = () => route();
async function setGate(ctx, gate, value) { await POST(`${ctx.base}/gate`, { gate, value }); toast(value ? "Approved." : "Gate reopened."); reload(); }
const charName = (ctx, id) => ctx.chars[id]?.name || id;
const selFrame = (sh) => (sh.frame.selected != null ? sh.frame.candidates[sh.frame.selected] : null);
const gateBtn = (ctx, gate, label) => ctx.e.gates[gate]
  ? `<button data-gate="${gate}" data-val="0">Reopen ${label}</button>`
  : `<button class="primary" data-gate="${gate}" data-val="1">Approve ${label}</button>`;

// ── 1. Story ────────────────────────────────────────────────────────────
async function tabStory(el, ctx) {
  const { e } = ctx;
  const nChars = Object.keys(ctx.chars).length, nLocs = Object.keys(ctx.locs).length;
  el.innerHTML = `
    <div class="card">
      <label>Title</label><input id="title" value="${esc(e.title)}" style="width:100%">
      <label>Story - a script (INT./EXT. headings, NAME: dialogue) or plain prose</label>
      <textarea id="story" style="min-height:360px">${esc(e.story)}</textarea>
      <div class="row" style="margin-top:10px">
        <button id="save">Save</button>
        <button id="draft">Draft bible from story</button>
        <button class="primary" id="plan">${e.shots.length ? "Re-plan all shots" : "Plan scenes and shots"}</button>
        <span class="muted small grow">Bible: ${nChars} characters, ${nLocs} locations. The planner may only use bible ids.</span>
      </div>
    </div>
    ${!nLocs ? `<div class="notice">The bible has no locations yet. Use "Draft bible from story", review the drafts, then plan.</div>` : ""}
    ${e.scenes.length ? `<h2>Scenes</h2>${e.scenes.map((s) => `<div class="card"><b>${esc(s.id)}</b> · ${esc(ctx.locs[s.location_id]?.name || s.location_id)} ${esc(s.time_of_day || "")}
      <div class="muted">${esc(s.summary)}</div><div class="small muted">${(s.characters || []).map((c) => esc(charName(ctx, c.id) + (c.outfit ? ` (${c.outfit})` : ""))).join(", ")}</div></div>`).join("")}` : ""}`;
  const save = () => PUT(`${ctx.base}/story`, { title: $("#title").value, story: $("#story").value });
  $("#save").onclick = async () => { await save(); App.dirty = false; toast("Saved."); };
  $("#draft").onclick = async () => { await save(); App.dirty = false; await startJob(`${ctx.base}/draft_bible`); };
  $("#plan").onclick = async () => {
    if (e.shots.length && !(await ask("Replace the whole shot list? Existing frames and takes stay on disk but are detached.", "Replace", true))) return;
    await save(); App.dirty = false; await startJob(`${ctx.base}/plan`);
  };
  App.refreshers.push(reload);
}

// ── 2. Shot list ────────────────────────────────────────────────────────
function charsToText(list) { return (list || []).map((c) => [c.id, c.position, c.outfit].filter(Boolean).join(":")).join(", "); }
function textToChars(t) {
  return t.split(",").map((x) => x.trim()).filter(Boolean).map((x) => { const [id, position, outfit] = x.split(":").map((y) => (y || "").trim()); return { id, position: position || "", outfit: outfit || "" }; });
}
function dlgToText(list) { return (list || []).map((d) => `${d.speaker}: ${d.line}`).join("\n"); }
function textToDlg(t) {
  return t.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => { const i = l.indexOf(":"); return i < 0 ? { speaker: "", line: l } : { speaker: l.slice(0, i).trim(), line: l.slice(i + 1).trim() }; });
}

async function tabShots(el, ctx) {
  const shots = JSON.parse(JSON.stringify(ctx.e.shots));
  const locIds = Object.keys(ctx.locs);
  const sel = (f, i, v, opts, labels) => `<select data-i="${i}" data-f="${f}">${opts.map((o, k) => `<option value="${esc(o)}" ${o === (v || "") ? "selected" : ""}>${esc(labels ? labels[k] : o || "inherit")}</option>`).join("")}</select>`;
  const render = () => {
    let lastScene = null;
    const sceneById = Object.fromEntries(ctx.e.scenes.map((s) => [s.id, s]));
    el.innerHTML = `
      <div class="row" style="margin-bottom:10px"><button class="primary" id="save">Save shot list</button>
        ${gateBtn(ctx, "shots", "shot list")}<button id="add">Add shot</button><button id="cont" title="master per scene, punch-ins for closer shots, reuse for returns to wide">Auto continuity</button>
        <span class="muted small grow">Characters: <code>id:position:outfit</code>, comma separated. Dialogue: one <code>id: line</code> per line. Editing a duration locks it.</span></div>
      <table class="shots"><thead><tr><th style="width:90px">Shot</th><th style="width:150px">Framing / camera</th><th style="width:170px">Where / who</th>
        <th>Action</th><th style="width:24%">Dialogue / sound</th><th style="width:150px">Timing / source</th><th style="width:96px"></th></tr></thead><tbody>
      ${shots.map((sh, i) => {
        let head = "";
        if (sh.scene_id !== lastScene) {
          lastScene = sh.scene_id; const sc = sceneById[sh.scene_id];
          head = `<tr class="scene-row"><td colspan="7"><b>${esc(sh.scene_id || "no scene")}</b> ${sc ? esc(sc.summary) : ""}
            ${sc ? `<button class="link" data-replan="${esc(sc.id)}">re-plan this scene</button>` : ""}</td></tr>`;
        }
        return head + `<tr>
          <td><b>${esc(sh.id || "new")}</b>${sh._stale ? `<br><span class="chip warn">stale</span>` : ""}${sh.frame?.locked ? `<br><span class="chip good">frame locked</span>` : ""}</td>
          <td>${sel("framing", i, sh.framing, FRAMINGS)}<input data-i="${i}" data-f="camera" value="${esc(sh.camera)}" placeholder="camera" style="margin-top:4px"></td>
          <td>${sel("location_id", i, sh.location_id, locIds, locIds.map((l) => ctx.locs[l].name))}
            <input data-i="${i}" data-f="time_of_day" value="${esc(sh.time_of_day)}" placeholder="time of day" style="margin-top:4px">
            <input data-i="${i}" data-f="characters" value="${esc(charsToText(sh.characters))}" placeholder="sam:left" style="margin-top:4px"></td>
          <td><textarea data-i="${i}" data-f="action">${esc(sh.action)}</textarea>
            <details><summary>prompts${sh.image_prompt_override || sh.motion_prompt_override ? " (overridden)" : ""}</summary>
              <label>Image prompt (auto)</label><pre class="prompt">${esc(sh._image_prompt)}</pre>
              <label>Extra for image</label><input data-i="${i}" data-f="image_prompt_extra" value="${esc(sh.image_prompt_extra)}" style="width:100%">
              <label>Motion prompt (auto, ${esc(sh._engine)})</label><pre class="prompt">${esc(sh._motion_prompt)}</pre>
              <label>Extra for motion</label><input data-i="${i}" data-f="motion_prompt_extra" value="${esc(sh.motion_prompt_extra)}" style="width:100%">
              <label>Full image prompt override (replaces auto)</label><textarea data-i="${i}" data-f="image_prompt_override">${esc(sh.image_prompt_override)}</textarea>
              <label>Full motion prompt override</label><textarea data-i="${i}" data-f="motion_prompt_override">${esc(sh.motion_prompt_override)}</textarea>
            </details></td>
          <td><textarea data-i="${i}" data-f="dialogue">${esc(dlgToText(sh.dialogue))}</textarea><input data-i="${i}" data-f="sound" value="${esc(sh.sound)}" placeholder="sound" style="margin-top:4px"></td>
          <td><div class="row"><input data-i="${i}" data-f="duration_s" type="number" step="0.1" value="${sh.duration_s}" style="width:70px">s
              ${sh.duration_locked ? `<button class="link" data-unlock="${i}" title="locked - click to auto-compute">🔒</button>` : ""}</div>
            ${sel("start_mode", i, sh.start_mode, ["new", "punch_in", "reuse", "chain"], ["new frame (master)", "punch-in from scene master", "reuse master angle", "chain: last frame of prev take"])}
            ${sel("frame_source", i, sh.frame_source, ["", "flux_lora", "zimage", "qwen_refs", "auto", "upload"], ["source: inherit (" + sourceLabel(sh._source) + ")", "Flux + LoRA", "Z-Image", "Reference photos (Qwen Edit)", "Auto (LoRA > photos > Z-Image)", "Upload"])}
            ${sel("engine", i, sh.engine, ["", "minimax_local", "ltx", "wan", "minimax"], ["engine: series default", "MiniMax H3 (local)", "LTX-2.3", "Wan 2.2", "MiniMax (cloud)"])}</td>
          <td><div class="row" style="gap:2px"><button class="link" data-up="${i}" title="move up">↑</button><button class="link" data-down="${i}" title="move down">↓</button>
            <button class="link" data-dup="${i}" title="split: duplicate below">split</button><button class="link" data-merge="${i}" title="merge with next">merge</button>
            <button class="link danger" data-del="${i}">delete</button></div></td></tr>`;
      }).join("")}</tbody></table>`;
    $$("table.shots tbody tr:not(.scene-row)", el).forEach(row => {
      [...row.children].forEach((cell, index) => { cell.dataset.label = ["Shot", "Framing / camera", "Where / who", "Action", "Dialogue / sound", "Timing / source", "Actions"][index]; });
    });
    bulkSelect(el, "[data-del]", async targets => {
      const indices = targets.map(t => +t.dataset.del).sort((a,b) => b-a);
      indices.forEach(i => shots.splice(i, 1)); App.dirty = true;
    }, render, "shots");
  };
  render();
  const payload = () => shots.map((sh) => ({ ...sh }));
  el.oninput = (ev) => {
    const t = ev.target; const i = t.dataset.i, f = t.dataset.f; if (i == null || !f) return;
    const sh = shots[+i];
    if (f === "characters") sh.characters = textToChars(t.value);
    else if (f === "dialogue") sh.dialogue = textToDlg(t.value);
    else if (f === "duration_s") { sh.duration_s = parseFloat(t.value) || 0; sh.duration_locked = true; }
    else sh[f] = t.value;
  };
  el.onchange = el.oninput;
  el.onclick = async (ev) => {
    const t = ev.target, ds = t.dataset;
    const move = (i, j) => { if (j < 0 || j >= shots.length) return; [shots[i], shots[j]] = [shots[j], shots[i]]; App.dirty = true; render(); };
    if (ds.up) move(+ds.up, +ds.up - 1);
    if (ds.down) move(+ds.down, +ds.down + 1);
    if (ds.dup) { const src = shots[+ds.dup]; const copy = { ...JSON.parse(JSON.stringify(src)), id: "", _copy_of: src.id, dialogue: [], duration_locked: false }; shots.splice(+ds.dup + 1, 0, copy); App.dirty = true; render(); }
    if (ds.merge) {
      const i = +ds.merge; if (i + 1 >= shots.length) return;
      const a = shots[i], b = shots[i + 1];
      a.action = [a.action, b.action].filter(Boolean).join(" Then "); a.dialogue = (a.dialogue || []).concat(b.dialogue || []);
      a.duration_locked = false; shots.splice(i + 1, 1); App.dirty = true; render();
    }
    if (ds.del) { if (await ask(`Delete ${shots[+ds.del].id || "this shot"}?`, "Delete", true)) { shots.splice(+ds.del, 1); App.dirty = true; render(); } }
    if (ds.unlock) { shots[+ds.unlock].duration_locked = false; App.dirty = true; render(); }
    if (ds.replan) { if (await ask(`Re-plan all shots of ${ds.replan}? Its current shots are replaced.`, "Re-plan", true)) startJob(`${ctx.base}/replan/${ds.replan}`); }
    if (t.id === "add") { shots.push({ id: "", scene_id: shots.at(-1)?.scene_id || "S01", location_id: shots.at(-1)?.location_id || locIds[0] || "", framing: "medium", camera: "", characters: [], action: "", dialogue: [], sound: "", action_seconds: 3, start_mode: "new", frame_source: "", engine: "" }); App.dirty = true; render(); }
    if (t.id === "cont") { await PUT(`${ctx.base}/shots`, payload()); await POST(`${ctx.base}/continuity`); App.dirty = false; toast("Continuity assigned: masters, punch-ins, reuses."); return reload(); }
    if (t.id === "save" || ds.gate) {
      await PUT(`${ctx.base}/shots`, payload()); App.dirty = false;
      if (ds.gate) return setGate(ctx, ds.gate, ds.val === "1");
      toast("Shot list saved."); reload();
    }
  };
  App.refreshers.push(reload);
}

// ── 3. First frames ─────────────────────────────────────────────────────
function cropModal(file, aspect) {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file), urls = [url], controller = new AbortController();
    let done = false, ready = false, triedPreview = false, drag = null, observer;
    let bx = 0, by = 0, bw = 0, bh = 0, cx = 0.5, cy = 0.5;
    modal(`<h3>Crop your photo</h3><p id="crop-status" class="muted small" aria-live="polite">Opening photo…</p>
      <div class="crop-stage"><div class="crop-wrap" id="cw"><img id="ci" alt="Photo to crop" draggable="false"><div class="crop-box" id="cb" tabindex="0" role="group" aria-label="Crop area. Drag or use arrow keys to move."></div></div></div>
      <div class="crop-controls"><label for="cs">Crop size</label><input type="range" id="cs" min="20" max="100" value="100" aria-label="Crop size">
      <div class="row"><button id="cn">Cancel</button><button class="primary" id="ck" disabled>Use this crop</button></div></div>`);
    $("#modal-body").classList.add("crop-dialog");
    const img = $("#ci"), box = $("#cb"), slider = $("#cs"), confirm = $("#ck"), status = $("#crop-status");
    const finish = (value) => {
      if (done) return;
      done = true; modalCleanup = null; controller.abort(); observer?.disconnect();
      urls.forEach(u => URL.revokeObjectURL(u)); closeModal(); resolve(value);
    };
    modalCleanup = () => finish(null);
    const layout = () => {
      if (!ready || done) return;
      const W = img.clientWidth, H = img.clientHeight, scale = Number(slider.value) / 100;
      if (!W || !H) return;
      if (W / H > aspect) { bh = H * scale; bw = bh * aspect; } else { bw = W * scale; bh = bw / aspect; }
      bx = Math.max(0, Math.min(W - bw, cx * W - bw / 2));
      by = Math.max(0, Math.min(H - bh, cy * H - bh / 2));
      cx = (bx + bw / 2) / W; cy = (by + bh / 2) / H;
      Object.assign(box.style, {left: `${bx}px`, top: `${by}px`, width: `${bw}px`, height: `${bh}px`});
    };
    img.onload = () => {
      if (done) return;
      ready = true; confirm.disabled = false;
      status.textContent = "Drag the frame with your finger. Adjust its size below.";
      layout();
    };
    img.onerror = async () => {
      if (done) return;
      ready = false; confirm.disabled = true;
      if (triedPreview) { status.textContent = "This photo could not be opened. Choose another photo."; return; }
      triedPreview = true; status.textContent = "Preparing photo…";
      try {
        const fd = new FormData(); fd.append("file", file);
        const response = await fetch("/api/image_preview", {method: "POST", body: fd, signal: controller.signal});
        if (!response.ok) { const data = await response.json(); throw new Error(data.detail || "Could not open photo"); }
        const preview = URL.createObjectURL(await response.blob());
        if (done) { URL.revokeObjectURL(preview); return; }
        urls.push(preview); img.src = preview;
      } catch (error) { if (!done) status.textContent = error.message; }
    };
    box.onpointerdown = (event) => {
      if (!ready || (event.pointerType === "mouse" && event.button !== 0)) return;
      drag = {id: event.pointerId, x: event.clientX, y: event.clientY, cx, cy};
      box.setPointerCapture(event.pointerId); event.preventDefault();
    };
    box.onpointermove = (event) => {
      if (!drag || event.pointerId !== drag.id) return;
      cx = drag.cx + (event.clientX - drag.x) / img.clientWidth;
      cy = drag.cy + (event.clientY - drag.y) / img.clientHeight;
      layout(); event.preventDefault();
    };
    box.onpointerup = box.onpointercancel = box.onlostpointercapture = () => { drag = null; };
    box.onkeydown = (event) => {
      const step = event.shiftKey ? 20 : 5;
      if (event.key === "ArrowLeft") cx -= step / img.clientWidth;
      else if (event.key === "ArrowRight") cx += step / img.clientWidth;
      else if (event.key === "ArrowUp") cy -= step / img.clientHeight;
      else if (event.key === "ArrowDown") cy += step / img.clientHeight;
      else return;
      layout(); event.preventDefault();
    };
    slider.oninput = layout;
    observer = new ResizeObserver(layout); observer.observe(img);
    const tap = (button, action) => {
      let start = null;
      button.onclick = action;
      button.onpointerdown = event => {
        if (event.pointerType !== "mouse") { start = {x: event.clientX, y: event.clientY}; event.preventDefault(); }
      };
      button.onpointercancel = () => { start = null; };
      button.onpointerup = event => {
        if (!start || event.pointerType === "mouse") return;
        const moved = Math.hypot(event.clientX - start.x, event.clientY - start.y); start = null;
        if (moved < 12) {
          event.preventDefault();
          // Closing a touch dialog can otherwise send a compatibility click to the screen underneath.
          const suppress = click => {
            if (click.detail && Math.hypot(click.clientX - event.clientX, click.clientY - event.clientY) < 30) {
              click.preventDefault(); click.stopImmediatePropagation(); document.removeEventListener("click", suppress, true);
            }
          };
          document.addEventListener("click", suppress, true);
          setTimeout(() => document.removeEventListener("click", suppress, true), 600);
          action();
        }
      };
    };
    tap($("#cn"), () => finish(null));
    tap(confirm, () => {
      layout();
      if (!ready || !img.clientWidth || !img.clientHeight) return;
      finish({x: bx / img.clientWidth, y: by / img.clientHeight, w: bw / img.clientWidth, h: bh / img.clientHeight});
    });
    img.src = url;
  });
}

async function uploadFrame(ctx, shotId, file, end) {
  if (App.interactionBusy) return toast("Finish the current photo first.");
  App.interactionBusy = true;
  try {
    const size = ctx.e._settings.image || ctx.e._settings.video;
    const crop = await cropModal(file, size.width / size.height);
    if (!crop) return;
    modal(`<h3>Uploading photo…</h3><p role="status">Keep this screen open while your photo is saved.</p><progress aria-label="Uploading photo"></progress>`);
    const fd = new FormData(); fd.append("file", file); fd.append("crop", JSON.stringify(crop)); if (end) fd.append("end", "1");
    await FORM(`${ctx.base}/shots/${shotId}/upload_frame`, fd);
    closeModal(); toast(end ? "End frame set." : "Uploaded and selected."); await reload();
  } catch (error) {
    closeModal();
    if (!document.querySelector(".toast.bad")) toast(error.message || "Upload failed. Please try again.", true);
  } finally { App.interactionBusy = false; }
}

function checkChip(c) {
  if (!c.check) return "";
  if (c.check.score == null) return `<span class="chip" title="${esc((c.check.issues || []).join("\n"))}">check n/a</span>`;
  const cls = c.check.score >= 7 ? "good" : c.check.score >= 4 ? "warn" : "bad";
  return `<span class="chip ${cls}" title="${esc((c.check.issues || []).join("\n") || "no issues")}">check ${c.check.score}/10</span>`;
}

async function tabFrames(el, ctx) {
  const shots = ctx.e.shots;
  const needs = shots.filter((s) => s.start_mode !== "chain" && s.frame.selected == null).length;
  el.innerHTML = `
    ${!ctx.e.gates.shots ? `<div class="notice">The shot list isn't approved yet. You can still render frames, but edits to shots later will mark them stale.</div>` : ""}
    <div class="card row">
      <button class="primary" id="render-missing">Render missing frames</button>
      <select id="src-override"><option value="">per-shot source</option><option value="flux_lora">force Flux + LoRA</option><option value="zimage">force Z-Image</option><option value="qwen_refs">force reference photos</option></select>
      <button id="render-all">Re-render all unlocked</button><button id="vision">Vision check</button>
      <span class="grow"></span>
      <input type="file" id="bulk" multiple accept="image/*" title="files named by shot id, e.g. S01_SH02.png"><button id="bulk-up">Bulk upload by shot id</button>
      ${gateBtn(ctx, "frames", "frames")}
    </div>
    <p class="muted small">${needs} shot(s) still need a selected frame. ${ctx.e._settings.candidates_per_shot} candidates per render. Chained shots get their frame from the previous take at the Takes stage.</p>
    <div id="frames-list">${shots.map((sh) => frameCard(ctx, sh)).join("")}</div>`;
  $("#render-missing").onclick = () => startJob(`${ctx.base}/frames`, { source: $("#src-override").value });
  $("#render-all").onclick = async () => { if (await ask("Render new candidates for every unlocked shot?", "Render")) startJob(`${ctx.base}/frames`, { force: true, source: $("#src-override").value, shot_ids: shots.filter((s) => !s.frame.locked).map((s) => s.id) }); };
  $("#vision").onclick = () => startJob(`${ctx.base}/vision`, {});
  $("#bulk-up").onclick = async () => {
    const files = $("#bulk").files; if (!files.length) return toast("Choose files first", true);
    const fd = new FormData(); [...files].forEach((f) => fd.append("files", f));
    const r = await FORM(`${ctx.base}/bulk_upload`, fd);
    toast(`Matched ${r.matched.length}${r.skipped.length ? `, skipped ${r.skipped.join(", ")}` : ""}`); reload();
  };
  el.onclick = async (ev) => {
    const t = ev.target, ds = t.dataset;
    if (ds.zoom) zoom(ds.zoom);
    if (ds.gate) setGate(ctx, ds.gate, ds.val === "1");
    if (ds.pick != null && ds.shot) { await POST(`${ctx.base}/shots/${ds.shot}/frame`, { select: +ds.pick }); reload(); }
    if (ds.rm != null && ds.shot) { await POST(`${ctx.base}/shots/${ds.shot}/frame`, { remove: +ds.rm }); reload(); }
    if (ds.qwenface != null && ds.shot) startJob(`${ctx.base}/shots/${ds.shot}/frame_correction`, { index: +ds.qwenface });
    if (ds.refine && ds.shot) startJob(`${ctx.base}/shots/${ds.shot}/refine`, { index: +ds.idx, engine: ds.refine, denoise: parseFloat($(`#dn-${ds.shot}`)?.value || "0.3") });
    if (ds.lock && ds.shot) { await POST(`${ctx.base}/shots/${ds.shot}/frame`, { lock: ds.lock === "1" }); reload(); }
    if (ds.render && ds.shot) startJob(`${ctx.base}/frames`, { shot_ids: [ds.shot], force: true, source: $(`#src-${ds.shot}`).value });
    if (ds.clearend && ds.shot) { await POST(`${ctx.base}/shots/${ds.shot}/frame`, { clear_end: true }); reload(); }
    if (ds.asend && ds.shot) { await POST(`${ctx.base}/shots/${ds.shot}/frame`, { set_end: ds.asend }); toast("Set as end frame."); reload(); }
  };
  el.onchange = (ev) => {
    const t = ev.target;
    if (t.dataset.upframe) { const f = t.files[0]; if (f) uploadFrame(ctx, t.dataset.upframe, f, false); t.value = ""; }
    if (t.dataset.upend) { const f = t.files[0]; if (f) uploadFrame(ctx, t.dataset.upend, f, true); t.value = ""; }
  };
  App.refreshers.push(reload);
}

function frameCard(ctx, sh) {
  const who = (sh.characters || []).map((c) => esc(charName(ctx, c.id) + (c.position ? ` (${c.position})` : ""))).join(", ");
  const cands = sh.frame.candidates || [];
  const chained = sh.start_mode === "chain";
  return `<div class="shot-card ${sh.frame.locked ? "locked" : ""}">
    <div class="shot-head"><span class="id">${esc(sh.id)}</span><span class="muted">${esc(sh.framing)} · ${who || "no characters"} · ${sh.duration_s}s</span>
      <span class="chip accent">${sourceLabel(sh._source)}</span>${sh._loras.length ? `<span class="chip" title="${esc(sh._loras.map((l) => l.name + " @" + l.strength).join("\n"))}">${sh._loras.length} LoRA</span>` : ""}
      ${sh._stale ? `<span class="chip warn" title="bible changed since these frames were made">stale</span>` : ""}
      ${sh.frame.locked ? `<span class="chip good">locked</span>` : ""}<span class="grow"></span>
      ${chained ? "" : `<select id="src-${sh.id}"><option value="">${sourceLabel(sh._source)}</option><option value="flux_lora">Flux + LoRA</option><option value="zimage">Z-Image</option><option value="qwen_refs">Reference photos</option></select>
      <button data-render="1" data-shot="${sh.id}">Render ${ctx.e._settings.candidates_per_shot} more</button>
      <label class="btn file-pick" style="margin:0;color:var(--ink)">Upload photo<input type="file" accept="image/*,.heic,.heif" data-upframe="${sh.id}" aria-label="Upload photo for ${esc(sh.id)}"></label>`}
      <button data-lock="${sh.frame.locked ? 0 : 1}" data-shot="${sh.id}">${sh.frame.locked ? "Unlock" : "Lock"}</button></div>
    <div class="muted small">${esc(sh.action)}${(sh.dialogue || []).length ? " · " + sh.dialogue.map((d) => esc(charName(ctx, d.speaker) + ": " + d.line)).join(" / ") : ""}</div>
    <details><summary>image prompt</summary><pre class="prompt">${esc(sh._image_prompt)}</pre></details>
    ${chained ? `<p class="muted">Chained: this shot starts from the last frame of ${esc("the previous shot's")} selected take (made at the Takes stage).</p>` : ""}
    <div class="row small" style="margin:6px 0">End frame (optional, LTX):
      ${sh.end_frame ? `<img src="${media(sh.end_frame)}" style="height:44px;border-radius:4px" data-zoom="${media(sh.end_frame)}"><button class="link" data-clearend="1" data-shot="${sh.id}">clear</button>` : `<span class="muted">none</span>`}
      <label class="btn file-pick" style="margin:0">Upload end frame<input type="file" accept="image/*,.heic,.heif" data-upend="${sh.id}" aria-label="Upload end frame for ${esc(sh.id)}"></label>
      <span class="grow"></span>refine strength <input id="dn-${sh.id}" type="number" min="0.1" max="0.8" step="0.05" value="0.3" style="width:64px"></div>
    <div class="cands"><div class="cands-in">${cands.map((c, k) => `<div class="cand ${sh.frame.selected === k ? "sel" : ""}">
      <div class="frame"><img src="${media(c.file)}" data-zoom="${media(c.file)}" loading="lazy"></div>
      <div class="meta"><span class="code">${esc(sh.id)}/${String.fromCharCode(65 + k)}</span>${sh.frame.selected === k ? `<span class="pick">picked</span>` : ""}<span>${sourceLabel(c.source)}${c.plate ? ", on location plate" : ""}</span> ${checkChip(c)}</div>
      <div class="acts">${sh.frame.selected === k ? ""  : `<button data-pick="${k}" data-shot="${sh.id}">Pick this</button>`}
        <button data-qwenface="${k}" data-shot="${sh.id}" title="Correct this frame using the uploaded character portrait">Correct face · Qwen</button>
        <button data-refine="flux_lora" data-idx="${k}" data-shot="${sh.id}" title="img2img with the character LoRA">Refine Flux</button>
        <button data-refine="zimage" data-idx="${k}" data-shot="${sh.id}" title="img2img to match style">Refine Z</button>
        <button data-asend="${esc(c.file)}" data-shot="${sh.id}" title="use as this shot's end frame">as end</button>
        <button class="danger" data-rm="${k}" data-shot="${sh.id}">✕</button></div></div>`).join("") || `<p class="muted" style="padding:20px">No candidates yet.</p>`}</div></div></div>`;
}

// ── 4. Animatic ─────────────────────────────────────────────────────────
async function tabAnimatic(el, ctx) {
  const items = ctx.e.shots.map((sh) => {
    const f = selFrame(sh) || sh.frame.candidates[0];
    const cap = (sh.dialogue || []).map((d) => `${charName(ctx, d.speaker)}: ${d.line}`).join("  /  ");
    return { id: sh.id, src: f ? media(f.file) : "", dur: +sh.duration_s || 2, cap, action: sh.action, missing: !f };
  });
  const total = items.reduce((a, b) => a + b.dur, 0);
  el.innerHTML = `
    <div class="row" style="margin-bottom:10px"><button class="primary" id="play">Play</button><button id="restart">Restart</button>
      <label class="row small" style="margin:0"><input type="checkbox" id="showact"> show action text</label>
      <span class="muted grow">${fmtSec(total)} · ${items.filter((i) => i.missing).length} shots without a frame</span>
      <button id="export">Export animatic.mp4</button>${ctx.e.outputs?.animatic ? `<a href="${media(ctx.e.outputs.animatic)}" target="_blank">open animatic.mp4</a>` : ""}
      ${gateBtn(ctx, "animatic", "pacing")}</div>
    <div class="player"><img id="pimg"><div class="tag" id="ptag"></div><div class="cap" id="pcap"></div></div>
    <div class="timeline" id="tl">${items.map((it, k) => `<div data-k="${k}" style="flex:${it.dur}" title="${esc(it.id)} ${it.dur}s">${esc(it.id)}</div>`).join("")}</div>
    <p class="muted small">Change durations in the Shot list tab (editing a duration locks it).</p>`;
  let k = 0, timer = null, playing = false;
  const show = (i) => {
    k = i; const it = items[i]; if (!it) return;
    $("#pimg").src = it.src || ""; $("#pimg").style.visibility = it.src ? "visible" : "hidden";
    $("#ptag").textContent = `${it.id} · ${it.dur}s${it.missing ? " · no frame" : ""}`;
    $("#pcap").textContent = $("#showact").checked && it.action ? `${it.cap ? it.cap + "\n" : ""}[${it.action}]` : it.cap;
    $$("#tl div").forEach((d, j) => d.classList.toggle("on", j === i));
  };
  const step = () => { if (!playing) return; timer = setTimeout(() => { if (k + 1 < items.length) { show(k + 1); step(); } else { playing = false; $("#play").textContent = "Play"; } }, items[k].dur * 1000); };
  $("#play").onclick = () => { playing = !playing; $("#play").textContent = playing ? "Pause" : "Play"; clearTimeout(timer); if (playing) step(); };
  $("#restart").onclick = () => { clearTimeout(timer); show(0); if (playing) step(); };
  $("#tl").onclick = (e) => { const i = e.target.dataset.k; if (i != null) { clearTimeout(timer); show(+i); if (playing) step(); } };
  $("#export").onclick = () => startJob(`${ctx.base}/animatic`);
  el.addEventListener("click", (e) => { if (e.target.dataset.gate) setGate(ctx, e.target.dataset.gate, e.target.dataset.val === "1"); });
  show(0);
}

// ── 5. Takes ────────────────────────────────────────────────────────────
async function tabTakes(el, ctx) {
  const shots = ctx.e.shots;
  const spf = ctx.e.stats?.spf || {};
  const models = await GET("/api/models");
  el.innerHTML = `
    ${!ctx.e.gates.frames ? `<div class="notice">First frames aren't approved yet. Shots without a selected frame are skipped.</div>` : ""}
    <div class="card row">
      <label class="row" style="margin:0">takes per shot <input id="ntakes" type="number" min="1" max="8" value="${ctx.e._settings.takes_per_shot}" style="width:60px"></label>
      <label>Video model <select id="take-model"><option value="">Per-shot / project default</option>${models.map(m => `<option value="${esc(m.id)}" data-engine="${m.engine}">${esc(m.name)}</option>`).join("")}</select></label>
      <button class="primary" id="render-missing">Render shots without takes</button>
      <button id="render-checked">Render checked shots</button>
      <span class="muted small grow">Measured speed: ${Object.entries(spf).map(([k, v]) => `${k} ${v} s/frame`).join(", ") || "not measured yet"} · est. ${fmtSec(ctx.e._estimate_s)} for all</span>
      ${gateBtn(ctx, "takes", "takes")}
    </div>
    ${shots.map((sh) => takeCard(ctx, sh)).join("")}`;
  const n = () => parseInt($("#ntakes").value) || 1;
  const modelChoice = () => {
    const option = $("#take-model").selectedOptions[0];
    return { engine: option.dataset.engine || "", model: option.value };
  };
  $("#render-missing").onclick = () => startJob(`${ctx.base}/takes`, { takes: n(), ...modelChoice() });
  $("#render-checked").onclick = () => {
    const ids = $$("input[data-chk]:checked").map((x) => x.dataset.chk);
    if (!ids.length) return toast("Tick some shots first", true);
    startJob(`${ctx.base}/takes`, { shot_ids: ids, takes: n(), force: true, ...modelChoice() });
  };
  el.onclick = async (ev) => {
    const t = ev.target, ds = t.dataset;
    if (ds.zoom) zoom(ds.zoom);
    if (ds.gate) setGate(ctx, ds.gate, ds.val === "1");
    if (ds.pick != null && ds.shot) { await POST(`${ctx.base}/shots/${ds.shot}/take`, { select: +ds.pick }); reload(); }
    if (ds.rm != null && ds.shot) { if (await ask("Remove this take from the list?", "Remove", true)) { await POST(`${ctx.base}/shots/${ds.shot}/take`, { remove: +ds.rm }); reload(); } }
    if (ds.lock && ds.shot) { await POST(`${ctx.base}/shots/${ds.shot}/take`, { lock: ds.lock === "1" }); reload(); }
    if (ds.more && ds.shot) startJob(`${ctx.base}/takes`, { shot_ids: [ds.shot], takes: n(), force: true, ...modelChoice() });
    if (ds.face != null && ds.shot) startJob(`${ctx.base}/shots/${ds.shot}/face_correction`, { index: +ds.face, model: $(`#face-${ds.shot}`).value });
    if (ds.next && ds.shot) { await POST(`${ctx.base}/shots/${ds.shot}/next_from_last`); toast("Next shot's first frame set from this take."); reload(); }
    if (ds.trim && ds.shot) { await POST(`${ctx.base}/shots/${ds.shot}/take`, { trim_in: parseFloat($(`#ti-${ds.shot}`).value) || 0, trim_out: parseFloat($(`#to-${ds.shot}`).value) || 0 }); App.dirty = false; toast("Trim saved."); }
  };
  App.refreshers.push(reload);
}

function takeCard(ctx, sh) {
  const f = selFrame(sh);
  const items = sh.takes.items || [];
  return `<div class="shot-card ${sh.takes.locked ? "locked" : ""}">
    <div class="shot-head"><input type="checkbox" data-chk="${sh.id}"><span class="id">${esc(sh.id)}</span>
      <span class="chip accent">${esc(sh._engine)}</span><span class="muted">${sh.duration_s}s · ${esc(sh.start_mode)}</span>
      ${f ? `<img src="${media(f.file)}" style="height:40px;border-radius:4px" data-zoom="${media(f.file)}">` : sh.start_mode === "chain" ? `<span class="chip">frame from previous take</span>` : `<span class="chip bad">no frame</span>`}
      ${sh.end_frame ? `<span class="chip">end frame</span>` : ""}<span class="grow"></span>
      <select id="face-${sh.id}" aria-label="Face correction model"><option value="qwen_identity" ${ctx.e._settings.face_model === "qwen_identity" ? "selected" : ""}>Qwen Image Edit · every frame</option><option value="minimax_identity" ${ctx.e._settings.face_model === "minimax_identity" ? "selected" : ""}>Match character references · MiniMax H3</option><option value="keep" ${ctx.e._settings.face_model === "keep" ? "selected" : ""}>KEEP (main face, temporal)</option><option value="codeformer" ${ctx.e._settings.face_model === "codeformer" ? "selected" : ""}>CodeFormer (all faces)</option></select>
      <button data-more="1" data-shot="${sh.id}">Render more takes</button>
      <button data-next="1" data-shot="${sh.id}" title="extract the last frame of the selected take as the next shot's first frame">Last frame → next shot</button>
      <button data-lock="${sh.takes.locked ? 0 : 1}" data-shot="${sh.id}">${sh.takes.locked ? "Unlock" : "Lock"}</button></div>
    <details><summary>motion prompt</summary><pre class="prompt">${esc(sh._motion_prompt)}</pre></details>
    <div class="cands"><div class="cands-in">${items.map((t, k) => `<div class="cand ${sh.takes.selected === k || (sh.takes.selected == null && k === 0) ? "sel" : ""}">
      <div class="frame"><video playsinline preload="metadata" controls preload="metadata" src="${media(t.file)}"></video></div>
      <div class="meta"><span class="code">${esc(sh.id)}/T${k + 1}</span>${sh.takes.selected === k || (sh.takes.selected == null && k === 0) ? `<span class="pick">picked</span>` : ""}<span>${esc(t.model || t.engine)}${t.face_correction ? " · " + esc(t.face_correction) + " corrected" : ""}, ${t.seconds}s, ${t.render_s}s render</span> ${(t.flags || []).map((x) => `<span class="chip warn">${esc(x)}</span>`).join(" ")}</div>
      <div class="acts">${sh.takes.selected === k ? "" : `<button data-pick="${k}" data-shot="${sh.id}">Pick this</button>`}
        <button data-face="${k}" data-shot="${sh.id}">Correct faces</button>
        <button class="danger" data-rm="${k}" data-shot="${sh.id}">✕</button></div></div>`).join("") || `<p class="muted" style="padding:20px">No takes yet.</p>`}</div></div>
    ${items.length ? `<div class="row small" style="margin-top:8px">Trim selected take: in <input id="ti-${sh.id}" type="number" step="0.05" min="0" value="${sh.takes.trim_in || 0}" style="width:70px">s
      out <input id="to-${sh.id}" type="number" step="0.05" min="0" value="${sh.takes.trim_out || 0}" style="width:70px">s (cut from end)
      <button data-trim="1" data-shot="${sh.id}">Save trim</button></div>` : ""}</div>`;
}

// ── 6. Cut ──────────────────────────────────────────────────────────────
async function tabCut(el, ctx) {
  const e = ctx.e, o = e.outputs || {}, m = e.music || {};
  const missing = e.shots.filter((s) => !(s.takes.items || []).length).map((s) => s.id);
  el.innerHTML = `
    ${missing.length ? `<div class="notice">No takes yet for: ${esc(missing.join(", "))}. They'll be left out.</div>` : ""}
    <div class="card form-grid">
      <div><label>Music or voice recording</label><input type="file" id="mfile" accept="audio/*,.mp3,.wav,.m4a,.flac,.ogg,.aac">
        <div id="music-current" class="small muted">${m.file ? "current: " + esc(m.file.split("/").pop()) : "none"}</div></div>
      <div><label>Music volume (0-1)</label><input id="mvol" type="number" step="0.05" min="0" max="1" value="${m.volume ?? 0.25}">
        <label class="row" style="margin-top:8px"><input type="checkbox" id="mduck" ${m.duck !== false ? "checked" : ""}> duck music under dialogue</label></div>
      <div class="full row" style="margin-top:10px"><button id="msave">Save music settings</button>
        <button id="lip-setup" ${m.file ? "" : "disabled"}>Lip-sync to uploaded audio</button><span class="grow"></span>
        <button class="primary" id="cut">Render final cut</button></div>
    </div>
    <p class="muted small">Each selected take is trimmed, scaled to ${e._settings.video.width}x${e._settings.video.height} @ ${e._settings.video.fps} fps and loudness-normalised, then joined. Wan takes (silent) get a silent track.</p>
    ${o.final ? `<h2>Final film ${o.runtime ? "· " + fmtSec(o.runtime) : ""}</h2>
      <video playsinline preload="metadata" controls src="${media(o.final)}" style="width:100%;max-width:1024px;border-radius:8px"></video>
      <div class="row" style="margin-top:8px"><a href="${media(o.final)}" download>final.mp4</a>${o.srt ? `<a href="${media(o.srt)}" download>subtitles (.srt)</a>` : ""}
        ${o.edl ? `<a href="${media(o.edl)}" download>edit list (.edl for DaVinci Resolve)</a>` : ""}${o.animatic ? `<a href="${media(o.animatic)}" download>animatic.mp4</a>` : ""}</div>
      <p class="muted small">In Resolve: import the takes folders as media, then File > Import > Timeline > the .edl.</p>` : ""}
    ${(o.lipsync_versions || []).map((version, i) => `<div class="card"><h2>Lip-sync preview ${i + 1}</h2>
      <p class="muted small">${esc(version.model)} · ${fmtSec(version.runtime)} · ${esc((version.audio_file || "").split("/").pop())} · ${version.isolated_vocals ? "isolated vocal guide" : "uploaded audio guide"}. The full uploaded music mix is used in this export.</p>
      <video playsinline preload="metadata" controls src="${media(version.file)}" style="width:100%;max-width:1024px;border-radius:8px"></video>
      <div class="row"><a href="${media(version.file)}" download>Download MP4</a>
        ${o.final === version.file ? `<span class="chip good">current final</span>` : `<button data-use-lipsync="${i}">Use as final</button>`}</div></div>`).join("")}
    ${o.before_lipsync ? `<p><a href="${media(o.before_lipsync)}" download>Download original final before lip-sync</a></p>` : ""}`;
  $("#msave").onclick = async () => {
    const button = $("#msave"); button.disabled = true;
    const fileInput = $("#mfile");
    const fd = new FormData(); const f = fileInput.files[0]; if (f) fd.append("file", f);
    fd.append("volume", $("#mvol").value); fd.append("duck", $("#mduck").checked ? "true" : "false");
    let saved;
    App.interactionBusy = true;
    try { saved = await FORM(`${ctx.base}/music`, fd); }
    finally { button.disabled = false; App.interactionBusy = false; }
    if (!fileInput.isConnected) return toast("Music saved.");
    ctx.e.music = saved; App.dirty = false;
    if (f && $("#mfile").files[0] === f) $("#mfile").value = "";
    $("#music-current").textContent = saved.file ? "current: " + saved.file.split("/").pop() : "none";
    $("#lip-setup").disabled = !saved.file; toast("Music saved.");
    if (f) {
      const choice = ask("Would you like to lip-sync the video to the voice in this upload?", "Choose shots");
      $("#ask-no").textContent = "Not now";
      if (await choice) openLipSync(ctx);
    }
  };
  $("#lip-setup").onclick = () => openLipSync(ctx);
  $$('[data-use-lipsync]', el).forEach(button => button.onclick = async () => {
    await POST(`${ctx.base}/lipsync/select`, {index: +button.dataset.useLipsync}); reload();
  });
  $("#cut").onclick = () => startJob(`${ctx.base}/cut`);
  App.refreshers.push(reload);
}

function openLipSync(ctx) {
  const shots = ctx.e.shots.filter(sh => sh.characters?.length === 1 && sh.takes.items?.length);
  if (!shots.length) return toast("Add takes to single-character shots first.", true);
  const characters = [...new Set(shots.map(sh => sh.characters[0].id))];
  modal(`<h2 style="margin-top:0">Lip-sync to your music or voice</h2>
    <p>Choose who sings or speaks. Only checked shots are changed. A separate preview keeps your original takes and final available.</p>
    <label for="lip-character">Character</label><select id="lip-character">${characters.map(id => `<option value="${esc(id)}">${esc(charName(ctx, id))}</option>`).join("")}</select>
    <div style="max-height:30vh;overflow:auto;margin:12px 0">${shots.map(sh => `<label class="row" style="margin:6px 0"><input type="checkbox" data-lip-shot="${esc(sh.id)}" data-character="${esc(sh.characters[0].id)}" ${sh.characters[0].id === characters[0] ? "checked" : ""}>${esc(sh.id)} · ${esc(charName(ctx, sh.characters[0].id))}</label>`).join("")}</div>
    <label for="lip-offset">Start in uploaded audio (seconds)</label><input id="lip-offset" type="number" min="0" step="0.1" value="0" style="width:100%">
    <label class="row" style="margin:12px 0"><input type="checkbox" id="lip-vocals" checked> Isolate the singing voice from music</label>
    <p class="muted small">Local LatentSync 1.6. The export replaces take audio with this upload at full volume. Vocal isolation guides the mouth; the full music mix stays in the export. Turn isolation off for a clean voice recording. Long audio is trimmed; short audio is padded with silence. Check the preview before using it as final.</p>
    <div class="row"><button id="lip-cancel">Not now</button><button id="lip-run" class="primary">Run lip-sync</button></div>`);
  $("#lip-character").onchange = ev => $$('[data-lip-shot]').forEach(box => { box.checked = box.dataset.character === ev.target.value; });
  $("#lip-cancel").onclick = closeModal;
  $("#lip-run").onclick = async () => {
    const shot_ids = $$('[data-lip-shot]:checked').map(box => box.dataset.lipShot);
    if (!shot_ids.length) return toast("Choose at least one shot.", true);
    const audio_offset = Number($("#lip-offset").value);
    if (!Number.isFinite(audio_offset) || audio_offset < 0) return toast("Enter a valid audio start.", true);
    const isolate_vocals = $("#lip-vocals").checked;
    const button = $("#lip-run"); button.disabled = true;
    try {
      await startJob(`${ctx.base}/lipsync`, {shot_ids, audio_offset, isolate_vocals}); closeModal();
    } finally { button.disabled = false; }
  };
}

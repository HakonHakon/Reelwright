// Reelwright - episode views: story, shots, frames, animatic, takes, cut.
"use strict";

const TABS = [["story", "1. Story", null, "Story"], ["shots", "2. Shot list", "shots", "Shots"], ["frames", "3. First frames", "frames", "Frames"],
  ["animatic", "4. Animatic", "animatic", "Animatic"], ["takes", "5. Takes", "takes", "Takes"], ["cut", "6. Cut", null, "Cut"]];
const FRAMINGS = ["extreme wide", "wide", "full", "medium wide", "medium", "medium close-up", "close-up", "extreme close-up", "over-the-shoulder", "two-shot", "insert"];

async function viewEpisode(view, sid, eid, tab) {
  const [d, e] = await Promise.all([getSeries(sid), GET(`/api/s/${sid}/e/${eid}`)]);
  const ctx = { sid, eid, d, e, base: `/api/s/${sid}/e/${eid}`, chars: d.bible.characters, locs: d.bible.locations };
  crumbs([[d.series.name, `#/s/${sid}`], [e.title, null]]);
  view.innerHTML = `<div id="ep-head"></div><div id="tab"></div>`;
  const paintHead = () => {
    const e = ctx.e, g = e.gates || {};
    const done = { story: e.shots.length > 0 || (e.scenes || []).length > 0, shots: !!g.shots, frames: !!g.frames, animatic: !!g.animatic, takes: !!g.takes, cut: !!(e.outputs && e.outputs.final) };
    const nDone = Object.values(done).filter(Boolean).length;
    $("#ep-head").innerHTML = `
    <div class="page-head"><div class="grow"><h1>${esc(e.title)}</h1>
      <div class="row sub"><span class="chip">${e.shots.length} shots</span><span class="chip">runtime ${fmtSec(e._runtime)}</span><span class="chip">est. render ${fmtSec(e._estimate_s)} at ${e._settings.takes_per_shot} takes/shot</span><span class="chip ${nDone === 6 ? "good" : "accent"}">${nDone}/6 stages done</span></div></div></div>
    <nav class="tabs" aria-label="Episode stages">${TABS.map(([k, label, gate, short], i) => `<a href="#/s/${sid}/e/${eid}/${k}" class="${k === tab ? "on" : ""} ${done[k] ? "done" : ""}" ${k === tab ? 'aria-current="page"' : ""}><span class="n">${done[k] ? ic("check") : i + 1}</span><span class="tl">${label.replace(/^\d+\.\s*/, "")}</span><span class="ts">${short}</span>${gate && g[gate] ? `<span class="gate chip good">approved</span>` : ""}</a>`).join("")}</nav>
    <div class="progress"><div style="width:${Math.round((nDone / 6) * 100)}%"></div></div>`;
    pageMenu($("#ep-head"), [["Delete episode…", async () => {
      if (await ask(`Delete episode ${ctx.e.title}? Files are kept in a .deleted folder.`, "Delete", true)) {
        await api("DELETE", ctx.base); location.hash = `#/s/${sid}`;
      }
    }, true]]);
  };
  paintHead();
  document.body.classList.add("has-stepper");  // phones: the stage stepper takes the bottom bar
  const el = $("#tab");
  const fn = { story: tabStory, shots: tabShots, frames: tabFrames, animatic: tabAnimatic, takes: tabTakes, cut: tabCut }[tab] || tabStory;
  await fn(el, ctx);
  const bulkRoot = () => (tab === "takes" ? $("#take-library", el) : el);
  const setupBulk = () => {
    if (tab !== "frames" && tab !== "takes") return;
    bulkSelect(bulkRoot(), "[data-rm]", async targets => {
      const items = {};
      for (const t of targets) (items[t.dataset.shot] ||= []).push(+t.dataset.rm);
      await POST(`${ctx.base}/delete_assets`, {kind: tab === "frames" ? "frame" : "take", items});
    }, reload, tab === "frames" ? "frames" : "takes");
  };
  setupBulk();
  // Soft reload: fetch the episode again and repaint only what changed. The page, the scroll position, open panels,
  // playing videos and the timeline editor (with its undo history) stay as they are.
  if (["frames", "takes", "animatic"].includes(tab)) {
    let chain = Promise.resolve();
    const run = async () => {
      let fresh;
      try { fresh = await GET(ctx.base); } catch { return route(); }
      const old = ctx.e;
      if (fresh.shots.length !== old.shots.length || fresh.shots.some((s, i) => s.id !== old.shots[i].id)) return route();  // shots added or removed
      const changed = fresh.shots.filter((s, i) => JSON.stringify(s) !== JSON.stringify(old.shots[i])).map((s) => s.id);
      old.shots.splice(0, old.shots.length, ...fresh.shots);  // same arrays and object, so open handlers see the new data
      const { shots: _ignored, ...rest } = fresh; Object.assign(old, rest);
      paintHead();
      if (ctx.repaint) {
        ctx.repaint(changed);
        const root = bulkRoot();
        if (root) { $$(".bulk-actions, .bulk-check", root).forEach((x) => x.remove()); root.classList.remove("bulk-off"); App.selecting = false; setupBulk(); }
      }
      $$("[data-gate]", el).forEach((b) => { b.outerHTML = gateBtn(ctx, b.dataset.gate, b.dataset.label); });
      App.timelineEditor?.draw();
    };
    App.soft = () => (chain = chain.then(run, run));
  }
}

const reload = () => (App.soft ? App.soft() : route());
async function setGate(ctx, gate, value) { await POST(`${ctx.base}/gate`, { gate, value }); toast(value ? "Approved." : "Gate reopened."); reload(); }
/** Frame-source values for the shot editor: local sources, the cloud image models, and the current value. */
function frameSourceValues(cur) {
  const v = ["", "flux_lora", "zimage", "qwen_refs", ...(App.ai?.choices.image || []).map((c) => c.source), "auto", "upload"];
  if (cur && !v.includes(cur)) v.push(cur);
  return v;
}
const charName = (ctx, id) => ctx.chars[id]?.name || `${id} (missing from bible)`;
const selFrame = (sh) => (sh.frame.selected != null ? sh.frame.candidates[sh.frame.selected] : null);
const gateBtn = (ctx, gate, label) => ctx.e.gates[gate]
  ? `<button data-gate="${gate}" data-label="${esc(label)}" data-val="0">Reopen ${label}</button>`
  : `<button class="primary" data-gate="${gate}" data-label="${esc(label)}" data-val="1">Approve ${label}</button>`;

/** Replace just the cards that changed. Untouched cards keep their DOM, scroll position and playing video. */
function repaintCards(root, ctx, ids, make) {
  if (!root) return;
  for (const id of ids) {
    const shot = ctx.e.shots.find((s) => s.id === id), card = $(`[data-card="${CSS.escape(id)}"]`, root);
    if (shot && card) card.outerHTML = make(ctx, shot);
  }
}

// ── 1. Story ────────────────────────────────────────────────────────────
async function tabStory(el, ctx) {
  const { e } = ctx;
  const nChars = Object.keys(ctx.chars).length, nLocs = Object.keys(ctx.locs).length;
  el.innerHTML = `
    <div class="card">
      <label>Title</label><input id="title" value="${esc(e.title)}" style="width:100%">
      <label>Story - a script (INT./EXT. headings, NAME: dialogue) or plain prose</label>
      <textarea id="story" class="story-text">${esc(e.story)}</textarea>
      <div class="row story-save"><button id="save">Save</button><span class="muted small" id="story-count"></span></div>
    </div>
    <div class="card story-flow">
      <div class="flow-steps">
        <button class="${e.scenes.length ? "" : "primary"}" id="plan-cards" title="Story spine (theme, arcs, setups and payoffs, continuity) and one card per scene">1. Write spine and scene cards</button>
        <button class="${e.spine && !e.shots.length ? "primary" : ""}" id="plan-shots" ${e.spine ? "" : "disabled title=\"Write the spine and scene cards first\""} title="Design the shots from the cards, then a script-supervisor review">2. Plan shots from the cards</button>
        ${e.shots.length ? `<a class="btn primary" href="#/s/${ctx.sid}/e/${ctx.eid}/shots">Review ${pl(e.shots.length, "shot")} →</a>` : ""}
      </div>
      <div class="row flow-more">
        <label class="row small" style="margin:0;gap:6px">Write with ${textPicker("plan-model", "planner", "Model that writes the bible draft, scenes and shots")}</label>
        <button id="draft">Draft bible from story</button>
        <button id="plan" title="The older one-step planner">Quick plan</button>
        <span class="muted small grow">Bible: ${nChars} characters, ${nLocs} locations. The planner may only use bible ids.</span>
      </div>
    </div>
    ${!nLocs ? `<div class="notice">The bible has no locations yet. Use "Draft bible from story", review the drafts, then plan.</div>` : ""}
    ${e.spine ? `<h2>Story spine</h2><div class="card"><b>${esc(e.spine.logline || "")}</b><div class="muted">Theme: ${esc(e.spine.theme || "")}</div>
      ${(e.spine.characters || []).map((c) => `<div class="small"><b>${esc(charName(ctx, c.id))}</b> wants ${esc(c.want)}; needs ${esc(c.need)}${c.secret ? `; secret: ${esc(c.secret)}` : ""}. <span class="muted">${esc(c.arc || "")}</span></div>`).join("")}
      ${(e.spine.setups || []).length ? `<h3>Setups and payoffs</h3>${e.spine.setups.map((x) => `<div class="small"><b>${esc(x.what)}</b>: ${esc(x.planted)} <span class="muted">→</span> ${esc(x.payoff)}</div>`).join("")}` : ""}</div>` : ""}
    ${e.scenes.length ? `<h2>Scene cards</h2>${e.scenes.map((s) => `<div class="card"><b>${esc(s.id)}</b> · ${esc(ctx.locs[s.location_id]?.name || s.location_id)} ${esc(s.time_of_day || "")}${s.turn ? ` <span class="chip">${esc(s.turn)}</span>` : ""}
      <div class="muted">${esc(s.summary)}</div>${s.purpose ? `<div class="small"><b>Purpose:</b> ${esc(s.purpose)}</div>` : ""}
      ${(s.entrances || []).length || (s.exits || []).length ? `<div class="small muted">In: ${esc((s.entrances || []).join("; ") || "-")} · Out: ${esc((s.exits || []).join("; ") || "-")}</div>` : ""}
      <div class="small muted">${(s.characters || []).map((c) => esc(charName(ctx, c.id) + (c.outfit ? ` (${c.outfit})` : ""))).join(", ")}</div></div>`).join("")}` : ""}
    ${(e.review || []).length ? `<details><summary>Script supervisor: ${e.review.length} fixes</summary>${e.review.map((x) => `<div class="small">${esc(x)}</div>`).join("")}</details>` : ""}`;
  const save = () => PUT(`${ctx.base}/story`, { title: $("#title").value, story: $("#story").value });
  const count = () => { const t = $("#story").value.trim(); $("#story-count").textContent = t ? `${t.split(/\s+/).length} words` : ""; };
  count();
  $("#story").oninput = () => { App.dirty = true; count(); };
  $("#title").oninput = () => { App.dirty = true; };
  $("#save").onclick = async () => { await save(); App.dirty = false; toast("Saved."); };
  saveShortcut(() => $("#save").click());
  const writer = () => ({ model: $("#plan-model")?.value || "" });
  $("#draft").onclick = async () => { await save(); App.dirty = false; await startJob(`${ctx.base}/draft_bible`, writer()); };
  $("#plan-cards").onclick = async () => {
    if (e.scenes.length && !(await ask("Rewrite the spine and scene cards? The current cards are replaced.", "Rewrite", true))) return;
    await save(); App.dirty = false; await startJob(`${ctx.base}/plan_cards`, writer());
  };
  $("#plan-shots").onclick = async () => {
    if (e.shots.length && !(await ask("Replace the whole shot list with shots designed from these cards? Existing frames and takes stay on disk but are detached.", "Replace", true))) return;
    await startJob(`${ctx.base}/plan_shots`, writer());
  };
  $("#plan").onclick = async () => {
    if (e.shots.length && !(await ask("Replace the whole shot list? Existing frames and takes stay on disk but are detached.", "Replace", true))) return;
    await save(); App.dirty = false; await startJob(`${ctx.base}/plan`, writer());
  };
  App.refreshers.push(reload);
}

// ── 2. Shot list ────────────────────────────────────────────────────────
function charsToText(list) { return (list || []).map((c) => [c.id, c.position, c.outfit].filter(Boolean).join(":")).join(", "); }
function textToChars(t) {
  return t.split(",").map((x) => x.trim()).filter(Boolean).map((x) => { const [id, position, outfit] = x.split(":").map((y) => (y || "").trim()); return { id, position: position || "", outfit: outfit || "" }; });
}
// Dialogue lines: "speaker [acting direction]: line" (the direction is optional)
function dlgToText(list) { return (list || []).map((d) => `${d.speaker}${d.emotion ? ` [${d.emotion}]` : ""}: ${d.line}`).join("\n"); }
function textToDlg(t) {
  return t.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
    const i = l.indexOf(":"); if (i < 0) return { speaker: "", line: l };
    const head = l.slice(0, i).trim(), m = head.match(/^(.*?)\s*\[([^\]]*)\]$/);
    const d = { speaker: (m ? m[1] : head).trim(), line: l.slice(i + 1).trim() };
    if (m && m[2].trim()) d.emotion = m[2].trim();
    return d;
  });
}

async function tabShots(el, ctx) {
  const shots = JSON.parse(JSON.stringify(ctx.e.shots));
  const locIds = Object.keys(ctx.locs);
  // Shot cards fold to a one-line summary. Phones start folded, desktops open; the choice survives re-renders.
  const open = new Set(matchMedia("(max-width: 700px)").matches ? [] : shots);
  const summary = (sh) => {
    const who = (sh.characters || []).map((c) => charName(ctx, c.id)).join(", ");
    const n = (sh.dialogue || []).length;
    return `<span class="sr-sum">${esc(sh.framing || "")}${who ? " · " + esc(who) : ""}${n ? ` · ${pl(n, "line")}` : ""}</span>
      <span class="sr-sum-action">${esc(sh.action || "No action yet")}</span>`;
  };
  const sel = (f, i, v, opts, labels) => `<select data-i="${i}" data-f="${f}">${opts.map((o, k) => `<option value="${esc(o)}" ${o === (v || "") ? "selected" : ""}>${esc(labels ? labels[k] : o || "inherit")}</option>`).join("")}</select>`;
  const render = () => {
    let lastScene = null;
    const sceneById = Object.fromEntries(ctx.e.scenes.map((s) => [s.id, s]));
    el.innerHTML = `
      <div class="toolbar"><div class="tb-group"><button class="primary" id="save">Save shot list</button><button id="add">Add shot</button></div>
        <div class="tb-group"><button id="cont" title="master per scene, punch-ins for closer shots, reuse for returns to wide">Auto continuity</button></div>
        ${shots.some((x) => !x.first_frame) ? `<div class="tb-group"><button id="block" title="Write first-frame blocking for shots that have none, keeping their action and dialogue (uses the re-plan model)">Add blocking to ${pl(shots.filter((x) => !x.first_frame).length, "shot")}</button></div>` : ""}
        <div class="tb-group"><label class="row small" style="margin:0;gap:6px">Re-plan scenes with ${textPicker("replan-model", "planner", "Model used by re-plan this scene")}</label></div>
        <span class="grow"></span>${gateBtn(ctx, "shots", "shot list")}</div>
      <div class="list-bar"><button id="fold-all">${open.size ? "Collapse all" : "Expand all"}</button>
        ${ctx.e.scenes.length > 1 ? `<select id="jump-scene" aria-label="Jump to scene"><option value="">Jump to scene…</option>${[...new Set(shots.map((x) => x.scene_id))].map((id) => `<option value="${esc(id)}">${esc(id)} · ${esc(sceneById[id]?.summary || "")}</option>`).join("")}</select>` : ""}
        <span class="muted small">${pl(shots.length, "shot")} · ${fmtSec(shots.reduce((a, x) => a + (+x.duration_s || 0), 0))}</span></div>
      <p class="muted small" style="margin:0 0 16px">Characters: <code>id:position:outfit</code>, comma separated. Dialogue: one <code>id [acting direction]: line</code> per line (the direction is optional). Editing a duration locks it.</p>
      <div class="shot-rows">
      ${shots.map((sh, i) => {
        let head = "";
        if (sh.scene_id !== lastScene) {
          lastScene = sh.scene_id; const sc = sceneById[sh.scene_id];
          head = `<div class="scene-head" id="scene-${esc(sh.scene_id || "none")}"><b>${esc(sh.scene_id || "no scene")}</b><span>${sc ? esc(sc.summary) : ""}</span>
            ${sc ? `<button class="link" data-replan="${esc(sc.id)}">re-plan this scene</button>` : ""}</div>`;
        }
        const f = (label, html, cls = "") => `<div class="sr-f ${cls}"><label>${label}</label>${html}</div>`;
        const isOpen = open.has(sh);
        return head + `<article class="shot-row ${isOpen ? "" : "folded"}">
          <header class="sr-head"><button class="sr-toggle" data-fold="${i}" aria-expanded="${isOpen}" aria-label="${isOpen ? "Collapse" : "Expand"} ${esc(sh.id || "new shot")}">${ic("chev")}</button><b class="sr-id" data-fold="${i}">${esc(sh.id || "new")}</b>
            ${sh._stale ? `<span class="chip warn">stale</span>` : ""}${sh.frame?.locked ? `<span class="chip good">frame locked</span>` : ""}
            <span class="muted small ${+sh.duration_s ? "" : "warn-text"}" title="${+sh.duration_s ? "" : "No duration yet: voice the dialogue or set one"}">${fmtDur(sh.duration_s)}</span><span class="grow"></span>
            <div class="row sr-acts"><button class="link" data-up="${i}" title="Move up" aria-label="Move shot up">↑</button><button class="link" data-down="${i}" title="Move down" aria-label="Move shot down">↓</button>
              <button class="link" data-dup="${i}" title="Duplicate below">split</button><button class="link" data-merge="${i}" title="Merge with next">merge</button>
              <button class="link danger" data-del="${i}">delete</button></div></header>
          ${isOpen ? "" : `<div class="sr-summary" data-fold="${i}">${summary(sh)}</div>`}
          <div class="sr-body">
          <div class="sr-grid">
            ${f("Framing", sel("framing", i, sh.framing, FRAMINGS))}
            ${f("Camera", `<input data-i="${i}" data-f="camera" value="${esc(sh.camera)}" placeholder="e.g. static, slow push-in">`)}
            ${f("Location", sel("location_id", i, sh.location_id, locIds, locIds.map((l) => ctx.locs[l].name)))}
            ${f("Time of day", `<input data-i="${i}" data-f="time_of_day" value="${esc(sh.time_of_day)}" placeholder="e.g. night">`)}
            ${f("Characters <span class='muted small'>id:position:outfit</span>", `<input data-i="${i}" data-f="characters" value="${esc(charsToText(sh.characters))}" placeholder="sam:left">`, "span2")}
            ${f("Action", `<textarea data-i="${i}" data-f="action" rows="3">${esc(sh.action)}</textarea>`, "span2 tall")}
            ${f("Dialogue <span class='muted small'>one id: line per row</span>", `<textarea data-i="${i}" data-f="dialogue" rows="3">${esc(dlgToText(sh.dialogue))}</textarea>`, "span2")}
            ${f("Sound", `<input data-i="${i}" data-f="sound" value="${esc(sh.sound)}" placeholder="sound">`, "span2")}
            ${f(`Duration ${sh.duration_locked ? `<button class="link" data-unlock="${i}" title="Locked - click to auto-compute">🔒 locked</button>` : ""}`, `<input data-i="${i}" data-f="duration_s" type="number" step="0.1" value="${Math.round((+sh.duration_s || 0) * 100) / 100}" aria-label="Duration in seconds">`)}
            ${f("Start", sel("start_mode", i, sh.start_mode, ["new", "punch_in", "reuse", "chain"], ["New frame (master)", "Punch-in from scene master", "Reuse master angle", "Chain: last frame of prev take"]))}
            ${f("Frame source", sel("frame_source", i, sh.frame_source, frameSourceValues(sh.frame_source), frameSourceValues(sh.frame_source).map((v) => v ? sourceLabel(v) : "Inherit (" + sourceLabel(sh._source) + ")")))}
            ${f("Video engine", sel("engine", i, sh.engine, ["", ...VIDEO_ENGINES], ["Project default", ...VIDEO_ENGINES.map(x => ENGINE_NAMES[x])]))}
          </div>
          <details class="sr-blocking"><summary>First-frame blocking${sh.first_frame ? " · planned" : " · legacy prompt"}</summary>
            ${sh.first_frame ? `<p class="muted small">The opening instant controls the still image. Action describes what happens afterward. Each character appears once in the declared zone.</p>
            <div class="sr-grid">${["camera", "layout", "moment", "lighting"].map(key => f(key[0].toUpperCase() + key.slice(1), `<textarea data-i="${i}" data-f="first_frame.${key}" rows="2">${esc(sh.first_frame[key])}</textarea>`, "span2")).join("")}</div>
            ${sh.first_frame.subjects.map((subject, index) => `<h4>${esc(charName(ctx, subject.id))}</h4><div class="sr-grid">${["position", "depth", "space", "zone", "pose", "facing", "gaze_target", "gaze_direction", "expression", "visibility", "wardrobe_state"].map(key => f(key.replaceAll("_", " "), `<input data-i="${i}" data-f="first_frame.subjects.${index}.${key}" value="${esc(subject[key])}">`)).join("")}</div>`).join("")}
            ${sh.first_frame.props.map((prop, index) => `<h4>${esc(prop.id)}</h4><div class="sr-grid">${["placement", "state", "holder"].map(key => f(key, `<input data-i="${i}" data-f="first_frame.props.${index}.${key}" value="${esc(prop[key])}">`)).join("")}</div>`).join("")}
            <label>Required details · one per line</label><textarea data-i="${i}" data-f="first_frame.constraints" rows="3">${esc(sh.first_frame.constraints.join("\n"))}</textarea>`
            : `<p class="muted small">This shot uses a legacy prompt. Re-plan the scene to create explicit camera, position, gaze, prop and visibility plans.</p>`}
          </details>
          <details class="sr-prompts"><summary>Prompts${sh.image_prompt_override || sh.motion_prompt_override ? " (overridden)" : ""}</summary>
            <label>Image prompt (auto)</label><pre class="prompt">${esc(sh._image_prompt)}</pre>
            <label>Extra for image${sh.first_frame ? " · edit the blocking plan instead" : ""}</label><input data-i="${i}" data-f="image_prompt_extra" value="${esc(sh.image_prompt_extra)}" ${sh.first_frame ? "disabled" : ""}>
            <label>Motion prompt (auto, ${esc(sh._engine)})</label><pre class="prompt">${esc(sh._motion_prompt)}</pre>
            <label>Extra for motion</label><input data-i="${i}" data-f="motion_prompt_extra" value="${esc(sh.motion_prompt_extra)}">
            <label>Full image prompt override${sh.first_frame ? " · controlled by blocking" : " (replaces auto)"}</label><textarea data-i="${i}" data-f="image_prompt_override" ${sh.first_frame ? "disabled" : ""}>${esc(sh.image_prompt_override)}</textarea>
            <label>Full motion prompt override</label><textarea data-i="${i}" data-f="motion_prompt_override">${esc(sh.motion_prompt_override)}</textarea>
          </details></div></article>`;
      }).join("")}</div>`;
    bulkSelect(el, "[data-del]", async targets => {
      const indices = targets.map(t => +t.dataset.del).sort((a,b) => b-a);
      indices.forEach(i => shots.splice(i, 1)); App.dirty = true;
    }, render, "shots");
    const jump = $("#jump-scene", el);
    if (jump) jump.onchange = () => { const h = document.getElementById("scene-" + jump.value); jump.value = ""; if (h) h.scrollIntoView({ behavior: "smooth", block: "start" }); };
  };
  render();
  const payload = () => shots.map((sh) => ({ ...sh }));
  el.oninput = (ev) => {
    const t = ev.target; const i = t.dataset.i, f = t.dataset.f; if (i == null || !f) return;
    const sh = shots[+i];
    if (f.startsWith("first_frame.")) {
      const keys = f.split("."), key = keys.pop(); let target = sh;
      for (const part of keys) target = target[part];
      target[key] = key === "constraints" ? t.value.split("\n").map(x => x.trim()).filter(Boolean) : t.value;
    }
    else if (f === "characters") sh.characters = textToChars(t.value);
    else if (f === "dialogue") sh.dialogue = textToDlg(t.value);
    else if (f === "duration_s") { sh.duration_s = parseFloat(t.value) || 0; sh.duration_locked = true; }
    else sh[f] = t.value;
    App.dirty = true;
  };
  saveShortcut(() => $("#save", el)?.click());
  el.onchange = el.oninput;
  el.onclick = async (ev) => {
    const t = ev.target, ds = t.dataset;
    const fold = t.closest("[data-fold]");
    if (fold) { const sh = shots[+fold.dataset.fold]; open.has(sh) ? open.delete(sh) : open.add(sh); return render(); }
    if (t.id === "fold-all") { if (open.size) open.clear(); else shots.forEach((x) => open.add(x)); return render(); }
    const move = (i, j) => { if (j < 0 || j >= shots.length) return; [shots[i], shots[j]] = [shots[j], shots[i]]; App.dirty = true; render(); };
    if (ds.up) move(+ds.up, +ds.up - 1);
    if (ds.down) move(+ds.down, +ds.down + 1);
    if (ds.dup) { const src = shots[+ds.dup]; const copy = { ...JSON.parse(JSON.stringify(src)), id: "", _copy_of: src.id, dialogue: [], duration_locked: false }; shots.splice(+ds.dup + 1, 0, copy); open.add(copy); App.dirty = true; render(); }
    if (ds.merge) {
      const i = +ds.merge; if (i + 1 >= shots.length) return;
      const a = shots[i], b = shots[i + 1];
      a.action = [a.action, b.action].filter(Boolean).join(" Then "); a.dialogue = (a.dialogue || []).concat(b.dialogue || []);
      a.duration_locked = false; shots.splice(i + 1, 1); App.dirty = true; render();
    }
    if (ds.del) { if (await ask(`Delete ${shots[+ds.del].id || "this shot"}?`, "Delete", true)) { shots.splice(+ds.del, 1); App.dirty = true; render(); } }
    if (ds.unlock) { shots[+ds.unlock].duration_locked = false; App.dirty = true; render(); }
    if (ds.replan) { if (await ask(`Re-plan all shots of ${ds.replan}? Its current shots are replaced.`, "Re-plan", true)) startJob(`${ctx.base}/replan/${ds.replan}`, { model: $("#replan-model")?.value || "" }); }
    if (t.id === "add") { shots.push({ id: "", scene_id: shots.at(-1)?.scene_id || "S01", location_id: shots.at(-1)?.location_id || locIds[0] || "", framing: "medium", camera: "", characters: [], action: "", dialogue: [], sound: "", action_seconds: 3, start_mode: "new", frame_source: "", engine: "" }); open.add(shots.at(-1)); App.dirty = true; render(); el.querySelector(".shot-row:last-of-type")?.scrollIntoView({ behavior: "smooth", block: "center" }); }
    if (t.id === "block") { if (App.dirty) await PUT(`${ctx.base}/shots`, payload()); App.dirty = false; return startJob(`${ctx.base}/block_shots`, { model: $("#replan-model")?.value || "" }); }
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

function framesStatus(ctx) {
  const needs = ctx.e.shots.filter((s) => s.start_mode !== "chain" && s.frame.selected == null).length;
  return { needs, html: `${needs ? `<span class="chip warn">${pl(needs, "shot")} without a frame</span>` : `<span class="chip good">${ic("check")} Every shot has a frame</span>`}
      <span class="muted small">${ctx.e._settings.candidates_per_shot} candidates per render. Chained shots get their frame from the previous take in Takes.</span>` };
}

async function tabFrames(el, ctx) {
  const shots = ctx.e.shots;
  const needs = shots.filter((s) => s.start_mode !== "chain" && s.frame.selected == null).length;
  el.innerHTML = `
    ${!ctx.e.gates.shots ? `<div class="notice">The shot list isn't approved yet. You can still render frames, but edits to shots later will mark them stale.</div>` : ""}
    <div class="toolbar">
      <div class="tb-group">
        <button class="${needs ? "primary" : ""}" id="render-missing" ${needs ? "" : "disabled title=\"Every shot already has a frame\""}>Render missing frames</button>
        <select id="src-override" aria-label="Frame source"><option value="">Per-shot source</option><option value="flux_lora">Force Flux + LoRA</option><option value="zimage">Force Z-Image</option><option value="qwen_refs">Force Z-Image + Qwen identity</option>${imageOptions("Force ")}</select>
        <label class="row small" style="margin:0;gap:6px">Check with ${textPicker("check-model", "vision", "Model that checks rendered frames")}</label>
      </div>
      <div class="tb-group">
        <button id="render-all">Re-render unlocked</button><button id="vision">Vision check</button>
        <label class="btn file-pick" title="Files named by shot id, e.g. S01_SH02.png">Bulk upload…<input type="file" id="bulk" multiple accept="image/*" aria-label="Bulk upload frames named by shot id"></label>
      </div>
      <span class="grow"></span>
      ${gateBtn(ctx, "frames", "frames")}
    </div>
    <p class="status-line">${framesStatus(ctx).html}</p>
    <p class="muted small">Z-Image + Qwen identity: Z-Image Turbo composes the shot from text, then Qwen Edit applies the reference faces while preserving pose, gaze and framing.</p>
    <div class="list-bar"><label class="row small" style="margin:0"><input type="checkbox" id="only-needs"> Only shots that still need a frame</label></div>
    <div id="frames-list">${shots.map((sh) => frameCard(ctx, sh)).join("")}</div>`;
  ctx.repaint = (ids) => {
    repaintCards($("#frames-list", el), ctx, ids, frameCard);
    const s = framesStatus(ctx), b = $("#render-missing", el);
    $(".status-line", el).innerHTML = s.html; b.disabled = !s.needs; b.className = s.needs ? "primary" : ""; b.title = s.needs ? "" : "Every shot already has a frame";
  };
  const checker = () => $("#check-model")?.value || "";
  $("#only-needs").onchange = (ev) => $("#frames-list", el).classList.toggle("only-needs", ev.target.checked);
  $("#render-missing").onclick = () => startJob(`${ctx.base}/frames`, { source: $("#src-override").value, check_model: checker() });
  $("#render-all").onclick = async () => { if (await ask("Render new candidates for every unlocked shot?", "Render")) startJob(`${ctx.base}/frames`, { force: true, source: $("#src-override").value, check_model: checker(), shot_ids: shots.filter((s) => !s.frame.locked).map((s) => s.id) }); };
  $("#vision").onclick = () => startJob(`${ctx.base}/vision`, { model: checker() });
  $("#bulk").onchange = async () => {
    const files = $("#bulk").files; if (!files.length) return;
    const fd = new FormData(); [...files].forEach((f) => fd.append("files", f));
    const r = await FORM(`${ctx.base}/bulk_upload`, fd);
    toast(`Matched ${r.matched.length}${r.skipped.length ? `, skipped ${r.skipped.join(", ")}` : ""}`); reload();
  };
  el.onclick = async (ev) => {
    const t = ev.target, ds = t.dataset;
    if (ds.zoom) zoom(ds.zoom);
    if (t.classList.contains("shot-desc")) t.classList.toggle("open");
    if (ds.gate) setGate(ctx, ds.gate, ds.val === "1");
    if (ds.pick != null && ds.shot) { await POST(`${ctx.base}/shots/${ds.shot}/frame`, { select: +ds.pick }); reload(); }
    if (ds.rm != null && ds.shot) { await POST(`${ctx.base}/shots/${ds.shot}/frame`, { remove: +ds.rm }); reload(); }
    if (ds.qwenface != null && ds.shot) startJob(`${ctx.base}/shots/${ds.shot}/frame_correction`, { index: +ds.qwenface });
    if (ds.refine && ds.shot) startJob(`${ctx.base}/shots/${ds.shot}/refine`, { index: +ds.idx, engine: ds.refine, denoise: parseFloat($(`#dn-${ds.shot}`)?.value || "0.3") });
    if (ds.lock && ds.shot) { await POST(`${ctx.base}/shots/${ds.shot}/frame`, { lock: ds.lock === "1" }); reload(); }
    if (ds.render && ds.shot) startJob(`${ctx.base}/frames`, { shot_ids: [ds.shot], force: true, source: $(`#src-${ds.shot}`).value, check_model: checker() });
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
  const needs = !chained && sh.frame.selected == null;
  return `<div class="shot-card ${sh.frame.locked ? "locked" : ""} ${needs ? "needs" : ""}" data-card="${esc(sh.id)}">
    <div class="shot-head"><span class="id">${esc(sh.id)}</span><span class="muted">${esc(sh.framing)} · ${who || "no characters"} · ${fmtDur(sh.duration_s)}</span>
      <span class="chip accent">${sourceLabel(sh._source)}</span>${sh._loras.length ? `<span class="chip" title="${esc(sh._loras.map((l) => l.name + " @" + l.strength).join("\n"))}">${sh._loras.length} LoRA</span>` : ""}
      ${sh._stale ? `<span class="chip warn" title="bible changed since these frames were made">stale</span>` : ""}
      ${sh.frame.locked ? `<span class="chip good">locked</span>` : ""}${needs ? `<span class="chip warn">pick a frame</span>` : ""}<span class="grow"></span>
      ${chained ? "" : `<select id="src-${sh.id}"><option value="">${sourceLabel(sh._source)}</option><option value="flux_lora">Flux + LoRA</option><option value="zimage">Z-Image</option><option value="qwen_refs">Z-Image + Qwen identity</option>${imageOptions()}</select>
      <button data-render="1" data-shot="${sh.id}">Render ${ctx.e._settings.candidates_per_shot} more</button>
      <label class="btn file-pick" style="margin:0;color:var(--ink)">Upload photo<input type="file" accept="image/*,.heic,.heif" data-upframe="${sh.id}" aria-label="Upload photo for ${esc(sh.id)}"></label>`}
      <button data-lock="${sh.frame.locked ? 0 : 1}" data-shot="${sh.id}">${sh.frame.locked ? "Unlock" : "Lock"}</button></div>
    <div class="muted small shot-desc" title="Click to show all">${esc(sh.action)}${(sh.dialogue || []).length ? " · " + sh.dialogue.map((d) => esc(charName(ctx, d.speaker) + ": " + d.line)).join(" / ") : ""}</div>
    ${chained ? `<p class="muted">Chained: this shot starts from the last frame of ${esc("the previous shot's")} selected take (made at the Takes stage).</p>` : ""}
    <details class="frame-opts"><summary>Prompt, end frame &amp; refine${sh.end_frame ? " · end frame set" : ""}</summary>
      ${sh.first_frame ? `<p class="muted small">This prompt is compiled from the opening-frame blocking in Shot list. Later actions and portrait poses do not set the opening positions.</p>` : ""}
      <pre class="prompt">${esc(sh._image_prompt)}</pre>
      <div class="row small" style="margin:6px 0">End frame (optional, LTX):
        ${sh.end_frame ? `<img src="${media(sh.end_frame)}" style="height:44px;border-radius:4px" data-zoom="${media(sh.end_frame)}"><button class="link" data-clearend="1" data-shot="${sh.id}">clear</button>` : `<span class="muted">none</span>`}
        <label class="btn file-pick" style="margin:0">Upload end frame<input type="file" accept="image/*,.heic,.heif" data-upend="${sh.id}" aria-label="Upload end frame for ${esc(sh.id)}"></label>
        <span class="grow"></span>refine strength <input id="dn-${sh.id}" type="number" min="0.1" max="0.8" step="0.05" value="0.3" style="width:64px"></div>
    </details>
    <div class="cands"><div class="cands-in">${cands.map((c, k) => `<div class="cand ${sh.frame.selected === k ? "sel" : ""}">
      <div class="frame"><img src="${media(c.file)}" data-zoom="${media(c.file)}" loading="lazy"></div>
      <div class="meta"><span class="code">${esc(sh.id)}/${String.fromCharCode(65 + k)}</span>${sh.frame.selected === k ? `<span class="pick">picked</span>` : ""}<span>${sourceLabel(c.source)}${c.plate ? ", on location plate" : ""}</span> ${checkChip(c)}</div>
      <div class="acts">${sh.frame.selected === k ? ""  : `<button data-pick="${k}" data-shot="${sh.id}">Pick this</button>`}
        <button data-qwenface="${k}" data-shot="${sh.id}" title="Correct this frame using the uploaded character portrait">Correct face</button>
        <details class="cand-more"><summary aria-label="More actions for ${esc(sh.id)}/${String.fromCharCode(65 + k)}">More</summary><div class="cand-pop">
          ${c.composition ? `<button data-zoom="${media(c.composition)}">View composition before identity</button>` : ""}
          <button data-refine="flux_lora" data-idx="${k}" data-shot="${sh.id}" title="img2img with the character LoRA">Refine with Flux + LoRA</button>
          <button data-refine="zimage" data-idx="${k}" data-shot="${sh.id}" title="img2img to match style">Refine with Z-Image</button>
          <button data-asend="${esc(c.file)}" data-shot="${sh.id}">Use as end frame</button></div></details>
        <button class="danger" data-rm="${k}" data-shot="${sh.id}" aria-label="Delete candidate ${String.fromCharCode(65 + k)}" title="Delete">✕</button></div></div>`).join("") || `<p class="muted" style="padding:20px">No candidates yet.</p>`}</div></div></div>`;
}

// ── 4. Animatic ─────────────────────────────────────────────────────────
async function tabAnimatic(el, ctx) {
  const items = ctx.e.shots.map((sh) => {
    const f = selFrame(sh) || sh.frame.candidates[0];
    const cap = (sh.dialogue || []).map((d) => `${charName(ctx, d.speaker)}: ${d.line}`).join("  /  ");
    return { id: sh.id, src: f ? media(f.file) : "", dur: +sh.duration_s || 2, cap, action: sh.action, missing: !f };
  });
  const total = items.reduce((a, b) => a + b.dur, 0);
  const nMissing = items.filter((i) => i.missing).length;
  const v = ctx.e._settings.video || {}, ratio = v.width && v.height ? `${v.width} / ${v.height}` : "16 / 9";
  const starts = items.map((_, i) => items.slice(0, i).reduce((a, b) => a + b.dur, 0));
  el.innerHTML = `
    <div class="row anim-bar"><span class="muted">${fmtSec(total)} · ${pl(items.length, "shot")}</span>${nMissing ? `<span class="chip warn">${pl(nMissing, "shot")} without a frame</span>` : ""}
      <span class="grow"></span>
      <button id="export">Export animatic.mp4</button>${ctx.e.outputs?.animatic ? `<a href="${media(ctx.e.outputs.animatic)}" target="_blank">open animatic.mp4</a>` : ""}
      ${gateBtn(ctx, "animatic", "pacing")}</div>
    <div class="anim-stage">
      <div class="player ${v.height > v.width ? "portrait" : ""}" id="player" style="aspect-ratio:${ratio};--ar:${v.width && v.height ? v.width / v.height : 16 / 9}" title="Click to play or pause"><img id="pimg" alt=""><div class="tag" id="ptag"></div><div class="cap" id="pcap"></div>
        <div class="pbar"><div id="pbar"></div></div></div>
      <div class="anim-controls">
        <button id="prev" aria-label="Previous shot" title="Previous shot (←)">${ic("left")}</button>
        <button class="primary" id="play" title="Play / pause (Space)">Play</button>
        <button id="next" aria-label="Next shot" title="Next shot (→)">${ic("chev")}</button>
        <button id="restart" title="Back to the first shot (Home)">Restart</button>
        <label class="row small" style="margin:0"><input type="checkbox" id="showact"> show action text</label>
        <span class="muted small" id="ptime"></span>
      </div>
    </div>
    <div class="timeline" id="tl">${items.map((it, k) => `<div data-k="${k}" class="${it.missing ? "missing" : ""}" style="flex:${it.dur}" title="${esc(it.id)} ${fmtDur(it.dur)}">${esc(it.id.replace(/^.*_SH/, "SH"))}</div>`).join("")}</div>
    <p class="muted small">Change durations in the Shot list tab (editing a duration locks it).</p>`;
  let k = 0, timer = null, playing = false;
  const show = (i) => {
    if (!el.isConnected) { playing = false; clearTimeout(timer); return; }  // left the page while playing
    k = i; const it = items[i]; if (!it) return;
    $("#pimg").src = it.src || ""; $("#pimg").style.visibility = it.src ? "visible" : "hidden";
    $("#ptag").textContent = `${it.id} · ${fmtDur(it.dur)}${it.missing ? " · no frame" : ""}`;
    $("#pcap").textContent = $("#showact").checked && it.action ? `${it.cap ? it.cap + "\n" : ""}[${it.action}]` : it.cap;
    $$("#tl div").forEach((d, j) => d.classList.toggle("on", j === i));
    $("#ptime").textContent = `${k + 1} / ${items.length} · ${fmtSec(starts[k])} of ${fmtSec(total)}`;
    $("#pbar").style.width = `${total ? (starts[k] / total) * 100 : 0}%`;
  };
  const step = () => { if (!playing) return; timer = setTimeout(() => { if (k + 1 < items.length) { show(k + 1); step(); } else { playing = false; $("#play").textContent = "Play"; } }, items[k].dur * 1000); };
  const player = $("#player");
  const go = (i) => { if (i < 0 || i >= items.length) return; clearTimeout(timer); show(i); if (playing) step(); };
  const toggle = () => { playing = !playing; $("#play").textContent = playing ? "Pause" : "Play"; clearTimeout(timer); if (playing) step(); };
  $("#play").onclick = toggle;
  $("#player").onclick = toggle;
  $("#prev").onclick = () => go(k - 1);
  $("#next").onclick = () => go(k + 1);
  $("#restart").onclick = () => go(0);
  $("#tl").onclick = (e) => { const i = e.target.dataset.k; if (i != null) go(+i); };
  // Swipe on the player for the previous/next shot.
  let sx = null;
  $("#player").addEventListener("touchstart", (e) => { sx = e.touches[0].clientX; }, { passive: true });
  $("#player").addEventListener("touchend", (e) => { if (sx == null) return; const dx = e.changedTouches[0].clientX - sx; sx = null; if (Math.abs(dx) > 40) { e.preventDefault(); go(k + (dx < 0 ? 1 : -1)); } });
  const keys = (e) => {
    if (!player.isConnected) return document.removeEventListener("keydown", keys);
    if (e.target.closest("input, textarea, select") || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === "ArrowRight") go(k + 1); else if (e.key === "ArrowLeft") go(k - 1); else if (e.key === "Home") go(0);
    else if (e.key === " ") { if (e.target.closest("button")) return; toggle(); } else return;
    e.preventDefault();
  };
  document.addEventListener("keydown", keys);
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
    <div id="take-timeline"></div>
    <details class="take-library-panel"><summary>Take library &amp; batch generation</summary>
    <div class="card row">
      <label class="row" style="margin:0">takes per shot <input id="ntakes" type="number" min="1" max="8" value="${ctx.e._settings.takes_per_shot}" style="width:60px"></label>
      <label>Video model <select id="take-model"><option value="">Per-shot / project default</option>${models.map(m => `<option value="${esc(m.id)}" data-engine="${m.engine}" ${m.available === false ? `disabled title="${esc(m.note)}"` : ""}>${esc(m.name)}${m.available === false ? " (not installed)" : ""}</option>`).join("")}</select></label>
      <button class="primary" id="render-missing">Render shots without takes</button>
      <label class="row small check-all" style="margin:0"><input type="checkbox" id="chk-all" data-chk-all aria-label="Select all shots for batch render"> All shots</label>
      <button id="render-checked" title="Render only the shots ticked on their cards">Render checked shots</button>
      <span class="grow"></span>
      <button class="danger" id="delete-checked" disabled title="Remove the takes of the ticked shots from their cards. The video files stay on disk.">Delete checked</button>
      <button class="danger" id="delete-all" title="Remove every take from every shot. The video files stay on disk.">Delete all takes</button>
      ${gateBtn(ctx, "takes", "takes")}
      <p class="muted small" style="flex-basis:100%;margin:2px 0 0">Measured speed: ${Object.entries(spf).map(([k, v]) => `${k} ${v} s/frame`).join(", ") || "not measured yet"} · est. ${fmtSec(ctx.e._estimate_s)} for all</p>
    </div>
    <h2>Take library</h2><p class="muted small">Generate candidates here, then add them to the timeline or replace its selected clip. Library picks and source files stay available.</p>
    <div id="take-library">${shots.map((sh) => takeCard(ctx, sh)).join("")}</div></details>`;
  // "All shots" ticks every shot card; the button shows how many are ticked. Ticks survive in-place updates of the cards.
  const boxes = () => $$("input[data-chk]", el);
  const refreshChecks = () => {
    const all = $("#chk-all", el), count = boxes().filter((b) => b.checked).length;
    all.checked = count > 0 && count === boxes().length; all.indeterminate = count > 0 && count < boxes().length;
    $("#render-checked", el).textContent = count ? `Render checked shots (${count})` : "Render checked shots";
    const del = $("#delete-checked", el); del.disabled = !count; del.textContent = count ? `Delete checked (${count})` : "Delete checked";
  };
  $("#chk-all", el).onchange = (ev) => { boxes().forEach((b) => { b.checked = ev.target.checked; }); refreshChecks(); };
  el.addEventListener("change", (ev) => { if (ev.target.matches("input[data-chk]")) refreshChecks(); });
  // Delete takes of the ticked shots, or of all shots. Locked shots are skipped. Only the lists change: the files stay on disk.
  const deleteTakes = async (ids) => {
    const items = {}; let takes = 0, locked = 0;
    for (const sh of ctx.e.shots) {
      if (ids && !ids.includes(sh.id)) continue;
      const count = (sh.takes.items || []).length;
      if (!count) continue;
      if (sh.takes.locked) { locked++; continue; }
      items[sh.id] = [...Array(count).keys()]; takes += count;
    }
    const shotCount = Object.keys(items).length;
    if (!takes) return toast(locked ? `${pl(locked, "shot")} with takes ${locked === 1 ? "is" : "are"} locked. Unlock ${locked === 1 ? "it" : "them"} first.` : "There are no takes to delete.", true);
    const note = locked ? ` ${pl(locked, "locked shot")} will be skipped.` : "";
    if (!(await ask(`Delete ${pl(takes, "take")} from ${pl(shotCount, "shot")}? The video files stay on disk, but the shots lose these takes, the Takes approval is reopened, and the rendered films listed in Cut are cleared (you can render again).${note}`, `Delete ${pl(takes, "take")}`, true))) return;
    try {
      await POST(`${ctx.base}/delete_assets`, { kind: "take", items });
      boxes().forEach((b) => { b.checked = false; }); refreshChecks();
      toast(`Deleted ${pl(takes, "take")}.`); await reload();
    } catch { /* the server's reason is already shown */ }
  };
  $("#delete-checked", el).onclick = () => deleteTakes($$("input[data-chk]:checked", el).map((x) => x.dataset.chk));
  $("#delete-all", el).onclick = () => deleteTakes(null);
  const n = () => parseInt($("#ntakes").value) || 1;
  const modelChoice = () => {
    const option = $("#take-model").selectedOptions[0];
    return { engine: option.dataset.engine || "", model: option.value };
  };
  ctx.repaint = (ids) => {
    const ticked = new Set($$("input[data-chk]:checked", el).map((b) => b.dataset.chk));
    repaintCards($("#take-library", el), ctx, ids, takeCard);
    $$("input[data-chk]", el).forEach((b) => { b.checked = ticked.has(b.dataset.chk); });
    refreshChecks();  // the "All shots" state and the count follow the restored ticks
  };
  const editor = await mountTakeTimeline($("#take-timeline"), ctx, shotId =>
    startJob(`${ctx.base}/takes`, {shot_ids: [shotId], takes: n(), force: true, ...modelChoice()}));
  $("#render-missing").onclick = () => startJob(`${ctx.base}/takes`, { takes: n(), ...modelChoice() });
  $("#render-checked").onclick = () => {
    const ids = $$("input[data-chk]:checked").map((x) => x.dataset.chk);
    if (!ids.length) return toast("Tick some shots first", true);
    startJob(`${ctx.base}/takes`, { shot_ids: ids, takes: n(), force: true, ...modelChoice() });
  };
  el.onclick = async (ev) => {
    const t = ev.target, ds = t.dataset;
    if (ds.timelineAdd != null && ds.shot) return editor.addTake(ds.shot, +ds.timelineAdd);
    if (ds.timelineReplace != null && ds.shot) return editor.addTake(ds.shot, +ds.timelineReplace, true);
    if (ds.timelineOverlay != null && ds.shot) return editor.addOverlay(ds.shot, +ds.timelineOverlay);
    if (editor.dirty && ds.shot) await editor.flush();
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
  return `<div class="shot-card ${sh.takes.locked ? "locked" : ""}" data-card="${esc(sh.id)}">
    <div class="shot-head"><input type="checkbox" data-chk="${sh.id}" aria-label="Select ${esc(sh.id)} for batch render" title="Select for batch render"><span class="id">${esc(sh.id)}</span>
      <span class="chip accent">${esc(sh._engine)}</span><span class="muted">${fmtDur(sh.duration_s)} · ${esc(sh.start_mode)}</span>
      ${f ? `<img src="${media(f.file)}" style="height:40px;border-radius:4px" data-zoom="${media(f.file)}">` : sh.start_mode === "chain" ? `<span class="chip">frame from previous take</span>` : `<span class="chip bad">no frame</span>`}
      ${sh.end_frame ? `<span class="chip">end frame</span>` : ""}<span class="grow"></span>
      <button data-more="1" data-shot="${sh.id}">Render more takes</button>
      <button data-lock="${sh.takes.locked ? 0 : 1}" data-shot="${sh.id}">${sh.takes.locked ? "Unlock" : "Lock"}</button>
      <details class="more-menu"><summary class="icon-btn" aria-label="More actions for ${esc(sh.id)}" title="More actions">${ic("dots")}</summary><div class="more-pop">
        <label for="face-${sh.id}" style="margin:6px 8px 2px">Face correction model</label>
        <select id="face-${sh.id}" style="margin:0 8px 6px;width:calc(100% - 16px)" aria-label="Face correction model"><option value="qwen_identity" ${ctx.e._settings.face_model === "qwen_identity" ? "selected" : ""}>Qwen Image Edit · every frame</option><option value="minimax_identity" ${ctx.e._settings.face_model === "minimax_identity" ? "selected" : ""}>Match character references · MiniMax H3</option><option value="keep" ${ctx.e._settings.face_model === "keep" ? "selected" : ""}>KEEP (main face, temporal)</option><option value="codeformer" ${ctx.e._settings.face_model === "codeformer" ? "selected" : ""}>CodeFormer (all faces)</option></select>
        <button data-next="1" data-shot="${sh.id}" title="Extract the last frame of the selected take as the next shot's first frame">Last frame → next shot's first frame</button></div></details></div>
    <details><summary>motion prompt</summary><pre class="prompt">${esc(sh._motion_prompt)}</pre></details>
    <div class="cands"><div class="cands-in">${items.map((t, k) => `<div class="cand ${sh.takes.selected === k || (sh.takes.selected == null && k === 0) ? "sel" : ""}">
      <div class="frame"><video playsinline preload="none" controls ${t.first_frame || f?.file ? `poster="${media(t.first_frame || f.file)}"` : ''} src="${media(t.file)}"></video></div>
      <div class="meta"><span class="code">${esc(sh.id)}/T${k + 1}</span>${sh.takes.selected === k || (sh.takes.selected == null && k === 0) ? `<span class="pick">picked</span>` : ""}<span>${esc(t.model || t.engine)}${t.face_correction ? " · " + esc(t.face_correction) + " corrected" : ""}, ${t.seconds}s, ${t.render_s}s render</span> ${(t.flags || []).map((x) => `<span class="chip warn">${esc(x)}</span>`).join(" ")}</div>
      <div class="acts">${sh.takes.selected === k ? "" : `<button data-pick="${k}" data-shot="${sh.id}">Pick this</button>`}
        <button data-timeline-add="${k}" data-shot="${sh.id}">Add to timeline</button>
        <button data-timeline-replace="${k}" data-shot="${sh.id}">Replace selected clip</button>
        <button data-timeline-overlay="${k}" data-shot="${sh.id}" title="Picture-in-picture / B-roll on top of the main track">Add as overlay</button>
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
    ${missing.length ? `<div class="notice">${pl(missing.length, "shot")} without takes will be left out: ${esc(missing.join(", "))}. <a href="#/s/${ctx.sid}/e/${ctx.eid}/takes">Render takes</a></div>` : ""}
    ${o.final ? `<section class="final-film"><h2>Final film ${o.runtime ? "· " + fmtSec(o.runtime) : ""}</h2>
      <video class="film-video" playsinline preload="metadata" controls src="${media(o.final)}#t=0.5" style="aspect-ratio:${e._settings.video.width} / ${e._settings.video.height}"></video>
      <div class="row" style="margin-top:8px"><a href="${media(o.final)}" download>final.mp4</a>${o.srt ? `<a href="${media(o.srt)}" download>subtitles (.srt)</a>` : ""}
        ${o.edl ? `<a href="${media(o.edl)}" download>edit list (.edl for DaVinci Resolve)</a>` : ""}${o.animatic ? `<a href="${media(o.animatic)}" download>animatic.mp4</a>` : ""}</div>
      <p class="muted small">In Resolve: import the takes folders as media, then File > Import > Timeline > the .edl.</p></section>` : `<div class="card empty-film"><b>No final film yet.</b> <span class="muted">Render it below once the takes are ready.</span></div>`}

    <div class="card render-card">
      ${e.timeline ? `<div class="row"><button class="primary" id="render-edit">${o.final ? "Re-render" : "Render"} saved timeline</button><span class="muted small grow">Uses the clip order, trims, mute settings, music placement and lip-sync choices saved in Takes. <a href="#/s/${ctx.sid}/e/${ctx.eid}/takes">Edit them in Takes</a>.</span></div>`
        : `<p class="muted small" style="margin-top:0">Each selected take is trimmed, scaled to ${e._settings.video.width}x${e._settings.video.height} @ ${e._settings.video.fps} fps and loudness-normalised, then joined. Wan takes (silent) get a silent track.</p>`}
    </div>
    <div class="card form-grid ${e.timeline ? 'hidden' : ''}">
      <div><label>Music or voice recording</label><input type="file" id="mfile" accept="audio/*,.mp3,.wav,.m4a,.flac,.ogg,.aac">
        <div id="music-current" class="small muted">${m.file ? "current: " + esc(m.file.split("/").pop()) : "none"}</div></div>
      <div><label>Music volume (0-1)</label><input id="mvol" type="number" step="0.05" min="0" max="1" value="${m.volume ?? 0.25}">
        <label class="row" style="margin-top:8px"><input type="checkbox" id="mduck" ${m.duck !== false ? "checked" : ""}> duck music under dialogue</label></div>
      <div class="full row" style="margin-top:10px"><button id="msave">Save music settings</button>
        <button id="lip-setup" ${m.file ? "" : "disabled"}>Lip-sync to uploaded audio</button><span class="grow"></span>
        <button class="primary" id="cut">Render final cut</button></div>
    </div>
    <h2>Dialogue &amp; sound</h2>
    <div class="card tool-card">
      <div class="tool-row"><button id="voice-dialogue">Voice all dialogue</button><span class="muted small">VoiceStudio voices every line with its acting direction, checks the words, and puts each line on the timeline as its own lip-sync guide. Unchanged lines are reused.</span></div>
      <div class="tool-row"><button id="voice-eleven">Voice with ElevenLabs</button><span class="muted small">Alternative voices for the LTX-2.3 lip-sync engine.</span></div>
      <div class="tool-row"><button id="make-sfx">Generate sound effects</button><span class="muted small">From each shot's sound description.</span></div>
      <details><summary>Build a cut automatically</summary>
        <p class="muted small">Rebuilds the Takes timeline from scratch. Lip-sync cut: each shot speaks its own lines, timed by the pause before each line and the shot's action seconds. Coverage cut: cuts between singles, inserts and the master with the dialogue running underneath.</p>
        <div class="row"><button id="lipsync-cut">Build cut from lip-sync takes</button><button id="coverage-cut">Build coverage cut</button></div></details>
    </div>
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
  if ($("#voice-dialogue")) $("#voice-dialogue").onclick = () => startJob(`${ctx.base}/dialogue`, {});
  if ($("#voice-eleven")) $("#voice-eleven").onclick = () => startJob(`${ctx.base}/dialogue_elevenlabs`, {});
  if ($("#make-sfx")) $("#make-sfx").onclick = () => startJob(`${ctx.base}/sfx`, {});
  if ($("#lipsync-cut")) $("#lipsync-cut").onclick = () => startJob(`${ctx.base}/lipsync_cut`, {});
  if ($("#coverage-cut")) $("#coverage-cut").onclick = () => startJob(`${ctx.base}/coverage_cut`, {});
  if ($('#render-edit')) $('#render-edit').onclick = () => startJob(`${ctx.base}/cut`);
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

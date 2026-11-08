// Reelwright - core shell, router, project and bible views. Episode views live in episode.js.
"use strict";

const APP_NAME = "Reelwright";
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const media = (rel) => (rel ? "/media/" + rel.split("/").map(encodeURIComponent).join("/") : "");
const fmtSec = (s) => { s = Math.round(s || 0); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60; return h ? `${h}h ${m}m` : m ? `${m}m ${r}s` : `${r}s`; };
const pl = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
const ic = (name) => `<svg class="i"><use href="#i-${name}"/></svg>`;
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};

const App = { status: null, loras: [], route: [], refreshers: [], lastJobs: {}, sd: {}, series: [] };
document.title = APP_NAME;
$("#app-name").textContent = APP_NAME;

async function api(method, url, body, isForm) {
  const opt = { method, headers: {} };
  if (body !== undefined) {
    if (isForm) opt.body = body;
    else { opt.body = JSON.stringify(body); opt.headers["Content-Type"] = "application/json"; }
  }
  const r = await fetch(url, opt);
  const text = await r.text();
  let data; try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!r.ok) { const msg = (data && data.detail) || text || r.statusText; toast(typeof msg === "string" ? msg : JSON.stringify(msg), true); throw new Error(msg); }
  return data;
}
const GET = (u) => api("GET", u), POST = (u, b) => api("POST", u, b ?? {}), PUT = (u, b) => api("PUT", u, b);
const FORM = (u, fd) => api("POST", u, fd, true);

// A shared selection control for every deletable collection.
// Hidden behind a quiet "Select" toggle so checkboxes only appear when the user asks for them.
function bulkSelect(root, selector, remove, refresh = route, label = "items") {
  const targets = $$(selector, root);
  if (!targets.length) return;
  const bar = document.createElement("div");
  bar.className = "row bulk-actions";
  bar.innerHTML = `<button class="bulk-toggle" aria-pressed="false">Select ${esc(label)}…</button>
    <span class="bulk-tools"><label><input type="checkbox" aria-label="Select all ${esc(label)}"> All</label><span class="muted small"></span><button class="danger" disabled>Delete selected</button></span>`;
  const head = $(":scope > .page-head", root);
  if (head) head.after(bar); else root.prepend(bar);
  root.classList.add("bulk-off");
  const toggle = $(".bulk-toggle", bar);
  toggle.onclick = () => {
    const on = root.classList.toggle("bulk-off") === false;
    toggle.setAttribute("aria-pressed", String(on)); toggle.textContent = on ? "Done" : `Select ${label}…`;
    if (!on) { boxes.forEach(b => { b.checked = false; }); sync(); }
  };
  const boxes = targets.map((target, i) => {
    const box = document.createElement("input"); box.type = "checkbox";
    box.className = "bulk-check"; box.setAttribute("aria-label", `Select ${label} ${i + 1}`);
    box.addEventListener("click", e => e.stopPropagation());
    if (target.tagName === "A") {
      const wrap = document.createElement("div"); target.replaceWith(wrap); wrap.append(box, target);
    } else target.before(box);
    return box;
  });
  const all = $(".bulk-tools input", bar), button = $(".bulk-tools button", bar), count = $(".bulk-tools span", bar);
  const sync = () => {
    const n = boxes.filter(b => b.checked).length;
    button.disabled = !n; count.textContent = `${n} selected`;
    all.checked = n === boxes.length; all.indeterminate = n > 0 && n < boxes.length;
    App.selecting = n > 0;
  };
  boxes.forEach(b => b.addEventListener("change", sync));
  all.onchange = () => { boxes.forEach(b => { b.checked = all.checked; }); sync(); };
  button.onclick = async (event) => {
    event.stopPropagation();
    const selected = targets.filter((_, i) => boxes[i].checked);
    if (!(await ask(`Delete ${selected.length} selected ${label}?`, "Delete selected", true))) return;
    button.disabled = true;
    try { await remove(selected); App.selecting = false; await refresh(); }
    catch { App.selecting = false; await refresh(); }
  };
}
// Destructive page actions live in a quiet overflow menu instead of a red button beside every title.
function pageMenu(view, actions) {
  const menu = document.createElement("details");
  menu.className = "more-menu";
  menu.innerHTML = `<summary class="icon-btn" aria-label="More actions" title="More actions">${ic("dots")}</summary><div class="more-pop" role="menu"></div>`;
  for (const [text, fn, danger] of actions) {
    const b = document.createElement("button");
    b.textContent = text; b.setAttribute("role", "menuitem"); if (danger) b.className = "danger";
    b.onclick = () => { menu.open = false; fn(); };
    $(".more-pop", menu).append(b);
  }
  $(".page-head", view).append(menu);
  return menu;
}
document.addEventListener("click", (e) => { $$(".more-menu[open]").forEach((m) => { if (!m.contains(e.target) || e.target.closest(".more-pop button")) m.open = false; }); });
// Seconds for humans: 1.1666667 -> "1.2s".
const fmtDur = (s) => `${Math.round((+s || 0) * 10) / 10}s`;
const VIDEO_ENGINES = ["infinitetalk", "minimax_local", "ltx", "ltx_lipsync", "wan", "minimax"];
const ENGINE_NAMES = {infinitetalk: "InfiniteTalk (local, lip-sync)", minimax_local: "MiniMax H3 (local)", ltx: "LTX-2.3", ltx_lipsync: "LTX-2.3 lip-sync", wan: "Wan 2.2", minimax: "MiniMax (cloud)"};
const engineOptions = (current) => VIDEO_ENGINES.map(x => `<option value="${x}" ${x === current ? "selected" : ""}>${esc(ENGINE_NAMES[x])}</option>`).join("");

// ── toasts ───────────────────────────────────────────────────────────────
function toast(msg, bad) {
  const d = document.createElement("div");
  d.className = "toast " + (bad ? "bad" : "ok");
  d.innerHTML = `${ic(bad ? "alert" : "check")}<span></span>`;
  d.lastChild.textContent = msg;
  const kill = () => { d.classList.add("leaving"); setTimeout(() => d.remove(), 200); };
  d.onclick = kill;
  $("#toasts").appendChild(d);
  while ($("#toasts").children.length > 4) $("#toasts").firstChild.remove();
  setTimeout(kill, bad ? 9000 : 3600);
}

// ── modal + lightbox gallery ─────────────────────────────────────────────
let modalCleanup = null;
function modal(html) { closeModal(); $("#lightbox").classList.remove("on"); document.body.classList.add("modal-open"); $("#modal-body").innerHTML = html; $("#modal").classList.remove("hidden"); }
let askDone = null;
function closeModal() {
  document.body.classList.remove("modal-open");
  const cleanup = modalCleanup; modalCleanup = null;
  if (cleanup) cleanup();
  $("#modal-body").classList.remove("crop-dialog");
  $("#modal").classList.add("hidden"); $("#modal-body").innerHTML = "";
  if (askDone) { const f = askDone; askDone = null; f(false); }
}
function ask(msg, okLabel = "Confirm", danger = false) {
  return new Promise((resolve) => {
    modal(`<h3 style="margin:0 0 4px;font-size:16px;max-width:420px">${esc(msg)}</h3><div class="row" style="justify-content:flex-end;margin-top:20px">
      <button id="ask-no">Cancel</button><button class="${danger ? "danger" : "primary"}" id="ask-yes">${esc(okLabel)}</button></div>`);
    askDone = resolve;
    $("#ask-no").onclick = closeModal;
    $("#ask-yes").onclick = () => { askDone = null; closeModal(); resolve(true); };
    $("#ask-yes").focus();
  });
}
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });

const LB = { items: [], i: 0 };
function zoom(src) {
  const seen = new Set(); LB.items = [];
  for (const el of $$("[data-zoom]", $("#view"))) { const s = el.dataset.zoom; if (s && !seen.has(s)) { seen.add(s); LB.items.push(s); } }
  if (!seen.has(src)) LB.items = [src];
  LB.i = Math.max(0, LB.items.indexOf(src));
  lbShow(); $("#lightbox").classList.add("on");
}
function lbShow() {
  $("#lb-img").src = LB.items[LB.i];
  $("#lb-count").textContent = `${LB.i + 1} / ${LB.items.length}`;
  $("#lb-prev").style.visibility = $("#lb-next").style.visibility = LB.items.length > 1 ? "visible" : "hidden";
}
const lbStep = (d) => { LB.i = (LB.i + d + LB.items.length) % LB.items.length; lbShow(); };
$("#lb-prev").onclick = (e) => { e.stopPropagation(); lbStep(-1); };
$("#lb-next").onclick = (e) => { e.stopPropagation(); lbStep(1); };
$("#lb-close").onclick = () => $("#lightbox").classList.remove("on");
$("#lightbox").addEventListener("click", (e) => { if (e.target.id === "lightbox") $("#lightbox").classList.remove("on"); });

function crumbs(parts) {
  const all = parts.length ? [["Projects", "#/"], ...parts] : [["Projects", null]];
  $("#crumbs").innerHTML = all.map(([t, h], i) => {
    const last = i === all.length - 1;
    return (i ? `<span class="sep">/</span>` : "") + (last ? `<b>${esc(t)}</b>` : `<a href="${h}">${esc(t)}</a>`);
  }).join("");
  document.title = `${all[all.length - 1][0]} · ${APP_NAME}`;
}

// ── AI models: connections, defaults and the per-job pickers ──────────────
async function loadAI() { try { App.ai = await GET("/api/ai"); } catch { /* keep the last copy */ } return App.ai; }
const aiImageLabel = (src) => (App.ai?.choices.image || []).find((c) => c.source === src)?.label;
/** A model picker for one job. role: "planner" (writing) or "vision" (frame checks). */
function textPicker(id, role, title) {
  const ai = App.ai; if (!ai) return "";
  const def = ai.defaults[role], opts = ai.choices.text.slice();
  if (def && !opts.some((o) => o.ref === def)) opts.unshift({ ref: def, label: ai.labels[role] });
  return `<select id="${id}" class="model-pick" aria-label="${esc(title)}" title="${esc(title)} (default set on the AI models page)">${opts.map((o) =>
    `<option value="${esc(o.ref)}" ${o.ref === def ? "selected" : ""}>${esc(o.label)}${o.ref === def ? " · default" : ""}${o.ready === false ? " · no key yet" : ""}</option>`).join("")}</select>`;
}
const imageOptions = (prefix = "") => (App.ai?.choices.image || []).map((c) =>
  `<option value="${esc(c.source)}">${esc(prefix + c.label)}${c.ready === false ? " · no key yet" : ""}</option>`).join("");

function sourceLabel(s) {
  if (s && s.startsWith("img:")) return aiImageLabel(s) || s.slice(4).replace(":", " · ");
  return { flux_lora: "Flux + LoRA", zimage: "Z-Image", qwen_refs: "Z-Image + Qwen identity", qwen_identity: "Qwen face correction", auto: "Auto (LoRA > photos > Z-Image)", upload: "Upload", reuse: "Reuse master angle", chain: "Last frame of prev take", punch_in: "Punch-in from master", openai_image: "ChatGPT image (OpenAI)", gemini_image: "Gemini image (Google)" }[s] || s || "inherit";
}

// ── sidebar ──────────────────────────────────────────────────────────────
function renderNav() {
  const [a, sid, kind, id] = App.route;
  const d = a === "s" ? App.sd[sid] : null;
  const item = (href, icon, label, extra = "", sub = false) => {
    const on = location.hash.replace(/\/(story|shots|frames|animatic|takes|cut)$/, "") === href.replace(/\/(story|shots|frames|animatic|takes|cut)$/, "");
    return `<a class="nav-item ${sub || ""} ${on ? "on" : ""}" href="${href}" ${on ? 'aria-current="page"' : ""} data-i="${esc(label.trim().charAt(0).toUpperCase())}" title="${esc(label)}"><span class="lbl">${esc(label)}</span>${extra}</a>`;
  };
  let html = item("#/", "folder", "Projects", "", "top");
  if (d) {
    const chars = Object.values(d.bible.characters), locs = Object.values(d.bible.locations);
    html += `<div class="nav-sec">${esc(d.series.name)}</div>` +
      item(`#/s/${sid}`, "folder", "Overview") + item(`#/s/${sid}/style`, "palette", "Style & props") +
      `<div class="nav-sec">Episodes</div>` + d.episodes.map((e) => item(`#/s/${sid}/e/${e.id}/story`, "clapper", e.title, `<span class="count">${e.shots}</span>`)).join("") +
      `<div class="nav-sec">Characters</div>` + chars.slice(0, 10).map((c) => item(`#/s/${sid}/c/${c.id}`, "user", c.name, c.draft ? `<span class="count">draft</span>` : "")).join("") +
      `<div class="nav-sec">Locations</div>` + locs.slice(0, 10).map((l) => item(`#/s/${sid}/l/${l.id}`, "pin", l.name, l.draft ? `<span class="count">draft</span>` : "")).join("");
  }
  html += `<div class="nav-sec">Studio</div>` + item("#/ai", "settings", "AI models", "", "top") + item("#/models", "settings", "Video models", "", "top") + item("#/settings", "settings", "Settings", "", "top");
  $("#nav").innerHTML = html;
}
async function getSeries(sid) {
  const d = await GET(`/api/s/${sid}`);
  App.sd[sid] = d; renderNav();
  return d;
}
function setCollapsed(c) { $("#app").classList.toggle("collapsed", c); store.set("rw-collapse", c ? "1" : "0"); }
$("#collapse-btn").onclick = () => setCollapsed(!$("#app").classList.contains("collapsed"));
function setMenu(open) {
  $("#app").classList.toggle("menu", open); $("#menu-backdrop").hidden = !open;
  $("#menu-btn").setAttribute("aria-expanded", String(open));
}
$("#menu-btn").onclick = () => setMenu(!$("#app").classList.contains("menu"));
$("#menu-backdrop").onclick = () => setMenu(false);
if (store.get("rw-collapse") === "1") setCollapsed(true);
function setTheme(t) {
  document.documentElement.dataset.theme = t; store.set("rw-theme", t);
  $("#theme-btn").innerHTML = ic(t === "dark" ? "moon" : "sun");
}
$("#theme-btn").onclick = () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
setTheme(document.documentElement.dataset.theme || "light");
const narrow = window.matchMedia("(max-width: 980px)");
const syncNarrow = () => { $("#menu-btn").style.display = narrow.matches ? "" : "none"; if (!narrow.matches) setMenu(false); };
narrow.addEventListener("change", syncNarrow); syncNarrow();

// ── status + jobs drawer ─────────────────────────────────────────────────
async function refreshStatus() {
  try {
    const s = await GET("/api/status");
    App.status = s; App.loras = s.loras || [];
    const line = (ok, text, title) => `<div class="line" title="${esc(title || text)}"><span class="dot ${ok ? "ok" : "bad"}"></span><span class="lbl">${esc(text)}</span></div>`;
    $("#status").innerHTML = line(s.comfy, s.comfy ? "ComfyUI online" : "ComfyUI offline", s.comfy_host) +
      (s.planner_provider && s.planner_provider !== "ollama" ? line(!s.missing.includes(s.planner_provider + ":api_key"), "Shots · " + s.planner, s.planner) : line(s.ollama_models.length > 0, s.ollama_models.length ? "Ollama · " + s.planner_model : "Ollama offline", s.planner_model)) +
      (s.voices ? line(s.voices.ok, s.voices.ok ? "Voices · VoiceStudio" : "VoiceStudio offline", s.voices.device || "systemctl --user start reelwright-voice") : "") +
      (s.missing.length ? `<div class="line"><span class="chip warn" title="${esc(s.missing.join("\n"))}">${s.missing.length} missing models</span></div>` : "");
  } catch { $("#status").innerHTML = `<div class="line"><span class="dot bad"></span><span class="lbl">Server unreachable</span></div>`; }
}

/** Reload the whole app: fetches fresh scripts and data. Asks first if there is something unsaved. */
async function reloadApp(clearCaches = false) {
  const button = $("#refresh-btn"); button.classList.add("spinning");
  try {
    let lost = App.dirty;
    if (App.timelineEditor?.dirty) { try { await App.timelineEditor.flush(); lost = false; } catch { lost = true; } }
    if (lost && !(await ask("You have changes that are not saved. Reload anyway?", "Reload", true))) return;
    App.dirty = false;  // we have asked; do not let the browser ask again
    try {
      const registration = await navigator.serviceWorker?.getRegistration();
      await registration?.update();
      if (clearCaches) { for (const key of await caches.keys()) await caches.delete(key); await registration?.unregister(); }
    } catch { /* reloading still works without the service worker */ }
    location.reload();
  } finally { button.classList.remove("spinning"); }
}
$("#refresh-btn").onclick = (event) => reloadApp(event.shiftKey || event.altKey);

function openJobs(on = true) { $("#jobs").classList.toggle("on", on); $("#scrim").classList.toggle("on", on); }
$("#jobs-btn").onclick = () => openJobs(!$("#jobs").classList.contains("on"));
$("#jobs-close").onclick = () => openJobs(false);
$("#scrim").onclick = () => { openJobs(false); openPalette(false); };

async function pollJobs() {
  try {
    const jobs = await GET("/api/jobs");
    const active = jobs.filter((j) => j.status === "running" || j.status === "queued");
    $("#jobs-title").textContent = active.length ? `Activity · ${active.length} running` : "Activity";
    $("#jobs-badge").hidden = !active.length; $("#jobs-badge").textContent = active.length;
    $("#jobs-btn").classList.toggle("busy", active.length > 0);
    $("#jobs-list").innerHTML = jobs.slice(0, 14).map((j) => {
      const pct = j.progress[1] ? Math.round((100 * j.progress[0]) / j.progress[1]) : (j.status === "done" ? 100 : 0);
      const cls = { done: "good", error: "bad", running: "accent", cancelled: "warn" }[j.status] || "";
      return `<div class="job ${j.status}"><div class="row"><b class="grow">${esc(j.title)}</b><span class="chip ${cls}">${j.status}${j.status === "running" && j.progress[1] ? " " + pct + "%" : ""}</span>
        ${j.status === "running" || j.status === "queued" ? `<button class="link" data-cancel="${j.id}">cancel</button>` : ""}</div>
        <div class="bar"><div style="width:${pct}%"></div></div>
        ${j.error ? `<div style="color:var(--bad)">${esc(j.error)}</div>` : ""}
        <details ${j.status === "running" || j.status === "error" ? "open" : ""}><summary>log</summary><pre>${esc(j.log.join("\n"))}</pre></details></div>`;
    }).join("") || `<div class="empty"><b>No activity yet</b>Renders and planning jobs show up here.</div>`;
    let finished = false;
    for (const j of jobs) {
      const prev = App.lastJobs[j.id];
      if (prev && prev !== j.status && ["done", "error", "cancelled"].includes(j.status)) {
        finished = true;
        toast(j.status === "done" ? `Finished: ${j.title}` : j.status === "error" ? `Failed: ${j.title}\n${j.error || ""}` : `Cancelled: ${j.title}`, j.status === "error");
      }
      if (j.status === "running" && j.progress[0] !== (App.lastJobs[j.id + ":p"] || 0)) { finished = true; App.lastJobs[j.id + ":p"] = j.progress[0]; }
      App.lastJobs[j.id] = j.status;
    }
    if (finished && App.refreshers.length) {
      if (App.selecting || App.interactionBusy || !$("#modal").classList.contains("hidden")) return;
      if (App.dirty) { if (!App.dirtyWarned) { toast("New results are ready - save your edits, then reload this section."); App.dirtyWarned = true; } }
      else App.refreshers.forEach((f) => f());
    }
  } catch { /* ignore */ }
}
$("#jobs-list").addEventListener("click", async (e) => {
  const id = e.target.dataset.cancel;
  if (id) { await POST(`/api/jobs/${id}/cancel`); pollJobs(); }
});
async function startJob(url, body) {
  const j = await POST(url, body);
  toast(`Queued: ${j.title}`);
  pollJobs();
  return j;
}

// ── command palette ──────────────────────────────────────────────────────
const PAL = { items: [], shown: [], sel: 0 };
async function buildPalette() {
  const items = [
    { g: "Go to", label: "Projects", icon: "folder", href: "#/" },
    { g: "Go to", label: "Settings", icon: "settings", href: "#/settings" },
    { g: "Actions", label: "Toggle light / dark theme", icon: "sun", run: () => $("#theme-btn").click() },
    { g: "Actions", label: "Toggle sidebar", icon: "panel", run: () => $("#collapse-btn").click() },
    { g: "Actions", label: "Open activity", icon: "activity", run: () => openJobs(true) },
    { g: "Actions", label: "Reload the app", icon: "refresh", run: () => reloadApp(false) },
    { g: "Actions", label: "Keyboard shortcuts", icon: "info", run: showShortcuts },
  ];
  try { App.series = await GET("/api/series"); } catch { /* offline */ }
  for (const s of App.series) items.push({ g: "Projects", label: s.name, icon: "folder", href: `#/s/${s.id}` });
  for (const [sid, d] of Object.entries(App.sd)) {
    for (const e of d.episodes) items.push({ g: "Episodes", label: e.title, hint: d.series.name, icon: "clapper", href: `#/s/${sid}/e/${e.id}/story` });
    for (const c of Object.values(d.bible.characters)) items.push({ g: "Characters", label: c.name, hint: d.series.name, icon: "user", href: `#/s/${sid}/c/${c.id}` });
    for (const l of Object.values(d.bible.locations)) items.push({ g: "Locations", label: l.name, hint: d.series.name, icon: "pin", href: `#/s/${sid}/l/${l.id}` });
  }
  PAL.items = items;
}
function paintPalette() {
  const q = $("#pal-input").value.trim().toLowerCase();
  const score = (it) => { const t = (it.label + " " + (it.hint || "")).toLowerCase(); if (!q) return 1; const i = t.indexOf(q); if (i >= 0) return 100 - i; let p = 0; for (const ch of q) { p = t.indexOf(ch, p); if (p < 0) return 0; p++; } return 1; };
  PAL.shown = PAL.items.map((it) => [score(it), it]).filter(([s]) => s > 0).sort((a, b) => b[0] - a[0]).map(([, it]) => it).slice(0, 40);
  PAL.sel = Math.min(PAL.sel, Math.max(0, PAL.shown.length - 1));
  let last = "";
  $("#pal-list").innerHTML = PAL.shown.map((it, k) => {
    const head = it.g !== last ? `<div class="pal-group">${esc(it.g)}</div>` : ""; last = it.g;
    return `${head}<div class="pal-item ${k === PAL.sel ? "on" : ""}" data-k="${k}">${ic(it.icon)}<span>${esc(it.label)}</span><span class="hint">${esc(it.hint || "")}</span></div>`;
  }).join("") || `<div class="empty">Nothing matches</div>`;
  $("#pal-list .on")?.scrollIntoView({ block: "nearest" });
}
async function openPalette(on = true) {
  $("#palette").classList.toggle("on", on); $("#scrim").classList.toggle("on", on || $("#jobs").classList.contains("on"));
  if (!on) return;
  $("#pal-input").value = ""; PAL.sel = 0;
  await buildPalette(); paintPalette(); $("#pal-input").focus();
}
function palGo(it) {
  openPalette(false);
  if (!it) return;
  if (it.href) location.hash = it.href; else it.run();
}
$("#pal-input").addEventListener("input", () => { PAL.sel = 0; paintPalette(); });
$("#pal-input").addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown") { PAL.sel = Math.min(PAL.sel + 1, PAL.shown.length - 1); paintPalette(); e.preventDefault(); }
  else if (e.key === "ArrowUp") { PAL.sel = Math.max(PAL.sel - 1, 0); paintPalette(); e.preventDefault(); }
  else if (e.key === "Enter") palGo(PAL.shown[PAL.sel]);
});
$("#pal-list").addEventListener("click", (e) => { const k = e.target.closest(".pal-item")?.dataset.k; if (k != null) palGo(PAL.shown[+k]); });
$("#search-btn").onclick = () => openPalette(true);

function showShortcuts() {
  const k = (x) => `<span class="kbd">${x}</span>`;
  modal(`<h2 style="margin-top:0">Keyboard shortcuts</h2><div class="shortcuts">
    <span>${k("Ctrl")} ${k("K")} or ${k("/")}</span><span>Search and jump anywhere</span>
    <span>${k("[")}</span><span>Collapse the sidebar</span>
    <span>${k("g")} then ${k("h")}</span><span>Go to projects</span>
    <span>${k("g")} then ${k("a")}</span><span>Open activity</span>
    <span>${k("←")} ${k("→")}</span><span>Previous / next image in the viewer</span>
    <span>${k("?")}</span><span>This help</span><span>${k("Esc")}</span><span>Close any panel</span></div>`);
}
let gPending = 0;
document.addEventListener("keydown", (e) => {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.target.isContentEditable;
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openPalette(true); return; }
  if (e.key === "Escape") {
    if (!$("#modal").classList.contains("hidden")) closeModal();
    else if ($("#lightbox").classList.contains("on")) $("#lightbox").classList.remove("on");
    else if ($("#palette").classList.contains("on")) openPalette(false);
    else if ($("#jobs").classList.contains("on")) openJobs(false);
    else { setMenu(false); closeModal(); }
    return;
  }
  if ($("#lightbox").classList.contains("on")) { if (e.key === "ArrowLeft") lbStep(-1); if (e.key === "ArrowRight") lbStep(1); return; }
  if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === "/") { e.preventDefault(); openPalette(true); }
  else if (e.key === "[") setCollapsed(!$("#app").classList.contains("collapsed"));
  else if (e.key === "?") showShortcuts();
  else if (e.key === "g") gPending = Date.now();
  else if (Date.now() - gPending < 900) { if (e.key === "h") location.hash = "#/"; if (e.key === "a") openJobs(true); gPending = 0; }
});

// ── router ───────────────────────────────────────────────────────────────
const SKELETON = `<div class="skel h1"></div><div class="skel row-card"></div><div class="skel row-card"></div>`;
async function route() {
  if (App.timelineEditor) {
    const editor = App.timelineEditor;
    try { if (editor.dirty) await editor.flush(); }
    catch (error) { toast('Timeline could not save. Your edit is still open: ' + error.message, true); return; }
    editor.destroy(); App.timelineEditor = null;
  }
  closeModal();
  App.refreshers = []; App.soft = null; App.selecting = false; App.dirty = false; App.dirtyWarned = false; App.saveKey = null;
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  const first = !App.route.length || App.route.join("/") !== parts.join("/");
  App.route = parts;
  setMenu(false);
  document.body.classList.remove("has-stepper");
  const view = $("#view");
  if (first) { view.innerHTML = SKELETON; view.style.animation = "none"; void view.offsetHeight; view.style.animation = ""; }
  renderNav();
  // Re-rendering the page you are already on must not throw away where you were in it.
  const keep = first ? null : { y: window.scrollY, open: $$("details", view).map((d) => d.open) };
  const dispatch = async () => {
    if (!parts.length) return await viewHome(view);
    if (parts[0] === "models") return await viewModels(view);
    if (parts[0] === "ai") return await viewAI(view);
    if (parts[0] === "settings") return await viewSettings(view);
    if (parts[0] === "s") {
      const sid = parts[1];
      if (parts.length === 2) return await viewSeries(view, sid);
      if (parts[2] === "c") return await viewCharacter(view, sid, parts[3]);
      if (parts[2] === "l") return await viewLocation(view, sid, parts[3]);
      if (parts[2] === "style") return await viewStyle(view, sid);
      if (parts[2] === "e") return await viewEpisode(view, sid, parts[3], parts[4] || "story");
    }
    crumbs([["Not found", null]]);
    view.innerHTML = `<div class="empty"><b>Page not found</b><a href="#/">Back to projects</a></div>`;
  };
  try { await dispatch(); }
  catch (err) { view.innerHTML = `<div class="notice">${ic("alert")}<span>${esc(err.message)}</span></div>`; }
  if (keep) {
    $$("details", view).forEach((d, i) => { if (i < keep.open.length) d.open = keep.open[i]; });
    window.scrollTo({ top: keep.y, behavior: "instant" });
  }
}
window.addEventListener("hashchange", route);
$("#view").addEventListener("input", (e) => { if (!e.target.matches("input[type=file], .bulk-check, .bulk-actions input, input[data-chk], input[data-chk-all], #take-model, #ntakes, #src-override, [data-edit-model]")) App.dirty = true; });
window.addEventListener("beforeunload", (e) => { if (App.dirty) { e.preventDefault(); e.returnValue = ""; } });

// ── shared bits ──────────────────────────────────────────────────────────
const firstImg = (...rels) => media(rels.find(Boolean) || "");
const charThumb = (c) => firstImg((c.refs || [])[0], ((c.sheet || [])[0] || {}).file);
const locThumb = (l) => firstImg(Object.values(l.plates || {})[0], ((l.candidates || [])[0] || {}).file);
const cover = (src, icon = "image", sq = false) => `<div class="cover ${sq ? "sq" : ""}">${src ? `<img src="${src}" loading="lazy" alt="">` : `<div class="ph">${ic(icon)}</div>`}</div>`;
function addCard(id, placeholder, btnId, btnLabel, primary) {
  return `<div class="addrow"><input id="${id}" placeholder="${placeholder}"><button class="${primary ? "primary" : ""}" id="${btnId}">${btnLabel}</button></div>`;
}
const printFig = (src, name, href, sub) => `<a class="print" href="${href}"><figure style="margin:0">${src ? `<img src="${src}" loading="lazy" alt="">` : `<div class="ph">${esc(name.charAt(0).toUpperCase())}</div>`}<figcaption><span class="nm">${esc(name)}</span><div class="muted small" style="font-weight:400">${esc(sub)}</div></figcaption></figure></a>`;
// Ctrl/Cmd+S runs the current page's save (set per page with saveShortcut, cleared on navigation).
const saveShortcut = (fn) => { App.saveKey = fn; };
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === "s" && App.saveKey) { e.preventDefault(); App.saveKey(); }
});
const onEnter = (sel, fn) => $(sel).addEventListener("keydown", (e) => { if (e.key === "Enter") fn(); });

// ── home ────────────────────────────────────────────────────────────────
async function viewHome(view) {
  crumbs([]);
  const list = await GET("/api/series");
  App.series = list;
  const st = App.status || {};
  const details = await Promise.all(list.map((s) => getSeries(s.id).catch(() => null)));
  view.innerHTML = `
    <div class="page-head"><div><h1>${list.length ? "Projects" : "Start your first project"}</h1>
      <p class="sub">${list.length ? `${pl(list.length, "project")}, ${pl(details.reduce((a, d) => a + (d ? d.episodes.length : 0), 0), "episode")}, ${pl(details.reduce((a, d) => a + (d ? Object.keys(d.bible.characters).length : 0), 0), "character")}` : "A project holds a story world: its characters, locations and episodes."}</p></div></div>
    ${st.comfy === false ? `<div class="notice">${ic("alert")}<span>ComfyUI is not reachable at ${esc(st.comfy_host)}. Start Comfy Desktop - planning works without it, rendering doesn't.</span></div>` : ""}
    ${(st.missing || []).length ? `<div class="notice">${ic("alert")}<span>Not found: ${esc(st.missing.join(", "))}. Fix names in Settings or install them.</span></div>` : ""}
    <div class="sheets">${list.map((s, k) => {
      const d = details[k];
      const imgs = d ? [...Object.values(d.bible.characters).map(charThumb), ...Object.values(d.bible.locations).map(locThumb)].filter(Boolean).slice(0, 3) : [];
      const cells = [0, 1, 2].map((i) => `<i>${imgs[i] ? `<img src="${imgs[i]}" loading="lazy" alt="">` : ""}</i>`).join("");
      return `<a class="sheet" href="#/s/${esc(s.id)}"><div class="strip">${cells}</div><h3>${esc(s.name)}</h3>
        <div class="muted small">${d ? `${pl(d.episodes.length, "episode")}, ${pl(Object.keys(d.bible.characters).length, "character")}, ${pl(Object.keys(d.bible.locations).length, "location")}` : ""}</div></a>`;
    }).join("")}</div>
    <div class="addrow" style="margin-top:34px"><input id="new-series" placeholder="New project name, e.g. Summer Stories"><button class="primary" id="add-series">Create project</button></div>`;
  const create = async () => {
    const name = $("#new-series").value.trim(); if (!name) return toast("Give the project a name", true);
    const s = await POST("/api/series", { name }); location.hash = `#/s/${s.id}`;
  };
  bulkSelect(view, ".sheet", async targets => {
    for (const target of targets) {
      const sid = target.getAttribute("href").split("/").at(-1);
      await api("DELETE", `/api/s/${sid}`); delete App.sd[sid];
    }
  }, route, "projects");
  $("#add-series").onclick = create; onEnter("#new-series", create);
}

// ── series overview ─────────────────────────────────────────────────────
async function viewSeries(view, sid) {
  const d = await getSeries(sid);
  crumbs([[d.series.name, null]]);
  const chars = Object.values(d.bible.characters), locs = Object.values(d.bible.locations);
  view.innerHTML = `
    <div class="page-head"><div><h1>${esc(d.series.name)}</h1>
      <p class="sub">${pl(d.episodes.length, "episode")}, ${pl(d.episodes.reduce((a, e) => a + e.shots, 0), "shot")}, ${pl(chars.length, "character")}, ${pl(locs.length, "location")}. <a href="#/s/${sid}/style">Style &amp; props</a></p></div></div>
    <div class="section-head"><h2>Episodes</h2></div>
    ${d.episodes.length ? `<div class="eplist">${d.episodes.map((e, k) => `<a class="eprow" href="#/s/${sid}/e/${e.id}/story"><span class="n">EP ${String(k + 1).padStart(2, "0")}</span><span class="t">${esc(e.title)}</span><span class="muted small">${pl(e.shots, "shot")}</span></a>`).join("")}</div>` : `<div class="empty"><b>No episodes yet</b>Name one below to start writing.</div>`}
    ${addCard("new-ep", "Episode title", "add-ep", "New episode", true)}
    <div class="section-head"><h2>Characters</h2></div>
    <div class="prints">${chars.map((c) => printFig(charThumb(c), c.name, `#/s/${sid}/c/${c.id}`, `${c.draft ? "draft, " : ""}${c.image_lora?.name ? "LoRA" : "no LoRA"}, ${pl((c.refs || []).length, "photo")}`)).join("")}</div>
    ${addCard("new-char", "Character name", "add-char", "Add character")}
    <div class="section-head"><h2>Locations</h2></div>
    <div class="prints wide">${locs.map((l) => printFig(locThumb(l), l.name, `#/s/${sid}/l/${l.id}`, `${l.draft ? "draft, " : ""}${Object.keys(l.variants || {}).join(", ") || "no variants"}`)).join("")}</div>
    ${addCard("new-loc", "Location name", "add-loc", "Add location")}`;
  pageMenu(view, [["Style & props", () => { location.hash = `#/s/${sid}/style`; }], ["Delete project…", async () => {
    if (await ask(`Delete ${d.series.name} and all its episodes? Files are kept in a .deleted folder.`, "Delete project", true)) {
      await api("DELETE", `/api/s/${sid}`); delete App.sd[sid]; location.hash = "#/";
    }
  }, true]]);
  bulkSelect(view, ".eprow, .prints a", async targets => {
    for (const target of targets) {
      const parts = target.getAttribute("href").split("/");
      const kind = parts[3], id = parts[4];
      await api("DELETE", kind === "e" ? `/api/s/${sid}/e/${id}` : `/api/s/${sid}/${kind === "c" ? "characters" : "locations"}/${id}`);
    }
  }, route, "episodes, characters and locations");
  const addEp = async () => { const e = await POST(`/api/s/${sid}/episodes`, { title: $("#new-ep").value || "New episode" }); location.hash = `#/s/${sid}/e/${e.id}/story`; };
  const addChar = async () => { const c = await POST(`/api/s/${sid}/characters`, { name: $("#new-char").value || "New character" }); location.hash = `#/s/${sid}/c/${c.id}`; };
  const addLoc = async () => { const l = await POST(`/api/s/${sid}/locations`, { name: $("#new-loc").value || "New location" }); location.hash = `#/s/${sid}/l/${l.id}`; };
  $("#add-ep").onclick = addEp; $("#add-char").onclick = addChar; $("#add-loc").onclick = addLoc;
  onEnter("#new-ep", addEp); onEnter("#new-char", addChar); onEnter("#new-loc", addLoc);
}

// ── helpers for key: value textareas ────────────────────────────────────
const kvToText = (o) => Object.entries(o || {}).map(([k, v]) => `${k}: ${v}`).join("\n");
function textToKv(t) {
  const o = {};
  for (const line of (t || "").split("\n")) {
    const i = line.indexOf(":");
    if (i < 0) { if (line.trim()) o[line.trim()] = ""; continue; }
    const k = line.slice(0, i).trim(); if (k) o[k] = line.slice(i + 1).trim();
  }
  return o;
}
function loraSelect(id, value) {
  const opts = [""].concat(App.loras || []);
  if (value && !opts.includes(value)) opts.push(value);
  return `<select id="${id}">${opts.map((o) => `<option value="${esc(o)}" ${o === value ? "selected" : ""}>${o ? esc(o) : "- none -"}</option>`).join("")}</select>`;
}
const sourceSelect = (id, value, allowInherit = true) => {
  const opts = (allowInherit ? [""] : []).concat(["flux_lora", "zimage", "qwen_refs"], (App.ai?.choices.image || []).map((c) => c.source), ["auto", "upload"]);
  if (value && !opts.includes(value)) opts.push(value);
  return `<select id="${id}">${opts.map((o) => `<option value="${esc(o)}" ${o === (value || "") ? "selected" : ""}>${o ? esc(sourceLabel(o)) : "inherit"}</option>`).join("")}</select>`;
};

// ── character ───────────────────────────────────────────────────────────
async function viewCharacter(view, sid, cid) {
  const d = await getSeries(sid);
  const c = d.bible.characters[cid];
  if (!c) { view.innerHTML = "Character not found"; return; }
  crumbs([[d.series.name, `#/s/${sid}`], [c.name, null]]);
  view.innerHTML = `
    <div class="page-head">${charThumb(c) ? `<img class="thumb square" src="${charThumb(c)}" data-zoom="${charThumb(c)}" style="width:72px;height:72px;border-radius:50%">` : ""}
      <div><h1>${esc(c.name)}</h1><div class="row sub"><span class="chip">v${c.version}</span><span class="chip ${c.image_lora?.name ? "accent" : ""}">${c.image_lora?.name ? "LoRA set" : "no LoRA"}</span><span class="chip">${(c.refs || []).length} reference photos</span></div></div></div>
    ${c.draft ? `<div class="notice">Drafted from the story - review the description, pick a LoRA, then save.</div>` : ""}
    <div class="card form-grid">
      <h3 class="form-sec full">Identity</h3>
      <div><label>Name</label><input id="f-name" value="${esc(c.name)}"></div>
      <div><label>Trigger token (from LoRA training, e.g. ohwx_sam)</label><input id="f-trigger" value="${esc(c.trigger)}"></div>
      <div class="full"><label>Locked appearance (50-80 words, copied verbatim into every prompt)</label><textarea id="f-app">${esc(c.appearance)}</textarea></div>
      <h3 class="form-sec full">Wardrobe &amp; acting</h3>
      <div class="full"><label>Outfits - one per line, "name: description"</label><textarea id="f-outfits">${esc(kvToText(c.outfits))}</textarea></div>
      <div><label>Default outfit</label><input id="f-defout" value="${esc(c.default_outfit)}"></div>
      <div><label>Never change (comma separated)</label><input id="f-never" value="${esc((c.never_change || []).join(", "))}"></div>
      <div class="full"><label>Acting notes</label><textarea id="f-acting" style="min-height:44px">${esc(c.acting_notes)}</textarea></div>
      <h3 class="form-sec full">Models</h3>
      <div><label>Image LoRA (Flux 1)</label>${loraSelect("f-ilora", c.image_lora?.name)}</div>
      <div><label>Image LoRA strength</label><input id="f-ilora-s" type="number" step="0.05" value="${c.image_lora?.strength ?? 0.9}"></div>
      <div><label>Video LoRA (Wan 2.2, optional)</label>${loraSelect("f-vlora", c.video_lora?.name)}</div>
      <div><label>Video LoRA strength</label><input id="f-vlora-s" type="number" step="0.05" value="${c.video_lora?.strength ?? 0.8}"></div>
      <div><label>First-frame source</label>${sourceSelect("f-src", c.frame_source)}</div>
      <h3 class="form-sec full">Voice</h3>
      <div><label>Speaking rate (words per minute)</label><input id="f-wpm" type="number" value="${c.voice?.wpm ?? 140}"></div>
      <div class="full"><label>Voice description (how the character sounds, e.g. "a cheerful four-year-old boy, high-pitched child voice"; also goes into the video prompt)</label><input id="f-voice" value="${esc(c.voice?.description)}"></div>
      <div class="full row save-bar"><button class="primary" id="save">Save character</button><span class="grow"></span><button class="danger" id="del">Delete character…</button></div>
    </div>
    <h2>Voice <span class="muted small">(the reference every dialogue line is cloned from)</span></h2>
    <div class="card row">${c.voice?.file ? `<audio controls src="${media(c.voice.file)}"></audio><span class="chip">${c.voice.profile ? `VoiceStudio: ${esc(c.voice.profile.name)}` : c.voice.designed ? "designed" : "recorded"}</span>` : `<span class="muted">none yet</span>`}
      <input type="file" id="voice-file" accept="audio/*"><button id="voice-up">Upload a recording (5-20 s)</button>
      <button id="voice-design" title="Uses the voice description above">Design voice from description</button></div>
    <div class="card row" style="margin-top:8px"><label for="voice-profile" class="small">VoiceStudio voice library</label>
      <select id="voice-profile" aria-label="VoiceStudio voice"><option value="">Loading voices…</option></select>
      <button id="voice-profile-use">Use this voice</button>
      <span class="muted small">Clone or design voices in VoiceStudio; they appear here.</span></div>
    <h2>References <span class="muted small">(approved identity images)</span></h2>
    <div class="cards">${(c.refs || []).map((r) => `<div class="card"><img class="thumb square" src="${media(r)}" data-zoom="${media(r)}"><button class="link danger" data-unref="${esc(r)}">remove</button></div>`).join("") || `<p class="muted">None yet. Render a character sheet below and approve the best images, or upload your own.</p>`}</div>
    <div class="row" style="margin-top:8px"><input type="file" id="ref-file" accept="image/*"><button id="ref-up">Upload reference</button></div>
    <h2>Character sheet</h2>
    <div class="row"><button class="primary" id="sheet-flux">Render sheet with Flux + LoRA</button><button id="sheet-z">Render with Z-Image</button>${imageOptions() ? `<span class="row"><select id="sheet-cloud" aria-label="Cloud image model">${imageOptions()}</select><button id="sheet-cloud-go" title="Keeps the face of the first reference photo">Render with this model</button></span>` : ""}
      <span class="muted small">Front, three-quarter, full body, profile. Save first if you changed the description.</span></div>
    <div class="cards" style="margin-top:12px">${(c.sheet || []).slice().reverse().map((s) => `<div class="card"><img class="thumb square" src="${media(s.file)}" data-zoom="${media(s.file)}">
      <div class="row small muted" style="margin-top:6px"><span class="grow">${esc(s.view)} · ${sourceLabel(s.source)}</span>
      ${(c.refs || []).includes(s.file) ? `<span class="chip good">reference</span>` : `<button data-ref="${esc(s.file)}">Use as reference</button>`}</div></div>`).join("")}</div>`;
  const collect = () => ({
    name: $("#f-name").value, trigger: $("#f-trigger").value.trim(), appearance: $("#f-app").value.trim(),
    outfits: textToKv($("#f-outfits").value), default_outfit: $("#f-defout").value.trim() || "default",
    never_change: $("#f-never").value.split(",").map((x) => x.trim()).filter(Boolean),
    acting_notes: $("#f-acting").value,
    image_lora: { name: $("#f-ilora").value, strength: parseFloat($("#f-ilora-s").value) || 0.9 },
    video_lora: { name: $("#f-vlora").value, strength: parseFloat($("#f-vlora-s").value) || 0.8 },
    frame_source: $("#f-src").value,
    voice: { ...(c.voice || {}), wpm: parseInt($("#f-wpm").value) || 140, description: $("#f-voice").value },
  });
  $("#save").onclick = async () => { await PUT(`/api/s/${sid}/characters/${cid}`, collect()); toast("Saved. Shots using this character are now marked stale."); route(); };
  $("#del").onclick = async () => { if (await ask(`Delete ${c.name}? Files are kept in a .deleted folder.`, "Delete", true)) { await api("DELETE", `/api/s/${sid}/characters/${cid}`); location.hash = `#/s/${sid}`; } };
  $("#sheet-flux").onclick = () => startJob(`/api/s/${sid}/characters/${cid}/sheet`, { source: "flux_lora" });
  $("#sheet-z").onclick = () => startJob(`/api/s/${sid}/characters/${cid}/sheet`, { source: "zimage" });
  if ($("#sheet-cloud-go")) $("#sheet-cloud-go").onclick = () => startJob(`/api/s/${sid}/characters/${cid}/sheet`, { source: $("#sheet-cloud").value });
  const upload = async (kind, input) => {
    const f = $(input).files[0]; if (!f) return toast("Choose a file first", true);
    const fd = new FormData(); fd.append("kind", kind); fd.append("file", f);
    await FORM(`/api/s/${sid}/characters/${cid}/upload`, fd); route();
  };
  $("#voice-up").onclick = () => upload("voice", "#voice-file");
  GET("/api/voices/profiles").then((list) => {
    const sel = $("#voice-profile"); if (!sel) return;
    sel.innerHTML = list.length ? list.map((p) => `<option value="${esc(p.id)}" ${c.voice?.profile?.id === p.id ? "selected" : ""}>${esc(p.name)}${p.own_voice ? " (own voice)" : ""}${p.kind === "design" ? " (designed)" : ""}${p.demo ? " (demo)" : ""}</option>`).join("")
      : `<option value="">No voices in VoiceStudio yet</option>`;
  }).catch(() => { const sel = $("#voice-profile"); if (sel) sel.innerHTML = `<option value="">VoiceStudio offline</option>`; });
  $("#voice-profile-use").onclick = async () => {
    const profile_id = $("#voice-profile").value;
    if (!profile_id) return toast("Choose a VoiceStudio voice first.", true);
    await POST(`/api/s/${sid}/characters/${cid}/voice/profile`, { profile_id });
    toast("Voice assigned. Re-voice the dialogue to hear it in the film."); route();
  };
  $("#voice-design").onclick = async () => {
    const description = $("#f-voice").value.trim();
    if (!description) return toast("Write a voice description first.", true);
    await PUT(`/api/s/${sid}/characters/${cid}`, collect()); App.dirty = false;
    startJob(`/api/s/${sid}/characters/${cid}/voice`, { description });
  };
  $("#ref-up").onclick = () => upload("ref", "#ref-file");
  view.onclick = async (e) => {
    const t = e.target;
    if (t.dataset.zoom) zoom(t.dataset.zoom);
    if (t.dataset.ref) { await POST(`/api/s/${sid}/characters/${cid}/refs`, { add: t.dataset.ref }); route(); }
    if (t.dataset.unref) { await POST(`/api/s/${sid}/characters/${cid}/refs`, { remove: t.dataset.unref }); route(); }
  };
  bulkSelect(view, "[data-unref]", async targets => {
    for (const t of targets) await POST(`/api/s/${sid}/characters/${cid}/refs`, { remove: t.dataset.unref });
  }, route, "references");
  App.refreshers.push(route);
}

// ── location ────────────────────────────────────────────────────────────
async function viewLocation(view, sid, lid) {
  const d = await getSeries(sid);
  const l = d.bible.locations[lid];
  if (!l) { view.innerHTML = "Location not found"; return; }
  crumbs([[d.series.name, `#/s/${sid}`], [l.name, null]]);
  const variants = Object.keys(l.variants || { day: "" });
  view.innerHTML = `
    <div class="page-head"><div><h1>${esc(l.name)}</h1><div class="row sub"><span class="chip">v${l.version}</span><span class="chip">${Object.keys(l.plates || {}).length} approved plates</span></div></div></div>
    <div class="card form-grid">
      <div><label>Name</label><input id="f-name" value="${esc(l.name)}"></div>
      <div><label>Colour palette</label><input id="f-pal" value="${esc(l.palette)}"></div>
      <div class="full"><label>Description (layout, materials, key objects)</label><textarea id="f-desc">${esc(l.description)}</textarea></div>
      <div class="full"><label>Time-of-day variants - one per line, "name: lighting description"</label><textarea id="f-var">${esc(kvToText(l.variants))}</textarea></div>
      <div class="full"><label>Ambient sound</label><input id="f-amb" value="${esc(l.ambience)}"></div>
      <div class="full row save-bar"><button class="primary" id="save">Save location</button><span class="grow"></span><button class="danger" id="del">Delete location…</button></div>
    </div>
    <h2>Establishing plates</h2>
    <div class="row"><select id="p-var">${variants.map((v) => `<option>${esc(v)}</option>`).join("")}</select>
      <button class="primary" id="gen-z">Render 4 with Z-Image</button><button id="gen-f">Render 4 with Flux</button>${imageOptions() ? `<select id="gen-cloud" aria-label="Cloud image model">${imageOptions()}</select><button id="gen-cloud-go">Render 4 with this model</button>` : ""}
      <input type="file" id="p-file" accept="image/*"><button id="p-up">Upload</button></div>
    <h3>Approved</h3>
    <div class="cards">${Object.entries(l.plates || {}).map(([v, f]) => `<div class="card"><img class="thumb" src="${media(f)}" data-zoom="${media(f)}"><div class="small muted">${esc(v)}</div></div>`).join("") || `<p class="muted">none</p>`}</div>
    <h3>Candidates</h3>
    <div class="cards">${(l.candidates || []).slice().reverse().map((c) => `<div class="card"><img class="thumb" src="${media(c.file)}" data-zoom="${media(c.file)}">
      <div class="row small muted" style="margin-top:6px"><span class="grow">${esc(c.variant)} · ${sourceLabel(c.source)}</span><button data-approve="${esc(c.file)}" data-var="${esc(c.variant)}">Approve</button></div></div>`).join("")}</div>`;
  $("#save").onclick = async () => {
    await PUT(`/api/s/${sid}/locations/${lid}`, { name: $("#f-name").value, palette: $("#f-pal").value, description: $("#f-desc").value.trim(), variants: textToKv($("#f-var").value), ambience: $("#f-amb").value });
    toast("Saved."); route();
  };
  $("#del").onclick = async () => { if (await ask(`Delete ${l.name}?`, "Delete", true)) { await api("DELETE", `/api/s/${sid}/locations/${lid}`); location.hash = `#/s/${sid}`; } };
  $("#gen-z").onclick = () => startJob(`/api/s/${sid}/locations/${lid}/plates`, { variant: $("#p-var").value, source: "zimage" });
  $("#gen-f").onclick = () => startJob(`/api/s/${sid}/locations/${lid}/plates`, { variant: $("#p-var").value, source: "flux_lora" });
  if ($("#gen-cloud-go")) $("#gen-cloud-go").onclick = () => startJob(`/api/s/${sid}/locations/${lid}/plates`, { variant: $("#p-var").value, source: $("#gen-cloud").value });
  $("#p-up").onclick = async () => {
    const f = $("#p-file").files[0]; if (!f) return toast("Choose a file first", true);
    const fd = new FormData(); fd.append("variant", $("#p-var").value); fd.append("file", f);
    await FORM(`/api/s/${sid}/locations/${lid}/upload`, fd); route();
  };
  view.onclick = async (e) => {
    const t = e.target;
    if (t.dataset.zoom) zoom(t.dataset.zoom);
    if (t.dataset.approve) { await POST(`/api/s/${sid}/locations/${lid}/approve`, { file: t.dataset.approve, variant: t.dataset.var }); route(); }
  };
  App.refreshers.push(route);
}

// ── style & props ───────────────────────────────────────────────────────
async function viewStyle(view, sid) {
  const d = await getSeries(sid);
  const st = d.bible.style;
  crumbs([[d.series.name, `#/s/${sid}`], ["Style", null]]);
  view.innerHTML = `
    <h1>Style &amp; props <span class="muted small">v${st.version}</span></h1>
    <div class="card form-grid">
      <div class="full"><label>Look (start of every prompt)</label><textarea id="f-look">${esc(st.look)}</textarea></div>
      <div class="full"><label>Colour grade (end of every image prompt)</label><input id="f-grade" value="${esc(st.grade)}"></div>
      <div class="full"><label>Negative prompt (images)</label><input id="f-neg" value="${esc(st.negative)}"></div>
      <div><label>Default first-frame source</label>${sourceSelect("f-src", st.default_frame_source, false)}</div>
      <div><label>Default video engine</label><select id="f-eng">${engineOptions(st.default_engine)}</select></div>
      <div class="full"><label>Music policy (notes)</label><input id="f-music" value="${esc(st.music_policy)}"></div>
      <div class="full"><label>Props - one per line, "id: description"</label><textarea id="f-props">${esc((d.bible.props || []).map((p) => `${p.id}: ${p.description}`).join("\n"))}</textarea></div>
      <div class="full row save-bar"><button class="primary" id="save">Save style</button></div>
    </div>
    <p class="muted small">Shots without any LoRA character use Z-Image automatically when the default is Flux + LoRA.</p>`;
  $("#save").onclick = async () => {
    await PUT(`/api/s/${sid}/style`, { look: $("#f-look").value, grade: $("#f-grade").value, negative: $("#f-neg").value, default_frame_source: $("#f-src").value, default_engine: $("#f-eng").value, music_policy: $("#f-music").value });
    await PUT(`/api/s/${sid}/props`, Object.entries(textToKv($("#f-props").value)).map(([id, description]) => ({ id, description })));
    toast("Saved."); route();
  };
}

// ── settings ────────────────────────────────────────────────────────────
// Common options as a form; everything else stays editable as JSON under "Advanced".
const SETTING_FIELDS = [
  ["Connections", "Where Studio finds its helpers.", [
    ["comfy_host", "ComfyUI address", "text", "127.0.0.1:8188"],
    ["ollama_host", "Ollama address", "text", "http://localhost:11434"],
    ["ffmpeg", "ffmpeg path", "text", "Leave blank to auto-detect"],
    ["ffprobe", "ffprobe path", "text", "Leave blank to auto-detect"]]],
  ["Ollama models (this PC)", "Local models used when Ollama is chosen. Choose ChatGPT, Gemini and other cloud models, API keys and the defaults on the AI models page.", [
    ["planner_model", "Ollama planner model", "model"],
    ["vision_model", "Ollama vision model", "model"],
    ["vision_check", "Check frames with the vision model after rendering", "bool"]]],
  ["Rendering", "How many candidates to make, and at what size.", [
    ["candidates_per_shot", "First-frame candidates per render", "int", "", 1, 12],
    ["takes_per_shot", "Takes per shot", "int", "", 1, 8],
    ["image.width", "Image width (px)", "int", "", 64, 4096],
    ["image.height", "Image height (px)", "int", "", 64, 4096],
    ["video.width", "Video width (px)", "int", "", 64, 4096],
    ["video.height", "Video height (px)", "int", "", 64, 4096],
    ["video.fps", "Video frame rate", "int", "", 1, 120],
    ["loudness_lufs", "Target loudness (LUFS)", "num", "", -40, 0]]],
  ["Lip-sync", "LatentSync 1.6. Higher guidance articulates the mouth more clearly; more steps are slower for little gain.", [
    ["lipsync.steps", "Steps", "int", "", 1, 100],
    ["lipsync.guidance", "Guidance (1 to 3)", "num", "", 1, 3]]],
];
const getPath = (o, p) => p.split(".").reduce((x, k) => (x == null ? undefined : x[k]), o);
function setPath(o, p, v) {
  const ks = p.split("."), last = ks.pop();
  const parent = ks.reduce((x, k) => (x[k] && typeof x[k] === "object" ? x[k] : (x[k] = {})), o);
  parent[last] = v;
}
async function viewSettings(view) {
  crumbs([["Settings", null]]);
  const d = await GET("/api/settings");
  const models = (App.status && App.status.ollama_models) || [];
  const field = ([path, label, type, hint, min, max]) => {
    const id = "set-" + path.replace(/\./g, "-"), v = getPath(d.effective, path);
    if (type === "select") return `<div><label for="${id}">${esc(label)}</label><select id="${id}">${hint.map(([o, t]) => `<option value="${esc(o)}" ${o === (v ?? "") ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></div>`;
    if (type === "bool") return `<label class="row check full"><input type="checkbox" id="${id}" ${v ? "checked" : ""}> ${esc(label)}</label>`;
    const attrs = type === "model" ? `list="set-models" autocomplete="off"` : type === "text" ? "" : `type="number" ${type === "int" ? 'step="1"' : 'step="any"'} min="${min}" max="${max}"`;
    return `<div><label for="${id}">${esc(label)}</label><input id="${id}" ${attrs} value="${esc(v ?? "")}" placeholder="${esc(hint || "")}"></div>`;
  };
  view.innerHTML = `
    <div class="page-head"><div><h1>Settings</h1><p class="sub">Saved to <code>studio_data/settings.json</code>. Changes apply to the next job.</p></div></div>
    <datalist id="set-models">${models.map((m) => `<option value="${esc(m)}">`).join("")}</datalist>
    ${SETTING_FIELDS.map(([title, sub, fields]) => `<div class="card form-grid"><h3 class="form-sec full">${esc(title)}</h3><p class="muted small full" style="margin:-4px 0 0">${esc(sub)}</p>${fields.map(field).join("")}</div>`).join("")}
    <details class="card adv"><summary>Advanced: all overrides as JSON</summary>
      <p class="muted small">Model file names, workflow options and anything not shown above. Model names must match ComfyUI's lists exactly. Fields above win over the same keys here only if you changed them.</p>
      <div class="form-grid"><div><label for="user">Your overrides</label><textarea id="user" class="mono" spellcheck="false" style="min-height:360px">${esc(JSON.stringify(d.user, null, 2))}</textarea></div>
      <div><label>Effective settings (read only)</label><pre class="prompt" style="max-height:392px;overflow:auto">${esc(JSON.stringify(d.effective, null, 2))}</pre></div></div></details>
    <div class="row save-bar page-save"><button class="primary" id="save">Save settings</button><span class="muted small" id="save-note"></span></div>`;
  $("#save").onclick = async () => {
    let obj; try { obj = JSON.parse($("#user").value || "{}"); } catch (e) { return toast("Advanced JSON is invalid: " + e.message, true); }
    const bad = [];
    for (const [, , fields] of SETTING_FIELDS) for (const [path, label, type, hint, min, max] of fields) {
      const el = $("#set-" + path.replace(/\./g, "-")); let v;
      if (type === "bool") v = el.checked;
      else if (type === "int" || type === "num") {
        v = el.value.trim() === "" ? NaN : Number(el.value);
        if (!Number.isFinite(v) || (type === "int" && !Number.isInteger(v)) || v < min || v > max) { bad.push(`${label}: enter ${type === "int" ? "a whole number" : "a number"} from ${min} to ${max}`); el.setAttribute("aria-invalid", "true"); continue; }
      } else v = el.value.trim();
      el.removeAttribute("aria-invalid");
      if (v !== getPath(d.effective, path) && !(v === "" && getPath(d.effective, path) == null)) setPath(obj, path, v);
    }
    if (bad.length) return toast(bad.join("\n"), true);
    await PUT("/api/settings", obj); App.dirty = false; toast("Settings saved."); refreshStatus(); route();
  };
}

// ── AI models page: connections (keys, models) and default choices ─────────
async function viewAI(view) {
  crumbs([["AI models", null]]);
  const ai = await loadAI();
  if (!ai) { view.innerHTML = `<p class="muted">Could not load the AI settings.</p>`; return; }
  let conns = ai.connections.map((c) => ({ ...c }));
  const keys = {};                 // typed, not yet saved keys (never echoed back by the server)
  const listed = {};               // model lists from "List models"
  const opt = (v, t, cur) => `<option value="${esc(v)}" ${v === cur ? "selected" : ""}>${esc(t)}</option>`;
  const textChoices = (role) => {
    const opts = ai.choices.text.slice(), def = ai.defaults[role];
    if (def && !opts.some((o) => o.ref === def)) opts.unshift({ ref: def, label: ai.labels[role] });
    return opts.map((o) => opt(o.ref, o.label + (o.ready === false ? " · no key yet" : ""), def)).join("");
  };
  const keyChip = (c) => c.key === "env" ? `<span class="chip good">key from ${esc(c.api_key_env)}</span>`
    : c.key === "saved" ? `<span class="chip good">key saved</span>` : `<span class="chip warn">no key</span>`;
  const card = (c, i) => {
    const f = (k, label, extra = "") => `<div><label for="c${i}-${k}">${label}</label><input id="c${i}-${k}" data-i="${i}" data-k="${k}" value="${esc(c[k] ?? "")}" ${extra}></div>`;
    return `<div class="card form-grid" data-conn="${i}">
      <h3 class="form-sec full row" style="gap:8px">${esc(c.name || c.id)} <span class="chip">${esc(ai.types[c.type] || c.type)}</span> ${c._new ? `<span class="chip warn">not saved yet</span>` : keyChip(c)}
        <span class="grow"></span><label class="row check" style="margin:0"><input type="checkbox" data-i="${i}" data-k="enabled" ${c.enabled !== false ? "checked" : ""}> Show in pickers</label></h3>
      ${f("name", "Name")}
      <div><label for="c${i}-id">ID <span class="muted small">(used in model choices)</span></label><input id="c${i}-id" data-i="${i}" data-k="id" value="${esc(c.id)}" ${c._new ? "" : "readonly"}></div>
      <div><label for="c${i}-type">Type</label><select id="c${i}-type" data-i="${i}" data-k="type" ${c._new ? "" : "disabled"}>${Object.entries(ai.types).map(([v, t]) => opt(v, t, c.type)).join("")}</select></div>
      ${f("base_url", "Address (API base URL)")}
      <div class="full"><label for="c${i}-key">API key</label><div class="row"><input id="c${i}-key" class="grow" type="password" autocomplete="off" data-key="${i}"
        placeholder="${c.key === "env" ? "Set by " + esc(c.api_key_env) + " on the server (that wins)" : c.key === "saved" ? "Saved. Paste a new key to replace it" : "Paste your API key"}" value="${esc(keys[i] || "")}">
        ${c.key === "saved" && !c._new ? `<button type="button" class="danger" data-delkey="${i}">Remove key</button>` : ""}</div>
        <p class="muted small" style="margin:4px 0 0">Stored in studio_data/secrets.json (owner-only), never shown again. Or set the environment variable below on the server.</p></div>
      ${f("api_key_env", "Environment variable (optional)", 'placeholder="e.g. OPENROUTER_API_KEY"')}
      ${f("max_refs", "Max reference photos sent", 'type="number" min="0" max="16"')}
      ${f("text_model", "Default text model (writing, checks)", `list="ml-${i}" autocomplete="off"`)}
      ${f("image_model", "Default image model (blank: none)", `list="ml-${i}" autocomplete="off"`)}
      <div class="full"><label for="c${i}-extra_models">More models to offer in the pickers <span class="muted small">(comma separated; names containing "image" go to image pickers)</span></label>
        <input id="c${i}-extra_models" data-i="${i}" data-k="extra_models" value="${esc(c.extra_models || "")}"></div>
      ${c.type === "openai" ? `<div><label for="c${i}-q">Image quality</label><select id="c${i}-q" data-i="${i}" data-k="image_quality">${[["", "Default"], ["low", "Low"], ["medium", "Medium"], ["high", "High"]].map(([v, t]) => opt(v, t, c.image_quality || "")).join("")}</select></div>` : ""}
      <datalist id="ml-${i}">${(listed[i] || []).map((m) => `<option value="${esc(m)}">`).join("")}</datalist>
      <div class="full row">
        <button type="button" data-list="${i}" ${c._new ? "disabled title=\"Save first\"" : ""}>List models</button>
        <button type="button" data-test="${i}" ${c._new ? "disabled title=\"Save first\"" : ""}>Test connection</button>
        <span class="muted small grow" id="c${i}-msg" role="status">${listed[i] ? listed[i].length + " models found: pick one in the model fields" : ""}</span>
        <button type="button" class="link danger" data-remove="${i}">Remove connection</button></div>
    </div>`;
  };
  const render = () => {
    view.innerHTML = `
      <div class="page-head"><div><h1>AI models</h1><p class="sub">Connect ChatGPT, Gemini or any OpenAI-compatible service with an API key. Choose the defaults here; every Plan, Render and Check button also lets you pick a model for that run.</p></div></div>
      <div class="card form-grid"><h3 class="form-sec full">Defaults</h3>
        <div><label for="def-planner">Write the bible draft, scenes and shots with</label><select id="def-planner">${textChoices("planner")}</select></div>
        <div><label for="def-vision">Check rendered frames with</label><select id="def-vision">${textChoices("vision")}</select></div>
        <p class="muted small full" style="margin:0">Images: pick a cloud image model as a shot's frame source, in "Force…" when rendering frames, or on a character's or location's page. Cloud models receive your prompts and reference photos and bill your account with that provider. A ChatGPT or Gemini app subscription does not include API use.</p></div>
      <h2>Connections</h2>
      ${conns.map(card).join("")}
      <div class="card row"><label for="preset" style="margin:0">Add a connection</label><select id="preset">${ai.presets.map((p, k) => `<option value="${k}">${esc(p.label)}</option>`).join("")}</select><button id="add">Add</button></div>
      <div class="row save-bar page-save"><button class="primary" id="save-ai">Save</button><span class="muted small" id="ai-note"></span></div>`;
    bind();
  };
  const collect = () => {
    view.querySelectorAll("[data-k]").forEach((el) => {
      const c = conns[+el.dataset.i]; if (!c) return;
      c[el.dataset.k] = el.type === "checkbox" ? el.checked : el.dataset.k === "max_refs" ? Number(el.value) : el.value.trim();
    });
    view.querySelectorAll("[data-key]").forEach((el) => { keys[+el.dataset.key] = el.value.trim(); });
  };
  const save = async () => {
    collect();
    const body = { connections: conns.map(({ key, _new, ...c }) => c), defaults: { planner: $("#def-planner").value, vision: $("#def-vision").value } };
    let next = await PUT("/api/ai", body);
    for (const [i, k] of Object.entries(keys)) if (k && conns[i]) next = await PUT(`/api/ai/${encodeURIComponent(conns[i].id)}/key`, { key: k });
    for (const k of Object.keys(keys)) delete keys[k];
    App.ai = next; App.dirty = false; toast("AI models saved."); refreshStatus(); return viewAI(view);
  };
  function bind() {
    view.querySelectorAll("input,select").forEach((el) => el.addEventListener("input", () => { App.dirty = true; }));
    $("#add").onclick = () => {
      collect();
      const p = { ...ai.presets[+$("#preset").value] }, ids = new Set(conns.map((c) => c.id));
      let id = p.id, n = 2; while (ids.has(id)) id = `${p.id}${n++}`;
      conns.push({ id, name: p.name, type: p.type, base_url: p.base_url, api_key_env: p.api_key_env || "", text_model: "", image_model: "",
        image_quality: "", max_refs: 6, extra_models: "", enabled: true, key: "", _new: true });
      App.dirty = true; render(); view.querySelector(`[data-conn="${conns.length - 1}"]`)?.scrollIntoView({ behavior: "smooth" });
    };
    view.querySelectorAll("[data-remove]").forEach((b) => b.onclick = async () => {
      const c = conns[+b.dataset.remove];
      if (!(await ask(`Remove ${c.name}? Its saved key is deleted when you save.`, "Remove", true))) return;
      collect(); conns.splice(+b.dataset.remove, 1); App.dirty = true; render();
    });
    view.querySelectorAll("[data-delkey]").forEach((b) => b.onclick = async () => {
      const c = conns[+b.dataset.delkey];
      if (!(await ask(`Delete the saved API key for ${c.name}?`, "Delete key", true))) return;
      App.ai = await PUT(`/api/ai/${encodeURIComponent(c.id)}/key`, { key: "" }); toast("Key deleted."); viewAI(view);
    });
    view.querySelectorAll("[data-list]").forEach((b) => b.onclick = async () => {
      const i = +b.dataset.list; collect();
      if (keys[i]) return toast("Save first to use the new key.", true);
      try { listed[i] = (await GET(`/api/ai/${encodeURIComponent(conns[i].id)}/models`)).models; render(); }
      catch (e) { $(`#c${i}-msg`).textContent = String(e.message || e); }
    });
    view.querySelectorAll("[data-test]").forEach((b) => b.onclick = async () => {
      const i = +b.dataset.test; collect();
      if (keys[i]) return toast("Save first to use the new key.", true);
      const msg = $(`#c${i}-msg`); msg.textContent = "Testing…"; b.disabled = true;
      try { const r = await POST(`/api/ai/${encodeURIComponent(conns[i].id)}/test`, { model: conns[i].text_model }); msg.textContent = (r.ok ? "✓ " : "✗ ") + r.message; }
      catch (e) { msg.textContent = "✗ " + String(e.message || e); }
      b.disabled = false;
    });
    $("#save-ai").onclick = () => save().catch((e) => toast(String(e.message || e), true));
  }
  render();
}

async function viewModels(view) {
  crumbs([["Models", null]]);
  const [settings, models] = await Promise.all([GET("/api/settings"), GET("/api/models")]);
  const cfg = settings.effective, face = cfg.face_correction;
  view.innerHTML = `<h1>Models</h1><h2>Video generation</h2>
    <p>Choose a model when rendering in Takes, or set a project's default engine in its Style &amp; props.</p>
    <div class="card form-grid"><div><label for="new-engine">Default video engine for new projects</label><select id="new-engine">${engineOptions(cfg.style_defaults?.default_engine || "infinitetalk")}</select></div></div>
    <div class="card model-list">${models.map(m => { const [nm, ...rest] = String(m.name).split(" · "); return `<div class="model-row"><b>${esc(nm)}</b><span class="grow"></span>${rest.join(" · ").split(", ").filter(Boolean).map(t => `<span class="chip ${/cloud/.test(t) ? "warn" : /silent/.test(t) ? "" : "good"}">${esc(t)}</span>`).join("")}</div>`; }).join("")}</div>
    <h2>Lip-sync · local</h2><p>LatentSync 1.6 synchronizes existing shots to an uploaded music or voice recording. Optional Demucs vocal isolation separates the singing voice for guidance while keeping the full music mix in the export. Upload audio in Cut, choose the character and shots, then review the new version. Runs in ComfyUI on this PC with no paid API.</p>
    <h2>MiniMax H3 · local</h2><p>Generates video and sound on this PC through ComfyUI, with no API key or per-generation API fee. Uses quantized open weights and 4-step Turbo sampling. Local clips are at least 4 seconds; trim shorter shots in Takes. Model weight license: <a href="https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE" target="_blank" rel="noopener">MiniMax H3 Community License</a>.</p>
    <h2>MiniMax cloud (optional, paid)</h2><p>Cloud generation sends your first frame and prompt to MiniMax and uses your paid API account. Set <code>${esc(cfg.minimax.api_key_env)}</code> in the server environment, then restart Studio.</p>
    <div class="card form-grid"><div><label>Default MiniMax model</label><select id="mm-model">${models.filter(m => m.engine === "minimax").map(m => `<option value="${esc(m.id)}" ${m.id === cfg.minimax.model ? "selected" : ""}>${esc(m.name)}</option>`).join("")}</select></div>
    <div><label>Resolution</label><select id="mm-resolution">${["768P", "1080P"].map(x => `<option ${x === cfg.minimax.resolution ? "selected" : ""}>${x}</option>`).join("")}</select></div></div>
    <h2>Face correction</h2><p>Qwen Image Edit corrects each original frame using the uploaded character portrait and retains the source soundtrack. It is also available on First frames. MiniMax H3 edits single-character videos up to 15 seconds. KEEP restores a single main face over time; CodeFormer restores multiple faces. Each correction adds a version for review and retains your original. See STUDIO.md for model installation.</p>
    <div class="card form-grid"><div><label>Default correction model</label><select id="face-model"><option value="qwen_identity" ${face.model === "qwen_identity" ? "selected" : ""}>Qwen Image Edit · every frame</option><option value="minimax_identity" ${face.model === "minimax_identity" ? "selected" : ""}>MiniMax H3 · match character references</option><option value="keep" ${face.model === "keep" ? "selected" : ""}>KEEP · temporal video restoration</option><option value="codeformer" ${face.model === "codeformer" ? "selected" : ""}>CodeFormer · multiple faces</option></select></div>
    <div><label>Restorer Python executable (blank: Studio Python)</label><input id="face-python" value="${esc(face.python)}"></div>
    <div><label>KEEP installation folder</label><input id="keep-dir" value="${esc(face.keep_dir)}"></div>
    <div><label>CodeFormer installation folder</label><input id="codeformer-dir" value="${esc(face.codeformer_dir)}"></div>
    <div><label>CodeFormer identity fidelity (0–1)</label><input type="number" id="face-fidelity" min="0" max="1" step="0.05" value="${face.fidelity}"></div></div>
    <details><summary>Local character reference files</summary><div class="form-grid">
    <div><label>MiniMax Ref2VA checkpoint</label><input id="identity-unet" value="${esc(face.identity_unet)}"></div>
    <div><label>Character Swap adapter</label><input id="identity-lora" value="${esc(face.identity_lora)}"></div>
    <div><label>Eight-step Turbo adapter</label><input id="identity-turbo" value="${esc(face.identity_turbo_lora)}"></div></div></details>
    <button class="primary" id="save-models">Save model settings</button>`;
  $("#save-models").onclick = async () => {
    const user = {...settings.user,
      style_defaults: {...(settings.user.style_defaults || {}), default_engine: $("#new-engine").value},
      minimax: {...(settings.user.minimax || {}), model: $("#mm-model").value, resolution: $("#mm-resolution").value},
      face_correction: {...(settings.user.face_correction || {}), model: $("#face-model").value,
        python: $("#face-python").value.trim(), keep_dir: $("#keep-dir").value.trim(),
        codeformer_dir: $("#codeformer-dir").value.trim(), fidelity: Number($("#face-fidelity").value),
        identity_unet: $("#identity-unet").value.trim(), identity_lora: $("#identity-lora").value.trim(),
        identity_turbo_lora: $("#identity-turbo").value.trim()}};
    await PUT("/api/settings", user); toast("Model settings saved.");
  };
}

refreshStatus();
setInterval(refreshStatus, 15000);
pollJobs();
setInterval(pollJobs, 2000);
// The model pickers need /api/ai; wait for it briefly before the first render (never re-render afterwards).
window.addEventListener("load", () => Promise.race([loadAI(), new Promise((r) => setTimeout(r, 1500))]).finally(route));

// Visible "unsaved changes" state (App.dirty is set by edits and cleared on save).
(() => {
  const chip = document.createElement("span");
  chip.className = "chip warn dirty-chip"; chip.textContent = "Unsaved changes"; chip.hidden = true; chip.setAttribute("role", "status");
  $("#crumbs").after(chip);
  setInterval(() => { chip.hidden = !App.dirty; }, 400);
})();

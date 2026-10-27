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
function modal(html) { $("#modal-body").innerHTML = html; $("#modal").classList.remove("hidden"); }
let askDone = null;
function closeModal() {
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

function sourceLabel(s) {
  return { flux_lora: "Flux + LoRA", zimage: "Z-Image", qwen_refs: "Reference photos (Qwen Edit)", auto: "Auto (LoRA > photos > Z-Image)", upload: "Upload", reuse: "Reuse master angle", chain: "Last frame of prev take", punch_in: "Punch-in from master" }[s] || s || "inherit";
}

// ── sidebar ──────────────────────────────────────────────────────────────
function renderNav() {
  const [a, sid, kind, id] = App.route;
  const d = a === "s" ? App.sd[sid] : null;
  const item = (href, icon, label, extra = "", sub = false) => {
    const on = location.hash.replace(/\/(story|shots|frames|animatic|takes|cut)$/, "") === href.replace(/\/(story|shots|frames|animatic|takes|cut)$/, "");
    return `<a class="nav-item ${sub ? "sub" : ""} ${on ? "on" : ""}" href="${href}" data-i="${esc(label.trim().charAt(0).toUpperCase())}" title="${esc(label)}"><span class="lbl">${esc(label)}</span>${extra}</a>`;
  };
  let html = item("#/", "folder", "Projects") + item("#/settings", "settings", "Settings");
  if (d) {
    const chars = Object.values(d.bible.characters), locs = Object.values(d.bible.locations);
    html += `<div class="nav-sec">${esc(d.series.name)}</div>` +
      item(`#/s/${sid}`, "folder", "Overview") + item(`#/s/${sid}/style`, "palette", "Style & props") +
      `<div class="nav-sec">Episodes</div>` + d.episodes.map((e) => item(`#/s/${sid}/e/${e.id}/story`, "clapper", e.title, `<span class="count">${e.shots}</span>`)).join("") +
      `<div class="nav-sec">Characters</div>` + chars.slice(0, 10).map((c) => item(`#/s/${sid}/c/${c.id}`, "user", c.name, c.draft ? `<span class="count">draft</span>` : "")).join("") +
      `<div class="nav-sec">Locations</div>` + locs.slice(0, 10).map((l) => item(`#/s/${sid}/l/${l.id}`, "pin", l.name, l.draft ? `<span class="count">draft</span>` : "")).join("");
  }
  $("#nav").innerHTML = html;
}
async function getSeries(sid) {
  const d = await GET(`/api/s/${sid}`);
  App.sd[sid] = d; renderNav();
  return d;
}
function setCollapsed(c) { $("#app").classList.toggle("collapsed", c); store.set("rw-collapse", c ? "1" : "0"); }
$("#collapse-btn").onclick = () => setCollapsed(!$("#app").classList.contains("collapsed"));
$("#menu-btn").onclick = () => $("#app").classList.toggle("menu");
if (store.get("rw-collapse") === "1") setCollapsed(true);
function setTheme(t) {
  document.documentElement.dataset.theme = t; store.set("rw-theme", t);
  $("#theme-btn").innerHTML = ic(t === "dark" ? "moon" : "sun");
}
$("#theme-btn").onclick = () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
setTheme(document.documentElement.dataset.theme || "light");
const narrow = window.matchMedia("(max-width: 980px)");
const syncNarrow = () => { $("#menu-btn").style.display = narrow.matches ? "" : "none"; };
narrow.addEventListener("change", syncNarrow); syncNarrow();

// ── status + jobs drawer ─────────────────────────────────────────────────
async function refreshStatus() {
  try {
    const s = await GET("/api/status");
    App.status = s; App.loras = s.loras || [];
    const line = (ok, text, title) => `<div class="line" title="${esc(title || text)}"><span class="dot ${ok ? "ok" : "bad"}"></span><span class="lbl">${esc(text)}</span></div>`;
    $("#status").innerHTML = line(s.comfy, s.comfy ? "ComfyUI online" : "ComfyUI offline", s.comfy_host) +
      line(s.ollama_models.length > 0, s.ollama_models.length ? "Ollama · " + s.planner_model : "Ollama offline", s.planner_model) +
      (s.missing.length ? `<div class="line"><span class="chip warn" title="${esc(s.missing.join("\n"))}">${s.missing.length} missing models</span></div>` : "");
  } catch { $("#status").innerHTML = `<div class="line"><span class="dot bad"></span><span class="lbl">Server unreachable</span></div>`; }
}

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
    if ($("#lightbox").classList.contains("on")) $("#lightbox").classList.remove("on");
    else if ($("#palette").classList.contains("on")) openPalette(false);
    else if ($("#jobs").classList.contains("on")) openJobs(false);
    else closeModal();
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
  App.refreshers = []; App.dirty = false; App.dirtyWarned = false;
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  const first = !App.route.length || App.route.join("/") !== parts.join("/");
  App.route = parts;
  $("#app").classList.remove("menu");
  const view = $("#view");
  if (first) { view.innerHTML = SKELETON; view.style.animation = "none"; void view.offsetHeight; view.style.animation = ""; }
  renderNav();
  try {
    if (!parts.length) return await viewHome(view);
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
  } catch (err) {
    view.innerHTML = `<div class="notice">${ic("alert")}<span>${esc(err.message)}</span></div>`;
  }
}
window.addEventListener("hashchange", route);
$("#view").addEventListener("input", (e) => { if (!e.target.matches("input[type=file]")) App.dirty = true; });
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
const sourceSelect = (id, value, allowInherit = true) =>
  `<select id="${id}">${(allowInherit ? [""] : []).concat(["flux_lora", "zimage", "qwen_refs", "auto", "upload"]).map((o) => `<option value="${o}" ${o === (value || "") ? "selected" : ""}>${o ? sourceLabel(o) : "inherit"}</option>`).join("")}</select>`;

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
      <div><label>Name</label><input id="f-name" value="${esc(c.name)}"></div>
      <div><label>Trigger token (from LoRA training, e.g. ohwx_sam)</label><input id="f-trigger" value="${esc(c.trigger)}"></div>
      <div class="full"><label>Locked appearance (50-80 words, copied verbatim into every prompt)</label><textarea id="f-app">${esc(c.appearance)}</textarea></div>
      <div class="full"><label>Outfits - one per line, "name: description"</label><textarea id="f-outfits">${esc(kvToText(c.outfits))}</textarea></div>
      <div><label>Default outfit</label><input id="f-defout" value="${esc(c.default_outfit)}"></div>
      <div><label>Never change (comma separated)</label><input id="f-never" value="${esc((c.never_change || []).join(", "))}"></div>
      <div class="full"><label>Acting notes</label><textarea id="f-acting" style="min-height:44px">${esc(c.acting_notes)}</textarea></div>
      <div><label>Image LoRA (Flux 1)</label>${loraSelect("f-ilora", c.image_lora?.name)}</div>
      <div><label>Image LoRA strength</label><input id="f-ilora-s" type="number" step="0.05" value="${c.image_lora?.strength ?? 0.9}"></div>
      <div><label>Video LoRA (Wan 2.2, optional)</label>${loraSelect("f-vlora", c.video_lora?.name)}</div>
      <div><label>Video LoRA strength</label><input id="f-vlora-s" type="number" step="0.05" value="${c.video_lora?.strength ?? 0.8}"></div>
      <div><label>First-frame source</label>${sourceSelect("f-src", c.frame_source)}</div>
      <div><label>Speaking rate (words per minute)</label><input id="f-wpm" type="number" value="${c.voice?.wpm ?? 140}"></div>
      <div class="full"><label>Voice description (goes into the video prompt)</label><input id="f-voice" value="${esc(c.voice?.description)}"></div>
      <div class="full row" style="margin-top:12px"><button class="primary" id="save">Save</button><span class="grow"></span><button class="danger" id="del">Delete character</button></div>
    </div>
    <h2>Voice sample (5 s .wav for LTX voice identity)</h2>
    <div class="card row">${c.voice?.file ? `<audio controls src="${media(c.voice.file)}"></audio>` : `<span class="muted">none</span>`}
      <input type="file" id="voice-file" accept="audio/*"><button id="voice-up">Upload voice</button></div>
    <h2>References <span class="muted small">(approved identity images)</span></h2>
    <div class="cards">${(c.refs || []).map((r) => `<div class="card"><img class="thumb square" src="${media(r)}" data-zoom="${media(r)}"><button class="link danger" data-unref="${esc(r)}">remove</button></div>`).join("") || `<p class="muted">None yet. Render a character sheet below and approve the best images, or upload your own.</p>`}</div>
    <div class="row" style="margin-top:8px"><input type="file" id="ref-file" accept="image/*"><button id="ref-up">Upload reference</button></div>
    <h2>Character sheet</h2>
    <div class="row"><button class="primary" id="sheet-flux">Render sheet with Flux + LoRA</button><button id="sheet-z">Render with Z-Image</button>
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
  const upload = async (kind, input) => {
    const f = $(input).files[0]; if (!f) return toast("Choose a file first", true);
    const fd = new FormData(); fd.append("kind", kind); fd.append("file", f);
    await FORM(`/api/s/${sid}/characters/${cid}/upload`, fd); route();
  };
  $("#voice-up").onclick = () => upload("voice", "#voice-file");
  $("#ref-up").onclick = () => upload("ref", "#ref-file");
  view.onclick = async (e) => {
    const t = e.target;
    if (t.dataset.zoom) zoom(t.dataset.zoom);
    if (t.dataset.ref) { await POST(`/api/s/${sid}/characters/${cid}/refs`, { add: t.dataset.ref }); route(); }
    if (t.dataset.unref) { await POST(`/api/s/${sid}/characters/${cid}/refs`, { remove: t.dataset.unref }); route(); }
  };
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
      <div class="full row" style="margin-top:12px"><button class="primary" id="save">Save</button><span class="grow"></span><button class="danger" id="del">Delete location</button></div>
    </div>
    <h2>Establishing plates</h2>
    <div class="row"><select id="p-var">${variants.map((v) => `<option>${esc(v)}</option>`).join("")}</select>
      <button class="primary" id="gen-z">Render 4 with Z-Image</button><button id="gen-f">Render 4 with Flux</button>
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
      <div><label>Default video engine</label><select id="f-eng">${["ltx", "wan"].map((o) => `<option value="${o}" ${o === st.default_engine ? "selected" : ""}>${o === "ltx" ? "LTX-2.3 (sound, dialogue)" : "Wan 2.2 (silent, 4-step)"}</option>`).join("")}</select></div>
      <div class="full"><label>Music policy (notes)</label><input id="f-music" value="${esc(st.music_policy)}"></div>
      <div class="full"><label>Props - one per line, "id: description"</label><textarea id="f-props">${esc((d.bible.props || []).map((p) => `${p.id}: ${p.description}`).join("\n"))}</textarea></div>
      <div class="full" style="margin-top:12px"><button class="primary" id="save">Save</button></div>
    </div>
    <p class="muted small">Shots without any LoRA character use Z-Image automatically when the default is Flux + LoRA.</p>`;
  $("#save").onclick = async () => {
    await PUT(`/api/s/${sid}/style`, { look: $("#f-look").value, grade: $("#f-grade").value, negative: $("#f-neg").value, default_frame_source: $("#f-src").value, default_engine: $("#f-eng").value, music_policy: $("#f-music").value });
    await PUT(`/api/s/${sid}/props`, Object.entries(textToKv($("#f-props").value)).map(([id, description]) => ({ id, description })));
    toast("Saved."); route();
  };
}

// ── settings ────────────────────────────────────────────────────────────
async function viewSettings(view) {
  crumbs([["Settings", null]]);
  const d = await GET("/api/settings");
  view.innerHTML = `
    <h1>Settings</h1>
    <p class="muted">Your overrides (JSON). Anything not listed uses the default shown on the right. Model names must match ComfyUI's lists exactly.</p>
    <div class="form-grid"><div><label>Your overrides</label><textarea id="user" style="min-height:420px;font-family:monospace">${esc(JSON.stringify(d.user, null, 2))}</textarea>
      <div style="margin-top:8px"><button class="primary" id="save">Save</button></div></div>
      <div><label>Effective settings</label><pre class="prompt" style="max-height:470px;overflow:auto">${esc(JSON.stringify(d.effective, null, 2))}</pre></div></div>`;
  $("#save").onclick = async () => {
    let obj; try { obj = JSON.parse($("#user").value || "{}"); } catch (e) { return toast("Invalid JSON: " + e.message, true); }
    await PUT("/api/settings", obj); toast("Saved."); refreshStatus(); route();
  };
}

refreshStatus();
setInterval(refreshStatus, 15000);
pollJobs();
setInterval(pollJobs, 2000);
window.addEventListener("load", route);

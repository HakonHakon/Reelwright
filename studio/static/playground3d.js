import * as THREE from "/static/vendor/three.module.js";

const clamp = (n, a, b) => Math.max(a, Math.min(b, n));
const colors = ["#c98761", "#7d9da4", "#c9b880", "#9285a8", "#789b73", "#b06f67"];

export class DirectorViewport {
  constructor(host, { characters = [], camera = {}, onChange = () => {} } = {}) {
    this.host = host;
    this.onChange = onChange;
    this.cameraState = { azimuth: +(camera.azimuth ?? 0), height: +(camera.height ?? 5.5), distance: +(camera.distance ?? 13), lens: +(camera.lens ?? 50) };
    this.mode = "move";
    this.selectedId = null;
    this.entities = new Map();
    this.highlight = null;
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color("#10171a");
    this.scene.fog = new THREE.Fog("#10171a", 25, 70);
    this.camera = new THREE.PerspectiveCamera(42, 1, 0.1, 160);
    this.cameraTarget = new THREE.Vector3(0, 0.6, 0);
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.setClearColor("#10171a");
    this.renderer.domElement.className = "pg3-canvas";
    this.renderer.domElement.tabIndex = 0;
    this.renderer.domElement.setAttribute("aria-label", "Interactive 3D scene viewport. Drag objects to move them; drag empty space to orbit the view.");
    host.replaceChildren(this.renderer.domElement);
    this.scene.add(new THREE.HemisphereLight("#d9e7f0", "#282d2b", 2.0));
    const key = new THREE.DirectionalLight("#ffe3c1", 2.4); key.position.set(6, 12, 8); this.scene.add(key);
    const fill = new THREE.DirectionalLight("#8ebbd0", 0.8); fill.position.set(-10, 5, -4); this.scene.add(fill);
    const ground = new THREE.Mesh(new THREE.PlaneGeometry(120, 120), new THREE.MeshStandardMaterial({ color: "#202827", roughness: 0.96 }));
    ground.rotation.x = -Math.PI / 2; ground.position.y = -0.07; ground.name = "ground"; this.scene.add(ground);
    const grid = new THREE.GridHelper(60, 60, "#72827c", "#394541"); grid.position.y = -0.045; grid.material.transparent = true; grid.material.opacity = 0.38; this.scene.add(grid);
    this.addStreet();
    this.raycaster = new THREE.Raycaster(); this.pointer = new THREE.Vector2(); this.floor = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
    this.drag = null;
    this.resize = new ResizeObserver(() => this.onResize()); this.resize.observe(host); this.onResize();
    this.onPointerDown = e => this.pointerDown(e); this.onPointerMove = e => this.pointerMove(e); this.onPointerUp = e => this.pointerUp(e); this.onWheel = e => this.wheel(e);
    this.renderer.domElement.addEventListener("pointerdown", this.onPointerDown);
    this.renderer.domElement.addEventListener("pointermove", this.onPointerMove);
    this.renderer.domElement.addEventListener("pointerup", this.onPointerUp);
    this.renderer.domElement.addEventListener("pointercancel", this.onPointerUp);
    this.renderer.domElement.addEventListener("wheel", this.onWheel, { passive: false });
    this.onKeyDown = e => { const mode = { w: "move", e: "rotate", r: "scale" }[e.key.toLowerCase()]; if (mode) { this.setMode(mode); this.onChange(this.getState()); } };
    this.renderer.domElement.addEventListener("keydown", this.onKeyDown);
    this.setCharacters(characters);
    this.updateCamera();
    this.running = true; this.renderLoop();
  }
  addStreet() {
    const sidewalk = new THREE.Mesh(new THREE.BoxGeometry(18, 0.12, 12), new THREE.MeshStandardMaterial({ color: "#424845", roughness: 0.95 }));
    sidewalk.position.set(0, 0, -2); sidewalk.name = "sidewalk"; this.scene.add(sidewalk);
    const road = new THREE.Mesh(new THREE.PlaneGeometry(32, 9), new THREE.MeshStandardMaterial({ color: "#272d2e", roughness: 0.9 }));
    road.rotation.x = -Math.PI / 2; road.position.set(0, -0.005, 8); this.scene.add(road);
    for (let x = -14; x <= 14; x += 5) {
      const dash = new THREE.Mesh(new THREE.BoxGeometry(2, 0.015, 0.09), new THREE.MeshBasicMaterial({ color: "#d7c79a" }));
      dash.position.set(x, 0.01, 8); this.scene.add(dash);
    }
    this.environment = new THREE.Group(); this.scene.add(this.environment); this.buildLocation("Location");
  }
  buildLocation(name) {
    this.environment.traverse(o => {
      if (o.geometry) o.geometry.dispose();
      const materials = Array.isArray(o.material) ? o.material : [o.material];
      materials.filter(Boolean).forEach(m => { m.map?.dispose?.(); m.dispose?.(); });
    });
    this.environment.clear();
    const wall = new THREE.Mesh(new THREE.BoxGeometry(14, 5, 0.7), new THREE.MeshStandardMaterial({ color: "#606763", roughness: 0.86 }));
    wall.position.set(0, 2.5, -8); this.environment.add(wall);
    const awning = new THREE.Mesh(new THREE.BoxGeometry(14.2, 0.18, 1.15), new THREE.MeshStandardMaterial({ color: "#303b39", roughness: 0.7 }));
    awning.position.set(0, 4.45, -7.25); this.environment.add(awning);
    for (let x = -4.6; x <= 4.7; x += 4.6) {
      const glass = new THREE.Mesh(new THREE.BoxGeometry(3.3, 2.45, 0.08), new THREE.MeshStandardMaterial({ color: "#91b7b9", emissive: "#283c3c", emissiveIntensity: 0.65, roughness: 0.28, metalness: 0.12 }));
      glass.position.set(x, 2.35, -7.59); this.environment.add(glass);
      const frame = new THREE.Mesh(new THREE.BoxGeometry(3.48, 2.62, 0.07), new THREE.MeshStandardMaterial({ color: "#242d2b", roughness: 0.55 }));
      frame.position.set(x, 2.35, -7.64); this.environment.add(frame); frame.material.color.set("#242d2b");
      // Keep the window pane in front of its simple dark frame.
      this.environment.remove(glass); this.environment.add(glass);
    }
    const signCanvas = document.createElement("canvas"); signCanvas.width = 512; signCanvas.height = 80;
    const ctx = signCanvas.getContext("2d"); ctx.fillStyle = "#e8e2d3"; ctx.font = "600 34px sans-serif"; ctx.textAlign = "center"; ctx.fillText(String(name).slice(0, 30).toUpperCase(), 256, 52);
    const texture = new THREE.CanvasTexture(signCanvas); texture.colorSpace = THREE.SRGBColorSpace;
    const sign = new THREE.Mesh(new THREE.PlaneGeometry(5.3, 0.8), new THREE.MeshBasicMaterial({ map: texture, transparent: true }));
    sign.position.set(0, 4.02, -7.59); this.environment.add(sign);
  }
  makeActor(ch, index) {
    const group = new THREE.Group(); group.userData = { id: ch.id, name: ch.name, kind: "character" };
    const color = new THREE.Color(colors[index % colors.length]);
    const body = new THREE.MeshStandardMaterial({ color, roughness: 0.72 });
    const dark = new THREE.MeshStandardMaterial({ color: color.clone().multiplyScalar(0.66), roughness: 0.8 });
    const skin = new THREE.MeshStandardMaterial({ color: "#d6ad91", roughness: 0.9 });
    const mesh = (geo, mat, x, y, z) => { const o = new THREE.Mesh(geo, mat); o.position.set(x, y, z); o.castShadow = true; o.userData.entityId = ch.id; group.add(o); return o; };
    mesh(new THREE.BoxGeometry(0.72, 0.9, 0.38), body, 0, 1.08, 0);
    mesh(new THREE.SphereGeometry(0.26, 18, 14), skin, 0, 1.78, 0);
    mesh(new THREE.BoxGeometry(0.23, 0.76, 0.27), dark, -0.19, 0.38, 0);
    mesh(new THREE.BoxGeometry(0.23, 0.76, 0.27), dark, 0.19, 0.38, 0);
    mesh(new THREE.BoxGeometry(0.19, 0.75, 0.25), body, -0.5, 1.12, 0);
    mesh(new THREE.BoxGeometry(0.19, 0.75, 0.25), body, 0.5, 1.12, 0);
    group.position.set(+(ch.position?.[0] ?? (index - 0.5) * 2), 0, +(ch.position?.[2] ?? 0));
    group.rotation.y = +(ch.rotation?.[1] ?? 0) * Math.PI / 180;
    group.scale.setScalar(+(ch.scale ?? 1));
    this.scene.add(group); this.entities.set(ch.id, { group, data: ch }); return group;
  }
  setCharacters(chars) {
    const keep = new Set(chars.map(c => c.id));
    for (const [id, item] of this.entities) if (item.data.kind !== "prop" && !keep.has(id)) { this.scene.remove(item.group); this.disposeGroup(item.group); this.entities.delete(id); }
    chars.forEach((ch, i) => { if (!this.entities.has(ch.id)) this.makeActor(ch, i); else this.entities.get(ch.id).data = ch; });
    if (this.selectedId && !this.entities.has(this.selectedId)) { this.selectedId = null; this.setHighlight(null); }
    this.onChange(this.getState());
  }
  addProp(kind) {
    const id = `prop_${crypto.randomUUID().slice(0, 8)}`, group = new THREE.Group();
    const name = kind === "car" ? "Car" : "Block prop";
    group.userData = { id, name, kind: "prop" }; group.position.set(0, 0, 1 + this.entities.size * 0.35);
    const mat = color => new THREE.MeshStandardMaterial({ color, roughness: 0.72 });
    const add = (geo, material, x, y, z) => { const mesh = new THREE.Mesh(geo, material); mesh.position.set(x, y, z); mesh.castShadow = true; mesh.userData.entityId = id; group.add(mesh); };
    if (kind === "car") {
      add(new THREE.BoxGeometry(2.6, 0.72, 1.3), mat("#20282a"), 0, 0.66, 0);
      add(new THREE.BoxGeometry(1.45, 0.68, 1.08), mat("#465356"), -0.12, 1.22, 0);
      const glass = new THREE.MeshStandardMaterial({ color: "#85aeb2", metalness: 0.18, roughness: 0.24 });
      add(new THREE.BoxGeometry(1.28, 0.48, 0.025), glass, -0.12, 1.25, 0.55);
      const tire = mat("#17191a");
      for (const x of [-0.82, 0.85]) for (const z of [-0.65, 0.65]) { const wheel = new THREE.Mesh(new THREE.CylinderGeometry(0.31, 0.31, 0.18, 16), tire); wheel.rotation.z = Math.PI / 2; wheel.position.set(x, 0.35, z); wheel.userData.entityId = id; group.add(wheel); }
    } else add(new THREE.BoxGeometry(1.3, 1.3, 1.3), mat("#ae916a"), 0, 0.65, 0);
    this.scene.add(group); this.entities.set(id, { group, data: { id, name, kind: "prop" } }); this.selectedId = id; this.setHighlight(group); this.onChange(this.getState());
  }
  removeEntity(id) {
    const item = this.entities.get(id); if (!item) return;
    this.scene.remove(item.group); this.disposeGroup(item.group); this.entities.delete(id);
    if (this.selectedId === id) { this.selectedId = null; this.setHighlight(null); }
    this.onChange(this.getState());
  }
  setHighlight(group) {
    if (this.highlight) { this.scene.remove(this.highlight); this.highlight.geometry.dispose(); this.highlight.material.dispose(); this.highlight = null; }
    if (group) { this.highlight = new THREE.BoxHelper(group, "#f08a55"); this.highlight.material.depthTest = false; this.highlight.material.transparent = true; this.highlight.material.opacity = 0.9; this.scene.add(this.highlight); }
  }
  disposeGroup(group) { group.traverse(o => { o.geometry?.dispose?.(); if (Array.isArray(o.material)) o.material.forEach(m => m.dispose()); else o.material?.dispose?.(); }); }
  setLocation(name, plate = "") {
    this.buildLocation(name);
    if (!plate) return;
    const tex = new THREE.TextureLoader().load(plate, () => { tex.colorSpace = THREE.SRGBColorSpace; tex.needsUpdate = true; });
    const backdrop = new THREE.Mesh(new THREE.PlaneGeometry(22, 9), new THREE.MeshBasicMaterial({ map: tex, side: THREE.DoubleSide }));
    backdrop.position.set(0, 4.4, -8.55); this.environment.add(backdrop);
    // Use the approved plate as the environment backdrop while retaining the editable ground blockout.
    this.environment.children.slice(0, -1).forEach(o => { o.visible = false; });
  }
  setMode(mode) { if (["move", "rotate", "scale"].includes(mode)) this.mode = mode; }
  setCameraState(key, value) { this.cameraState[key] = +value; this.updateCamera(); this.onChange(this.getState()); }
  updateCamera() {
    const c = this.cameraState, a = c.azimuth * Math.PI / 180;
    this.camera.position.set(c.distance * Math.sin(a), c.height, c.distance * Math.cos(a));
    this.cameraTarget.set(0, 0.6, 0); this.camera.lookAt(this.cameraTarget);
    this.camera.fov = THREE.MathUtils.radToDeg(2 * Math.atan(36 / (2 * Math.max(18, c.lens)))); this.camera.updateProjectionMatrix();
  }
  onResize() { if (!this.host.clientWidth || !this.host.clientHeight) return; this.renderer.setSize(this.host.clientWidth, this.host.clientHeight, false); this.camera.aspect = this.host.clientWidth / this.host.clientHeight; this.camera.updateProjectionMatrix(); }
  renderLoop() { if (!this.running) return; requestAnimationFrame(() => this.renderLoop()); this.highlight?.update(); this.renderer.render(this.scene, this.camera); }
  setPointer(e) { const r = this.renderer.domElement.getBoundingClientRect(); this.pointer.x = ((e.clientX - r.left) / r.width) * 2 - 1; this.pointer.y = -((e.clientY - r.top) / r.height) * 2 + 1; this.raycaster.setFromCamera(this.pointer, this.camera); }
  hitEntity(e) { this.setPointer(e); const roots = [...this.entities.values()].map(x => x.group); const hits = this.raycaster.intersectObjects(roots, true); return hits.find(h => h.object.userData.entityId)?.object.userData.entityId || null; }
  groundPoint(e) { this.setPointer(e); const p = new THREE.Vector3(); return this.raycaster.ray.intersectPlane(this.floor, p) ? p : null; }
  pointerDown(e) {
    if (e.button !== 0 && e.button !== 2) return;
    this.renderer.domElement.focus({ preventScroll: true });
    this.renderer.domElement.setPointerCapture(e.pointerId);
    const id = e.button === 0 ? this.hitEntity(e) : null;
    if (id) {
      this.selectedId = id; const item = this.entities.get(id), p = this.groundPoint(e); this.setHighlight(item.group);
      this.drag = { type: this.mode, id, x: e.clientX, y: e.clientY, start: p ? p.clone() : null, origin: item.group.position.clone(), rotation: item.group.rotation.y, scale: item.group.scale.x, moved: false };
    } else { if (e.button === 0) { this.selectedId = null; this.setHighlight(null); } this.drag = { type: e.button === 2 ? "pan" : "orbit", x: e.clientX, y: e.clientY, azimuth: this.cameraState.azimuth, height: this.cameraState.height }; }
    this.onChange(this.getState());
  }
  pointerMove(e) {
    if (!this.drag) return;
    const d = this.drag, dx = e.clientX - d.x, dy = e.clientY - d.y;
    if (Math.abs(dx) + Math.abs(dy) > 2) d.moved = true;
    if (d.type === "move" && d.id) {
      const p = this.groundPoint(e); if (p && d.start) { const obj = this.entities.get(d.id).group; obj.position.x = d.origin.x + p.x - d.start.x; obj.position.z = d.origin.z + p.z - d.start.z; }
    } else if (d.type === "rotate" && d.id) this.entities.get(d.id).group.rotation.y = d.rotation + dx * 0.012;
    else if (d.type === "scale" && d.id) this.entities.get(d.id).group.scale.setScalar(clamp(d.scale * Math.exp(-dy * 0.008), 0.45, 2.6));
    else if (d.type === "orbit") { this.cameraState.azimuth = d.azimuth - dx * 0.45; this.cameraState.height = clamp(d.height + dy * 0.045, 1.2, 13); this.updateCamera(); }
    else if (d.type === "pan") { this.cameraTarget.x -= dx * 0.012; this.cameraTarget.z += dy * 0.012; this.camera.position.x -= dx * 0.012; this.camera.position.z += dy * 0.012; this.camera.lookAt(this.cameraTarget); }
    this.onChange(this.getState());
  }
  pointerUp() { this.drag = null; }
  wheel(e) { e.preventDefault(); this.cameraState.distance = clamp(this.cameraState.distance * Math.exp(e.deltaY * 0.001), 5, 35); this.updateCamera(); this.onChange(this.getState()); }
  getState() { return { selectedId: this.selectedId, mode: this.mode, camera: { ...this.cameraState, target: [this.cameraTarget.x, this.cameraTarget.y, this.cameraTarget.z] }, entities: [...this.entities].map(([id, v]) => ({ id, name: v.data.name || id, kind: v.data.kind || "character", position: [v.group.position.x, v.group.position.y, v.group.position.z], rotation: [0, v.group.rotation.y * 180 / Math.PI, 0], scale: v.group.scale.x })) }; }
  focus(id) { if (!this.entities.has(id)) return; this.selectedId = id; this.setHighlight(this.entities.get(id).group); this.onChange(this.getState()); }
  destroy() { this.running = false; this.resize.disconnect(); this.renderer.domElement.removeEventListener("keydown", this.onKeyDown); this.renderer.dispose(); this.renderer.domElement.remove(); }
}

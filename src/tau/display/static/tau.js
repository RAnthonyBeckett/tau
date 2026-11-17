// TAU display: an inverted neon triangle (the frame = TAU's overall state)
// with importance-ranked rings around a dark core (everything TAU tracks).
// Three.js (MIT), vendored so it runs offline on loopback.
import * as THREE from './vendor/three.module.min.js';
import { buildField } from './field.js';

const params = new URLSearchParams(location.search);
const DEMO = params.get('demo');            // idle | listening | thinking | speaking | error | burst
const POPOUT = params.get('mode') === 'popout';
if (POPOUT) document.body.classList.add('popout');
const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------- renderer ---
const stage = document.getElementById('stage');
const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, preserveDrawingBuffer: !!DEMO });
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
renderer.setClearColor(0x040203, 1);
stage.appendChild(renderer.domElement);
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(32, 1, 0.1, 100);
camera.position.set(0, 0, 13);

const rig = new THREE.Group();    // tilts with the mouse (parallax)
rig.position.y = 0.55;
scene.add(rig);

// ------------------------------------------------------------- background ---
function marbleTexture() {
  const c = document.createElement('canvas'); c.width = c.height = 1024;
  const g = c.getContext('2d');
  const grd = g.createRadialGradient(512, 470, 60, 512, 512, 720);
  grd.addColorStop(0, '#120806'); grd.addColorStop(0.6, '#080404'); grd.addColorStop(1, '#020101');
  g.fillStyle = grd; g.fillRect(0, 0, 1024, 1024);
  let seed = 7; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  for (let v = 0; v < 70; v++) {                      // faint marble veins
    let x = rnd() * 1024, y = rnd() * 1024, a = rnd() * Math.PI * 2;
    g.strokeStyle = `rgba(${150 + rnd() * 60},${100 + rnd() * 40},${60 + rnd() * 30},${0.025 + rnd() * 0.05})`;
    g.lineWidth = 0.5 + rnd() * 1.2; g.beginPath(); g.moveTo(x, y);
    for (let s = 0; s < 40; s++) { a += (rnd() - 0.5) * 0.9; x += Math.cos(a) * 9; y += Math.sin(a) * 9; g.lineTo(x, y); }
    g.stroke();
  }
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t;
}
const bg = new THREE.Mesh(new THREE.PlaneGeometry(30, 30), new THREE.MeshBasicMaterial({ map: marbleTexture() }));
bg.position.z = -4; scene.add(bg);

// ------------------------------------------------------------ neon lines ---
const neonMat = (color, width) => new THREE.ShaderMaterial({
  transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
  uniforms: { uColor: { value: new THREE.Color(color) }, uGain: { value: 1 }, uCore: { value: 0.11 } },
  vertexShader: `varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }`,
  fragmentShader: `uniform vec3 uColor; uniform float uGain; uniform float uCore; varying vec2 vUv;
    void main(){ float d = abs(vUv.y - 0.5) * 2.0;
      float core = smoothstep(uCore, 0.0, d);
      float halo = exp(-d * d * 7.0) * 0.6 + exp(-d * 2.6) * 0.25;
      vec3 col = uColor * halo + mix(uColor, vec3(1.0, 0.85, 0.8), 0.55) * core * 1.4;
      gl_FragColor = vec4(col * uGain, (halo + core) * uGain); }`,
});

function neonPolyline(points, color, width) {
  const group = new THREE.Group();
  const mat = neonMat(color, width);
  for (let i = 0; i < points.length - 1; i++) {
    const a = points[i], b = points[i + 1];
    const len = a.distanceTo(b) + width * 0.08;
    const m = new THREE.Mesh(new THREE.PlaneGeometry(len, width), mat);
    m.position.copy(a).add(b).multiplyScalar(0.5);
    m.rotation.z = Math.atan2(b.y - a.y, b.x - a.x);
    group.add(m);
  }
  group.userData.mat = mat;
  return group;
}

// Inverted triangle: outer + inner together form ONE element, the frame.
const S = 7.2, H = S * Math.sqrt(3) / 2;
const triPts = (k) => [new THREE.Vector3(-S / 2 * k, H / 3 * k, 0), new THREE.Vector3(S / 2 * k, H / 3 * k, 0),
  new THREE.Vector3(0, -2 * H / 3 * k, 0), new THREE.Vector3(-S / 2 * k, H / 3 * k, 0)];
const frame = new THREE.Group();
const outer = neonPolyline(triPts(1.0), 0xff1e1e, 0.62);
const inner = neonPolyline(triPts(0.84), 0xff1e1e, 0.52);
frame.add(outer, inner);
rig.add(frame);
const frameMats = []; frame.traverse(o => { if (o.userData.mat) frameMats.push(o.userData.mat); });

let field = null, easedEnergy = 0.12;
try {
  field = buildField(THREE, {
    corners: triPts(1.0).slice(0, 3),
    coreRadius: 0.46,
    small: (stage.clientWidth || window.innerWidth) < 420,
  });
  if (field && field.group) {
    field.group.position.z = -0.05;
    rig.add(field.group);
  }
} catch (err) {
  console.warn('Field build failed:', err);
  field = null;
}

// The bottom tip: three small marks, as on the wall unit. They are the pin: they glow while
// pin cards wait, and a click opens them.
const pinGroup = new THREE.Group(); rig.add(pinGroup);
const pinMats = [];
for (let i = 0; i < 3; i++) {
  const y = -2 * H / 3 * 0.84 + 0.55 + i * 0.22, w = 0.18 + i * 0.07;
  const m = neonPolyline([new THREE.Vector3(-w, y, 0), new THREE.Vector3(w, y, 0)], 0xff2a2a, 0.12);
  pinGroup.add(m); pinMats.push(m.userData.mat);
}
const pinHit = new THREE.Mesh(new THREE.PlaneGeometry(0.9, 0.95), new THREE.MeshBasicMaterial({ visible: false }));
pinHit.position.set(0, -2 * H / 3 * 0.84 + 0.77, 0.05); pinGroup.add(pinHit);
pinHit.userData.pin = true;

// Organ ticks: one per organ along the band between the two top edges.
const tickGroup = new THREE.Group(); tickGroup.position.z = 0.04; rig.add(tickGroup);
let tickMeshes = [];
const BAND_Y = (H / 3 + H / 3 * 0.84) / 2;
function buildTicks(organs) {
  tickGroup.clear(); tickMeshes = [];
  const n = organs.length, span = S * 0.84 * 0.62;
  organs.forEach((o, i) => {
    const x = n > 1 ? -span / 2 + span * i / (n - 1) : 0;
    const mat = neonMat(0xffb13b, 0.1);
    const m = new THREE.Mesh(new THREE.PlaneGeometry(0.1, 0.26), mat);
    m.position.set(x, BAND_Y, 0);
    const hit = new THREE.Mesh(new THREE.PlaneGeometry(Math.max(0.16, span / Math.max(n, 1)), 0.4),
      new THREE.MeshBasicMaterial({ visible: false }));
    hit.position.set(x, BAND_Y, 0.01);
    hit.userData = { organ: o, mat };
    tickGroup.add(m, hit); tickMeshes.push(hit);
  });
}

// Corner switches at the two top inner corners: Quiet (left) and Hard day (right).
const switchGroup = new THREE.Group(); switchGroup.position.z = 0.04; rig.add(switchGroup);
let switchMeshes = [];
function buildSwitches(switches) {
  switchGroup.clear(); switchMeshes = [];
  switches.forEach((sw) => {
    const sx = (sw.corner === 'left' ? -1 : 1) * (S / 2 * 0.84 - 0.62), sy = H / 3 * 0.84 - 0.3;
    const mat = new THREE.MeshBasicMaterial({ color: 0xff2a2a, transparent: true, opacity: 0.6,
      blending: THREE.AdditiveBlending, depthWrite: false });
    const ring = new THREE.Mesh(new THREE.RingGeometry(0.1, 0.15, 32), mat);
    const dotMat = new THREE.MeshBasicMaterial({ color: 0xffb547, transparent: true, opacity: 0,
      blending: THREE.AdditiveBlending, depthWrite: false });
    const dot = new THREE.Mesh(new THREE.CircleGeometry(0.075, 24), dotMat);
    const hit = new THREE.Mesh(new THREE.CircleGeometry(0.32, 16), new THREE.MeshBasicMaterial({ visible: false }));
    for (const m of [ring, dot, hit]) m.position.set(sx, sy, 0);
    hit.userData = { sw, mat, dotMat };
    switchGroup.add(ring, dot, hit); switchMeshes.push(hit);
  });
}

// ----------------------------------------------------------------- core ---
const RCORE = 0.46;
const coreGroup = new THREE.Group(); coreGroup.position.z = -0.25; rig.add(coreGroup);
const tealGlow = new THREE.Mesh(new THREE.CircleGeometry(0.95, 64), new THREE.ShaderMaterial({
  transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
  uniforms: { uGain: { value: 0.5 } },
  vertexShader: `varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }`,
  fragmentShader: `uniform float uGain; varying vec2 vUv; void main(){ float r = length(vUv - 0.5) * 2.0;
    float g = smoothstep(1.0, 0.45, r) * smoothstep(0.35, 0.6, r);
    gl_FragColor = vec4(vec3(0.15, 0.75, 0.55) * g * uGain, g * uGain); }`,
}));
coreGroup.add(tealGlow);
const pupil = new THREE.Mesh(new THREE.CircleGeometry(RCORE, 64), new THREE.MeshBasicMaterial({ color: 0x000000 }));
pupil.position.z = 0.02; coreGroup.add(pupil);
pupil.userData.core = true;

// ---------------------------------------------------------------- rings ---
const ringMat = (seed) => new THREE.ShaderMaterial({
  transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
  uniforms: { uColor: { value: new THREE.Color(0xffb13b) }, uGain: { value: 1 }, uTicks: { value: 90 + seed * 37 },
    uTime: { value: 0 }, uR0: { value: 1 }, uR1: { value: 1.1 } },
  vertexShader: `varying vec2 vP; void main(){ vP = position.xy; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }`,
  fragmentShader: `uniform vec3 uColor; uniform float uGain; uniform float uTicks; uniform float uR0; uniform float uR1; varying vec2 vP;
    void main(){ float r = length(vP); float a = atan(vP.y, vP.x);
      float w = (r - uR0) / (uR1 - uR0);
      float band = smoothstep(0.0, 0.25, w) * smoothstep(1.0, 0.75, w);
      float tick = 0.55 + 0.45 * smoothstep(0.35, 0.0, abs(fract(a * uTicks / 6.2831853) - 0.5) - 0.15);
      float rim = smoothstep(0.12, 0.0, abs(w - 0.5));
      vec3 col = uColor * (band * tick * 0.9 + rim * 0.8);
      gl_FragColor = vec4(col * uGain, (band * tick + rim * 0.5) * uGain); }`,
});

const ringsGroup = new THREE.Group(); ringsGroup.position.z = 0.1; rig.add(ringsGroup);
const decoGroup = new THREE.Group(); decoGroup.position.z = 0.05; rig.add(decoGroup);
let segMeshes = [];       // pickable segments
let ringLayers = [];      // one group per ring level
const RMIN = 0.62, RMAX = 2.12;

function buildDeco(levels) {
  decoGroup.clear();
  // fine dotted filigree circles between the rings
  const n = Math.max(levels, 3) + 2;
  for (let i = 0; i < n; i++) {
    const r = RMIN - 0.06 + (RMAX - RMIN + 0.12) * i / (n - 1);
    const pts = [], count = 220 + i * 40;
    for (let k = 0; k < count; k++) {
      const a = k / count * Math.PI * 2, jitter = (k % 3 === 0 ? 0.012 : 0);
      pts.push(Math.cos(a) * (r + jitter), Math.sin(a) * (r + jitter), 0);
    }
    const geo = new THREE.BufferGeometry(); geo.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
    const dots = new THREE.Points(geo, new THREE.PointsMaterial({ color: 0xffa640, size: 0.018, transparent: true,
      opacity: 0.55 - i * 0.05, blending: THREE.AdditiveBlending, depthWrite: false }));
    dots.userData.spin = (i % 2 ? 1 : -1) * (0.02 + i * 0.006);
    decoGroup.add(dots);
  }
}

function buildRings(segments) {
  ringsGroup.clear(); segMeshes = []; ringLayers = [];
  const levels = segments.reduce((m, s) => Math.max(m, s.ring), 0) + 1;
  const step = (RMAX - RMIN) / Math.max(levels, 1);
  for (let lv = 0; lv < levels; lv++) {
    const layer = new THREE.Group(); layer.userData.level = lv; ringsGroup.add(layer); ringLayers.push(layer);
    const segs = segments.filter(s => s.ring === lv);
    const r0 = RMIN + lv * step + step * 0.12, r1 = RMIN + (lv + 1) * step - step * 0.12;
    const gap = segs.length > 1 ? 0.06 : 0.0;
    const span = (Math.PI * 2) / Math.max(segs.length, 1);
    segs.forEach((s, i) => {
      const mat = ringMat(lv * 3 + i);
      mat.uniforms.uR0.value = r0; mat.uniforms.uR1.value = r1;
      const geo = new THREE.RingGeometry(r0, r1, 96, 1, Math.PI / 2 + i * span + gap / 2, span - gap);
      const m = new THREE.Mesh(geo, mat);
      m.userData = { seg: s, level: lv, mat };
      layer.add(m); segMeshes.push(m);
    });
  }
  buildDeco(levels);
}

// ------------------------------------------------- What I see: the slide-out panel ---
// The rings carry no numbers (the owner's ruling: they hid the display). Every reading is written
// out here instead, grouped in sections that open and close. Readable size, never hover-only.
function currentSeg(id) { return (S0.segments || []).find(x => x.id === id) || { id, name: id, value: '' }; }
const store = { get(k) { try { return localStorage.getItem(k); } catch (_) { return null; } },
                set(k, v) { try { localStorage.setItem(k, v); } catch (_) {} } };
function setSide(open, remember) {
  document.body.classList.toggle('side-open', open);
  $('sidetab').setAttribute('aria-expanded', String(open));
  if (remember) store.set('tau.side', open ? 'open' : 'closed');
}
let readSig = '';
function renderReadings() {
  const segs = (S0.segments || []).filter(x => !String(x.id).startsWith('node:'));
  const machines = S0.machines || [];
  const receipts = S0.receipts || [];
  const sig = JSON.stringify([segs.map(x => [x.id, x.name, x.value, x.status]), machines, receipts, S0.pins]);
  if (sig === readSig) return;
  readSig = sig;
  $('rdlist').innerHTML = segs.map(x => `<button class="rd ${esc(x.status || 'lit')}" data-seg="${esc(x.id)}">` +
    `<span class="nm">${esc(x.name || x.label)}</span><span class="vl">${esc(x.value || '')}</span></button>`).join('');
  $('mcount').textContent = machines.length ? `(${machines.length})` : '';
  $('mlist').innerHTML = machines.length ? machines.map(m => `<button class="rd ${m.connected ? 'lit' : 'dim'}" data-seg="node:${esc(m.name)}">` +
    `<span class="nm">${esc(m.name)}${m.also ? ' <small>(' + esc(m.also) + ')</small>' : ''}</span><span class="vl">${esc(m.state)}</span></button>`).join('')
    : '<p class="sideempty">No other machines yet. Pair one in Settings, Lab.</p>';
  const pins = S0.pins || 0;
  $('pcount').textContent = pins ? `(${pins})` : '';
  $('plist').innerHTML = pins ? `<button class="rd attention" data-pins="1"><span class="nm">${pins} pin card${pins > 1 ? 's' : ''} waiting</span>` +
    '<span class="vl">Click to open them.</span></button>' : '<p class="sideempty">No pin cards waiting.</p>';
  $('acount').textContent = receipts.length ? `(${receipts.length})` : '';
  $('alist').innerHTML = receipts.length ? receipts.slice(-12).reverse().map(r => `<div class="rcpt"><b>${esc(r.what)}</b>` +
    `<span>${esc(r.when)}${r.machine ? ' · ' + esc(r.machine) : ''}${r.who ? ' · approved by ' + esc(r.who) : ''}</span></div>`).join('')
    : '<p class="sideempty">Nothing yet. Every permission change and every action I take leaves a receipt here.</p>';
}
function openSegment(seg) {
  if (seg.status === 'empty' && seg.settings && window.TAUSettings) return window.TAUSettings.open(seg.settings);
  const card = document.getElementById('card');
  const btns = [`<button data-check="1">Check now</button>`]
    .concat((seg.buttons || []).map((b, i) => `<button data-b="${i}">${esc(b.label)}</button>`))
    .concat(seg.settings ? [`<button data-settings="${esc(seg.settings)}">Open in Settings</button>`] : [])
    .concat(['<button data-close="1">Close</button>']);
  card.innerHTML = `<h2>${esc(seg.name || seg.label)}</h2>` +
    `<p class="big">${esc(seg.value || '')}</p>` + (seg.detail || []).map(d => `<p>${esc(d)}</p>`).join('') +
    `<div class="said" style="display:none"></div>` + btns.join('');
  card.style.display = 'block';
  card.onclick = async (e) => {
    const t = e.target.closest('button'); if (!t) return;
    const said = card.querySelector('.said');
    if (t.dataset.close) { card.style.display = 'none'; return; }
    if (t.dataset.settings) { card.style.display = 'none'; return window.TAUSettings && window.TAUSettings.open(t.dataset.settings); }
    if (t.dataset.check || t.dataset.b !== undefined) {
      said.style.display = 'block'; said.textContent = 'Checking\u2026';
      const body = t.dataset.check ? { segment: seg.id } : seg.buttons[+t.dataset.b].action;
      const res = await post('action', body);
      if (res && res.pins) { card.style.display = 'none'; return showPins(res.pins); }
      said.textContent = (res && res.say) || (res && res.ok ? 'Done.' : 'Nothing to report.');
    }
  };
}

const lerp = (a, b, k) => a + (b - a) * k;

// ------------------------------------------------------------ voice pulse ---
// While TAU speaks, the rings brighten and swell slightly with the loudness of her voice, then
// settle back when she stops. Fast to rise, slower to fall, so it follows speech without flicker.
let voicePulse = 0;
function followVoice(level, speaking) {
  const want = speaking ? Math.max(0, Math.min(1, level)) : 0;
  voicePulse = lerp(voicePulse, want, want > voicePulse ? 0.45 : 0.08);
  return voicePulse;
}

// ---------------------------------------------------------------- state ---
let S0 = { state: 'idle', level: 0, burst: false, throttle: false, segments: [] };
let shownSig = '';
const cur = { aperture: 1, frameGain: 1, ringGain: 1, spin: 1, teal: 0.45 };

const DEMO_SEGMENTS = [
  { id: 'brain', label: 'Brain: local', ring: 0, status: 'lit' },
  { id: 'mic', label: 'Listening for my name', ring: 0, status: 'lit' },
  { id: 'gpu', label: 'GPU: 14 GB free', ring: 1, status: 'lit' },
  { id: 'pins', label: '2 pin cards waiting', ring: 1, status: 'attention' },
  { id: 'svc:model-server', label: 'model-server: up', ring: 2, status: 'lit' },
  { id: 'svc:media', label: 'media: unknown', ring: 2, status: 'dim' },
  { id: 'svc:backup', label: 'backup: up', ring: 2, status: 'lit' },
  { id: 'handoff', label: '1 hand-off job running', ring: 3, status: 'lit' },
  { id: 'node:workshop', label: 'workshop: connected', ring: 4, status: 'lit' },
  { id: 'node:garage', label: 'garage: away', ring: 4, status: 'dim' },
];

const DEMO_ORGANS = ['voice', 'display', 'chat', 'services', 'notifications', 'gpu', 'window', 'processes',
  'heartbeat', 'cmdfail', 'drives', 'appbridge', 'models', 'apps', 'handoff', 'clipboard', 'files', 'knowledge', 'gesture']
  .map((id, i) => ({ id, name: id, level: ['act', 'act', 'act', 'sense', 'sense', 'sense'][i] || 'off', label: id }));

function applyState(s) {
  S0 = Object.assign({}, S0, s);
  const sig = JSON.stringify(S0.segments.map(x => [x.id, x.ring, x.status === 'empty']));
  if (sig !== shownSig) { buildRings(S0.segments); shownSig = sig; }
  const osig = JSON.stringify((S0.organs || []).map(o => o.id));
  if (osig !== organSig) { buildTicks(S0.organs || []); organSig = osig; }
  const wsig = JSON.stringify((S0.switches || []).map(o => o.id));
  if (wsig !== switchSig) { buildSwitches(S0.switches || []); switchSig = wsig; }
  tickMeshes.forEach(h => { const o = (S0.organs || []).find(x => x.id === h.userData.organ.id); if (o) h.userData.organ = o; });
  switchMeshes.forEach(h => { const w = (S0.switches || []).find(x => x.id === h.userData.sw.id); if (w) h.userData.sw = w; });
  if (S0.cloud_notice) showNotice(S0.cloud_notice);
  renderReadings();
  renderProposal();
  renderUI();
  if (window.__tauWork) window.__tauWork(S0);
}
function renderProposal() {
  const p = S0.proposal, box = document.getElementById('proposal');
  if (!p) { box.style.display = 'none'; return; }
  document.getElementById('proptext').textContent = p.text + (p.machine ? ` (on ${p.machine})` : '');
  box.dataset.id = p.id; box.style.display = 'block';
}
let organSig = '', switchSig = '';

// Called at the end of the file: applying a state touches things declared further down.
function startFeed() {
if (DEMO) {
  const st = DEMO === 'burst' ? 'idle' : DEMO;
  const segs = DEMO_SEGMENTS.map(x => Object.assign({}, x));
  if (DEMO === 'burst') { segs[0].label = 'Brain: burst'; segs[0].accent = 'cyan'; }
  if (DEMO === 'error') { segs[4].status = 'attention'; segs[4].label = 'model-server: down'; }
  applyState({ state: st, level: DEMO === 'speaking' ? 0.8 : 0, burst: DEMO === 'burst', segments: segs,
    organs: DEMO_ORGANS, switches: [{ id: 'quiet', corner: 'left', on: false, label: 'Quiet: off' },
      { id: 'hard_day', corner: 'right', on: false, label: 'Hard day: off' }], pins: 2, first_run: false,
    status: { idle: 'Ready. Click Talk, press Space, or type below.', listening: 'Listening. Speak now; I stop when you pause.',
      thinking: 'Thinking. This usually takes a few seconds.', speaking: 'Speaking. Click Stop or press Esc to stop me.',
      error: 'The model server stopped answering.', burst: 'Ready. Using the cloud brain.' }[DEMO] });
} else {
  fetch('state').then(r => r.json()).then(applyState).catch(() => {});
  const es = new EventSource('events');
  es.onmessage = (e) => { try { applyState(JSON.parse(e.data)); } catch (_) {} };
}
}

// --------------------------------------------------------------- input ---
const mouse = new THREE.Vector2(), target = new THREE.Vector2();
const ray = new THREE.Raycaster();
const tip = document.getElementById('tip');
function pick(e) {
  ray.setFromCamera(new THREE.Vector2((e.clientX / innerWidth) * 2 - 1, -(e.clientY / innerHeight) * 2 + 1), camera);
  const sw = ray.intersectObjects(switchMeshes, false)[0];
  if (sw) return { kind: 'switch', obj: sw.object };
  const tk = ray.intersectObjects(tickMeshes, false)[0];
  if (tk) return { kind: 'organ', obj: tk.object };
  if (ray.intersectObject(pinHit)[0]) return { kind: 'pin' };
  if (ray.intersectObject(pupil)[0]) return { kind: 'core' };
  const sg = ray.intersectObjects(segMeshes, false)[0];
  if (sg) return { kind: 'segment', obj: sg.object };
  return null;
}
function tipText(h) {
  if (h.kind === 'switch') return [h.obj.userData.sw.label, ''];
  if (h.kind === 'organ') { const o = h.obj.userData.organ; return [o.label, o.what ? 'Lets me ' + o.what + '.' : '']; }
  if (h.kind === 'pin') return [S0.pins ? `${S0.pins} pin card${S0.pins > 1 ? 's' : ''} waiting. Click to open.` : 'Pin cards: none waiting.', 'Answers I keep for you show here.'];
  if (h.kind === 'core') return [S0.state === 'speaking' ? 'Click to stop me speaking.' : 'Click to talk.', 'Same as the Talk button or Space.'];
  const sg = currentSeg(h.obj.userData.seg.id); return [`${sg.name}: ${sg.value}`, 'Click for details.'];
}
window.addEventListener('pointermove', (e) => {
  target.set((e.clientX / innerWidth) * 2 - 1, -(e.clientY / innerHeight) * 2 + 1);
  mouse.copy(target);
  if (e.target !== renderer.domElement) { tip.style.display = 'none'; return; }
  const h = pick(e);
  if (h) {
    const [a, b] = tipText(h);
    tip.innerHTML = esc(a) + (b ? `<small>${esc(b)}</small>` : '');
    tip.style.display = 'block';
    tip.style.left = Math.min(e.clientX + 14, innerWidth - 330) + 'px'; tip.style.top = (e.clientY + 12) + 'px';
  } else tip.style.display = 'none';
  document.body.style.cursor = h ? 'pointer' : 'default';
});
const post = (path, body) => fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body) }).then(r => r.json()).catch(() => ({}));
function handleResult(res) {
  if (!res) return;
  if (res.pins) showPins(res.pins);
}
async function talk() { handleResult(await post('action', { core: true })); }
async function openPins() { handleResult(await post('action', { segment: 'pins' })); }
renderer.domElement.addEventListener('click', async (e) => {
  const h = pick(e);
  if (!h) return;
  if (h.kind === 'core') return talk();
  if (h.kind === 'pin') return openPins();
  if (h.kind === 'switch') return handleResult(await post('action', { switch: h.obj.userData.sw.id }));
  if (h.kind === 'organ') return handleResult(await post('action', { organ: h.obj.userData.organ.id }));
  openSegment(currentSeg(h.obj.userData.seg.id));
});

// ------------------------------------------------------------ page controls ---
const askin = $('askin'), talkBtn = $('talk');
async function send() {
  const text = askin.value.trim();
  if (!text) return;
  askin.value = '';
  handleResult(await post('say', { text }));
}
$('send').addEventListener('click', send);
askin.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); send(); } });
talkBtn.addEventListener('click', talk);
$('pinbtn').addEventListener('click', openPins);
$('msgs').addEventListener('click', async (e) => {
  const b = e.target.closest('button.copy'); if (!b) return;
  const m = (S0.conversation || [])[+b.dataset.copy]; if (!m) return;
  let ok = false;
  try { await navigator.clipboard.writeText(m.text); ok = true; } catch (_) {
    const t = document.createElement('textarea'); t.value = m.text; document.body.appendChild(t); t.select();
    try { ok = document.execCommand('copy'); } catch (_) {} t.remove();
  }
  b.textContent = ok ? 'Copied' : 'Copy failed'; showNote(ok ? 'Copied that message.' : 'I couldn\u2019t copy that.');
  setTimeout(() => { b.textContent = 'Copy'; }, 2000);
});
$('readings').addEventListener('click', (e) => {
  const b = e.target.closest('.rd');
  if (!b) return;
  if (b.dataset.pins) return openPins();
  openSegment(currentSeg(b.dataset.seg));
});
['yes', 'no'].forEach(a => $('prop' + a).addEventListener('click', async () => {
  const id = $('proposal').dataset.id; $('proposal').style.display = 'none';
  handleResult(await post('action', { proposal: id, answer: a }));
}));
$('sidetab').addEventListener('click', () => setSide(!document.body.classList.contains('side-open'), true));
$('sideclose').addEventListener('click', () => setSide(false, true));
setSide(store.get('tau.side') === 'open', false);      // closed by default
document.querySelectorAll('#readings details').forEach(d => {
  const k = 'tau.sec.' + d.id, v = store.get(k);
  if (v) d.open = v === 'open';
  d.addEventListener('toggle', () => store.set(k, d.open ? 'open' : 'closed'));
});
$('helpbtn').addEventListener('click', () => { $('help').style.display = 'block'; });
function closeHelp() { $('help').style.display = 'none'; post('action', { help_seen: true }); helpShown = true; }
$('helpok').addEventListener('click', closeHelp);
$('help').addEventListener('click', (e) => { if (e.target.tagName !== 'BUTTON') closeHelp(); });
$('flashclose').addEventListener('click', () => { $('flash').style.display = 'none'; post('action', { flash_clear: true }); });
$('handsfree').addEventListener('click', async () => {
  const on = !(S0.talk && S0.talk.hands_free);
  if (on && !confirm(HANDS_FREE_PRIVACY + '\n\nSwitch Listen to all speech on?')) return;
  handleResult(await post('action', { hands_free: on }));
});
const HANDS_FREE_PRIVACY = 'Hands-free means I check everything the microphone hears for speech and turn any ' +
  'speech into text on this computer, so nearby talk may be picked up; nothing is saved or sent anywhere until it becomes a message.';
window.addEventListener('keydown', (e) => {
  if ($('settings').style.display === 'flex') return;
  const typing = document.activeElement === askin;
  if (e.key === '/' && !typing) { e.preventDefault(); askin.focus(); }
  if ((e.key === ' ' || e.code === 'Space') && !typing && document.activeElement.tagName !== 'BUTTON') { e.preventDefault(); talk(); }
  if (e.key === 'Escape') {
    if (S0.state === 'speaking') talk();
    document.querySelectorAll('.sheet').forEach(x => x.style.display = 'none');
    $('card').style.display = 'none';
    if (typing) askin.blur();
  }
});

let helpShown = false, lastFlash = null, convoSig = '';
function renderUI() {
  document.body.dataset.state = S0.state || 'idle';
  if (S0.note && S0.note.at !== noteSeen) { noteSeen = S0.note.at; showNote(S0.note.text); }
  if ((S0.state || 'idle') !== 'idle') noteUntil = 0;     // what I'm doing now always wins over a note
  $('status').textContent = (performance.now() < noteUntil ? noteText : '') || S0.status || '';
  const t = S0.talk || {};
  talkBtn.disabled = !t.available && S0.state !== 'speaking';
  talkBtn.dataset.mode = S0.state;
  talkBtn.textContent = S0.state === 'listening' ? 'Listening...' : S0.state === 'speaking' ? 'Stop' :
    (t.available ? 'Talk' : 'Talk (no microphone)');
  talkBtn.title = t.available ? 'Click, then speak. Shortcut: Space' : 'No microphone is working. Type below instead; a pin card has the fix.';
  const hf = $('handsfree');
  hf.textContent = 'Listen to all speech: ' + (t.hands_free ? 'on' : 'off');
  hf.title = HANDS_FREE_PRIVACY;
  hf.style.display = t.available ? '' : 'none';
  const pb = $('pinbtn');
  pb.textContent = 'Pin cards: ' + (S0.pins || 0);
  pb.classList.toggle('glow', (S0.pins || 0) > 0);
  const conv = S0.conversation || [];
  const speaking = S0.state === 'speaking';
  const csig = JSON.stringify([conv.length, conv.length ? conv[conv.length - 1].text : '', speaking]);
  if (csig !== convoSig) {
    convoSig = csig;
    const box = $('msgs');
    const lastTau = conv.map(m => m.who === 'tau' && m.kind !== 'pin').lastIndexOf(true);
    box.innerHTML = conv.length ? conv.map((m, i) => {
      const who = m.who === 'you' ? 'You' : esc(S0.name || 'TAU');
      const when = m.at ? new Date(m.at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';
      const isPin = m.kind === 'pin';
      const now = speaking && i === lastTau && !isPin;
      const tags = (isPin ? `<span class="tag">Pin card: ${esc(m.title || '')}</span>` : '') +
        (m.pinned ? '<span class="tag">Kept on a pin card</span>' : '') + (now ? '<span class="tag now">Speaking now</span>' : '');
      return `<div class="msg ${m.who === 'you' ? 'you' : 'tau'}${isPin ? ' pin' : ''}${now ? ' speaking' : ''}">` +
        `<div class="head"><span class="who">${who}</span><span class="when">${when}</span>` +
        `<button class="copy" data-copy="${i}" aria-label="Copy this message">Copy</button></div>${tags}` +
        `<span class="text">${esc(m.text)}</span></div>`;
    }).join('') : '<p class="empty">What we say appears here.</p>';
    const c = $('convo'); c.scrollTop = c.scrollHeight;
  }
  if (S0.flash && JSON.stringify(S0.flash) !== lastFlash) {
    lastFlash = JSON.stringify(S0.flash);
    $('flashtext').textContent = S0.flash.text;
    $('flash').className = S0.flash.kind === 'warning' ? 'warning' : '';
    $('flash').style.display = 'block';
    flashUntil = performance.now() + 1800;
  }
  if (!S0.flash) { $('flash').style.display = 'none'; lastFlash = null; }
  if (S0.first_run && !helpShown && !POPOUT && !DEMO) { $('help').style.display = 'block'; helpShown = true; }
}
let flashUntil = 0;
// A change made in Settings (or anywhere) is repeated on the status line for a few seconds.
let noteSeen = null, noteText = '', noteUntil = 0;
function showNote(text) {
  noteText = text; noteUntil = performance.now() + 8000;
  $('status').textContent = text;
  setTimeout(() => { if (performance.now() >= noteUntil) $('status').textContent = S0.status || ''; }, 8100);
}
window.__tauNote = showNote;

function esc(t) { return String(t).replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c])); }
function showPins(pins) {
  const el = document.getElementById('pins');
  el.innerHTML = '<h2>Pin cards</h2>' + (pins.length ? '' : '<p>None waiting.</p>') + pins.map(p =>
    `<div><b>${esc(p.title)}</b><pre>${esc(p.text)}</pre><button data-clear="${p.id}">Clear</button></div>`).join('') +
    '<button data-close="1">Close</button>';
  el.style.display = 'block';
  el.onclick = async (e) => {
    const id = e.target.dataset.clear;
    if (id) { await post('action', { pin_clear: id }); e.target.parentElement.remove(); }
    if (e.target.dataset.close) el.style.display = 'none';
  };
}
function showNotice(n) {
  const el = document.getElementById('notice');
  el.innerHTML = `<h2>Privacy</h2><p>${esc(n.text)}</p>` + n.choices.map(c =>
    `<button data-choice="${c.value}">${esc(c.label)}</button>`).join('');
  el.style.display = 'block';
  el.onclick = (e) => { if (e.target.dataset.choice) { post('action', { cloud_choice: e.target.dataset.choice }); el.style.display = 'none'; } };
}

// ---------------------------------------------------------------- loop ---
function resize() {
  const w = innerWidth, h = innerHeight; renderer.setSize(w, h, false);
  renderer.domElement.style.width = w + 'px'; renderer.domElement.style.height = h + 'px';
  camera.aspect = w / h;
  // keep the triangle clear of the conversation panel (right) and the bar (bottom)
  const wide = w > 760 && !POPOUT, wider = w > 1000 && !POPOUT;
  const side = wide ? Math.min(380, w * 0.30) + 14 : 0, left = wider ? Math.min(300, w * 0.26) + 14 : 0;
  const bottom = POPOUT ? 30 : 70;
  const vw = w - side - left, vh = h - bottom - 50;
  const fit = Math.max(1, 1.05 * (vh / vw)) * (h / vh);
  camera.position.z = 16.5 * fit;
  camera.setViewOffset(w, h, (side - left) / 2, (bottom - 50) / 2, w, h);
  camera.updateProjectionMatrix();
}
addEventListener('resize', resize); resize();

const clock = new THREE.Clock();
let last = 0;
const AMBER = new THREE.Color(0xffb13b), ATTN = new THREE.Color(0xff8a1a), CYAN = new THREE.Color(0x3fe6ff),
  DIMC = new THREE.Color(0x6b4a22), WHITE_GOLD = new THREE.Color(0xfff0c0);

function frameLoop(now) {
  requestAnimationFrame(frameLoop);
  const fps = S0.throttle ? 10 : 60;
  if (now - last < 1000 / fps - 1) return;
  last = now;
  const t = clock.getElapsedTime();
  const st = S0.state;
  // targets per state
  const tgt = { aperture: 1, frameGain: 1, ringGain: 1, spin: 1, teal: 0.45 };
  if (st === 'listening') { tgt.aperture = 0.62; tgt.ringGain = 1.5; tgt.spin = 0.4; tgt.teal = 0.85; }
  if (st === 'thinking' || st === 'transcribing') { tgt.frameGain = 0.85 + 0.3 * Math.sin(t * 6); tgt.spin = 3.2; tgt.ringGain = 1.1; }
  if (st === 'speaking') { tgt.ringGain = 1.1; tgt.spin = 1.2; tgt.teal = 0.7; }
  if (st === 'error') { tgt.frameGain = (Math.sin(t * 23) > 0.2 ? 1.2 : 0.5); tgt.ringGain = 0.45; tgt.spin = 0.2; tgt.teal = 0.15; }
  if (now < flashUntil) tgt.frameGain = (Math.sin(t * 23) > 0.2 ? 1.25 : 0.45);   // warning flash
  for (const k in cur) cur[k] = lerp(cur[k], tgt[k], k === 'frameGain' ? 0.5 : 0.12);

  const breathe = 1 + 0.012 * Math.sin(t * 0.9);
  const pulse = followVoice(S0.level || 0, st === 'speaking');
  let targetEnergy = st === 'listening' ? 0.35 : st === 'transcribing' ? 0.6 : st === 'thinking' ? 0.85 :
    st === 'speaking' ? 0.3 + 0.6 * pulse : st === 'error' ? 0.08 : 0.12;
  if (S0.burst) targetEnergy += 0.1;
  easedEnergy = lerp(easedEnergy, targetEnergy, 0.05);
  if (field) field.update(t, easedEnergy, pulse);
  frameMats.forEach(m => { m.uniforms.uGain.value = cur.frameGain * (st === 'idle' ? breathe : 1);
    m.uniforms.uColor.value.setHex(st === 'error' ? 0xc80a0a : 0xff1e1e); });
  // rings: inner levels tighten when listening (shutter), slow counter-rotation when idle
  ringLayers.forEach((layer, lv) => {
    const tighten = lerp(cur.aperture, 1, Math.min(1, lv / 3));
    layer.scale.setScalar(tighten * breathe * (1 + pulse * 0.07 * (1 - lv * 0.12)));   // swell with the voice
    layer.rotation.z += (lv % 2 ? 1 : -1) * 0.0016 * cur.spin * (1 + lv * 0.25);
    layer.children.forEach((m) => {
      if (m.userData.label) return;
      const s = m.userData.seg, u = m.userData.mat.uniforms;
      const importanceFade = Math.max(0.35, 1 - lv * 0.16);
      let col = AMBER.clone(), gain = importanceFade * cur.ringGain * (1 + pulse * 0.9);
      if (s.status === 'dim') { col = DIMC.clone(); gain *= 0.35; }
      if (s.status === 'empty') { col = DIMC.clone(); gain *= 0.18; }
      if (s.status === 'attention') { col = ATTN.clone(); gain *= 0.55 + 0.55 * (0.5 + 0.5 * Math.sin(t * 4.2)); }
      if (s.accent === 'cyan') col.lerp(CYAN, 0.85);
      if (st === 'listening' && lv < 2) col.lerp(WHITE_GOLD, 0.5);
      if (st === 'error') col.lerp(DIMC, 0.5);
      u.uColor.value.copy(col); u.uGain.value = gain;
    });
  });
  const pinOn = (S0.pins || 0) > 0;
  pinMats.forEach(m => { m.uniforms.uColor.value.setHex(pinOn ? 0xffb547 : 0xff2a2a);
    m.uniforms.uGain.value = pinOn ? 0.8 + 0.5 * (0.5 + 0.5 * Math.sin(t * 3.2)) : 0.55; });
  tickMeshes.forEach(h => { const o = h.userData.organ, u = h.userData.mat.uniforms;
    const g = { off: 0.28, sense: 0.55, suggest: 0.8, act: 1.05 }[o.level] ?? 0.3;
    u.uColor.value.setHex(o.warn ? 0xff8a1a : o.level === 'off' ? 0x8a2a1a : 0xffb13b);
    u.uGain.value = o.warn ? g * (0.5 + 0.6 * (0.5 + 0.5 * Math.sin(t * 4.2))) + 0.3 : g; });
  switchMeshes.forEach(h => { const on = h.userData.sw.on;
    h.userData.mat.color.setHex(on ? 0xffb547 : 0xff2a2a); h.userData.mat.opacity = on ? 0.95 : 0.45;
    h.userData.dotMat.opacity = on ? 0.9 : 0; });
  decoGroup.children.forEach((d, i) => { d.rotation.z += d.userData.spin * 0.01 * cur.spin;
    d.scale.setScalar(lerp(cur.aperture, 1, Math.min(1, i / 3)) * breathe); });
  coreGroup.scale.setScalar(lerp(1, 0.72, (1 - cur.aperture) / 0.38));
  tealGlow.material.uniforms.uGain.value = cur.teal * (S0.burst ? 0.4 : 1);
  if (S0.burst) tealGlow.material.uniforms.uGain.value += 0.25;
  // parallax: the frame and rings sit in front of a recessed back panel
  rig.rotation.y = lerp(rig.rotation.y, target.x * 0.12, 0.05);
  rig.rotation.x = lerp(rig.rotation.x, -target.y * 0.09, 0.05);
  bg.position.x = lerp(bg.position.x, -target.x * 0.35, 0.05);
  bg.position.y = lerp(bg.position.y, -target.y * 0.25, 0.05);
  renderer.render(scene, camera);
}
startFeed();
requestAnimationFrame(frameLoop);
window.__tau = { applyState, ready: true, pulse: () => voicePulse,
  ringScale: () => (ringLayers[0] ? ringLayers[0].scale.x : 1),
  field: () => (field ? Object.assign({ energy: easedEnergy }, field.stats()) : null) };

// Wiring only. Live mode: upload → GLM agent starts → every tool call drives the screen
// (LOCATE → drops on map, view_image → vision inspector, kinematics → trails, reports → intel) → verdict.
import * as api from './api.js';
import { CFG, labelTr, prettyZone } from './config.js';
import { sleep } from './geo.js';
import { createMap } from './map.js';
import { createImageView } from './imageView.js';
import * as ui from './panels.js';
import { LEVELS, levelsByDet, loadAgent, maxLevel, renderAlerts } from './agent.js';
import { createBrain } from './brain.js';
import { createInspector } from './inspect.js';
import { createIntel } from './intel.js';
import { createLayout } from './layout.js';

const $ = (s) => document.querySelector(s);
const state = { zones: {}, done: new Set(), vehicles: 0, alerts: 0, busy: false, queue: [] };
const trk = { ofDet: {}, kin: {}, cache: {} }; // det key → track id, track id → kinematics / points

const layout = await api.getLayout();
const images = await api.getImages();
const known = new Set(images.map((i) => i.image_id));
const map = createMap($('#map'), layout);
const iv = createImageView($('#imgview'));
const brain = createBrain($('#brain'));
const inspector = createInspector($('#inspect'));
const intel = createIntel($('#intel'));

ui.renderZoneBoard(layout);
$('#sample').innerHTML = '<option value="">Görüntü seç…</option>' +
  images.map((i) => `<option value="${i.image_id}">${i.image_id} · ${i.capture_time}</option>`).join('');
await map.ready;
const side = createLayout(map.map);
map.setFrames(images);
map.onFrameClick((id) => enqueueSample(id));
map.onDetClick((key) => selectVehicle(key));
map.onEmptyClick(() => selectVehicle(null));
ui.log(`Sistem hazır · ${images.length} kayıtlı çerçeve · ${layout.zones.length} bölge + merkez`, 'ok');
ui.onLogLine((cls) => side.badge('log', '•', { hot: cls === 'err' })); // unread marker for later lines

// ---------- inputs ----------
$('#file').onchange = (e) => enqueueFiles(e.target.files);
const mask = $('#dropmask'); // drop an image anywhere on the page
let dragDepth = 0;
window.addEventListener('dragenter', (e) => { if (e.dataTransfer?.types.includes('Files')) { dragDepth++; mask.classList.add('on'); } });
window.addEventListener('dragleave', () => { if (--dragDepth <= 0) { dragDepth = 0; mask.classList.remove('on'); } });
window.addEventListener('dragover', (e) => e.preventDefault());
window.addEventListener('drop', (e) => { e.preventDefault(); dragDepth = 0; mask.classList.remove('on'); enqueueFiles(e.dataTransfer.files); });
$('#sample').onchange = (e) => e.target.value && enqueueSample(e.target.value);
$('#basemap').onchange = (e) => map.setBasemap(e.target.value);
$('#demo').onclick = async () => {
  const withAgent = [];
  for (const i of images) if (!state.done.has(i.image_id) && (await loadAgent(i.image_id))) withAgent.push(i.image_id);
  $('#glm-mode').value = 'cached';
  ui.log(`DEMO · GLM çıktısı olan ${withAgent.length} çerçeve sıraya alındı`, 'warn');
  withAgent.forEach(enqueueSample);
};

// deep link: ?image=img_003839&glm=cached|live|off
const qs = new URLSearchParams(location.search);
if (qs.get('glm')) $('#glm-mode').value = qs.get('glm');
if (known.has(qs.get('image'))) { $('#sample').value = qs.get('image'); enqueueSample(qs.get('image')); }

function enqueueFiles(files) {
  for (const f of files) state.queue.push({ file: f, imageId: $('#meta-id').value.trim() });
  pump();
}
async function enqueueSample(id) {
  state.queue.push({ file: await api.sampleFile(id), imageId: id });
  pump();
}
async function pump() {
  if (state.busy) return;
  state.busy = true;
  while (state.queue.length) {
    const job = state.queue.shift();
    try { await run(job.file, job.imageId); } catch (e) { ui.log(`HATA · ${e.message}`, 'err'); iv.scanning(false); }
  }
  state.busy = false;
}

// ---------- one frame ----------
async function run(file, imageId) {
  const id = imageId || file.name.replace(/\.[^.]+$/, '');
  if (!known.has(id)) {
    $('#meta-id').classList.add('need');
    throw new Error(`'${id}' için köşe koordinatı yok — image_id gir ya da listeden seç`);
  }
  $('#meta-id').classList.remove('need');
  const ctx = { id, url: URL.createObjectURL(file), res: null, chain: Promise.resolve(), inspecting: false };
  ui.log(`▲ YÜKLEME · ${file.name} (${(file.size / 1024).toFixed(0)} KB)`);
  await iv.show(ctx.url);
  iv.scanning(true);
  intel.clear();
  side.badge('reports', '');
  map.clearTracks();

  const mode = $('#glm-mode').value;
  let agent = null;
  if (mode !== 'live') {
    locate(ctx, await api.analyze(file, id, CFG.minConf));
    await ctx.chain;
    if (mode === 'cached') agent = await loadAgent(id);
    if (agent) { ui.log(`✦ GLM · kayıtlı çıktı kullanıldı (${id})`, 'warn'); brain.cached(id); }
    if (mode === 'off') brain.off(id);
  }
  if (mode === 'live' || (mode === 'cached' && !agent)) agent = await runLive(ctx);
  await ctx.chain;
  if (!ctx.res) locate(ctx, await api.analyze(file, id, CFG.minConf)); // agent died before LOCATE
  await ctx.chain;
  applyAgent(ctx.res, agent);
}

// LOCATE result (from GLM's get_image_info, or /api/analyze) → frame on map, drops, zone counts.
function locate(ctx, res) {
  if (ctx.res) return;
  ctx.res = res;
  res.detections.forEach((d) => (d.key = `${res.image_id}/${d.det_id}`));
  iv.scanning(false);
  iv.setSize(res.size);
  ctx.chain = ctx.chain.then(() => placeFrame(ctx, res));
}

async function placeFrame(ctx, res) {
  const fresh = !state.done.has(res.image_id);
  ui.log(`◉ ${res.image_id} · ${res.capture_time} · ${prettyZone(res.zone)} · üsse ${res.base_dist_m} m · ${res.detections.length} araç`, 'ok');
  ui.renderDetections(res, (key, on) => { map.highlight(key, on); iv.highlight(on ? key : null); }, selectVehicle);
  side.badge('image', String(res.detections.length));

  await map.focusFrame(res, ctx.url);
  for (const d of res.detections) {
    iv.addBox(d);
    await map.dropDetection(d, () =>
      ui.log(`&nbsp;&nbsp;${d.det_id} ${labelTr(d.label)} ${(d.conf * 100).toFixed(0)}% → ${d.lat.toFixed(5)}N ${d.lon.toFixed(5)}E → <b>${prettyZone(d.zone)}</b>`));
  }
  map.link(res);
  await sleep(600);
  await map.overview();
  if (!fresh) return ui.log(`${res.image_id} zaten sayılmıştı, bölge sayaçları değişmedi`);

  state.done.add(res.image_id);
  for (const d of res.detections) {
    const z = (state.zones[d.zone] ||= { total: 0, byLabel: {} });
    z.total++; z.byLabel[d.label] = (z.byLabel[d.label] || 0) + 1;
    state.vehicles++;
    const maxT = Math.max(...Object.values(state.zones).map((v) => v.total));
    map.setZoneCount(d.zone, z.total);
    ui.updateZoneCard(d.zone, z.byLabel, z.total, maxT);
    ui.setStats({ frames: state.done.size, vehicles: state.vehicles, alerts: state.alerts });
    await sleep(CFG.dropDelayMs);
  }
}

// ---------- live GLM: each streamed tool call drives a visual ----------
async function runLive(ctx) {
  ui.log(`✦ GLM AJAN BAŞLADI · ${ctx.id} · LOCATE → TRACKS → MOTION → REPORTS → ASSESS`, 'warn');
  brain.reset(ctx.id);
  try {
    return await api.runAgent(ctx.id, (ev) => onAgentEvent(ctx, ev));
  } catch (e) {
    brain.finish(`HATA · ${e.message}`);
    ui.log(`GLM HATA · ${e.message}`, 'err');
    return null;
  }
}

function onAgentEvent(ctx, ev) {
  brain.event(ev);
  if (ev.type === 'done') {
    brain.finish(`${ctx.id} · ${ev.llm_calls} GLM çağrısı · ${ev.secs}s`);
    ui.log(`✎ tam konuşma geçmişi → ${ev.debug}`, 'ok');
  }
  if (ev.type !== 'trace') return;
  if (ev.kind === 'llm' && ctx.inspecting) { // GLM's reply after looking = its finding
    ctx.inspecting = false;
    inspector.finding(ev.text || ev.calls.map((c) => c.name).join(', '));
    setTimeout(() => iv.setCrop(null), 6000);
  }
  if (ev.ui_error) ui.log(`görsel hata · ${ev.ui_error}`, 'err');
  const u = ev.ui;
  if (!u) return;

  if (u.locate) locate(ctx, u.locate);
  if (u.view) {
    const img = iv.image();
    const size = ctx.res?.size || [img.naturalWidth, img.naturalHeight];
    inspector.open(img, size, u.view.crop, ctx.res?.detections || []);
    iv.setCrop(u.view.crop);
    ctx.inspecting = true;
  }
  if (u.matches) {
    ctx.chain = ctx.chain.then(() => {
      tagMatches(ctx.id, u.matches);
      ui.log(`⌖ KİLİT · ${u.matches.map((m) => `${m.det_id}↔${m.track_id}`).join('  ')} · izi görmek için araca tıkla`, 'ok');
    });
  }
  if (u.track) {
    trk.kin[u.track.track_id] = u.track.kin;
    trk.cache[u.track.track_id] = u.track;
    ctx.chain = ctx.chain.then(() => map.flashTrack(u.track));
  }
  if (u.report) { intel.add(u.report); reportBadge(); }
}

function reportBadge() {
  const c = intel.counts();
  side.badge('reports', String(intel.count()), { hot: !!c.bad });
}

// ---------- vehicle ↔ track ----------
function tagMatches(imageId, matches = []) {
  for (const m of matches) {
    const key = `${imageId}/${m.det_id}`;
    trk.ofDet[key] = m.track_id;
    map.tagDet(key, `${m.det_id}·${m.track_id}`);
  }
}

async function selectVehicle(key) {
  document.querySelectorAll('.det.sel, #dets tr.sel').forEach((e) => e.classList.remove('sel'));
  if (!key) return map.showTrack(null);
  document.querySelectorAll(`.det[data-det="${key}"], #dets tr[data-key="${key}"]`).forEach((e) => e.classList.add('sel'));
  const tid = trk.ofDet[key];
  if (!tid) {
    map.showTrack(null);
    return ui.log(`${key} · eşleşen track yok (park halinde ya da kaydı yok)`, 'warn');
  }
  const t = trk.cache[tid] || (trk.cache[tid] = await api.getTrack(tid));
  map.showTrack({ ...t, kin: trk.kin[tid] });
  const k = trk.kin[tid] || {};
  ui.log(`◎ ${key} ↔ ${tid}${k.trend ? ` · ${k.trend}` : ''}${k.eta_min != null ? ` · ETA ${k.eta_min} dk` : ''}`, 'ok');
}

// ---------- GLM verdict drives threat levels ----------
function applyAgent(res, agent) {
  Object.assign(trk.kin, agent?.kinematics || {});
  tagMatches(res.image_id, agent?.matches?.matches);
  const levels = levelsByDet(agent);
  for (const d of res.detections) {
    const lv = levels[d.det_id];
    if (!lv) continue;
    map.setDetLevel(d.key, lv);
    ui.markDetectionRow(d.key, lv);
  }
  renderAlerts($('#alerts'), agent, res.image_id);
  if (!agent) return;
  if (!intel.count()) { // saved output: the live stream didn't fill the reports, rebuild them from report_checks
    for (const [rid, c] of Object.entries(agent.report_checks || {})) intel.add({ report_id: rid, ...c });
    reportBadge();
  }
  const n = agent.assessment?.alerts?.length || 0;
  state.alerts += n;
  ui.setStats({ frames: state.done.size, vehicles: state.vehicles, alerts: state.alerts });
  const top = maxLevel(levels);
  side.badge('alerts', String(n), { hot: top === 'yuksek' });
  if (top) { map.setZoneThreat(res.zone, LEVELS[top]); ui.setZoneLevel(res.zone, top); }
  ui.log(`✦ GLM KARAR · ${n} uyarı · en yüksek: ${top ? top.toUpperCase() : '—'}`, top === 'yuksek' ? 'err' : 'warn');
}

// Wiring only: upload → analyze → image boxes → map drops → GLM alerts → zone counters.
import * as api from './api.js';
import { CFG, labelTr, prettyZone } from './config.js';
import { sleep } from './geo.js';
import { createMap } from './map.js';
import { createImageView } from './imageView.js';
import * as ui from './panels.js';
import { LEVELS, levelsByDet, loadAgent, maxLevel, renderAlerts } from './agent.js';

const $ = (s) => document.querySelector(s);
const state = { zones: {}, done: new Set(), vehicles: 0, alerts: 0, busy: false, queue: [] };

const layout = await api.getLayout();
const images = await api.getImages();
const map = createMap($('#map'), layout);
const iv = createImageView($('#imgview'));

ui.renderZoneBoard(layout);
ui.startClock();
$('#sample').innerHTML = '<option value="">— örnek görüntü —</option>' +
  images.map((i) => `<option value="${i.image_id}">${i.image_id} · ${i.capture_time}</option>`).join('');
await map.ready;
map.setFrames(images);
map.onFrameClick((id) => enqueueSample(id));
ui.log(`Sistem hazır · ${images.length} kayıtlı çerçeve · ${layout.zones.length} bölge + merkez`, 'ok');

// ---------- inputs ----------
$('#file').onchange = (e) => enqueueFiles(e.target.files);
const dz = $('#drop');
dz.ondragover = (e) => { e.preventDefault(); dz.classList.add('over'); };
dz.ondragleave = () => dz.classList.remove('over');
dz.ondrop = (e) => { e.preventDefault(); dz.classList.remove('over'); enqueueFiles(e.dataTransfer.files); };
$('#sample').onchange = (e) => e.target.value && enqueueSample(e.target.value);
$('#basemap').onchange = (e) => map.setBasemap(e.target.value);
$('#demo').onclick = async () => {
  const withAgent = [];
  for (const i of images) if (!state.done.has(i.image_id) && (await loadAgent(i.image_id))) withAgent.push(i.image_id);
  ui.log(`DEMO · GLM çıktısı olan ${withAgent.length} çerçeve sıraya alındı`, 'warn');
  withAgent.forEach(enqueueSample);
};

function enqueueFiles(files) {
  for (const f of files) state.queue.push({ file: f, imageId: $('#meta-id').value });
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

// ---------- main sequence ----------
async function run(file, imageId) {
  const url = URL.createObjectURL(file);
  ui.log(`▲ YÜKLEME · ${file.name} (${(file.size / 1024).toFixed(0)} KB)`);
  await iv.show(url);
  iv.scanning(true);

  let res;
  try {
    res = await api.analyze(file, imageId, CFG.minConf);
  } catch (e) {
    if (e.detail?.needs_meta) {
      ui.log(`⚠ ${e.message}`, 'err');
      $('#meta-row').classList.add('need');
    }
    throw e;
  }
  $('#meta-row').classList.remove('need');
  const agentP = loadAgent(res.image_id);
  const fresh = !state.done.has(res.image_id);
  res.detections.forEach((d) => (d.key = `${res.image_id}/${d.det_id}`));

  await sleep(500);
  iv.scanning(false);
  iv.setSize(res.size);
  ui.log(`◉ ${res.image_id} · ${res.capture_time} · ${prettyZone(res.zone)} · üsse ${res.base_dist_m} m · ${res.detections.length} araç`, 'ok');
  ui.renderDetections(res, (key, on) => { map.highlight(key, on); iv.highlight(on ? key : null); });

  await map.focusFrame(res, url);
  for (const d of res.detections) {
    iv.addBox(d);
    await map.dropDetection(d, () =>
      ui.log(`&nbsp;&nbsp;${d.det_id} ${labelTr(d.label)} ${(d.conf * 100).toFixed(0)}% → ${d.lat.toFixed(5)}N ${d.lon.toFixed(5)}E → <b>${prettyZone(d.zone)}</b>`));
  }
  map.link(res);

  // GLM verdict drives threat levels
  const agent = await agentP;
  const levels = levelsByDet(agent);
  for (const d of res.detections) {
    const lv = levels[d.det_id];
    if (!lv) continue;
    map.setDetLevel(d.key, lv);
    ui.markDetectionRow(d.key, lv);
  }
  renderAlerts($('#alerts'), agent, res.image_id);
  const n = agent?.assessment?.alerts?.length || 0;
  ui.log(agent ? `✦ GLM · ${n} uyarı · en yüksek: ${maxLevel(levels) || '—'}` : `✦ GLM çıktısı yok (${res.image_id})`, agent ? 'warn' : '');

  await sleep(900);
  await map.overview();
  if (!fresh) return ui.log(`${res.image_id} zaten sayılmıştı, bölge sayaçları değişmedi`);

  state.done.add(res.image_id);
  state.alerts += n;
  const top = maxLevel(levels);
  if (top) { map.setZoneThreat(res.zone, LEVELS[top]); ui.setZoneLevel(res.zone, top); }
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

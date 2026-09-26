// Side column content: zone board, detection table, agent log, top stats.
import { labelColor, labelTr, prettyZone } from './config.js';
import { LEVEL_TR } from './agent.js';

const $ = (s) => document.querySelector(s);

export function renderZoneBoard(layout) {
  const names = [layout.base.name, ...layout.zones.map((z) => z.name)];
  $('#zones').innerHTML = names.map((n) => `
    <div class="zcard" data-zone="${n}">
      <div class="zhead"><span>${prettyZone(n)}</span><b>0</b></div>
      <div class="zbar"><i style="width:0"></i></div>
      <div class="zlabels"></div>
    </div>`).join('');
}

export function updateZoneCard(name, byLabel, total, maxTotal) {
  const el = document.querySelector(`.zcard[data-zone="${name}"]`);
  if (!el) return;
  el.querySelector('.zhead b').textContent = total;
  el.querySelector('.zbar i').style.width = `${(100 * total) / Math.max(1, maxTotal)}%`;
  el.querySelector('.zlabels').innerHTML = Object.entries(byLabel)
    .map(([l, n]) => `<span style="--c:${labelColor(l)}">${labelTr(l)} ${n}</span>`).join('');
  el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash');
}

export function setZoneLevel(name, level) {
  const el = document.querySelector(`.zcard[data-zone="${name}"]`);
  if (el && level) el.dataset.level = level;
}

export function renderDetections(res, onHover, onClick) {
  const tb = $('#dets tbody');
  tb.innerHTML = '';
  for (const d of res.detections) {
    const tr = document.createElement('tr');
    tr.dataset.key = d.key;
    tr.title = `${d.lat.toFixed(5)}N ${d.lon.toFixed(5)}E`;
    tr.innerHTML = `<td>${d.det_id}</td><td style="color:${labelColor(d.label)}">${labelTr(d.label)}</td>
      <td>${(d.conf * 100).toFixed(0)}%</td><td>${prettyZone(d.zone)}<br><small>${d.base_dist_m} m · ${d.direction}</small></td><td class="lvl"></td>`;
    tr.onmouseenter = () => onHover(d.key, true);
    tr.onmouseleave = () => onHover(d.key, false);
    tr.onclick = () => onClick?.(d.key);
    tb.appendChild(tr);
  }
  $('#det-title').textContent = `Görüntü ve tespitler · ${res.image_id} · ${res.capture_time}`;
}

export function markDetectionRow(key, level, conf = null) {
  const td = document.querySelector(`#dets tr[data-key="${key}"] .lvl`);
  if (td) td.innerHTML = `<span class="lv lv-${level}">${LEVEL_TR[level] || level}</span>` +
    (conf != null ? `<br><small title="alert güven skoru">güven %${Math.round(conf * 100)}</small>` : '');
}

let onLog = null;
export const onLogLine = (fn) => (onLog = fn);

// detail (optional, plain text): full content shown when the line is clicked.
export function log(msg, cls = '', detail = '') {
  const el = document.createElement('div');
  el.className = `ln ${cls}${detail ? ' has-detail' : ''}`;
  const t = new Date().toLocaleTimeString('tr-TR');
  el.innerHTML = `<div class="l1"><span class="ts">${t}</span> ${msg}</div>`;
  if (detail) {
    const pre = document.createElement('pre');
    pre.textContent = detail;
    el.appendChild(pre);
    el.querySelector('.l1').onclick = () => el.classList.toggle('open');
  }
  const box = $('#log');
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 30;
  box.appendChild(el);
  if (atBottom) box.scrollTop = box.scrollHeight; // don't yank the view while someone is reading
  onLog?.(cls);
}

export function setStats({ frames, vehicles, alerts }) {
  $('#st-frames').textContent = frames;
  $('#st-veh').textContent = vehicles;
  $('#st-alerts').textContent = alerts;
}

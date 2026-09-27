// GLM agent layer: reads outputs/<id>.json via /api/agent and turns alerts into UI state.
// The agent's decision is the source of truth for threat levels.
import { getAgent } from './api.js';

export const LEVELS = { yuksek: 3, orta: 2, dusuk: 1 };
export const LEVEL_TR = { yuksek: 'YÜKSEK', orta: 'ORTA', dusuk: 'DÜŞÜK' };

export async function loadAgent(imageId) {
  try { return await getAgent(imageId); } catch { return null; }
}

// det_id → highest alert level that mentions it (alert.ids or "D04/T0183" subject).
export function levelsByDet(agent) {
  const out = {};
  for (const a of agent?.assessment?.alerts || []) {
    const ids = new Set([...(a.ids || []), ...String(a.subject || '').split(/[\/,\s]+/)]);
    for (const id of ids) {
      if (/^D\d+$/.test(id) && (LEVELS[a.level] || 0) > (LEVELS[out[id]] || 0)) out[id] = a.level;
    }
  }
  return out;
}

// det_id → confidence of the highest-level alert that mentions it (null when the output has none)
export function confByDet(agent) {
  const best = {}, out = {};
  for (const a of agent?.assessment?.alerts || []) {
    const ids = new Set([...(a.ids || []), ...String(a.subject || '').split(/[\/,\s]+/)]);
    for (const id of ids) {
      if (!/^D\d+$/.test(id) || (LEVELS[a.level] || 0) < (best[id] || 0)) continue;
      best[id] = LEVELS[a.level] || 0;
      out[id] = a.confidence ?? null;
    }
  }
  return out;
}

const BAND_TR = { yuksek: 'yüksek', orta: 'orta', dusuk: 'düşük' };
const pct = (c) => `%${Math.round(c * 100)}`;
const esc = (s) => String(s ?? '').replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[c]);

// "GÜVEN %53" chip + bottleneck line + factor breakdown; empty for outputs saved before confidence existed
function confidenceBlock(al) {
  if (al.confidence == null) return { chip: '', body: '' };
  const band = al.confidence_band || (al.confidence >= 0.8 ? 'yuksek' : al.confidence >= 0.5 ? 'orta' : 'dusuk');
  const d = al.confidence_detail || {};
  const chip = `<span class="conf cf-${band}" title="Güven: delillerden hesaplanan zincir skoru (${BAND_TR[band]})">GÜVEN ${pct(al.confidence)}</span>`;
  const neck = d.bottleneck ? `<div class="cneck">darboğaz: ${esc(d.bottleneck.name)} (${pct(d.bottleneck.confidence)})</div>` : '';
  const rows = (d.factors || []).map((f) => `<li><span class="cbar"><i style="width:${Math.round(f.confidence * 100)}%"></i></span>
    <b>${esc(f.name)}</b> ${pct(f.confidence)}<small>${esc(f.why)}</small></li>`).join('');
  const list = rows ? `<details class="cdet"><summary>GÜVEN DÖKÜMÜ (${d.factors.length})</summary><ul class="cfactors">${rows}</ul></details>` : '';
  return { chip, body: neck + list };
}

export function maxLevel(levels) {
  return Object.values(levels).reduce((m, l) => ((LEVELS[l] || 0) > (LEVELS[m] || 0) ? l : m), null);
}

export function renderAlerts(root, agent, imageId) {
  const a = agent?.assessment;
  if (!a) {
    root.insertAdjacentHTML('afterbegin', `<div class="alert none"><b>${imageId}</b> için GLM çıktısı yok
      <small>python -m scripts.run ${imageId}</small></div>`);
    return;
  }
  const cards = [...a.alerts].sort((x, y) => (LEVELS[y.level] || 0) - (LEVELS[x.level] || 0)).map((al) => {
    const cf = confidenceBlock(al);
    return `
    <div class="alert lv-${al.level}">
      <div class="ah"><span class="lv lv-${al.level}">${LEVEL_TR[al.level] || al.level}</span>${cf.chip}<b>${al.title}</b></div>
      <div class="as">${imageId} · ${al.subject}</div>
      <p>${al.reason}</p>${cf.body}
      <details><summary>DELİLLER (${al.evidence.length})</summary><ul>${al.evidence.map((e) => `<li>${e}</li>`).join('')}</ul></details>
    </div>`;
  }).join('');
  const ignored = a.ignored_reports?.length ? `<div class="ign">Yok sayılan raporlar: ${a.ignored_reports.join(', ')}</div>` : '';
  root.insertAdjacentHTML('afterbegin', `<div class="agroup"><div class="asum"><b>GLM · ${imageId}</b> ${a.summary}</div>${cards}${ignored}</div>`);
}

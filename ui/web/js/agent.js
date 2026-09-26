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

export function maxLevel(levels) {
  return Object.values(levels).reduce((m, l) => ((LEVELS[l] || 0) > (LEVELS[m] || 0) ? l : m), null);
}

export function renderAlerts(root, agent, imageId) {
  root.querySelector('.empty')?.remove();
  const a = agent?.assessment;
  if (!a) {
    root.insertAdjacentHTML('afterbegin', `<div class="alert none"><b>${imageId}</b> için GLM çıktısı yok
      <small>GLM modu “canlı” seçilip tekrar çalıştırılabilir</small></div>`);
    return;
  }
  const cards = [...a.alerts].sort((x, y) => (LEVELS[y.level] || 0) - (LEVELS[x.level] || 0)).map((al) => `
    <div class="alert lv-${al.level}">
      <div class="ah"><span class="lv lv-${al.level}">${LEVEL_TR[al.level] || al.level}</span><b>${al.title}</b></div>
      <div class="as">${imageId} · ${al.subject}</div>
      <p>${al.reason}</p>
      <details><summary>Deliller (${al.evidence.length})</summary><ul>${al.evidence.map((e) => `<li>${e}</li>`).join('')}</ul></details>
    </div>`).join('');
  const ignored = a.ignored_reports?.length ? `<div class="ign">Yok sayılan raporlar: ${a.ignored_reports.join(', ')}</div>` : '';
  root.insertAdjacentHTML('afterbegin', `<div class="agroup"><div class="asum"><b>GLM · ${imageId}</b> ${a.summary}</div>${cards}${ignored}</div>`);
}

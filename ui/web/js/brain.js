// GLM "brain" HUD: stage chips + trace lines into the log while the real agent runs.
import { log } from './panels.js';

const STAGES = ['LOCATE', 'TRACKS', 'MOTION', 'REPORTS', 'ASSESS'];
const TR = { LOCATE: 'KONUM', TRACKS: 'İZ EŞLEME', MOTION: 'HAREKET', REPORTS: 'RAPORLAR', ASSESS: 'KARAR' };
const pretty = (s) => { try { return JSON.stringify(JSON.parse(s), null, 1); } catch { return s; } };
const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[c]);

export function createBrain(root) {
  root.innerHTML = STAGES.map((s) => `<span class="st" data-s="${s}">${TR[s]}</span>`).join('') +
    `<span class="bs"><b>GLM ajan</b> · <span class="bst">beklemede — üst menüden bir görüntü seçin</span></span>`;
  const chip = (s) => root.querySelector(`[data-s="${s}"]`);
  const status = (t) => (root.querySelector('.bst').textContent = t);
  let cur = '';

  return {
    reset(imageId) {
      cur = imageId;
      root.classList.add('on');
      root.querySelectorAll('.st').forEach((e) => (e.className = 'st'));
      status(`${imageId} · başlatılıyor…`);
    },
    event(ev) {
      if (ev.type === 'start') return status(`${ev.image_id} · ajan başladı`);
      if (ev.type !== 'trace') return;
      status(`${cur} · ${TR[ev.stage] || ev.stage} aşaması · ${ev.t}s`);
      const c = chip(ev.stage);
      if (c && !c.classList.contains('done')) c.classList.add('active');
      const tag = `<span class="stg">[${ev.t}s ${ev.stage}]</span>`;
      const short = (x, n = 160) => esc(x.length > n ? x.slice(0, n) + '…' : x);
      if (ev.kind === 'llm') {
        const calls = ev.calls.map((x) => `${x.name}(${x.args})`).join(', ');
        if (calls) log(`${tag} GLM › <b>${short(calls)}</b>`, 'glm', ev.calls.map((x) => `${x.name}(${pretty(x.args)})`).join('\n\n'));
        if (ev.text) log(`${tag} GLM: ${short(ev.text)}`, 'glm dim', ev.text);
      } else if (ev.kind === 'tool') {
        log(`${tag} ${ev.source === 'auto' ? '⚙ auto' : '⚙'} ${ev.name} ⇒ ${short(ev.result, 120)}`, 'tool',
          `ARGS\n${pretty(ev.args)}\n\nSONUÇ\n${pretty(ev.result)}`);
      } else {
        log(`${tag} GATE ${ev.status}${ev.missing?.length ? ' · eksik: ' + esc(ev.missing) : ''}`, ev.status === 'pass' ? 'ok' : 'warn');
        if (c && ev.status !== 'nudge') { c.classList.remove('active'); c.classList.add('done', ev.status); }
      }
    },
    finish(msg) { status(msg); root.classList.remove('on'); },
    cached(imageId) { // saved output shown: every stage already ran earlier
      root.querySelectorAll('.st').forEach((e) => (e.className = 'st done'));
      status(`${imageId} · kayıtlı çıktı gösteriliyor`);
    },
    off(imageId) {
      root.querySelectorAll('.st').forEach((e) => (e.className = 'st'));
      status(`${imageId} · GLM kapalı, yalnızca tespitler`);
    },
  };
}

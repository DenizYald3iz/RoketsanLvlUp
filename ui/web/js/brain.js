// GLM "brain" HUD: stage chips + trace lines into the log while the real agent runs.
import { log } from './panels.js';

const STAGES = ['LOCATE', 'TRACKS', 'MOTION', 'REPORTS', 'ASSESS'];
const TR = { LOCATE: 'KONUM', TRACKS: 'İZ EŞLEME', MOTION: 'HAREKET', REPORTS: 'RAPORLAR', ASSESS: 'KARAR' };
const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[c]);

export function createBrain(root) {
  root.innerHTML = `<span class="bt">GLM AJAN</span>` +
    STAGES.map((s) => `<span class="st" data-s="${s}">${TR[s]}</span>`).join('<i>›</i>') + `<span class="bs">BEKLEMEDE</span>`;
  const chip = (s) => root.querySelector(`[data-s="${s}"]`);
  const status = (t) => (root.querySelector('.bs').textContent = t);

  return {
    reset(imageId) {
      root.classList.add('on');
      root.querySelectorAll('.st').forEach((e) => (e.className = 'st'));
      status(`${imageId} · başlatılıyor…`);
    },
    event(ev) {
      if (ev.type === 'start') return status(`${ev.image_id} · harcanan $${ev.spend}`);
      if (ev.type !== 'trace') return;
      const c = chip(ev.stage);
      if (c && !c.classList.contains('done')) c.classList.add('active');
      const tag = `<span class="stg">[${ev.t}s ${ev.stage}]</span>`;
      if (ev.kind === 'llm') {
        const calls = ev.calls.map((x) => `${x.name}(${esc(x.args)})`).join(', ');
        if (calls) log(`${tag} GLM › <b>${esc(calls)}</b>`, 'glm');
        if (ev.text) log(`${tag} GLM: ${esc(ev.text)}`, 'glm dim');
      } else if (ev.kind === 'tool') {
        log(`${tag} ${ev.source === 'auto' ? '⚙ auto' : '⚙'} ${ev.name} ⇒ ${esc(ev.result)}`, 'tool');
      } else {
        log(`${tag} GATE ${ev.status}${ev.missing?.length ? ' · eksik: ' + esc(ev.missing) : ''}`, ev.status === 'pass' ? 'ok' : 'warn');
        if (c && ev.status !== 'nudge') { c.classList.remove('active'); c.classList.add('done', ev.status); }
      }
    },
    finish(msg) { status(msg); root.classList.remove('on'); },
  };
}

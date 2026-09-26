// Field reports for the current image, stamped with the rule-check verdict (compare_report).
const STAMP = {
  consistent: ['✓ DOĞRULANDI', 'ok'], contradicts: ['✗ ÇELİŞKİ', 'bad'],
  unverifiable: ['? DOĞRULANAMADI', 'unk'], irrelevant: ['— İLGİSİZ', 'unk'],
};
const EMPTY = '<p class="empty">Görüntüyle ilgili raporlar ve doğrulama sonuçları burada görünür.</p>';
const esc = (s) => String(s ?? '').replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[c]);

export function createIntel(root) {
  const byId = {};
  return {
    add(r) {
      root.querySelector('.empty')?.remove();
      const [label, cls] = STAMP[r.verdict] || [r.verdict || '…', 'unk'];
      const el = byId[r.report_id] || document.createElement('div');
      el.className = `icard ${cls}`;
      el.innerHTML = `<div class="ih2"><b>${esc(r.report_id)}</b> · ${esc(r.time)} · ${r.source === 'official' ? 'resmî' : '3. taraf'}
        <span class="stamp">${label}</span></div><p>${esc(r.text)}</p>${r.reason ? `<div class="why">${esc(r.reason)}</div>` : ''}`;
      if (!byId[r.report_id]) root.appendChild(el);
      byId[r.report_id] = el;
    },
    count: () => Object.keys(byId).length,
    counts() {
      const c = {};
      for (const el of Object.values(byId)) for (const k of ['ok', 'bad', 'unk']) if (el.classList.contains(k)) c[k] = (c[k] || 0) + 1;
      return c;
    },
    clear() { for (const k of Object.keys(byId)) delete byId[k]; root.innerHTML = EMPTY; },
  };
}

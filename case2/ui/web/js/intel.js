// Intel feed: field report cards stamped with the rule-check verdict as GLM compares them.
const STAMP = {
  consistent: ['✓ DOĞRULANDI', 'ok'], contradicts: ['✗ ÇELİŞKİ', 'bad'],
  unverifiable: ['? DOĞRULANAMADI', 'unk'], irrelevant: ['— İLGİSİZ', 'unk'],
};
const MAX = 4;

export function createIntel(root) {
  return {
    add(r) {
      const [label, cls] = STAMP[r.verdict] || [r.verdict || '…', 'unk'];
      const el = document.createElement('div');
      el.className = `icard ${cls}`;
      el.innerHTML = `<div class="ih2"><b>${r.report_id}</b> · ${r.time || ''} · ${r.source === 'official' ? 'RESMİ' : '3. TARAF'}
        <span class="stamp">${label}</span></div><p>${r.text || ''}</p>`;
      root.prepend(el);
      while (root.children.length > MAX) root.lastChild.remove();
    },
    clear() { root.innerHTML = ''; },
  };
}

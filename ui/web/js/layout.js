// Side column: collapse toggle, section tabs (anchors + scroll-spy) and tab badges.
const $ = (s) => document.querySelector(s);

export function createLayout(map) {
  const main = $('main'), side = $('#side');
  const tabs = [...document.querySelectorAll('.tabs a')];

  const resize = () => { // keep the map sized while the grid animates
    const t0 = performance.now();
    const step = () => { map.resize(); if (performance.now() - t0 < 450) requestAnimationFrame(step); };
    requestAnimationFrame(step);
  };
  $('#toggle-side').onclick = () => {
    const closed = main.classList.toggle('no-side');
    $('#toggle-side').textContent = closed ? '◀' : '▶';
    resize();
  };

  // tabs scroll the column (or the page, when the layout is stacked on narrow screens);
  // the tab of the section just under the sticky header stays highlighted
  const sections = tabs.map((a) => document.getElementById(a.dataset.sec));
  const stacked = () => getComputedStyle(side).overflowY === 'visible';
  // where the sticky header's bottom edge sits once it is stuck (top of the scroller + its height)
  const headBottom = () => (stacked() ? 0 : side.getBoundingClientRect().top) + $('.side-top').offsetHeight;
  const atBottom = () => (stacked()
    ? window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4
    : side.scrollTop + side.clientHeight >= side.scrollHeight - 4);
  const spy = () => {
    let cur = sections[0];
    for (const s of sections) if (s.getBoundingClientRect().top <= headBottom() + 12) cur = s;
    if (atBottom() && sections.at(-1).getBoundingClientRect().top < window.innerHeight) cur = sections.at(-1);
    tabs.forEach((a) => a.classList.toggle('on', a.dataset.sec === cur.id));
    badge(cur.id.replace('sec-', ''), null, { seen: true });
  };
  tabs.forEach((a) => (a.onclick = (e) => {
    e.preventDefault();
    const dy = document.getElementById(a.dataset.sec).getBoundingClientRect().top - headBottom() - 8;
    if (stacked()) window.scrollBy({ top: dy, behavior: 'smooth' });
    else side.scrollBy({ top: dy, behavior: 'smooth' });
  }));
  side.addEventListener('scroll', spy, { passive: true });
  window.addEventListener('scroll', spy, { passive: true });
  window.addEventListener('resize', spy);
  spy();

  // badge(name, text, {hot, seen}) — text '' clears; `new` marks unseen activity until the section is scrolled to
  function badge(name, text, { hot = false, seen = false } = {}) {
    const el = $(`#nb-${name}`);
    if (!el) return;
    if (seen) return el.classList.remove('new');
    if (text != null) el.textContent = text;
    el.classList.toggle('hot', hot);
    const visible = tabs.find((a) => a.classList.contains('on'))?.dataset.sec === `sec-${name}`;
    el.classList.toggle('new', !visible && text !== '');
  }

  return { badge };
}

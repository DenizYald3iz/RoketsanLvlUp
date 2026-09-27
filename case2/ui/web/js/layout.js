// Collapsible side panels + log. Map is resized while the grid animates.
const $ = (s) => document.querySelector(s);

export function createLayout(map) {
  const main = $('main');
  const resize = () => {
    const t0 = performance.now();
    const step = () => { map.resize(); if (performance.now() - t0 < 450) requestAnimationFrame(step); };
    requestAnimationFrame(step);
  };
  const side = (cls, btn, openArrow, closedArrow) => (closed) => {
    main.classList.toggle(cls, closed);
    $(btn).textContent = main.classList.contains(cls) ? closedArrow : openArrow;
    resize();
  };
  const left = side('no-left', '#toggle-left', '◀', '▶');
  const right = side('no-right', '#toggle-right', '▶', '◀');
  $('#toggle-left').onclick = () => left();
  $('#toggle-right').onclick = () => right();
  $('#toggle-log').onclick = () => {
    const c = $('#logbox').classList.toggle('closed');
    $('#toggle-log').textContent = c ? '▴' : '▾';
  };
  $('#big-log').onclick = () => $('#logbox').classList.toggle('big');
  return { closeLeft: () => left(true), openLeft: () => left(false) };
}

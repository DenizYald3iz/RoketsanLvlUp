// Drone image with animated boxes on a canvas.
import { labelColor, labelTr } from './config.js';

export function createImageView(root) {
  root.innerHTML = `<div class="iv-empty">GÖRÜNTÜ BEKLENİYOR</div><canvas></canvas><div class="scan"></div>`;
  const canvas = root.querySelector('canvas');
  const ctx = canvas.getContext('2d');
  let img = null, boxes = [], size = [1, 1], hl = null;

  function draw() {
    if (!img) return;
    const w = root.clientWidth, h = Math.round((w * img.naturalHeight) / img.naturalWidth);
    const dpr = window.devicePixelRatio || 1;
    canvas.width = w * dpr; canvas.height = h * dpr;
    canvas.style.width = `${w}px`; canvas.style.height = `${h}px`;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.drawImage(img, 0, 0, w, h);
    const sx = w / size[0], sy = h / size[1];
    for (const d of boxes) {
      const c = labelColor(d.label), x = (d.cx - d.w / 2) * sx, y = (d.cy - d.h / 2) * sy, bw = d.w * sx, bh = d.h * sy;
      const on = hl === d.key;
      ctx.lineWidth = on ? 3 : 1.5;
      ctx.strokeStyle = c;
      ctx.shadowColor = c; ctx.shadowBlur = on ? 16 : 6;
      corners(x, y, bw, bh, Math.min(10, bw / 3, bh / 3));
      ctx.shadowBlur = 0;
      if (on) { ctx.fillStyle = c + '33'; ctx.fillRect(x, y, bw, bh); }
      const tag = `${d.det_id} ${labelTr(d.label)} ${(d.conf * 100).toFixed(0)}%`;
      ctx.font = '600 10px "JetBrains Mono", monospace';
      const tw = ctx.measureText(tag).width + 8;
      ctx.fillStyle = c; ctx.fillRect(x, y - 14, tw, 14);
      ctx.fillStyle = '#021016'; ctx.fillText(tag, x + 4, y - 4);
    }
  }

  function corners(x, y, w, h, k) {
    ctx.beginPath();
    for (const [px, py, dx, dy] of [[x, y, 1, 1], [x + w, y, -1, 1], [x, y + h, 1, -1], [x + w, y + h, -1, -1]]) {
      ctx.moveTo(px + dx * k, py); ctx.lineTo(px, py); ctx.lineTo(px, py + dy * k);
    }
    ctx.stroke();
    ctx.globalAlpha = 0.35; ctx.strokeRect(x, y, w, h); ctx.globalAlpha = 1;
  }

  new ResizeObserver(draw).observe(root);

  return {
    async show(url) {
      img = new Image();
      img.src = url;
      await img.decode();
      boxes = []; hl = null;
      root.classList.add('has-img');
      draw();
    },
    scanning(on) { root.classList.toggle('scanning', on); },
    setSize(s) { size = s; },
    addBox(d) { boxes.push(d); draw(); },
    highlight(key) { hl = key; draw(); },
  };
}

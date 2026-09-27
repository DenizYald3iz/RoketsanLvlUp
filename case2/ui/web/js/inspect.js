// "Target analysis" overlay: when GLM calls view_image, show exactly what it is looking at.
import { labelColor } from './config.js';

export function createInspector(root) {
  root.innerHTML = `
    <div class="ih"><span>◎ HEDEF ANALİZİ · GLM VISION</span><span class="ic"></span></div>
    <div class="iw"><canvas></canvas><div class="grid"></div><div class="xh"></div><div class="sweep"></div></div>
    <div class="if"><span class="it">GLM görüntüyü inceliyor</span><span class="dots"></span></div>`;
  const canvas = root.querySelector('canvas'), ctx = canvas.getContext('2d');
  let closeTimer = null;

  return {
    // img: HTMLImageElement of the current frame; size: meta [w,h]; crop: [x,y,w,h] | null (original px)
    open(img, size, crop, dets = []) {
      clearTimeout(closeTimer);
      const [cx, cy, cw, ch] = crop || [0, 0, size[0], size[1]];
      const k = img.naturalWidth / size[0];
      const W = 520, H = Math.round((W * ch) / cw);
      canvas.width = W; canvas.height = Math.min(H, 420);
      ctx.imageSmoothingEnabled = false; // pixelated zoom looks like a real enhance
      ctx.drawImage(img, cx * k, cy * k, cw * k, ch * k, 0, 0, W, H);
      const s = W / cw;
      for (const d of dets) {
        const x = (d.cx - d.w / 2 - cx) * s, y = (d.cy - d.h / 2 - cy) * s;
        if (x > W || y > H || x + d.w * s < 0 || y + d.h * s < 0) continue;
        ctx.strokeStyle = labelColor(d.label); ctx.lineWidth = 2; ctx.setLineDash([6, 4]);
        ctx.strokeRect(x, y, d.w * s, d.h * s);
        ctx.setLineDash([]); ctx.fillStyle = labelColor(d.label); ctx.font = '600 11px "JetBrains Mono"';
        ctx.fillText(d.det_id, x + 3, y - 4);
      }
      root.querySelector('.ic').textContent = crop ? `CROP x${cx} y${cy} · ${cw}×${ch}px · ${(W / cw).toFixed(1)}×` : 'TAM KARE';
      root.querySelector('.it').textContent = 'GLM görüntüyü inceliyor';
      root.classList.remove('found');
      root.classList.add('on');
    },
    finding(text) {
      if (!root.classList.contains('on')) return;
      root.querySelector('.it').textContent = `BULGU › ${text || 'değerlendirme tamamlandı'}`;
      root.classList.add('found');
      closeTimer = setTimeout(() => root.classList.remove('on'), 6000);
    },
    close() { root.classList.remove('on'); },
  };
}

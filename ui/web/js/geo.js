// Tiny spherical helpers. Points are [lon, lat].
const R = 6371000;
const rad = (d) => (d * Math.PI) / 180;
const deg = (r) => (r * 180) / Math.PI;

export function dest([lon, lat], bearingDeg, distM) {
  const d = distM / R, b = rad(bearingDeg), p1 = rad(lat), l1 = rad(lon);
  const p2 = Math.asin(Math.sin(p1) * Math.cos(d) + Math.cos(p1) * Math.sin(d) * Math.cos(b));
  const l2 = l1 + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(p1), Math.cos(d) - Math.sin(p1) * Math.sin(p2));
  return [deg(l2), deg(p2)];
}

export function circle(center, r, steps = 128) {
  const pts = [];
  for (let i = 0; i <= steps; i++) pts.push(dest(center, (360 * i) / steps, r));
  return pts;
}

export function wedge(center, b0, b1, r0, r1, steps = 32) {
  const outer = [], inner = [];
  for (let i = 0; i <= steps; i++) {
    const b = b0 + ((b1 - b0) * i) / steps;
    outer.push(dest(center, b, r1));
    inner.push(dest(center, b, r0));
  }
  return [...outer, ...inner.reverse(), outer[0]];
}

export const metersPerPixel = (lat, zoom) => (78271.517 * Math.cos(rad(lat))) / 2 ** zoom;
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

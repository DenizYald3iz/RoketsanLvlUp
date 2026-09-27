// All tunables in one place. Change look & timing here, not in the modules.
export const CFG = {
  outerRadiusM: 8000,          // sector ring outer edge (tracks reach ~8 km from base)
  ringStepM: 1000,             // range rings every N metres
  overviewPad: 24,             // px around the whole zone ring in the overview (bigger = further out)
  frameMaxZoom: 17.3,          // max zoom when focusing a drone frame
  trackMaxZoom: 15,            // max zoom when a vehicle's track is selected
  dropDelayMs: 220,            // stagger between detection "drops"
  flyMs: 1600,
  snapOutMs: 450,              // sudden zoom-out before GLM's track trails
  basemap: 'sat',              // 'sat' | 'dark'
  minConf: 0.3,
  trackTailMin: 30,
  stopStepM: 30,               // track step shorter than this = vehicle stopped (5-min samples)            // minutes of track history drawn on the map
};

export const LABELS = {
  car:   { color: '#22e6ff', tr: 'OTOMOBİL' },
  van:   { color: '#b388ff', tr: 'PANELVAN' },
  truck: { color: '#ffb020', tr: 'KAMYON' },
  bus:   { color: '#ff4d6d', tr: 'OTOBÜS' },
};
export const labelColor = (l) => (LABELS[l] || { color: '#9fb3c8' }).color;
export const labelTr = (l) => (LABELS[l] || { tr: l.toUpperCase() }).tr;

// "Kuzeydogu Kavsagi" → "KUZEYDOĞU KAVŞAĞI" (display only; ids stay ASCII)
const FIX = { Kuzeydogu: 'Kuzeydoğu', Guneydogu: 'Güneydoğu', Guneybati: 'Güneybatı', Kuzeybati: 'Kuzeybatı',
  Dogu: 'Doğu', Bati: 'Batı', Guney: 'Güney', Kavsagi: 'Kavşağı', Yerlesimi: 'Yerleşimi', Kapisi: 'Kapısı',
  Yaklasimi: 'Yaklaşımı', Us: 'Üs' };
export const prettyZone = (n) => n.split(' ').map((w) => FIX[w] || w).join(' ').toLocaleUpperCase('tr');

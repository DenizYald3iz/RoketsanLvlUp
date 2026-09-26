// Tactical map: base + center ring + 8 sectors, radar sweep, drone frames, detection drops.
import { CFG, labelColor, labelTr, prettyZone } from './config.js';
import { circle, dest, metersPerPixel, sleep, wedge } from './geo.js';

const BASEMAPS = {
  sat: { tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
    paint: { 'raster-saturation': -0.7, 'raster-brightness-max': 0.45, 'raster-contrast': 0.15 } },
  dark: { tiles: ['https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png'], paint: {} },
};

function style() {
  const sources = {}, layers = [{ id: 'bg', type: 'background', paint: { 'background-color': '#03070c' } }];
  for (const [k, b] of Object.entries(BASEMAPS)) {
    sources[k] = { type: 'raster', tiles: b.tiles, tileSize: 256, maxzoom: 19 };
    layers.push({ id: `bm-${k}`, type: 'raster', source: k, paint: b.paint,
      layout: { visibility: k === CFG.basemap ? 'visible' : 'none' } });
  }
  return { version: 8, sources, layers };
}

const fc = (features) => ({ type: 'FeatureCollection', features });
const poly = (ring, properties = {}) => ({ type: 'Feature', properties, geometry: { type: 'Polygon', coordinates: [ring] } });
const line = (coords, properties = {}) => ({ type: 'Feature', properties, geometry: { type: 'LineString', coordinates: coords } });

export function createMap(container, layout) {
  const base = [layout.base.lon, layout.base.lat];
  const map = new maplibregl.Map({ container, style: style(), center: base, zoom: CFG.overviewZoom,
    attributionControl: false, pitch: 0 });
  const zoneEls = {};
  const links = [];
  const ready = new Promise((r) => map.on('load', r));

  ready.then(() => {
    addSectors();
    addRadar();
    map.addSource('frames', { type: 'geojson', data: fc([]), promoteId: 'image_id' });
    map.addLayer({ id: 'frames-fill', type: 'fill', source: 'frames',
      paint: { 'fill-color': '#22e6ff', 'fill-opacity': ['case', ['boolean', ['feature-state', 'done'], false], 0.0, 0.12] } });
    map.addLayer({ id: 'frames-line', type: 'line', source: 'frames',
      paint: { 'line-color': ['case', ['boolean', ['feature-state', 'done'], false], '#ffb020', '#22e6ff'],
        'line-width': ['case', ['boolean', ['feature-state', 'done'], false], 2, 1], 'line-opacity': 0.8 } });
    map.addSource('tracks', { type: 'geojson', data: fc([]) });
    map.addLayer({ id: 'tracks-glow', type: 'line', source: 'tracks', layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': ['get', 'color'], 'line-width': 8, 'line-blur': 6, 'line-opacity': 0.5 } });
    map.addLayer({ id: 'tracks', type: 'line', source: 'tracks', layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': ['get', 'color'], 'line-width': 2.5 } });
    map.addSource('sel', { type: 'geojson', data: fc([]), lineMetrics: true });
    map.addLayer({ id: 'sel-glow', type: 'line', source: 'sel', layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': '#ffb020', 'line-width': 9, 'line-blur': 7, 'line-opacity': 0.35 } });
    map.addLayer({ id: 'sel', type: 'line', source: 'sel', layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-width': 3, 'line-gradient': ['interpolate', ['linear'], ['line-progress'], 0, 'rgba(0,0,0,0)', 1, '#ffb020'] } });
    map.addSource('links', { type: 'geojson', data: fc([]) });
    map.addLayer({ id: 'links', type: 'line', source: 'links',
      paint: { 'line-color': '#ffb020', 'line-width': 1.2, 'line-dasharray': [2, 3], 'line-opacity': 0.7 } });
    const onMarker = (e) => e.originalEvent.target.closest?.('.maplibregl-marker');
    map.on('click', 'frames-fill', (e) => !onMarker(e) && onFrameClick?.(e.features[0].properties.image_id));
    map.on('click', (e) => !onMarker(e) && !map.queryRenderedFeatures(e.point, { layers: ['frames-fill'] }).length && onEmptyClick?.());
    map.on('mouseenter', 'frames-fill', () => (map.getCanvas().style.cursor = 'pointer'));
    map.on('mouseleave', 'frames-fill', () => (map.getCanvas().style.cursor = ''));
  });

  function addSectors() {
    const R0 = layout.center_radius_m, R1 = CFG.outerRadiusM;
    const sectors = layout.zones.map((z) => poly(wedge(base, z.bearing - 22.5, z.bearing + 22.5, R0, R1), { name: z.name }));
    sectors.push(poly(circle(base, R0), { name: layout.base.name }));
    map.addSource('sectors', { type: 'geojson', data: fc(sectors), promoteId: 'name' });
    // heat: detection density 0..1; threat: GLM alert level 0..3 — the hotter one wins
    const heat = ['max', ['coalesce', ['feature-state', 'heat'], 0], ['/', ['coalesce', ['feature-state', 'threat'], 0], 3]];
    map.addLayer({ id: 'sectors-fill', type: 'fill', source: 'sectors', paint: {
      'fill-color': ['interpolate', ['linear'], heat, 0, '#22e6ff', 0.5, '#ffb020', 1, '#ff3355'],
      'fill-opacity': ['case', ['boolean', ['feature-state', 'flash'], false], 0.45,
        ['interpolate', ['linear'], heat, 0, 0.03, 1, 0.28]],
      'fill-opacity-transition': { duration: 500 } } });
    map.addLayer({ id: 'sectors-line', type: 'line', source: 'sectors',
      paint: { 'line-color': '#22e6ff', 'line-opacity': 0.55, 'line-width': 1.2 } });

    const rings = [];
    for (let r = CFG.ringStepM; r <= CFG.outerRadiusM; r += CFG.ringStepM) rings.push(line(circle(base, r), { r }));
    map.addSource('rings', { type: 'geojson', data: fc(rings) });
    map.addLayer({ id: 'rings', type: 'line', source: 'rings',
      paint: { 'line-color': '#22e6ff', 'line-opacity': 0.18, 'line-width': 1, 'line-dasharray': [1, 4] } });

    for (const z of layout.zones) zoneEls[z.name] = zoneLabel(z.name, dest(base, z.bearing, CFG.outerRadiusM * 0.78));
    zoneEls[layout.base.name] = zoneLabel(layout.base.name, base, true);
  }

  function zoneLabel(name, lngLat, isBase = false) {
    const el = document.createElement('div');
    el.className = `zone-label${isBase ? ' base' : ''}`;
    el.innerHTML = `${isBase ? '<div class="base-core"></div>' : ''}<span class="zn">${prettyZone(name)}</span><span class="zc">0</span>`;
    new maplibregl.Marker({ element: el }).setLngLat(lngLat).addTo(map);
    return el;
  }

  function addRadar() {
    const el = document.createElement('div');
    el.className = 'radar';
    el.innerHTML = '<i></i>'; // rotate the child: the marker element's transform is owned by MapLibre
    new maplibregl.Marker({ element: el, pitchAlignment: 'map' }).setLngLat(base).addTo(map);
    const fit = () => {
      const px = (2 * CFG.outerRadiusM) / metersPerPixel(layout.base.lat, map.getZoom());
      el.style.width = el.style.height = `${px}px`;
      el.style.display = px > 16000 ? 'none' : '';
    };
    map.on('zoom', fit);
    fit();
  }

  const tracks = {}, trackMarkers = [];
  const trackColor = (k) => (k?.trend === 'approaching' ? '#ff3355' : k?.stopped_now ? '#6f8fa0' : '#ffb020');

  let onFrameClick = null, onDetClick = null, onEmptyClick = null;
  let heatMax = 1;

  return {
    ready,
    map,
    onFrameClick: (fn) => (onFrameClick = fn),
    onDetClick: (fn) => (onDetClick = fn),
    onEmptyClick: (fn) => (onEmptyClick = fn),

    setBasemap(k) {
      for (const b of Object.keys(BASEMAPS)) map.setLayoutProperty(`bm-${b}`, 'visibility', b === k ? 'visible' : 'none');
    },

    setFrames(images) {
      map.getSource('frames').setData(fc(images.map((i) => poly([...i.footprint, i.footprint[0]], { image_id: i.image_id }))));
    },

    async focusFrame(res, imageUrl) {
      const lons = res.footprint.map((p) => p[0]), lats = res.footprint.map((p) => p[1]);
      const srcId = `img-${res.image_id}`;
      if (!map.getSource(srcId)) {
        map.addSource(srcId, { type: 'image', url: imageUrl, coordinates: res.footprint });
        map.addLayer({ id: srcId, type: 'raster', source: srcId, paint: { 'raster-opacity': 0.92, 'raster-fade-duration': 600 } },
          'frames-fill');
      }
      map.setFeatureState({ source: 'frames', id: res.image_id }, { done: true });
      map.fitBounds([[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]],
        { padding: 80, maxZoom: CFG.frameMaxZoom, duration: CFG.flyMs });
      await sleep(CFG.flyMs + 150);
    },

    async dropDetection(d, onLanded) {
      const el = document.createElement('div');
      el.className = `det det-${d.label}`;
      el.style.setProperty('--c', labelColor(d.label));
      el.dataset.det = d.key;
      el.title = `${d.det_id} · ${labelTr(d.label)} · ${(d.conf * 100).toFixed(0)}%\n${d.lat}, ${d.lon}\n${prettyZone(d.zone)}`;
      el.innerHTML = `<i></i><b>${d.det_id}</b>`;
      el.onclick = (e) => { e.stopPropagation(); onDetClick?.(d.key); };
      new maplibregl.Marker({ element: el }).setLngLat([d.lon, d.lat]).addTo(map);
      onLanded?.(d);
      await sleep(CFG.dropDelayMs);
      return el;
    },

    link(res) {
      links.push(line([res.center, base]));
      map.getSource('links').setData(fc(links));
    },

    async overview() {
      map.flyTo({ center: base, zoom: CFG.overviewZoom, duration: CFG.flyMs });
      await sleep(CFG.flyMs);
    },

    setZoneCount(name, n) {
      heatMax = Math.max(heatMax, n);
      const el = zoneEls[name];
      if (el) {
        el.querySelector('.zc').textContent = n;
        el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash');
      }
      map.setFeatureState({ source: 'sectors', id: name }, { heat: Math.min(1, n / Math.max(6, heatMax)), flash: true });
      setTimeout(() => map.setFeatureState({ source: 'sectors', id: name }, { flash: false }), 450);
    },

    setZoneThreat(name, lvl) {
      const cur = map.getFeatureState({ source: 'sectors', id: name }).threat || 0;
      map.setFeatureState({ source: 'sectors', id: name }, { threat: Math.max(cur, lvl) });
      zoneEls[name]?.classList.add(`threat-${Math.max(cur, lvl)}`);
    },

    setDetLevel(key, level) {
      document.querySelectorAll(`.det[data-det="${key}"]`).forEach((e) => e.classList.add(`lv-${level}`));
    },

    // Animated trail of a track (GLM asked for its kinematics) + ETA tag at its head.
    // GLM is looking at this track right now: flash its recent tail, then fade it out.
    async flashTrack({ track_id, points, kin }) {
      if (!points?.length) return;
      const tail = points.slice(-(Math.round(CFG.trackTailMin / 5) + 1));
      const color = trackColor(kin);
      for (let i = 2; i <= tail.length; i++) {
        tracks[track_id] = line(tail.slice(0, i), { color });
        map.getSource('tracks').setData(fc(Object.values(tracks)));
        await sleep(60);
      }
      setTimeout(() => { delete tracks[track_id]; map.getSource('tracks').setData(fc(Object.values(tracks))); }, 1200);
    },

    // Selected vehicle only: full history fading old → new, heading arrow, speed/ETA tag. null clears.
    showTrack(t) {
      trackMarkers.splice(0).forEach((m) => m.remove());
      if (!t?.points?.length) return map.getSource('sel').setData(fc([]));
      const color = trackColor(t.kin), pts = t.points;
      map.setPaintProperty('sel', 'line-gradient',
        ['interpolate', ['linear'], ['line-progress'], 0, 'rgba(0,0,0,0)', 0.5, color + '66', 1, color]);
      map.setPaintProperty('sel-glow', 'line-color', color);
      map.getSource('sel').setData(fc([line(pts)]));

      const head = pts.at(-1), prev = pts.findLast((p) => p[0] !== head[0] || p[1] !== head[1]);
      const k = t.kin || {};
      const el = document.createElement('div');
      el.className = 'track-tag';
      el.style.setProperty('--c', color);
      el.innerHTML = `<b>${t.track_id}</b>${k.last30_speed_mps != null ? ` · ${k.last30_speed_mps} m/s` : ''}` +
        `${k.eta_min != null ? ` · ETA ${k.eta_min} dk` : ''}${k.loitering ? ' · DOLAŞMA' : ''}${k.stopped_now ? ' · DURUYOR' : ''}` +
        `<small>${t.times?.[0] || ''} → ${t.times?.at(-1) || ''}${k.base_dist_now_m != null ? ` · üsse ${k.base_dist_now_m} m` : ''}</small>`;
      trackMarkers.push(new maplibregl.Marker({ element: el, anchor: 'left', offset: [14, 0] }).setLngLat(head).addTo(map));
      if (prev) {
        const a = document.createElement('div');
        a.className = 'track-arrow';
        a.style.setProperty('--c', color);
        const brg = (Math.atan2((head[0] - prev[0]) * Math.cos((head[1] * Math.PI) / 180), head[1] - prev[1]) * 180) / Math.PI;
        a.innerHTML = `<i style="rotate:${brg}deg"></i>`;
        trackMarkers.push(new maplibregl.Marker({ element: a }).setLngLat(head).addTo(map));
      }
      const lons = pts.map((p) => p[0]), lats = pts.map((p) => p[1]);
      map.fitBounds([[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]],
        { padding: 140, maxZoom: 16, duration: 1000 });
    },

    clearTracks() {
      for (const k of Object.keys(tracks)) delete tracks[k];
      map.getSource('tracks').setData(fc([]));
      this.showTrack(null);
    },

    tagDet(key, text) {
      document.querySelectorAll(`.det[data-det="${key}"]`).forEach((e) => {
        e.querySelector('b').textContent = text;
        e.classList.add('lock');
      });
    },

    highlight(detId, on) {
      document.querySelectorAll(`.det[data-det="${detId}"]`).forEach((e) => e.classList.toggle('hl', on));
    },
  };
}

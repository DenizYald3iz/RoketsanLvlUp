// Backend calls only. Every fetch lives here.
async function json(res) {
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(body.detail?.msg || body.detail || res.statusText), { detail: body.detail });
  return body;
}

export const getLayout = () => fetch('/api/layout').then(json);
export const getImages = () => fetch('/api/images').then(json);

export function analyze(file, imageId = '', minConf) {
  const fd = new FormData();
  fd.append('file', file);
  fd.append('image_id', imageId);
  if (minConf != null) fd.append('min_conf', minConf);
  return fetch('/api/analyze', { method: 'POST', body: fd }).then(json);
}

export async function sampleFile(imageId) {
  const blob = await fetch(`/api/images/${imageId}/file`).then((r) => r.blob());
  return new File([blob], `${imageId}.jpg`, { type: blob.type });
}
export const getAgent = (imageId) => fetch(`/api/agent/${imageId}`).then(json);

// Live GLM run: NDJSON stream → onEvent(ev) per line; resolves with the final agent output.
export async function runAgent(imageId, onEvent) {
  const res = await fetch(`/api/agent/${imageId}/run`, { method: 'POST' });
  if (!res.ok) throw new Error(`agent: ${res.status}`);
  const reader = res.body.getReader(), dec = new TextDecoder();
  let buf = '', final = null;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf('\n')) >= 0) {
      const ev = JSON.parse(buf.slice(0, i));
      buf = buf.slice(i + 1);
      onEvent(ev);
      if (ev.type === 'done') final = ev.agent;
      if (ev.type === 'error') throw new Error(ev.msg);
    }
  }
  return final;
}

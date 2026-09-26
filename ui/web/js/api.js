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

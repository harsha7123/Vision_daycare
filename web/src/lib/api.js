const KEY = "vd.apiUrl";
const DEFAULT_URL = (import.meta.env.VITE_API_URL || "http://localhost:7860").replace(/\/$/, "");

export function getApiUrl() {
  try {
    return (localStorage.getItem(KEY) || DEFAULT_URL).replace(/\/$/, "");
  } catch {
    return DEFAULT_URL;
  }
}
export function setApiUrl(url) {
  try {
    if (url) localStorage.setItem(KEY, url.replace(/\/$/, ""));
    else localStorage.removeItem(KEY);
  } catch { /* storage blocked */ }
}
export const defaultApiUrl = DEFAULT_URL;

export const absUrl = (path) => (path && path.startsWith("/api/") ? getApiUrl() + path : path);

async function request(path, init = {}) {
  let r;
  try {
    r = await fetch(getApiUrl() + path, init);
  } catch {
    throw new Error("Can't reach the analysis server. It may be starting up (free servers sleep); try again in a minute.");
  }
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try { msg = (await r.json()).detail || msg; } catch { /* not json */ }
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return r.json();
}

export const api = {
  health: () => request("/api/health"),
  config: () => request("/api/config"),
  job: (id) => request(`/api/jobs/${id}`),
  result: (id) => request(`/api/jobs/${id}/result`),
  rerun: (id, opts) =>
    request(`/api/jobs/${id}/rerun`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(opts) }),
  remove: (id) => request(`/api/jobs/${id}`, { method: "DELETE" }),
  call: (to, message) =>
    request("/api/notify/call", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ to, message }) }),
  sms: (to, message) =>
    request("/api/notify/sms", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ to, message }) }),

  /** Upload with progress (fetch has no upload progress). */
  upload(file, options, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", getApiUrl() + "/api/jobs");
      xhr.upload.onprogress = (e) => e.lengthComputable && onProgress?.(e.loaded / e.total);
      xhr.onload = () => {
        let body = {};
        try { body = JSON.parse(xhr.responseText); } catch { /* ignore */ }
        if (xhr.status >= 200 && xhr.status < 300) resolve(body);
        else reject(new Error(body.detail || `Upload failed (${xhr.status})`));
      };
      xhr.onerror = () => reject(new Error("Can't reach the analysis server. Check the server address in Settings."));
      const fd = new FormData();
      fd.append("video", file);
      fd.append("options", JSON.stringify(options));
      xhr.send(fd);
    });
  },
};

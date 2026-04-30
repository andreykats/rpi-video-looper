// Thin REST wrappers. All endpoints are same-origin so no auth or CORS.

async function apiGet(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json();
}

async function apiPost(path, body) {
  const r = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  // 202 = accepted (config save / reboot); 200 = ok.
  if (!r.ok && r.status !== 202) {
    let detail;
    try { detail = await r.json(); } catch { detail = await r.text(); }
    const e = new Error(`${path} → ${r.status}`);
    e.status = r.status;
    e.detail = detail;
    throw e;
  }
  if (r.status === 202) return { ok: true, _accepted: true };
  return r.json();
}

const API = {
  getState: () => apiGet('/api/state'),
  getPool: () => apiGet('/api/pool'),
  getStorage: () => apiGet('/api/storage'),
  getPlaylist: (ch) => apiGet(`/api/playlist/${ch}`),
  savePlaylist: (ch, entries, name) => {
    // Server expects {filename} per entry (schema v4 — USB-root-relative).
    // Duplicates encode repetition. Omit `name` when undefined so the
    // server preserves the existing value (drag-reorder shouldn't wipe
    // a user-set channel name).
    const body = {
      entries: entries.map(e => ({ filename: e.filename })),
    };
    if (name !== undefined) body.name = name;
    return apiPost(`/api/playlist/${ch}`, body);
  },
  renameFile: (path, newName) => apiPost('/api/file/rename', { path, newName }),
  deleteFile: (path) => apiPost('/api/file/delete', { path }),
  moveFile: (path, targetDir) => apiPost('/api/file/move', { path, targetDir }),
  // Raw-body upload. fetch() can't report upload progress, so XHR.
  uploadFile: (file, opts) => new Promise((resolve, reject) => {
    const onProgress = opts && opts.onProgress;
    const signal = opts && opts.signal;
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `/api/file/upload?name=${encodeURIComponent(file.name)}`);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try { resolve(JSON.parse(xhr.responseText)); }
        catch { resolve({ ok: true }); }
      } else {
        let detail;
        try { detail = JSON.parse(xhr.responseText); }
        catch { detail = xhr.responseText; }
        const err = new Error(`/api/file/upload → ${xhr.status}`);
        err.status = xhr.status;
        err.detail = detail;
        reject(err);
      }
    };
    xhr.onerror = () => reject(new Error('upload network error'));
    xhr.onabort = () => reject(new Error('upload aborted'));
    if (signal) signal.addEventListener('abort', () => xhr.abort());
    xhr.send(file);
  }),
  renameFolder: (path, newName) => apiPost('/api/folder/rename', { path, newName }),
  deleteFolder: (path) => apiPost('/api/folder/delete', { path }),
  getConfig: () => apiGet('/api/config'),
  saveConfig: (cfg) => apiPost('/api/config', cfg),
  reboot: () => apiPost('/api/system/reboot', {}),
  resetBroadcast: () => apiPost('/api/broadcast/reset', {}),
};

window.API = API;

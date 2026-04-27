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
  savePlaylist: (ch, entries) => apiPost(`/api/playlist/${ch}`, { entries }),
  renameFile: (path, newName) => apiPost('/api/file/rename', { path, newName }),
  deleteFile: (path) => apiPost('/api/file/delete', { path }),
  moveFile: (path, targetDir) => apiPost('/api/file/move', { path, targetDir }),
  renameFolder: (path, newName) => apiPost('/api/folder/rename', { path, newName }),
  deleteFolder: (path) => apiPost('/api/folder/delete', { path }),
  getConfig: () => apiGet('/api/config'),
  saveConfig: (cfg) => apiPost('/api/config', cfg),
  reboot: () => apiPost('/api/system/reboot', {}),
};

window.API = API;

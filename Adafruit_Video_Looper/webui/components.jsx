// Format seconds as M:SS or H:MM:SS
function fmtDur(sec) {
  if (!sec || !isFinite(sec)) return '0:00';
  sec = Math.floor(sec);
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (h) return `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
  return `${m}:${String(s).padStart(2,'0')}`;
}

// Format an elapsed-broadcast offset as +HH:MM:SS — used for the global
// broadcast clock display so the user can read the shared T0 baseline.
function fmtBcastElapsed(sec) {
  if (!isFinite(sec) || sec < 0) sec = 0;
  sec = Math.floor(sec);
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  return `+${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
}

// Format minutes from start as HH:MM (broadcast clock starting at e.g. 18:00)
function fmtClock(minFromStart, baseHour = 18) {
  const totalMin = baseHour * 60 + minFromStart;
  const h = Math.floor(totalMin / 60) % 24;
  const m = Math.floor(totalMin) % 60;
  return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}`;
}

function fmtBytes(n) {
  if (!n || !isFinite(n)) return '0 B';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

// Chunky labeled hardware button — beige cap with engraved label
function HwButton({ label, color = 'beige', lit = false, onClick, disabled = false, big = false, indicator }) {
  const palette = {
    beige:  { face: '#c8ccd0', edge: '#7a7e82', text: '#1c1e22' },
    orange: { face: '#d97740', edge: '#a85020', text: '#1a0f08' },
    red:    { face: '#b83828', edge: '#7a1f12', text: '#fff' },
    green:  { face: '#3d7a3d', edge: '#1f4a1f', text: '#fff' },
    blue:   { face: '#3a6a9c', edge: '#1c3858', text: '#fff' },
    black:  { face: '#25272b', edge: '#0e1014', text: '#c8ccd0' },
  };
  const c = palette[color];
  return (
    <button
      onClick={disabled ? undefined : onClick}
      disabled={disabled}
      className={`hw-btn${disabled ? ' disabled' : ''}`}
      style={{
        '--face': c.face,
        '--edge': c.edge,
        '--text': c.text,
        height: big ? 44 : 30,
        padding: big ? '0 18px' : '0 12px',
      }}>
      {indicator !== undefined && (
        <span className="hw-led" data-on={indicator ? '1' : '0'} />
      )}
      {label}
    </button>
  );
}

// Engraved label — used on chassis sections
function EngravedLabel({ children, size = 'xs' }) {
  return (
    <span className={`engraved engraved-${size}`}>{children}</span>
  );
}

// LED indicator
function LED({ on, color = 'red', label }) {
  return (
    <span className="led-wrap">
      <span className="led" data-color={color} data-on={on ? '1' : '0'} />
      {label && <span className="led-lbl">{label}</span>}
    </span>
  );
}

// ─────────── Video Pool Sidebar ───────────
function VideoPoolSidebar({ pool, uploads, uploadExtensions, onDragStart, usedIds, onRename, onDelete, onRenameFolder, onDeleteFolder, onUpload }) {
  const [hideUsed, setHideUsed] = React.useState(false);
  const [sortBy, setSortBy] = React.useState('name'); // name | size

  const [menu, setMenu] = React.useState(null);
  const [renaming, setRenaming] = React.useState(null);
  const [renameValue, setRenameValue] = React.useState('');
  const fileInputRef = React.useRef(null);
  const ghostUploads = uploads || [];

  React.useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(null);
    const onKey = (e) => { if (e.key === 'Escape') setMenu(null); };
    window.addEventListener('mousedown', close);
    window.addEventListener('scroll', close, true);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('mousedown', close);
      window.removeEventListener('scroll', close, true);
      window.removeEventListener('keydown', onKey);
    };
  }, [menu]);

  const startRenameFile = (v) => {
    setMenu(null);
    setRenameValue(v.name);
    setRenaming({ kind: 'file', key: v.id });
  };
  const startRenameFolder = (node) => {
    setMenu(null);
    setRenameValue(node.name);
    setRenaming({ kind: 'folder', key: node.path });
  };
  const commitRename = () => {
    if (!renaming) return;
    const nm = renameValue.trim();
    if (nm) {
      if (renaming.kind === 'file' && onRename) onRename(renaming.key, nm);
      else if (renaming.kind === 'folder' && onRenameFolder) onRenameFolder(renaming.key, nm);
    }
    setRenaming(null);
  };
  const cancelRename = () => setRenaming(null);

  const handleDeleteFile = (v) => {
    setMenu(null);
    if (onDelete) onDelete(v.id);
  };
  const handleDeleteFolder = (node) => {
    setMenu(null);
    if (onDeleteFolder) onDeleteFolder(node.path);
  };

  const filtered = pool.filter(v => !(hideUsed && usedIds && usedIds.has(v.relPath)));

  const tree = React.useMemo(() => {
    return buildTreeFromList(filtered, sortBy);
  }, [filtered, sortBy]);

  const allFolderPaths = React.useMemo(() => {
    const out = new Set();
    const walk = (n) => { out.add(n.path); n.childList.forEach(walk); };
    walk(tree);
    return out;
  }, [tree]);

  const [expanded, setExpanded] = React.useState(() => new Set(['/']));
  const effectiveExpanded = expanded;
  const toggle = (p) => {
    setExpanded(prev => {
      const n = new Set(prev);
      if (n.has(p)) n.delete(p); else n.add(p);
      return n;
    });
  };
  const expandAll = () => setExpanded(new Set(allFolderPaths));
  const collapseAll = () => setExpanded(new Set(['/']));
  const expandDescendants = (node) => {
    setExpanded(prev => {
      const n = new Set(prev);
      const walk = (x) => { n.add(x.path); x.childList.forEach(walk); };
      walk(node);
      return n;
    });
  };
  const collapseDescendants = (node) => {
    setExpanded(prev => {
      const n = new Set(prev);
      const walk = (x) => { n.delete(x.path); x.childList.forEach(walk); };
      walk(node);
      return n;
    });
  };

  const usedCount = usedIds ? usedIds.size : 0;

  const renderNode = (node, depth = 0) => {
    const open = effectiveExpanded.has(node.path);
    const fileCount = countFiles(node);
    if (fileCount === 0 && depth > 0) return null;
    const isMenuTarget = menu && menu.kind === 'folder' && menu.node.path === node.path;
    const isRenamingThis = renaming && renaming.kind === 'folder' && renaming.key === node.path;
    return (
      <React.Fragment key={node.path}>
        <div
          className={`tree-row tree-folder${isMenuTarget ? ' menu-target' : ''}`}
          style={{ paddingLeft: 6 + depth * 8 }}
          onClick={() => !isRenamingThis && toggle(node.path)}
          onDoubleClick={(e) => { e.stopPropagation(); startRenameFolder(node); }}
          onContextMenu={(e) => {
            e.preventDefault();
            e.stopPropagation();
            setMenu({ kind: 'folder', x: e.clientX, y: e.clientY, node });
          }}
          title={node.path}>
          <span className="tree-twist">{open ? '▾' : '▸'}</span>
          <span className="tree-folder-icon">{open ? '📂' : '📁'}</span>
          {isRenamingThis ? (
            <input
              autoFocus
              className="pool-rename-input"
              value={renameValue}
              onChange={(e) => setRenameValue(e.target.value)}
              onClick={(e) => e.stopPropagation()}
              onMouseDown={(e) => e.stopPropagation()}
              onBlur={commitRename}
              onKeyDown={(e) => {
                if (e.key === 'Enter') commitRename();
                else if (e.key === 'Escape') cancelRename();
              }}
            />
          ) : (
            <span className="tree-folder-name">{node.name}</span>
          )}
          <span className="tree-folder-count">{fileCount}</span>
        </div>
        {open && (
          <>
            {node.childList.map(c => renderNode(c, depth + 1))}
            {node.files.map(v => {
              const used = usedIds && usedIds.has(v.relPath);
              const isFileMenuTarget = menu && menu.kind === 'file' && menu.video.id === v.id;
              const isFileRenaming = renaming && renaming.kind === 'file' && renaming.key === v.id;
              return (
                <div
                  key={v.id}
                  className={`tree-row tree-file pool-item${used ? ' used' : ''}${isFileMenuTarget ? ' menu-target' : ''}`}
                  style={{ paddingLeft: 6 + (depth + 1) * 8 }}
                  draggable={!isFileRenaming}
                  onDragStart={(e) => onDragStart(e, v, 'pool')}
                  onDoubleClick={() => startRenameFile(v)}
                  onContextMenu={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    setMenu({ kind: 'file', x: e.clientX, y: e.clientY, video: v });
                  }}>
                  <span className="tree-twist tree-twist-leaf">·</span>
                  {isFileRenaming ? (
                    <input
                      autoFocus
                      className="pool-rename-input"
                      value={renameValue}
                      onChange={(e) => setRenameValue(e.target.value)}
                      onClick={(e) => e.stopPropagation()}
                      onMouseDown={(e) => e.stopPropagation()}
                      onBlur={commitRename}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') commitRename();
                        else if (e.key === 'Escape') cancelRename();
                      }}
                    />
                  ) : (
                    <div className="pool-name">{v.name}</div>
                  )}
                  {used && !isFileRenaming && <span className="pool-used-dot" title="In a playlist" />}
                  <div className="pool-dur">{v.fileType === 'nes' ? 'ROM' : fmtBytes(v.sizeBytes)}</div>
                </div>
              );
            })}
          </>
        )}
      </React.Fragment>
    );
  };

  return (
    <>
    <aside className="pool">
      <div className="pool-hd">
        <button
          className="pool-tb-btn pool-hd-upload"
          onClick={() => fileInputRef.current && fileInputRef.current.click()}
          title="Upload files to the USB drive">
          <span className="pool-tb-icon">⤴</span>
          <span>UPLOAD</span>
        </button>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept={(uploadExtensions && uploadExtensions.length)
            ? uploadExtensions.map(e => '.' + e).join(',')
            : ''}
          style={{ display: 'none' }}
          onChange={(e) => {
            const files = Array.from(e.target.files || []);
            if (onUpload && files.length) onUpload(files);
            e.target.value = '';
          }}
        />
        <span className="pool-count">{filtered.length} FILES</span>
      </div>
      <div className="pool-toolbar">
        <div className="pool-tb-group">
          {(() => {
            const anyExpanded = expanded.size > 1;
            return (
              <button
                className="pool-tb-btn"
                onClick={anyExpanded ? collapseAll : expandAll}
                title={anyExpanded ? 'Collapse all folders' : 'Expand all folders'}>
                <span className="pool-tb-icon">{anyExpanded ? '⊟' : '⊞'}</span>
                <span>{anyExpanded ? 'COLLAPSE' : 'EXPAND'}</span>
              </button>
            );
          })()}
        </div>

        <div className="pool-tb-group">
          <button
            className={`pool-tb-btn pool-tb-toggle${hideUsed ? ' on' : ''}`}
            onClick={() => setHideUsed(v => !v)}
            title={hideUsed ? `${usedCount} added items hidden — click to show` : 'Hide files already added to a channel'}>
            <span className="pool-tb-icon">{hideUsed ? '◉' : '○'}</span>
            <span>{hideUsed ? 'HIDE USED' : 'SHOW ALL'}</span>
            {usedCount > 0 && <span className="pool-tb-badge">{usedCount}</span>}
          </button>
        </div>

        <div className="pool-tb-group pool-tb-right">
          <label className="pool-tb-sort" title="Sort files within each folder">
            <span>SORT</span>
            <select value={sortBy} onChange={e => setSortBy(e.target.value)}>
              <option value="name">NAME</option>
              <option value="size">SIZE</option>
            </select>
          </label>
        </div>
      </div>

      <div className="pool-list">
        {ghostUploads.map(u => (
          <div
            key={u.id}
            className={`tree-row tree-file pool-item${u.status === 'uploading' ? ' uploading' : ''}`}
            style={{ paddingLeft: 8 }}>
            <span className="tree-twist tree-twist-leaf">·</span>
            <div className="pool-name">{u.name}</div>
            <div className="pool-dur">
              {u.status === 'uploading'
                ? `${Math.round((u.progressPct || 0) * 100)}%`
                : fmtBytes(u.sizeBytes)}
            </div>
          </div>
        ))}
        {tree.childList.map(c => renderNode(c, 0))}
        {/* Files at the root mount level */}
        {tree.files && tree.files.map(v => {
          const used = usedIds && usedIds.has(v.relPath);
          return (
            <div
              key={v.id}
              className={`tree-row tree-file pool-item${used ? ' used' : ''}`}
              style={{ paddingLeft: 8 }}
              draggable
              onDragStart={(e) => onDragStart(e, v, 'pool')}>
              <span className="tree-twist tree-twist-leaf">·</span>
              <div className="pool-name">{v.name}</div>
              {used && <span className="pool-used-dot" title="In a playlist" />}
              <div className="pool-dur">{v.fileType === 'nes' ? 'ROM' : fmtBytes(v.sizeBytes)}</div>
            </div>
          );
        })}
      </div>
      <div className="pool-ft">
        <EngravedLabel size="xs">{filtered.length} of {pool.length} files</EngravedLabel>
      </div>
    </aside>
    {menu && menu.kind === 'file' && (
      <PoolContextMenu
        x={menu.x}
        y={menu.y}
        video={menu.video}
        used={usedIds && usedIds.has(menu.video.relPath)}
        onRename={() => startRenameFile(menu.video)}
        onDelete={() => handleDeleteFile(menu.video)}
        onClose={() => setMenu(null)}
      />
    )}
    {menu && menu.kind === 'folder' && (
      <FolderContextMenu
        x={menu.x}
        y={menu.y}
        node={menu.node}
        fileCount={countFiles(menu.node)}
        usedCount={countUsedInFolder(menu.node, usedIds)}
        onRename={() => startRenameFolder(menu.node)}
        onDelete={() => handleDeleteFolder(menu.node)}
        onExpandAll={() => { expandDescendants(menu.node); setMenu(null); }}
        onCollapseAll={() => { collapseDescendants(menu.node); setMenu(null); }}
        onClose={() => setMenu(null)}
      />
    )}
    </>
  );
}

// Build a folder tree from a flat list of pool items. Each item's `path`
// is the directory containing it; we walk the path segments to bucket
// files under the appropriate folder node.
function buildTreeFromList(items, sortBy) {
  const root = { type: 'folder', name: 'USB-DRIVE', path: '/', children: {}, files: [] };
  for (const v of items) {
    const segs = (v.path || '/').split('/').filter(Boolean);
    let node = root;
    let cur = '';
    for (const seg of segs) {
      cur += '/' + seg;
      if (!node.children[seg]) {
        node.children[seg] = { type: 'folder', name: seg, path: cur, children: {}, files: [] };
      }
      node = node.children[seg];
    }
    node.files.push(v);
  }
  const finalize = (n) => {
    n.childList = Object.values(n.children);
    n.childList.forEach(finalize);

    const filesBytes = n.files.reduce((s, f) => s + (f.sizeBytes || 0), 0);
    const childBytes = n.childList.reduce((s, c) => s + (c.totalBytes || 0), 0);
    n.totalBytes = filesBytes + childBytes;

    if (sortBy === 'size') {
      n.childList.sort((a, b) => (b.totalBytes || 0) - (a.totalBytes || 0));
      n.files.sort((a, b) => (b.sizeBytes || 0) - (a.sizeBytes || 0));
    } else {
      const cmp = (a, b) => a.name.localeCompare(b.name, undefined, { numeric: true });
      n.childList.sort(cmp);
      n.files.sort(cmp);
    }
    return n;
  };
  return finalize(root);
}

function PoolContextMenu({ x, y, video, used, onRename, onDelete, onClose }) {
  const ref = React.useRef(null);
  const [pos, setPos] = React.useState({ left: x, top: y });
  const [confirmDel, setConfirmDel] = React.useState(false);

  React.useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    let left = x, top = y;
    if (left + rect.width + 4 > window.innerWidth) left = window.innerWidth - rect.width - 4;
    if (top + rect.height + 4 > window.innerHeight) top = window.innerHeight - rect.height - 4;
    if (left < 4) left = 4;
    if (top < 4) top = 4;
    setPos({ left, top });
  }, [x, y]);

  return (
    <div
      ref={ref}
      className="pool-ctx"
      style={{ left: pos.left, top: pos.top }}
      onMouseDown={(e) => e.stopPropagation()}
      onContextMenu={(e) => e.preventDefault()}>
      <div className="pool-ctx-hd">
        <span className="pool-ctx-icon">▸</span>
        <span className="pool-ctx-title" title={video.name}>{video.name}</span>
      </div>
      <div className="pool-ctx-meta">
        {video.fileType === 'nes' ? 'NES ROM' : 'VIDEO'} · {fmtBytes(video.sizeBytes)}
      </div>
      <div className="pool-ctx-sep" />
      <button className="pool-ctx-item" onClick={onRename}>
        <span className="pool-ctx-key">R</span>
        <span>RENAME</span>
        <span className="pool-ctx-hint">F2</span>
      </button>
      {!confirmDel ? (
        <button
          className="pool-ctx-item danger"
          onClick={(e) => { e.stopPropagation(); setConfirmDel(true); }}>
          <span className="pool-ctx-key">D</span>
          <span>DELETE...</span>
          {used && <span className="pool-ctx-hint warn">IN USE</span>}
        </button>
      ) : (
        <button
          className="pool-ctx-item danger confirm"
          onClick={onDelete}>
          <span className="pool-ctx-key">!</span>
          <span>{used ? 'DELETE & UNSCHEDULE' : 'CONFIRM DELETE'}</span>
        </button>
      )}
      <div className="pool-ctx-sep" />
      <button className="pool-ctx-item subtle" onClick={onClose}>
        <span className="pool-ctx-key">×</span>
        <span>CANCEL</span>
        <span className="pool-ctx-hint">ESC</span>
      </button>
    </div>
  );
}

function countFiles(node) {
  let n = node.files.length;
  for (const c of node.childList) n += countFiles(c);
  return n;
}

function countUsedInFolder(node, usedIds) {
  if (!usedIds) return 0;
  // usedIds contains USB-root-relative paths (matching pool item .relPath).
  let n = 0;
  for (const v of node.files) if (usedIds.has(v.relPath)) n++;
  for (const c of node.childList) n += countUsedInFolder(c, usedIds);
  return n;
}

function FolderContextMenu({ x, y, node, fileCount, usedCount, onRename, onDelete, onExpandAll, onCollapseAll, onClose }) {
  const ref = React.useRef(null);
  const [pos, setPos] = React.useState({ left: x, top: y });
  const [confirmDel, setConfirmDel] = React.useState(false);

  React.useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    let left = x, top = y;
    if (left + rect.width + 4 > window.innerWidth) left = window.innerWidth - rect.width - 4;
    if (top + rect.height + 4 > window.innerHeight) top = window.innerHeight - rect.height - 4;
    if (left < 4) left = 4;
    if (top < 4) top = 4;
    setPos({ left, top });
  }, [x, y]);

  const hasSubfolders = node.childList && node.childList.length > 0;

  return (
    <div
      ref={ref}
      className="pool-ctx"
      style={{ left: pos.left, top: pos.top }}
      onMouseDown={(e) => e.stopPropagation()}
      onContextMenu={(e) => e.preventDefault()}>
      <div className="pool-ctx-hd">
        <span className="pool-ctx-icon">📁</span>
        <span className="pool-ctx-title" title={node.path}>{node.name}</span>
      </div>
      <div className="pool-ctx-meta">
        {node.path} · {fileCount} FILE{fileCount === 1 ? '' : 'S'}
      </div>
      <div className="pool-ctx-sep" />
      <button className="pool-ctx-item" onClick={onRename}>
        <span className="pool-ctx-key">R</span>
        <span>RENAME</span>
        <span className="pool-ctx-hint">F2</span>
      </button>
      {hasSubfolders && (
        <>
          <button className="pool-ctx-item" onClick={onExpandAll}>
            <span className="pool-ctx-key">⊞</span>
            <span>EXPAND ALL</span>
          </button>
          <button className="pool-ctx-item" onClick={onCollapseAll}>
            <span className="pool-ctx-key">⊟</span>
            <span>COLLAPSE ALL</span>
          </button>
        </>
      )}
      <div className="pool-ctx-sep" />
      {!confirmDel ? (
        <button
          className="pool-ctx-item danger"
          onClick={(e) => { e.stopPropagation(); setConfirmDel(true); }}>
          <span className="pool-ctx-key">D</span>
          <span>DELETE FOLDER...</span>
          {usedCount > 0 && <span className="pool-ctx-hint warn">{usedCount} USED</span>}
        </button>
      ) : (
        <button
          className="pool-ctx-item danger confirm"
          onClick={onDelete}>
          <span className="pool-ctx-key">!</span>
          <span>
            {usedCount > 0
              ? `DELETE ${fileCount} FILES & UNSCHEDULE ${usedCount}`
              : `DELETE ${fileCount} FILE${fileCount === 1 ? '' : 'S'}`}
          </span>
        </button>
      )}
      <div className="pool-ctx-sep" />
      <button className="pool-ctx-item subtle" onClick={onClose}>
        <span className="pool-ctx-key">×</span>
        <span>CANCEL</span>
        <span className="pool-ctx-hint">ESC</span>
      </button>
    </div>
  );
}

// ─────────── Settings Modal — looper INI editor ───────────
function SettingsModal({ open, onClose, config, storage, onSave, onReboot, restarting,
                         timelineScale, onTimelineScaleChange }) {
  const [draft, setDraft] = React.useState(() => deepClone(config));
  React.useEffect(() => { setDraft(deepClone(config)); }, [config, open]);

  if (!open) return null;

  const get = (sec, key, fallback = '') => (draft[sec] && draft[sec][key] != null ? draft[sec][key] : fallback);
  const set = (sec, key, value) => setDraft(d => {
    const next = { ...d, [sec]: { ...(d[sec] || {}), [key]: value } };
    return next;
  });

  const isBool = (v) => v === 'true' || v === true;
  const toBool = (v) => (v ? 'true' : 'false');

  const usedPct = storage && storage.total ? (storage.used / storage.total) * 100 : 0;

  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <div className="modal-hd">
          <div className="modal-title">
            <span className="modal-dot" />
            SYSTEM CONFIGURATION
          </div>
          <button className="modal-x" onClick={onClose}>✕</button>
        </div>

        <div className="modal-body">

          <section className="cfg-sec">
            <div className="cfg-sec-hd">
              <EngravedLabel size="sm">PLAYBACK</EngravedLabel>
            </div>
            <div className="cfg-grid">
              <label className="cfg-row">
                <span>RANDOM ORDER</span>
                <div className="seg">
                  <button className={!isBool(get('process_manager','is_random'))?'on':''} onClick={()=>set('process_manager','is_random',toBool(false))}>SEQ</button>
                  <button className={isBool(get('process_manager','is_random'))?'on':''} onClick={()=>set('process_manager','is_random',toBool(true))}>RANDOM</button>
                </div>
              </label>
              <label className="cfg-row">
                <span>WAIT BETWEEN VIDEOS (S)</span>
                <input className="cfg-in" type="number" min="0" max="60"
                  value={get('process_manager','wait_time','0')}
                  onChange={e=>set('process_manager','wait_time',e.target.value)} />
              </label>
            </div>
          </section>

          <section className="cfg-sec">
            <div className="cfg-sec-hd">
              <EngravedLabel size="sm">VIDEO (MPV)</EngravedLabel>
            </div>
            <div className="cfg-grid">
              <label className="cfg-row">
                <span>SOUND OUT</span>
                <div className="seg">
                  {['hdmi','local','alsa'].map(m => (
                    <button key={m} className={get('mpv','sound')===m?'on':''} onClick={()=>set('mpv','sound',m)}>{m.toUpperCase()}</button>
                  ))}
                </div>
              </label>
              <label className="cfg-row">
                <span>STRETCH 4:3</span>
                <div className="seg">
                  <button className={!isBool(get('mpv','video_stretch'))?'on':''} onClick={()=>set('mpv','video_stretch',toBool(false))}>OFF</button>
                  <button className={isBool(get('mpv','video_stretch'))?'on':''} onClick={()=>set('mpv','video_stretch',toBool(true))}>ON</button>
                </div>
              </label>
              <label className="cfg-row">
                <span>HW DECODE</span>
                <div className="seg">
                  {['auto','v4l2m2m','drm','no'].map(m => (
                    <button key={m} className={get('mpv','hwdec')===m?'on':''} onClick={()=>set('mpv','hwdec',m)}>{m.toUpperCase()}</button>
                  ))}
                </div>
              </label>
            </div>
          </section>

          <section className="cfg-sec">
            <div className="cfg-sec-hd">
              <EngravedLabel size="sm">NES (RETROARCH)</EngravedLabel>
            </div>
            <div className="cfg-grid">
              <label className="cfg-row">
                <span>VERBOSE LOGS</span>
                <div className="seg">
                  <button className={!isBool(get('retroarch','verbose'))?'on':''} onClick={()=>set('retroarch','verbose',toBool(false))}>OFF</button>
                  <button className={isBool(get('retroarch','verbose'))?'on':''} onClick={()=>set('retroarch','verbose',toBool(true))}>ON</button>
                </div>
              </label>
              <label className="cfg-row">
                <span>AUDIO</span>
                <div className="seg">
                  <button className={!isBool(get('retroarch','audio_enable'))?'on':''} onClick={()=>set('retroarch','audio_enable',toBool(false))}>OFF</button>
                  <button className={isBool(get('retroarch','audio_enable'))?'on':''} onClick={()=>set('retroarch','audio_enable',toBool(true))}>ON</button>
                </div>
              </label>
            </div>
          </section>

          <section className="cfg-sec">
            <div className="cfg-sec-hd">
              <EngravedLabel size="sm">LOGGING</EngravedLabel>
            </div>
            <div className="cfg-grid">
              <label className="cfg-row">
                <span>RELAY DEBUG</span>
                <div className="seg">
                  <button className={!isBool(get('logging','relay_debug'))?'on':''} onClick={()=>set('logging','relay_debug',toBool(false))}>OFF</button>
                  <button className={isBool(get('logging','relay_debug'))?'on':''} onClick={()=>set('logging','relay_debug',toBool(true))}>ON</button>
                </div>
              </label>
            </div>
          </section>

          {/* Browser-side preference: applies immediately, not part of the draft/APPLY flow */}
          <section className="cfg-sec">
            <div className="cfg-sec-hd">
              <EngravedLabel size="sm">USER INTERFACE</EngravedLabel>
            </div>
            <div className="cfg-grid">
              <label className="cfg-row">
                <span>TIMELINE SCALE</span>
                <div className="seg">
                  {(window.TIMELINE_SCALE_OPTIONS || [5,15,30,60]).map(m => (
                    <button
                      key={m}
                      className={timelineScale === m ? 'on' : ''}
                      onClick={() => onTimelineScaleChange && onTimelineScaleChange(m)}>
                      {m === 60 ? '1H' : `${m}M`}
                    </button>
                  ))}
                </div>
              </label>
            </div>
          </section>

          {storage && (
            <section className="cfg-sec">
              <div className="cfg-sec-hd">
                <EngravedLabel size="sm">STORAGE</EngravedLabel>
              </div>
              <div className="storage-bar">
                <div className="storage-fill" style={{ width: `${usedPct}%` }} />
                <div className="storage-ticks">
                  {[...Array(20)].map((_,i)=> <span key={i} />)}
                </div>
              </div>
              <div className="storage-stats">
                <div><EngravedLabel size="xs">USED</EngravedLabel><div className="storage-num">{fmtBytes(storage.used)}</div></div>
                <div><EngravedLabel size="xs">FREE</EngravedLabel><div className="storage-num">{fmtBytes(storage.free)}</div></div>
                <div><EngravedLabel size="xs">TOTAL</EngravedLabel><div className="storage-num">{fmtBytes(storage.total)}</div></div>
                <div><EngravedLabel size="xs">FILES</EngravedLabel><div className="storage-num">{storage.files}</div></div>
              </div>
            </section>
          )}

          <section className="cfg-sec">
            <div className="cfg-sec-hd">
              <EngravedLabel size="sm">SYSTEM</EngravedLabel>
            </div>
            <div className="cfg-grid">
              <label className="cfg-row">
                <span>REBOOT PI</span>
                <HwButton label="⟳ REBOOT" color="red" onClick={() => {
                  if (window.confirm('Reboot the Pi? The looper and the web UI will go down for ~30 seconds.')) {
                    onReboot();
                  }
                }} disabled={restarting} />
              </label>
            </div>
          </section>

        </div>

        <div className="modal-ft">
          <HwButton label="CANCEL" color="beige" onClick={onClose} disabled={restarting} />
          <HwButton label={restarting ? 'RESTARTING...' : 'APPLY'} color="orange"
            disabled={restarting}
            onClick={() => onSave(draft)} />
        </div>
      </div>
    </div>
  );
}

function deepClone(o) { return JSON.parse(JSON.stringify(o || {})); }

// ─────────── Log Drawer — collapsible bottom panel for ws log stream ───────────
function LogDrawer({ logs, open }) {
  const scrollRef = React.useRef(null);
  const [paused, setPaused] = React.useState(false);
  const [hovering, setHovering] = React.useState(false);

  React.useEffect(() => {
    if (!open || paused || hovering) return;
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [logs, open, paused, hovering]);

  if (!open) return null;
  const recent = logs.slice(-200);

  return (
    <div className="log-drawer open">
      <div className="log-drawer-body"
           ref={scrollRef}
           onMouseEnter={() => setHovering(true)}
           onMouseLeave={() => setHovering(false)}>
        {recent.length === 0 ? (
          <div className="log-empty">— no log lines yet —</div>
        ) : (
          recent.map((e, i) => (
            <div key={i} className={`log-line level-${(e.level||'info').toLowerCase()}`}>
              <span className="log-ts">{fmtLogTs(e.ts)}</span>
              <span className="log-level">{e.level}</span>
              <span className="log-name">{e.name}</span>
              <span className="log-msg">{e.msg}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function fmtLogTs(ts) {
  const d = new Date((ts || 0) * 1000);
  const hh = String(d.getHours()).padStart(2,'0');
  const mm = String(d.getMinutes()).padStart(2,'0');
  const ss = String(d.getSeconds()).padStart(2,'0');
  const ms = String(d.getMilliseconds()).padStart(3,'0');
  return `${hh}:${mm}:${ss}.${ms}`;
}

Object.assign(window, {
  fmtDur, fmtClock, fmtBcastElapsed, fmtBytes,
  HwButton, EngravedLabel, LED,
  VideoPoolSidebar, SettingsModal, LogDrawer,
});

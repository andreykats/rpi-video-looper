// Format seconds as M:SS or H:MM:SS
function fmtDur(sec) {
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (h) return `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
  return `${m}:${String(s).padStart(2,'0')}`;
}

// Format minutes from start as HH:MM (broadcast clock starting at e.g. 18:00)
function fmtClock(minFromStart, baseHour = 18) {
  const totalMin = baseHour * 60 + minFromStart;
  const h = Math.floor(totalMin / 60) % 24;
  const m = Math.floor(totalMin) % 60;
  return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}`;
}

// Chunky labeled hardware button — beige cap with engraved label
function HwButton({ label, color = 'beige', lit = false, onClick, big = false, indicator }) {
  const palette = {
    beige:  { face: '#c8ccd0', edge: '#7a7e82', text: '#1c1e22' },
    orange: { face: '#d97740', edge: '#a85020', text: '#1a0f08' },
    red:    { face: '#b83828', edge: '#7a1f12', text: '#fff' },
    green:  { face: '#3d7a3d', edge: '#1f4a1f', text: '#fff' },
    black:  { face: '#25272b', edge: '#0e1014', text: '#c8ccd0' },
  };
  const c = palette[color];
  return (
    <button
      onClick={onClick}
      className="hw-btn"
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
function EngravedLabel({ children, size = 10 }) {
  return (
    <span className="engraved" style={{ fontSize: size }}>{children}</span>
  );
}

// Dymo label tape — black tape with embossed white text
function DymoTape({ children, color = 'black' }) {
  return (
    <span className="dymo" data-color={color}>{children}</span>
  );
}

// Editable Dymo tape — click to rename
function EditableDymo({ value, onChange }) {
  const [editing, setEditing] = React.useState(false);
  const [draft, setDraft] = React.useState(value);
  const inputRef = React.useRef(null);
  React.useEffect(() => { setDraft(value); }, [value]);
  React.useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [editing]);
  const commit = () => {
    const v = draft.trim().toUpperCase();
    if (v && v !== value) onChange(v);
    else setDraft(value);
    setEditing(false);
  };
  if (editing) {
    return (
      <input
        ref={inputRef}
        className="dymo dymo-input"
        value={draft}
        maxLength={12}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') commit();
          else if (e.key === 'Escape') { setDraft(value); setEditing(false); }
        }}
      />
    );
  }
  return (
    <span
      className="dymo dymo-editable"
      title="Click to rename channel"
      onClick={() => setEditing(true)}>
      {value}
    </span>
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
function VideoPoolSidebar({ pool, onDragStart, search, setSearch, usedIds, onRename, onDelete, onRenameFolder, onDeleteFolder }) {
  const q = search.trim().toLowerCase();
  const [hideUsed, setHideUsed] = React.useState(false);
  const [sortBy, setSortBy] = React.useState('name'); // name | duration | kind

  // Right-click menu state (files or folders)
  const [menu, setMenu] = React.useState(null);
  // Inline rename state
  const [renaming, setRenaming] = React.useState(null); // { kind, key } where key is video.id or folder.path
  const [renameValue, setRenameValue] = React.useState('');

  // Close menu on any click/escape/scroll
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
    if (renameValue.trim()) {
      if (renaming.kind === 'file' && onRename) onRename(renaming.key, renameValue.trim());
      else if (renaming.kind === 'folder' && onRenameFolder) onRenameFolder(renaming.key, renameValue.trim());
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

  const filtered = pool.filter(v => {
    if (hideUsed && usedIds && usedIds.has(v.id)) return false;
    if (!q) return true;
    return v.name.toLowerCase().includes(q) ||
           v.kind.toLowerCase().includes(q) ||
           (v.path || '').toLowerCase().includes(q);
  });

  const kindColor = {
    cartoon: '#e8a838', news: '#5a9fd4', promo: '#c97cb8',
    ad: '#d97740', sitcom: '#7ab87a', show: '#a884d4',
    movie: '#d44a4a', doc: '#5ab8a8', game: '#c060ff', tech: '#888',
  };

  const tree = React.useMemo(() => {
    const t = buildPoolTree(filtered);
    // Apply sort to each folder's files
    const sortFiles = (n) => {
      n.files.sort((a, b) => {
        if (sortBy === 'duration') return b.duration - a.duration;
        if (sortBy === 'kind') return a.kind.localeCompare(b.kind) || a.name.localeCompare(b.name);
        return a.name.localeCompare(b.name);
      });
      n.childList.forEach(sortFiles);
    };
    sortFiles(t);
    return t;
  }, [filtered, sortBy]);

  const allFolderPaths = React.useMemo(() => {
    const out = new Set();
    const walk = (n) => { out.add(n.path); n.childList.forEach(walk); };
    walk(tree);
    return out;
  }, [tree]);

  const [expanded, setExpanded] = React.useState(() => new Set(['/']));
  // When searching, force-expand everything so matches surface
  const effectiveExpanded = q ? allFolderPaths : expanded;
  const toggle = (p) => {
    setExpanded(prev => {
      const n = new Set(prev);
      if (n.has(p)) n.delete(p); else n.add(p);
      return n;
    });
  };
  const expandAll = () => setExpanded(new Set(allFolderPaths));
  const collapseAll = () => setExpanded(new Set(['/']));

  // Expand/collapse all descendants of a specific folder
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

  const totalDur = pool.reduce((a, v) => a + (v.duration || 0), 0);
  const totalGB = (pool.length * 0.7).toFixed(1);
  const usedCount = usedIds ? usedIds.size : 0;

  const renderNode = (node, depth = 0) => {
    const open = effectiveExpanded.has(node.path);
    const fileCount = countFiles(node);
    if (fileCount === 0) return null; // hide empty folders (when filters strip everything)
    const isMenuTarget = menu && menu.kind === 'folder' && menu.node.path === node.path;
    const isRenamingThis = renaming && renaming.kind === 'folder' && renaming.key === node.path;
    return (
      <React.Fragment key={node.path}>
        <div
          className={`tree-row tree-folder${isMenuTarget ? ' menu-target' : ''}`}
          style={{ paddingLeft: 8 + depth * 14 }}
          onClick={() => !q && !isRenamingThis && toggle(node.path)}
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
              const used = usedIds && usedIds.has(v.id);
              const isFileMenuTarget = menu && menu.kind === 'file' && menu.video.id === v.id;
              const isFileRenaming = renaming && renaming.kind === 'file' && renaming.key === v.id;
              return (
                <div
                  key={v.id}
                  className={`tree-row tree-file pool-item${used ? ' used' : ''}${isFileMenuTarget ? ' menu-target' : ''}`}
                  style={{ paddingLeft: 8 + (depth + 1) * 14 }}
                  draggable={!isFileRenaming}
                  onDragStart={(e) => onDragStart(e, v, 'pool')}
                  onDoubleClick={() => startRenameFile(v)}
                  onContextMenu={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    setMenu({ kind: 'file', x: e.clientX, y: e.clientY, video: v });
                  }}>
                  <span className="tree-twist tree-twist-leaf">·</span>
                  <span className="pool-kind" style={{ background: kindColor[v.kind] }} />
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
                  {used && !isFileRenaming && <span className="pool-used-dot" title="In playlist" />}
                  <div className="pool-dur">{v.fileType === 'nes' ? 'ROM' : fmtDur(v.duration)}</div>
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
        <EngravedLabel size={11}>USB SOURCE</EngravedLabel>
        <span className="pool-count">{filtered.length} FILES</span>
      </div>
      <div className="pool-mount">
        <span className="pool-mount-led" />
        <span className="pool-mount-label">/dev/sdb1 · {totalGB} GB</span>
      </div>

      {/* View / Sort toolbar */}
      <div className="pool-toolbar">
        <div className="pool-tb-group">
          {(() => {
            const anyExpanded = expanded.size > 1; // more than just root
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
              <option value="duration">DURATION</option>
              <option value="kind">TYPE</option>
            </select>
          </label>
        </div>
      </div>

      <div className="pool-search">
        <input
          type="text"
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="SEARCH..."
          className="pool-input"
        />
      </div>
      <div className="pool-list">
        {filtered.length === 0 ? (
          <div className="pool-empty">— NO MATCHES —</div>
        ) : (
          tree.childList.map(c => renderNode(c, 0))
        )}
      </div>
      <div className="pool-ft">
        <EngravedLabel size={9}>{totalDur > 0 ? `TOTAL ${fmtDur(totalDur)}` : ''}</EngravedLabel>
      </div>
    </aside>
    {menu && menu.kind === 'file' && (
      <PoolContextMenu
        x={menu.x}
        y={menu.y}
        video={menu.video}
        used={usedIds && usedIds.has(menu.video.id)}
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

// ─────────── Pool Context Menu ───────────
function PoolContextMenu({ x, y, video, used, onRename, onDelete, onClose }) {
  const ref = React.useRef(null);
  const [pos, setPos] = React.useState({ left: x, top: y });
  const [confirmDel, setConfirmDel] = React.useState(false);

  // After mount, clamp to viewport
  React.useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    let left = x;
    let top = y;
    if (left + rect.width + 4 > vw) left = vw - rect.width - 4;
    if (top + rect.height + 4 > vh) top = vh - rect.height - 4;
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
        {video.kind.toUpperCase()} · {fmtDur(video.duration)}
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

// helper: count all files under a folder node, recursively
function countFiles(node) {
  let n = node.files.length;
  for (const c of node.childList) n += countFiles(c);
  return n;
}

// helper: count files that are currently in a playlist
function countUsedInFolder(node, usedIds) {
  if (!usedIds) return 0;
  let n = 0;
  for (const v of node.files) if (usedIds.has(v.id)) n++;
  for (const c of node.childList) n += countUsedInFolder(c, usedIds);
  return n;
}

// ─────────── Folder Context Menu ───────────
function FolderContextMenu({ x, y, node, fileCount, usedCount, onRename, onDelete, onExpandAll, onCollapseAll, onClose }) {
  const ref = React.useRef(null);
  const [pos, setPos] = React.useState({ left: x, top: y });
  const [confirmDel, setConfirmDel] = React.useState(false);

  React.useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    let left = x;
    let top = y;
    if (left + rect.width + 4 > vw) left = vw - rect.width - 4;
    if (top + rect.height + 4 > vh) top = vh - rect.height - 4;
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

// ─────────── Settings Modal ───────────
function SettingsModal({ open, onClose, settings, setSettings, storage }) {
  if (!open) return null;
  const update = (k, v) => setSettings(s => ({ ...s, [k]: v }));
  const usedPct = (storage.used / storage.total) * 100;

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
              <EngravedLabel size={11}>NETWORK / IP</EngravedLabel>
              <LED on={settings.networkUp} color="green" label="LINK" />
            </div>
            <div className="cfg-grid">
              <label className="cfg-row">
                <span>HOSTNAME</span>
                <input className="cfg-in" value={settings.hostname} onChange={e=>update('hostname',e.target.value)} />
              </label>
              <label className="cfg-row">
                <span>IP MODE</span>
                <div className="seg">
                  {['DHCP','STATIC'].map(m => (
                    <button key={m} className={settings.ipMode===m?'on':''} onClick={()=>update('ipMode',m)}>{m}</button>
                  ))}
                </div>
              </label>
              <label className="cfg-row">
                <span>IP ADDRESS</span>
                <input className="cfg-in" value={settings.ip} onChange={e=>update('ip',e.target.value)} disabled={settings.ipMode==='DHCP'} />
              </label>
              <label className="cfg-row">
                <span>SUBNET</span>
                <input className="cfg-in" value={settings.subnet} onChange={e=>update('subnet',e.target.value)} disabled={settings.ipMode==='DHCP'} />
              </label>
              <label className="cfg-row">
                <span>GATEWAY</span>
                <input className="cfg-in" value={settings.gateway} onChange={e=>update('gateway',e.target.value)} disabled={settings.ipMode==='DHCP'} />
              </label>
              <label className="cfg-row">
                <span>MAC ADDR</span>
                <input className="cfg-in" value={settings.mac} disabled />
              </label>
            </div>
          </section>

          <section className="cfg-sec">
            <div className="cfg-sec-hd">
              <EngravedLabel size={11}>PLAYBACK BEHAVIOR</EngravedLabel>
            </div>
            <div className="cfg-grid">
              <label className="cfg-row">
                <span>END OF PLAYLIST</span>
                <div className="seg">
                  {['LOOP','STOP','SHUFFLE'].map(m => (
                    <button key={m} className={settings.loopMode===m?'on':''} onClick={()=>update('loopMode',m)}>{m}</button>
                  ))}
                </div>
              </label>
              <label className="cfg-row">
                <span>CROSS-CHANNEL SYNC</span>
                <div className="seg">
                  {['REAL-TIME','INDEPENDENT'].map(m => (
                    <button key={m} className={settings.syncMode===m?'on':''} onClick={()=>update('syncMode',m)}>{m}</button>
                  ))}
                </div>
              </label>
              <label className="cfg-row">
                <span>STARTUP CHANNEL</span>
                <input className="cfg-in" type="number" min="2" max="13" value={settings.startupChan} onChange={e=>update('startupChan',Number(e.target.value))} />
              </label>
            </div>
          </section>

          <section className="cfg-sec">
            <div className="cfg-sec-hd">
              <EngravedLabel size={11}>STORAGE</EngravedLabel>
            </div>
            <div className="storage-bar">
              <div className="storage-fill" style={{ width: `${usedPct}%` }} />
              <div className="storage-ticks">
                {[...Array(20)].map((_,i)=> <span key={i} />)}
              </div>
            </div>
            <div className="storage-stats">
              <div><EngravedLabel size={9}>USED</EngravedLabel><div className="storage-num">{storage.used.toFixed(1)} GB</div></div>
              <div><EngravedLabel size={9}>FREE</EngravedLabel><div className="storage-num">{(storage.total-storage.used).toFixed(1)} GB</div></div>
              <div><EngravedLabel size={9}>TOTAL</EngravedLabel><div className="storage-num">{storage.total.toFixed(0)} GB</div></div>
              <div><EngravedLabel size={9}>FILES</EngravedLabel><div className="storage-num">{storage.files}</div></div>
            </div>
          </section>

        </div>

        <div className="modal-ft">
          <HwButton label="CANCEL" color="beige" onClick={onClose} />
          <HwButton label="APPLY" color="orange" onClick={onClose} />
        </div>
      </div>
    </div>
  );
}

Object.assign(window, {
  fmtDur, fmtClock, HwButton, EngravedLabel, DymoTape, EditableDymo, LED,
  VideoPoolSidebar, SettingsModal,
});

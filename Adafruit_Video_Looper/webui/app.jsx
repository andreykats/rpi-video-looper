// Main app — CRT TV Station Control Board
const { useState, useEffect, useRef, useMemo } = React;

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "phosphor": "amber",
  "increment": 15,
  "era": "tan"
}/*EDITMODE-END*/;

// Build initial playlist instances with unique inst ids
function buildInitialPlaylists() {
  let counter = 0;
  const out = {};
  for (const ch of CHANNELS) {
    out[ch.num] = (SEED_PLAYLISTS[ch.num] || []).map(vid => ({
      instId: `inst_${++counter}`,
      videoId: vid,
    }));
  }
  return { playlists: out, counter };
}

function App() {
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);

  const [channels, setChannels] = useState(CHANNELS);
  const updateChannelCall = (num, call) => {
    setChannels(cs => cs.map(c => c.num === num ? { ...c, call } : c));
  };

  const [pool, setPool] = useState(VIDEO_POOL);

  const renameVideo = (id, newName) => {
    const trimmed = (newName || '').trim();
    if (!trimmed) return;
    setPool(p => p.map(v => v.id === id ? { ...v, name: trimmed } : v));
  };

  const deleteVideo = (id) => {
    setPool(p => p.filter(v => v.id !== id));
    setState(s => {
      const next = {};
      for (const k of Object.keys(s.playlists)) {
        next[k] = s.playlists[k].filter(inst => inst.videoId !== id);
      }
      return { ...s, playlists: next };
    });
  };

  // Rename a folder: replace path prefix on all descendant files
  // oldPath like "/COMMERCIALS/30S", newName like "60S" → newPath "/COMMERCIALS/60S"
  const renameFolder = (oldPath, newName) => {
    const trimmed = (newName || '').trim().replace(/\//g, '');
    if (!trimmed) return;
    const segs = oldPath.split('/').filter(Boolean);
    if (segs.length === 0) return;
    segs[segs.length - 1] = trimmed;
    const newPath = '/' + segs.join('/');
    if (newPath === oldPath) return;
    setPool(p => p.map(v => {
      if (v.path === oldPath) return { ...v, path: newPath };
      if (v.path && v.path.startsWith(oldPath + '/')) {
        return { ...v, path: newPath + v.path.slice(oldPath.length) };
      }
      return v;
    }));
  };

  // Delete a folder: drop every file at or under this path
  const deleteFolder = (folderPath) => {
    const matches = (p) => p === folderPath || (p && p.startsWith(folderPath + '/'));
    const removedIds = new Set(pool.filter(v => matches(v.path)).map(v => v.id));
    setPool(p => p.filter(v => !matches(v.path)));
    setState(s => {
      const next = {};
      for (const k of Object.keys(s.playlists)) {
        next[k] = s.playlists[k].filter(inst => !removedIds.has(inst.videoId));
      }
      return { ...s, playlists: next };
    });
  };

  const [{ playlists, counter }, setState] = useState(() => buildInitialPlaylists());
  const counterRef = useRef(counter);

  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState(null);
  const [settingsOpen, setSettingsOpen] = useState(false);

  // Resizable sidebar
  const [poolW, setPoolW] = useState(() => {
    const v = parseFloat(localStorage.getItem('crt_pool_w') || '');
    return isNaN(v) ? 280 : Math.max(200, Math.min(560, v));
  });
  const [resizing, setResizing] = useState(false);
  useEffect(() => { localStorage.setItem('crt_pool_w', String(poolW)); }, [poolW]);
  const onResizeDown = (e) => {
    e.preventDefault();
    setResizing(true);
    const startX = e.clientX;
    const startW = poolW;
    const move = (ev) => {
      const next = Math.max(200, Math.min(560, startW + (ev.clientX - startX)));
      setPoolW(next);
    };
    const up = () => {
      setResizing(false);
      window.removeEventListener('mousemove', move);
      window.removeEventListener('mouseup', up);
    };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
  };

  // Live broadcast clock — minute counter advances in real time
  const [currentMinute, setCurrentMinute] = useState(() => {
    const stored = parseFloat(localStorage.getItem('crt_pos') || '');
    return isNaN(stored) ? 75 : stored;
  });
  const [playing, setPlaying] = useState(true);

  useEffect(() => {
    if (!playing) return;
    const id = setInterval(() => {
      setCurrentMinute(m => {
        const next = (m + 0.05) % TIMELINE_MIN; // 1 broadcast min per 1.2s — fast forward feel
        localStorage.setItem('crt_pos', String(next));
        return next;
      });
    }, 60);
    return () => clearInterval(id);
  }, [playing]);

  const [settings, setSettings] = useState({
    hostname: 'CRT-PLAYOUT-01',
    ipMode: 'STATIC',
    ip: '192.168.1.42',
    subnet: '255.255.255.0',
    gateway: '192.168.1.1',
    mac: '00:1B:44:11:3A:B7',
    networkUp: true,
    loopMode: 'LOOP',
    syncMode: 'REAL-TIME',
    startupChan: 2,
  });

  const storage = {
    used: 142.3,
    total: 500,
    files: pool.length,
  };

  // ─── Drag-drop handlers ───
  const handleDrop = (chanNum, idx, videoId) => {
    counterRef.current += 1;
    const newInst = { instId: `inst_${counterRef.current}`, videoId };
    setState(s => {
      const next = { ...s.playlists };
      const arr = [...(next[chanNum] || [])];
      arr.splice(idx, 0, newInst);
      next[chanNum] = arr;
      return { playlists: next, counter: counterRef.current };
    });
  };

  const handleMove = (fromChan, instId, toChan, toIdx) => {
    setState(s => {
      const next = { ...s.playlists };
      const fromArr = [...(next[fromChan] || [])];
      const fromIdx = fromArr.findIndex(x => x.instId === instId);
      if (fromIdx < 0) return s;
      const [item] = fromArr.splice(fromIdx, 1);
      next[fromChan] = fromArr;

      let insertIdx = toIdx;
      if (fromChan === toChan && fromIdx < toIdx) insertIdx = toIdx - 1;
      const toArr = fromChan === toChan ? fromArr : [...(next[toChan] || [])];
      toArr.splice(insertIdx, 0, item);
      next[toChan] = toArr;
      return { ...s, playlists: next };
    });
  };

  const handleRemove = (chanNum, instId) => {
    setState(s => {
      const next = { ...s.playlists };
      next[chanNum] = (next[chanNum] || []).filter(x => x.instId !== instId);
      return { ...s, playlists: next };
    });
  };

  const handlePoolDragStart = (e, video) => {
    e.dataTransfer.effectAllowed = 'copy';
    e.dataTransfer.setData('text/plain', video.id);
    // bridge to timeline's dragRef
    if (window.__poolDragBridge) window.__poolDragBridge({ source: 'pool', video });
  };

  // Phosphor color
  const phosphor = {
    amber: '#ffb347',
    green: '#7fff7f',
    white: '#e8e8e8',
  }[t.phosphor];

  // Stats: total scheduled time per channel
  const channelStats = useMemo(() => {
    const out = {};
    for (const ch of channels) {
      const items = playlists[ch.num] || [];
      const total = items.reduce((acc, inst) => {
        const v = pool.find(x => x.id === inst.videoId);
        return acc + (v && v.duration ? v.duration : 0);
      }, 0);
      out[ch.num] = { count: items.length, total };
    }
    return out;
  }, [playlists, channels]);

  // Set of video ids currently scheduled anywhere
  const usedIds = useMemo(() => {
    const s = new Set();
    for (const ch of channels) {
      for (const inst of (playlists[ch.num] || [])) s.add(inst.videoId);
    }
    return s;
  }, [playlists, channels]);

  // Suppress the native browser context menu everywhere — our custom menus
  // (file/folder/clip) call preventDefault themselves and then stopPropagation,
  // so this only kicks in for "anywhere else" right-clicks.
  // Allow it inside text inputs/textareas so paste still works.
  useEffect(() => {
    const handler = (e) => {
      const t = e.target;
      const tag = t && t.tagName;
      const editable = tag === 'INPUT' || tag === 'TEXTAREA' || (t && t.isContentEditable);
      if (!editable) e.preventDefault();
    };
    window.addEventListener('contextmenu', handler);
    return () => window.removeEventListener('contextmenu', handler);
  }, []);

  return (
    <div className="chassis" style={{ '--phosphor': phosphor }}>
      <style>{`:root{--phosphor:${phosphor}}`}</style>

      {/* TOP CHASSIS BAR */}
      <header className="chassis-top">
        <div className="chassis-top-c">
          <div className="led-bank">
            <LED on={true} color="green" label="PWR" />
            <LED on={settings.networkUp} color="green" label="NET" />
            <LED on={playing} color="red" label="AIR" />
            <LED on={false} color="amber" label="REC" />
          </div>
          <div className="phosphor-display">
            <div className="phosphor-screen">
              <div className="phos-row"><span>BCAST</span><span>{fmtClock(currentMinute)}:{String(Math.floor((currentMinute % 1) * 60)).padStart(2,'0')}</span></div>
              <div className="phos-row"><span>RUNTIME</span><span>{fmtDur(Math.floor(currentMinute * 60))}</span></div>
            </div>
            <div className="phosphor-glow" />
          </div>
        </div>

        <div className="chassis-top-r">

          <div className="transport">
            <HwButton label="◼ STOP" color="beige" />
            <HwButton label="▶ SYNC" color="green" />
            <HwButton label="● REC" color="red" />
            <HwButton label="⟳ REBOOT" color="beige" />
            <HwButton label="⚙ SETTINGS" color="orange" onClick={() => setSettingsOpen(true)} />
          </div>
        </div>
      </header>

      {/* MAIN GRID */}
      <main className="chassis-main" style={{ '--pool-w': poolW + 'px' }}>

        <VideoPoolSidebar
          pool={pool}
          onDragStart={handlePoolDragStart}
          search={search}
          setSearch={setSearch}
          usedIds={usedIds}
          onRename={renameVideo}
          onDelete={deleteVideo}
          onRenameFolder={renameFolder}
          onDeleteFolder={deleteFolder}
        />

        <div
          className={`pool-resize${resizing ? ' dragging' : ''}`}
          onMouseDown={onResizeDown}
          title="Drag to resize"
        />

        <section className="timeline-area">

          {/* Channel header strip (left) + timeline */}
          <div className="tl-header-bar">
            <div className="tl-corner">
              <EngravedLabel size={11}>CHANNEL</EngravedLabel>
            </div>
            <div className="tl-corner-r">
              <EngravedLabel size={11}>BROADCAST TIMECODE — 6 HR BLOCK</EngravedLabel>
            </div>
          </div>

          <div className="tl-body">
            {/* Channel labels column — one per row, fixed */}
            <div className="tl-channels">
              {channels.map(ch => {
                const stat = channelStats[ch.num];
                return (
                  <div key={ch.num} className="tl-chan">
                    <div className="tl-chan-num">
                      <span className="tl-chan-digit">{String(ch.num).padStart(2,'0')}</span>
                    </div>
                    <div className="tl-chan-info">
                      <EditableDymo
                        value={ch.call}
                        onChange={(v) => updateChannelCall(ch.num, v)}
                      />
                      <div className="tl-chan-stats">
                        {stat.count} · {fmtDur(stat.total)}
                      </div>
                    </div>
                    <div className="tl-chan-leds">
                      <LED on={stat.count > 0} color="green" />
                      <LED on={false} color="red" />
                    </div>
                  </div>
                );
              })}
            </div>

            <ChannelTimeline
              channels={channels}
              playlists={playlists}
              pool={pool}
              increment={t.increment}
              currentMinute={currentMinute}
              onDrop={handleDrop}
              onMove={handleMove}
              onRemove={handleRemove}
              selected={selected}
              setSelected={setSelected}
            />
          </div>

          {/* Bottom strip — instructions / vent */}
          <div className="chassis-vent">
            <div className="vent-grille">
              {[...Array(40)].map((_,i) => <span key={i} />)}
            </div>
            <div className="vent-info">
              <EngravedLabel size={9}>DBL-CLICK CLIP TO REMOVE · DRAG TO MOVE BETWEEN CHANNELS · DRAG FROM LIBRARY TO ADD</EngravedLabel>
              <span className="vent-serial">S/N · VPC-{settings.mac.replace(/:/g,'').slice(-6)}</span>
            </div>
          </div>
        </section>
      </main>

      <SettingsModal
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        settings={settings}
        setSettings={setSettings}
        storage={storage}
      />

      <TweaksPanel title="Tweaks">
        <TweakSection label="Display" />
        <TweakRadio
          label="Phosphor"
          value={t.phosphor}
          options={['amber', 'green', 'white']}
          onChange={(v) => setTweak('phosphor', v)}
        />
        <TweakRadio
          label="Era"
          value={t.era}
          options={[
            { value: 'tan', label: 'TAN' },
            { value: 'olive', label: 'OLIVE' },
            { value: 'graphite', label: 'GRAPH' },
          ]}
          onChange={(v) => setTweak('era', v)}
        />
        <TweakSection label="Timeline" />
        <TweakRadio
          label="Time Increment"
          value={t.increment}
          options={[
            { value: 5, label: '5m' },
            { value: 15, label: '15m' },
            { value: 30, label: '30m' },
            { value: 60, label: '1h' },
          ]}
          onChange={(v) => setTweak('increment', v)}
        />
      </TweaksPanel>

      <div data-era={t.era} className="era-overlay" />
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);

// Main app — CRT TV Station Control Board.
const { useState, useEffect, useRef, useMemo } = React;

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "phosphor": "amber",
  "increment": 15,
  "era": "tan"
}/*EDITMODE-END*/;

const PHOSPHOR = { amber: '#ffb347', green: '#7fff7f', white: '#e8e8e8' };

let UID_COUNTER = 1;
const newUid = (prefix) => `${prefix || 'u'}_${UID_COUNTER++}`;

function buildInitialPlaylists(channelData) {
  const out = {};
  for (const [chStr, info] of Object.entries(channelData || {})) {
    const ch = parseInt(chStr, 10);
    out[ch] = (info.entries || []).map((e) => ({
      filename: e.filename,
      repeat: e.repeat || 1,
      durationSec: e.durationSec || 0,
      fileType: e.fileType,
      _uid: newUid(`s${ch}`),
    }));
  }
  return out;
}

function buildInitialPositions(channelData) {
  const out = {};
  for (const [chStr, info] of Object.entries(channelData || {})) {
    const ch = parseInt(chStr, 10);
    if (info.current && info.current.loopPositionSec != null) {
      out[ch] = {
        loopPositionSec: info.current.loopPositionSec,
        loopDurationSec: info.totalDurationSec,
        currentFilename: info.current.filename,
        positionSec: info.current.positionSec,
        durationSec: info.current.durationSec,
      };
    }
  }
  return out;
}

function buildChannelDirs(channelData) {
  const out = {};
  for (const [chStr, info] of Object.entries(channelData || {})) {
    if (info && info.channelDir) {
      out[parseInt(chStr, 10)] = info.channelDir;
    }
  }
  return out;
}

function buildChannelNames(channelData) {
  const out = {};
  for (const [chStr, info] of Object.entries(channelData || {})) {
    if (info && info.name) {
      out[parseInt(chStr, 10)] = info.name;
    }
  }
  return out;
}

function App() {
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const initial = window.__INITIAL_STATE;
  const initialState = initial.state;

  const [pool, setPool] = useState(() => flattenPool(initial.pool));
  const [poolTreeRaw, setPoolTreeRaw] = useState(initial.pool);
  const [playlists, setPlaylists] = useState(
    () => buildInitialPlaylists(initialState.broadcast.channels)
  );
  const [positionsByChannel, setPositionsByChannel] = useState(
    () => buildInitialPositions(initialState.broadcast.channels)
  );
  const [channelDirs, setChannelDirs] = useState(
    () => buildChannelDirs(initialState.broadcast.channels)
  );
  const [channelNames, setChannelNames] = useState(
    () => buildChannelNames(initialState.broadcast.channels)
  );
  const [editingChannel, setEditingChannel] = useState(null);
  const [editingChannelValue, setEditingChannelValue] = useState('');
  const [currentChannel, setCurrentChannel] = useState(initialState.currentChannel);
  const [activePlayer, setActivePlayer] = useState(initialState.activePlayer);
  const [playbackStopped, setPlaybackStopped] = useState(initialState.playbackStopped);
  const [config, setConfig] = useState(initialState.config || {});
  const [storage, setStorage] = useState(null);
  const [logs, setLogs] = useState([]);
  const [logOpen, setLogOpen] = useState(false);
  const [wsState, setWsState] = useState('connecting');
  const [restarting, setRestarting] = useState(false);
  const [search, setSearch] = useState('');
  const [settingsOpen, setSettingsOpen] = useState(false);
  const mountRoot = (initialState.mountRoots && initialState.mountRoots[0]) || '/mnt/usbdrive';

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

  // Storage on mount
  useEffect(() => {
    let cancelled = false;
    API.getStorage().then(s => { if (!cancelled) setStorage(s); }).catch(() => {});
    return () => { cancelled = true; };
  }, []);

  // ─── WebSocket subscription ───
  useEffect(() => {
    const ws = connectWebSocket();
    const offState = ws.onState((s) => {
      setWsState(s);
      if (s === 'open' && restarting) {
        setRestarting(false);
        Promise.all([API.getState(), API.getPool(), API.getStorage()])
          .then(([st, p, store]) => {
            setPlaylists(buildInitialPlaylists(st.broadcast.channels));
            setPositionsByChannel(buildInitialPositions(st.broadcast.channels));
            setChannelDirs(buildChannelDirs(st.broadcast.channels));
            setChannelNames(buildChannelNames(st.broadcast.channels));
            setCurrentChannel(st.currentChannel);
            setActivePlayer(st.activePlayer);
            setPlaybackStopped(st.playbackStopped);
            setConfig(st.config || {});
            setPool(flattenPool(p));
            setPoolTreeRaw(p);
            setStorage(store);
          })
          .catch(e => console.warn('post-restart refresh failed', e));
      }
    });

    const subs = [
      ws.subscribe('state.snapshot', (env) => {
        const st = env.payload;
        setPlaylists(buildInitialPlaylists(st.broadcast.channels));
        setPositionsByChannel(buildInitialPositions(st.broadcast.channels));
        setChannelDirs(buildChannelDirs(st.broadcast.channels));
        setChannelNames(buildChannelNames(st.broadcast.channels));
        setCurrentChannel(st.currentChannel);
        setActivePlayer(st.activePlayer);
        setPlaybackStopped(st.playbackStopped);
        setConfig(st.config || {});
      }),
      ws.subscribe('state.channel', (env) => setCurrentChannel(env.channel)),
      ws.subscribe('state.playback', (env) => {
        setPlaybackStopped(env.stopped);
        setActivePlayer(env.activePlayer);
      }),
      ws.subscribe('state.positions', (env) => {
        const next = {};
        for (const [chStr, p] of Object.entries(env.channels || {})) {
          next[parseInt(chStr, 10)] = p;
        }
        setPositionsByChannel(next);
      }),
      ws.subscribe('playlist.changed', (env) => {
        API.getPlaylist(env.channel).then(({ entries, name }) => {
          setPlaylists(p => ({
            ...p,
            [env.channel]: (entries || []).map(e => ({
              ...e, _uid: newUid(`r${env.channel}`),
            })),
          }));
          setChannelNames(prev => {
            const next = { ...prev };
            if (name) next[env.channel] = name;
            else delete next[env.channel];
            return next;
          });
        }).catch(() => {});
      }),
      ws.subscribe('pool.changed', () => {
        Promise.all([API.getPool(), API.getStorage()]).then(([p, s]) => {
          setPool(flattenPool(p));
          setPoolTreeRaw(p);
          setStorage(s);
        }).catch(() => {});
      }),
      ws.subscribe('log.line', (env) => {
        setLogs(prev => {
          const next = [...prev, env];
          return next.length > 500 ? next.slice(-500) : next;
        });
      }),
      ws.subscribe('log.dropped', (env) => {
        setLogs(prev => [...prev, {
          ts: env.ts, level: 'WARNING', name: 'looper.web',
          msg: `(${env.count} log line(s) dropped)`
        }]);
      }),
    ];
    return () => {
      offState();
      subs.forEach(off => off && off());
      ws.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [restarting]);

  // ─── Playlist mutations ───
  const persistPlaylist = (chan, items) => {
    const entries = items.map(e => ({ filename: e.filename, repeat: e.repeat || 1 }));
    return API.savePlaylist(chan, entries).catch(err => {
      console.warn('savePlaylist failed', chan, err);
      API.getPlaylist(chan).then(({ entries: server }) => {
        setPlaylists(p => ({
          ...p,
          [chan]: (server || []).map(e => ({ ...e, _uid: newUid(`r${chan}`) })),
        }));
      }).catch(() => {});
    });
  };

  const startRenameChannel = (chan) => {
    setEditingChannel(chan);
    setEditingChannelValue(channelNames[chan] || '');
  };
  const cancelRenameChannel = () => {
    setEditingChannel(null);
    setEditingChannelValue('');
  };
  const commitRenameChannel = () => {
    if (editingChannel == null) return;
    const chan = editingChannel;
    const trimmed = editingChannelValue.trim();
    const prev = channelNames[chan] || '';
    setEditingChannel(null);
    setEditingChannelValue('');
    if (trimmed === prev) return;
    setChannelNames(curr => {
      const next = { ...curr };
      if (trimmed) next[chan] = trimmed;
      else delete next[chan];
      return next;
    });
    const items = playlists[chan] || [];
    const entries = items.map(e => ({ filename: e.filename, repeat: e.repeat || 1 }));
    // Empty string clears the name on the server.
    API.savePlaylist(chan, entries, trimmed).catch(err => {
      console.warn('rename channel failed', chan, err);
      // Roll back to whatever the server has.
      API.getPlaylist(chan).then(({ name }) => {
        setChannelNames(curr => {
          const next = { ...curr };
          if (name) next[chan] = name;
          else delete next[chan];
          return next;
        });
      }).catch(() => {});
    });
  };

  const handleSavePlaylist = (fromChan, toChan, uid, toIdx) => {
    setPlaylists(p => {
      const fromItems = [...(p[fromChan] || [])];
      const fromIdx = fromItems.findIndex(e => e._uid === uid);
      if (fromIdx < 0) return p;
      const [item] = fromItems.splice(fromIdx, 1);
      const next = { ...p, [fromChan]: fromItems };
      if (toChan == null || toIdx == null) {
        persistPlaylist(fromChan, fromItems);
        return next;
      }
      const toItems = fromChan === toChan ? fromItems : [...(p[toChan] || [])];
      let insertIdx = toIdx;
      if (fromChan === toChan && fromIdx < toIdx) insertIdx = toIdx - 1;
      toItems.splice(insertIdx, 0, item);
      next[toChan] = toItems;
      if (fromChan !== toChan) next[fromChan] = fromItems;
      persistPlaylist(fromChan, fromItems);
      if (fromChan !== toChan) persistPlaylist(toChan, toItems);
      return next;
    });
  };

  const handleAddPoolItem = async (chan, poolItem, idx) => {
    const channelDir = channelDirs[chan];
    if (!channelDir) {
      window.alert(`Channel ${chan} has no folder on the USB drive.`);
      return;
    }
    const fileInChannel = poolItem.absParent === channelDir;
    const newEntry = {
      filename: poolItem.name,
      repeat: 1,
      durationSec: poolItem.durationSec || 0,
      fileType: poolItem.fileType,
      _uid: newUid('drop'),
    };

    let nextItems = null;
    setPlaylists(p => {
      const items = [...(p[chan] || [])];
      const insertAt = Math.min(idx, items.length);
      items.splice(insertAt, 0, newEntry);
      nextItems = items;
      return { ...p, [chan]: items };
    });

    try {
      if (!fileInChannel) {
        await API.moveFile(poolItem.id, channelDir);
      }
      await persistPlaylist(chan, nextItems);
    } catch (err) {
      console.warn('add to channel failed', err);
      const detail = err.detail && err.detail.error ? err.detail.error : err.message;
      window.alert('Add failed: ' + detail);
      API.getPlaylist(chan).then(({ entries }) => {
        setPlaylists(p => ({
          ...p,
          [chan]: (entries || []).map(e => ({ ...e, _uid: newUid(`r${chan}`) })),
        }));
      }).catch(() => {});
    }
  };

  // ─── Pool file ops ───
  const handleRenameFile = async (id, newName) => {
    try { await API.renameFile(id, newName); }
    catch (e) {
      const d = e.detail && e.detail.error ? e.detail.error : e.message;
      window.alert('Rename failed: ' + d);
    }
  };
  const handleDeleteFile = async (id) => {
    try { await API.deleteFile(id); }
    catch (e) {
      const d = e.detail && e.detail.error ? e.detail.error : e.message;
      window.alert('Delete failed: ' + d);
    }
  };
  const handleRenameFolder = async (path, newName) => {
    try { await API.renameFolder(path, newName); }
    catch (e) {
      const d = e.detail && e.detail.error ? e.detail.error : e.message;
      window.alert('Rename failed: ' + d);
    }
  };
  const handleDeleteFolder = async (path) => {
    if (!window.confirm('Delete this folder and everything inside it? This cannot be undone.')) return;
    try { await API.deleteFolder(path); }
    catch (e) {
      const d = e.detail && e.detail.error ? e.detail.error : e.message;
      window.alert('Delete failed: ' + d);
    }
  };

  const handlePoolDragStart = (e, video) => {
    e.dataTransfer.effectAllowed = 'copy';
    e.dataTransfer.setData('text/plain', video.id);
    if (window.__poolDragBridge) {
      window.__poolDragBridge({
        source: 'pool',
        poolItem: video,
        fileType: video.fileType,
      });
    }
  };

  const handleSaveConfig = async (newConfig) => {
    try {
      setRestarting(true);
      await API.saveConfig(newConfig);
    } catch (e) {
      setRestarting(false);
      const d = e.detail && e.detail.error ? e.detail.error : e.message;
      window.alert('Apply failed: ' + d);
    }
  };

  const handleReboot = async () => {
    try {
      setRestarting(true);
      await API.reboot();
    } catch (e) {
      setRestarting(false);
      window.alert('Reboot failed: ' + (e.message || e));
    }
  };

  const usedIds = useMemo(() => {
    const s = new Set();
    for (const ch of CHANNELS) {
      const dir = channelDirs[ch.num];
      if (!dir) continue;
      for (const entry of (playlists[ch.num] || [])) {
        s.add(`${dir}/${entry.filename}`);
      }
    }
    return s;
  }, [playlists, channelDirs]);

  const phosphor = PHOSPHOR[t.phosphor] || PHOSPHOR.amber;
  const curPos = positionsByChannel[currentChannel];
  const bcastSec = curPos ? curPos.loopPositionSec : 0;
  const runtimeSec = curPos ? curPos.positionSec : 0;

  useEffect(() => {
    const handler = (e) => {
      const tg = e.target;
      const tag = tg && tg.tagName;
      const editable = tag === 'INPUT' || tag === 'TEXTAREA' || (tg && tg.isContentEditable);
      if (!editable) e.preventDefault();
    };
    window.addEventListener('contextmenu', handler);
    return () => window.removeEventListener('contextmenu', handler);
  }, []);

  const renderChannelHeader = (chNum) => {
    const items = playlists[chNum] || [];
    const totalDur = items.reduce(
      (a, e) => a + ((e.durationSec || 0) * (e.repeat || 1)), 0
    );
    const isCurrent = chNum === currentChannel;
    const name = channelNames[chNum];
    const placeholder = `CHANNEL ${String(chNum).padStart(2,'0')}`;
    const isEditing = editingChannel === chNum;
    return (
      <div className={`tl-chan-cell${isCurrent ? ' is-current' : ''}`}>
        <div className="tl-chan-num">
          <span className="tl-chan-digit">{String(chNum).padStart(2,'0')}</span>
        </div>
        <div className="tl-chan-info">
          {isEditing ? (
            <input
              autoFocus
              className="tl-chan-name-input"
              value={editingChannelValue}
              maxLength={64}
              placeholder={placeholder}
              onChange={(e) => setEditingChannelValue(e.target.value)}
              onFocus={(e) => e.target.select()}
              onBlur={commitRenameChannel}
              onKeyDown={(e) => {
                if (e.key === 'Enter') commitRenameChannel();
                else if (e.key === 'Escape') cancelRenameChannel();
              }}
            />
          ) : (
            <div
              className={`tl-chan-name${name ? '' : ' is-placeholder'}`}
              title="Click to rename"
              onClick={() => startRenameChannel(chNum)}>
              {name || placeholder}
            </div>
          )}
          <div className="tl-chan-stats">
            {items.length} · {fmtDur(totalDur)}
          </div>
        </div>
        <div className="tl-chan-leds">
          <LED on={items.length > 0} color="green" />
          <LED on={isCurrent} color="red" />
        </div>
      </div>
    );
  };

  return (
    <div className="chassis" style={{ '--phosphor': phosphor }}>
      <style>{`:root{--phosphor:${phosphor}}`}</style>

      {restarting && (
        <div className="restart-overlay">
          <div className="restart-box">
            <div className="restart-spinner" />
            <div className="restart-msg">RESTARTING…</div>
            <div className="restart-sub">Reconnecting in a moment.</div>
          </div>
        </div>
      )}

      <header className="chassis-top">
        <div className="chassis-top-c">
          <div className="led-bank">
            <LED on={true} color="green" label="PWR" />
            <LED on={wsState === 'open'} color="green" label="NET" />
            <LED on={!!activePlayer && !playbackStopped} color="red" label="AIR" />
            <LED on={false} color="amber" label="REC" />
          </div>
          <div className="phosphor-display">
            <div className="phosphor-screen">
              <div className="phos-row">
                <span>BCAST</span>
                <span>{fmtDur(bcastSec)}</span>
              </div>
              <div className="phos-row">
                <span>CH {String(currentChannel).padStart(2,'0')}</span>
                <span>{fmtDur(runtimeSec)}</span>
              </div>
            </div>
            <div className="phosphor-glow" />
          </div>
        </div>

        <div className="chassis-top-r">
          <div className="transport">
            <HwButton label="◼ STOP" color="beige" disabled />
            <HwButton label="▶ SYNC" color="green" disabled />
            <HwButton label="● REC" color="red" disabled />
            <HwButton label="⟳ REBOOT" color="beige" onClick={() => {
              if (window.confirm('Reboot the Pi? The looper and the web UI will go down for ~30 seconds.')) {
                handleReboot();
              }
            }} disabled={restarting} />
            <HwButton label="⚙ SETTINGS" color="orange" onClick={() => setSettingsOpen(true)} />
          </div>
        </div>
      </header>

      <main className="chassis-main" style={{ '--pool-w': poolW + 'px' }}>
        <VideoPoolSidebar
          pool={pool}
          onDragStart={handlePoolDragStart}
          search={search}
          setSearch={setSearch}
          usedIds={usedIds}
          onRename={handleRenameFile}
          onDelete={handleDeleteFile}
          onRenameFolder={handleRenameFolder}
          onDeleteFolder={handleDeleteFolder}
        />

        <div
          className={`pool-resize${resizing ? ' dragging' : ''}`}
          onMouseDown={onResizeDown}
          title="Drag to resize"
        />

        <section className="timeline-area">
          <div className="tl-header-bar">
            <div className="tl-corner">
              <EngravedLabel size="sm">CHANNEL</EngravedLabel>
            </div>
            <div className="tl-corner-r">
              <EngravedLabel size="sm">BROADCAST TIMECODE — 6 HR FRAME</EngravedLabel>
            </div>
          </div>

          <div className="tl-body">
            <ChannelTimeline
              channels={CHANNELS}
              playlists={playlists}
              increment={t.increment}
              positionsByChannel={positionsByChannel}
              currentChannel={currentChannel}
              mountRoot={mountRoot}
              onSavePlaylist={handleSavePlaylist}
              onMovePoolFileToChannel={handleAddPoolItem}
              renderChannelHeader={renderChannelHeader}
            />
          </div>

          <div className="chassis-vent">
            <div className="vent-grille">
              {[...Array(40)].map((_,i) => <span key={i} />)}
            </div>
            <div className="vent-info">
              <EngravedLabel size="xs">DBL-CLICK CLIP TO REMOVE · DRAG TO MOVE BETWEEN CHANNELS · DRAG FROM LIBRARY TO ADD</EngravedLabel>
              <span className="vent-serial">CHANNEL CHANGES VIA ROTARY ENCODER ONLY</span>
            </div>
          </div>
        </section>
      </main>

      <SettingsModal
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        config={config}
        storage={storage}
        onSave={(c) => { setSettingsOpen(false); handleSaveConfig(c); }}
        onReboot={() => { setSettingsOpen(false); handleReboot(); }}
        restarting={restarting}
      />

      <LogDrawer
        logs={logs}
        open={logOpen}
        setOpen={setLogOpen}
        wsState={wsState}
      />

      {typeof TweaksPanel === 'function' && (() => {
        const params = new URLSearchParams(window.location.search);
        if (!params.has('tweaks')) return null;
        return (
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
        );
      })()}

      <div data-era={t.era} className="era-overlay" />
    </div>
  );
}

window.__INITIAL_STATE_PROMISE
  .then(initial => {
    window.__INITIAL_STATE = initial;
    ReactDOM.createRoot(document.getElementById('root')).render(<App />);
  })
  .catch(err => {
    console.error('startup fetch failed', err);
    document.getElementById('root').innerText = 'Startup failed: ' + (err && err.message ? err.message : err);
  });

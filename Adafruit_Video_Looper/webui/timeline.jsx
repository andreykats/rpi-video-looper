// ─────────── Timeline ───────────
// All channel rows share one horizontal time axis whose width represents
// `tMax = max(channel total_duration over all channels)`. Channels
// shorter than tMax show blank tail space; runtime-only hidden default
// filler is not rendered in the editor timeline.
// A single global playhead at `(elapsed % tMax) / tMax * row_width` runs
// across all rows so the user can read down to see what's playing on
// each channel at any instant.
//
// Playlist entries arrive from the server with the shape:
//   { filename, durationSec, fileType, _uid }
// where _uid is a client-side counter we attach for stable React keys.
// Each entry plays exactly once per loop iteration; users drag a clip
// in twice to repeat it.

const ROW_WIDTH_PX = 2880;        // visual width of a full tMax row
const HEADER_PX = 180;            // must match CSS .tl-chan-cell width
const TMAX_FALLBACK_S = 60;       // when no channel has content (rare)

function rulerStepFor(tMax) {
  // Pick an interval that yields somewhere around 6–24 ticks across the
  // visible range, in human-friendly units.
  if (tMax <= 60)    return 5;       // every 5 s
  if (tMax <= 300)   return 30;      // every 30 s
  if (tMax <= 1800)  return 60;      // every 1 min
  if (tMax <= 7200)  return 300;     // every 5 min
  if (tMax <= 21600) return 1800;    // every 30 min
  return 3600;                       // every 1 hour
}

function fmtTickLabel(sec) {
  sec = Math.max(0, Math.floor(sec));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (h) return `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
  if (m) return `${m}:${String(s).padStart(2,'0')}`;
  return `${s}s`;
}

function ChannelTimeline({
  channels, playlists, positionsByChannel, currentChannel,
  broadcastElapsedSec,
  mountRoot, onSavePlaylist, onMovePoolFileToChannel, renderChannelHeader,
}) {
  const dragRef = React.useRef(null);
  const [clipMenu, setClipMenu] = React.useState(null);

  React.useEffect(() => {
    if (!clipMenu) return;
    const close = () => setClipMenu(null);
    const onKey = (e) => { if (e.key === 'Escape') setClipMenu(null); };
    window.addEventListener('mousedown', close);
    window.addEventListener('scroll', close, true);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('mousedown', close);
      window.removeEventListener('scroll', close, true);
      window.removeEventListener('keydown', onKey);
    };
  }, [clipMenu]);

  // Pool drag bridge — VideoPoolSidebar fires an onDragStart that calls
  // window.__poolDragBridge with the file payload, which we stash here.
  React.useEffect(() => {
    window.__poolDragBridge = (payload) => { dragRef.current = payload; };
    return () => { delete window.__poolDragBridge; };
  }, []);

  // Per-channel total durations and tMax.
  const channelTotals = React.useMemo(() => {
    const out = {};
    for (const ch of channels) {
      const items = playlists[ch.num] || [];
      // ROM entries have durationSec=0; sum naturally excludes them so
      // ROM-only channels stay total=0 and don't drive tMax.
      out[ch.num] = items.reduce((a, e) => a + (e.durationSec || 0), 0);
    }
    return out;
  }, [channels, playlists]);

  const tMax = React.useMemo(() => {
    let m = 0;
    for (const ch of channels) {
      if (channelTotals[ch.num] > m) m = channelTotals[ch.num];
    }
    return m;
  }, [channels, channelTotals]);

  const effectiveTMax = tMax > 0 ? tMax : TMAX_FALLBACK_S;
  const pxPerSec = ROW_WIDTH_PX / effectiveTMax;
  const totalPx = ROW_WIDTH_PX;

  // Ruler ticks.
  const tickStep = rulerStepFor(effectiveTMax);
  const ticks = React.useMemo(() => {
    const out = [];
    for (let s = 0; s <= effectiveTMax; s += tickStep) out.push(s);
    return out;
  }, [effectiveTMax, tickStep]);

  // Single global playhead position. When the elapsed wrap detection
  // fires, bump a key so React remounts the element without animating
  // backwards across the row.
  const elapsedSafe = (typeof broadcastElapsedSec === 'number'
                       && isFinite(broadcastElapsedSec))
    ? Math.max(0, broadcastElapsedSec) : 0;
  const playheadFrac = tMax > 0 ? (elapsedSafe % tMax) / tMax : 0;

  const prevFracRef = React.useRef(playheadFrac);
  const wrapKeyRef = React.useRef(0);
  if (playheadFrac < prevFracRef.current - 0.0001) {
    // Wrapped from near-100% back to 0%; bump the key so the next render
    // mounts a fresh element that doesn't animate the jump.
    wrapKeyRef.current += 1;
  }
  prevFracRef.current = playheadFrac;

  const onDragOverRow = (e, chanNum, items) => {
    e.preventDefault();
    const d = dragRef.current;
    if (!d) return;
    const draggingNes = d.fileType === 'nes';
    const rowHasContent = items.length > 0;
    if (draggingNes && rowHasContent
        && !(d.source === 'timeline' && d.originChan === chanNum)) {
      e.dataTransfer.dropEffect = 'none';
      return;
    }
    const rowHasNes = items.some(i => i.fileType === 'nes');
    if (!draggingNes && rowHasNes) {
      e.dataTransfer.dropEffect = 'none';
      return;
    }
    e.dataTransfer.dropEffect = d.source === 'pool' ? 'copy' : 'move';
  };

  const computeDropIndex = (e, rowEl, items) => {
    const rect = rowEl.getBoundingClientRect();
    const x = e.clientX - rect.left;
    let acc = 0;
    for (let i = 0; i < items.length; i++) {
      const w = clipPxWidth(items[i], pxPerSec);
      if (x < acc + w / 2) return i;
      acc += w;
    }
    return items.length;
  };

  const onDropRow = async (e, chanNum) => {
    e.preventDefault();
    const d = dragRef.current;
    if (!d) return;
    const items = playlists[chanNum] || [];
    const draggingNes = d.fileType === 'nes';
    const rowHasNes = items.some(i => i.fileType === 'nes');
    // ROM cap of 1 per row — UI-side rule. Backend stays lenient.
    if (draggingNes && items.length > 0
        && !(d.source === 'timeline' && d.originChan === chanNum)) {
      dragRef.current = null;
      return;
    }
    if (!draggingNes && rowHasNes) {
      dragRef.current = null;
      return;
    }
    const idx = computeDropIndex(e, e.currentTarget, items);

    if (d.source === 'pool') {
      onMovePoolFileToChannel(chanNum, d.poolItem, idx);
    } else {
      onSavePlaylist(d.originChan, chanNum, d.originUid, idx);
    }
    dragRef.current = null;
  };

  return (
    <div className="tl-wrap">
      <div className="tl-scroll">
        <div className="tl-inner" style={{ width: HEADER_PX + totalPx + 80 }}>
          <div className="tl-ruler">
            <div className="tl-ruler-corner" />
            <div className="tl-ruler-ticks" style={{ width: totalPx }}>
              {ticks.map(s => {
                const isMajor = (s % (tickStep * 4) === 0);
                return (
                  <div
                    key={s}
                    className={isMajor ? 'tl-tick hour' : 'tl-tick'}
                    style={{ left: s * pxPerSec }}>
                    <span className="tl-tick-label">{fmtTickLabel(s)}</span>
                  </div>
                );
              })}
            </div>
          </div>

          <div className="tl-rows-wrap" style={{ position: 'relative' }}>
            {channels.map(ch => {
              const items = playlists[ch.num] || [];
              const isNesRow = items.some(i => i.fileType === 'nes');
              const drag = dragRef.current;
              let dragInvalid = false;
              if (drag && drag.fileType !== undefined) {
                const draggingNes = drag.fileType === 'nes';
                if (draggingNes && items.length > 0
                    && !(drag.source === 'timeline' && drag.originChan === ch.num)) {
                  dragInvalid = true;
                } else if (!draggingNes && isNesRow) {
                  dragInvalid = true;
                }
              }
              const isCurrent = ch.num === currentChannel;
              const palette = (window.CHANNEL_COLORS || {})[ch.num];

              return (
                <div key={ch.num} className={`tl-row-wrap${isCurrent ? ' is-current' : ''}`}>
                  {renderChannelHeader && renderChannelHeader(ch.num)}
                  <div
                    className={`tl-row${isNesRow ? ' nes-row' : ''}${dragInvalid ? ' drop-invalid' : ''}`}
                    style={{ width: totalPx }}
                    onDragOver={(e) => onDragOverRow(e, ch.num, items)}
                    onDrop={(e) => onDropRow(e, ch.num)}>
                    {items.map((entry, i) => {
                      const isNesClip = entry.fileType === 'nes';
                      const w = isNesClip ? totalPx : clipPxWidth(entry, pxPerSec);
                      return (
                        <ClipBlock
                          key={entry._uid || `${entry.filename}-${i}`}
                          entry={entry}
                          width={w}
                          isNes={isNesClip}
                          colorStripe={palette ? palette.stripe : null}
                          colorBg={palette ? palette.bg : null}
                          onDoubleClick={() => onSavePlaylist(ch.num, null, entry._uid, null)}
                          onContextMenu={(e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            setClipMenu({
                              x: e.clientX, y: e.clientY,
                              entry, chan: ch.num,
                            });
                          }}
                          onDragStart={(e) => {
                            dragRef.current = {
                              source: 'timeline',
                              originChan: ch.num,
                              originUid: entry._uid,
                              fileType: entry.fileType,
                            };
                            e.dataTransfer.effectAllowed = 'move';
                            e.dataTransfer.setData('text/plain', entry.filename);
                          }}
                        />
                      );
                    })}
                    {items.length === 0 && (
                      <div className="tl-empty">— DROP CLIPS HERE —</div>
                    )}
                  </div>
                </div>
              );
            })}

            {/* Single global playhead — spans all rows. Hidden when no
                channel has content (tMax=0). Wrap-safe via key bump. */}
            {tMax > 0 && (
              <div
                key={wrapKeyRef.current}
                className="tl-playhead-global"
                style={{
                  left: HEADER_PX + playheadFrac * totalPx,
                }}>
                <div className="tl-playhead-global-line" />
              </div>
            )}
          </div>
        </div>
      </div>

      {clipMenu && (
        <ClipContextMenu
          x={clipMenu.x}
          y={clipMenu.y}
          entry={clipMenu.entry}
          chan={clipMenu.chan}
          onRemove={() => {
            onSavePlaylist(clipMenu.chan, null, clipMenu.entry._uid, null);
            setClipMenu(null);
          }}
          onClose={() => setClipMenu(null)}
        />
      )}
    </div>
  );
}

function clipPxWidth(entry, pxPerSec) {
  // Each entry plays exactly once per loop iteration; repetition is
  // encoded by adding the same filename twice. Width is just duration.
  const dur = entry.durationSec || 0;
  return Math.max(8, dur * pxPerSec);
}

function ClipBlock({ entry, width, isNes, colorStripe, colorBg, onDoubleClick, onContextMenu, onDragStart }) {
  const tooSmall = width < 60;
  if (isNes) {
    return (
      <div
        className="clip clip-nes"
        draggable
        onDragStart={onDragStart}
        onDoubleClick={onDoubleClick}
        onContextMenu={onContextMenu}
        style={{ width }}
        title={`${entry.filename} — interactive ROM (right-click to eject)`}>
        <div className="clip-nes-body">
          <span className="clip-nes-badge">NES</span>
          <span className="clip-nes-name">{entry.filename}</span>
          <span className="clip-nes-tag">INTERACTIVE · FULL CHANNEL</span>
        </div>
      </div>
    );
  }
  const style = { width };
  if (colorStripe) style['--clip-stripe'] = colorStripe;
  if (colorBg) style['--clip-bg'] = colorBg;
  return (
    <div
      className="clip"
      draggable
      onDragStart={onDragStart}
      onDoubleClick={onDoubleClick}
      onContextMenu={onContextMenu}
      style={style}
      title={`${entry.filename} — ${fmtDur(entry.durationSec)}`}>
      <div className="clip-stripe" />
      <div className="clip-body">
        {!tooSmall && <div className="clip-name">{entry.filename}</div>}
        {!tooSmall && (
          <div className="clip-dur">{fmtDur(entry.durationSec)}</div>
        )}
        {tooSmall && <div className="clip-mini">{fmtDur(entry.durationSec)}</div>}
      </div>
    </div>
  );
}

function ClipContextMenu({ x, y, entry, chan, onRemove, onClose }) {
  const ref = React.useRef(null);
  const [pos, setPos] = React.useState({ left: x, top: y });

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
        <span className="pool-ctx-title" title={entry.filename}>{entry.filename}</span>
      </div>
      <div className="pool-ctx-meta">
        CH {String(chan).padStart(2,'0')} · {entry.fileType === 'nes' ? 'NES ROM' : fmtDur(entry.durationSec)}
      </div>
      <div className="pool-ctx-sep" />
      <button className="pool-ctx-item danger" onClick={onRemove}>
        <span className="pool-ctx-key">×</span>
        <span>EJECT</span>
        <span className="pool-ctx-hint">DEL</span>
      </button>
      <div className="pool-ctx-sep" />
      <button className="pool-ctx-item subtle" onClick={onClose}>
        <span className="pool-ctx-key">×</span>
        <span>CANCEL</span>
        <span className="pool-ctx-hint">ESC</span>
      </button>
    </div>
  );
}

window.ChannelTimeline = ChannelTimeline;

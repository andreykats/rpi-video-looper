// ─────────── Timeline ───────────
// Each channel row is a horizontal stack of clip blocks. Clips can be:
//  - dragged from the pool (creates a new instance, possibly moving the
//    underlying file into the channel folder server-side)
//  - dragged within or between rows (reorders)
//
// Playlist entries arrive from the server with the shape:
//   { filename, repeat, durationSec, fileType, _uid }
// where _uid is a client-side counter we attach for stable React keys
// (the same filename can appear multiple times in a single channel).

const PX_PER_MIN = 8;
const TIMELINE_MIN = 360;          // 6-hour visual frame
const HEADER_PX = 200;             // must match CSS .tl-chan-cell width

function ChannelTimeline({
  channels, playlists, increment, positionsByChannel, currentChannel,
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
      const w = clipPxWidth(items[i]);
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
      // Build the new entry. If the file isn't already in this channel's
      // folder, ask the parent to move it before saving the playlist.
      onMovePoolFileToChannel(chanNum, d.poolItem, idx);
    } else {
      // Move within timeline: rebuild entries on origin and target rows.
      onSavePlaylist(d.originChan, chanNum, d.originUid, idx);
    }
    dragRef.current = null;
  };

  const totalPx = TIMELINE_MIN * PX_PER_MIN;
  const ticks = [];
  for (let m = 0; m <= TIMELINE_MIN; m += increment) ticks.push(m);

  return (
    <div className="tl-wrap">
      <div className="tl-scroll">
        <div className="tl-inner" style={{ width: HEADER_PX + totalPx + 80 }}>
          <div className="tl-ruler">
            <div className="tl-ruler-corner" />
            <div className="tl-ruler-ticks" style={{ width: totalPx }}>
              {ticks.map(m => {
                const isHour = m % 60 === 0;
                return (
                  <div key={m} className={isHour ? 'tl-tick hour' : 'tl-tick'} style={{ left: m * PX_PER_MIN }}>
                    <span className="tl-tick-label">{fmtClock(m)}</span>
                  </div>
                );
              })}
            </div>
          </div>

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
            const pos = positionsByChannel ? positionsByChannel[ch.num] : null;
            const playheadLeft = pos && typeof pos.loopPositionSec === 'number'
              ? (pos.loopPositionSec / 60) * PX_PER_MIN
              : null;
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
                    const w = isNesClip ? totalPx : clipPxWidth(entry);
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
                  {playheadLeft !== null && (
                    <div className="tl-playhead-row" style={{ left: playheadLeft }}>
                      <div className="tl-playhead-line" />
                    </div>
                  )}
                </div>
              </div>
            );
          })}
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

function clipPxWidth(entry) {
  // Each repeat extends the visual width — the looper plays the clip N times.
  const dur = (entry.durationSec || 0) * (entry.repeat || 1);
  return Math.max(8, (dur / 60) * PX_PER_MIN);
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
      title={`${entry.filename} — ${fmtDur(entry.durationSec)}${entry.repeat > 1 ? ` ×${entry.repeat}` : ''}`}>
      <div className="clip-stripe" />
      <div className="clip-body">
        {!tooSmall && <div className="clip-name">{entry.filename}</div>}
        {!tooSmall && (
          <div className="clip-dur">
            {fmtDur(entry.durationSec)}
            {entry.repeat > 1 && <span className="clip-rpt"> ×{entry.repeat}</span>}
          </div>
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
        {entry.repeat > 1 && ` · ×${entry.repeat}`}
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
window.PX_PER_MIN = PX_PER_MIN;
window.TIMELINE_MIN = TIMELINE_MIN;

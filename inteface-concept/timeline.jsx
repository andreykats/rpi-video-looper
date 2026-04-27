// ─────────── Timeline ───────────
// Each channel row is a horizontal stack of clip blocks. Clips can be:
//  - dragged from the pool (creates a new instance)
//  - dragged within or between rows (moves)
// Drop targets: within a row, between any two clips OR at the end.

const PX_PER_MIN = 8;       // base scale
const TIMELINE_MIN = 360;   // 6 hours = 360 minutes

function ChannelTimeline({
  channels, playlists, pool, increment, currentMinute, onDrop, onMove, onRemove, selected, setSelected,
}) {
  const lookupVideo = (id) => pool.find(v => v.id === id);
  const dragRef = React.useRef(null);

  // Bridge: pool dragstart writes into our dragRef so onDragOver/onDrop can read it
  React.useEffect(() => {
    window.__poolDragBridge = (payload) => { dragRef.current = payload; };
    return () => { if (window.__poolDragBridge) delete window.__poolDragBridge; };
  }, []);

  // Right-click menu for clips
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

  // dragged: { source: 'pool'|'timeline', payload, originChan, originIdx }
  const onDragStartCard = (e, video, source, originChan, originIdx, instId) => {
    dragRef.current = { source, video, originChan, originIdx, instId };
    e.dataTransfer.effectAllowed = source === 'pool' ? 'copy' : 'move';
    e.dataTransfer.setData('text/plain', video.id);
  };

  const onDragOverRow = (e, chanNum, rowItems, isNesRow) => {
    e.preventDefault();
    const d = dragRef.current;
    if (!d) return;
    // Block drop visually for invalid combos
    const draggingNes = d.video && d.video.fileType === 'nes';
    if (draggingNes && rowItems && rowItems.length > 0 && !(d.source === 'timeline' && d.originChan === chanNum)) {
      e.dataTransfer.dropEffect = 'none';
      return;
    }
    if (!draggingNes && isNesRow) {
      e.dataTransfer.dropEffect = 'none';
      return;
    }
    e.dataTransfer.dropEffect = d.source === 'pool' ? 'copy' : 'move';
  };

  // Compute drop index from cursor X relative to the row's clips
  const computeDropIndex = (e, rowEl, items) => {
    const rect = rowEl.getBoundingClientRect();
    const x = e.clientX - rect.left;
    let acc = 0;
    for (let i = 0; i < items.length; i++) {
      const v = lookupVideo(items[i].videoId);
      const w = (v.duration / 60) * PX_PER_MIN;
      if (x < acc + w / 2) return i;
      acc += w;
    }
    return items.length;
  };

  const onDropRow = (e, chanNum) => {
    e.preventDefault();
    const d = dragRef.current;
    if (!d) return;
    const rowEl = e.currentTarget;
    const items = playlists[chanNum] || [];
    const draggingNes = d.video && d.video.fileType === 'nes';
    const rowHasNes = items.some(inst => {
      const v = lookupVideo(inst.videoId);
      return v && v.fileType === 'nes';
    });
    // Validate
    if (draggingNes && items.length > 0 && !(d.source === 'timeline' && d.originChan === chanNum)) {
      dragRef.current = null;
      return;
    }
    if (!draggingNes && rowHasNes) {
      dragRef.current = null;
      return;
    }
    const idx = computeDropIndex(e, rowEl, items);
    if (d.source === 'pool') {
      onDrop(chanNum, idx, d.video.id);
    } else {
      onMove(d.originChan, d.instId, chanNum, idx);
    }
    dragRef.current = null;
  };

  // Time ruler ticks
  const totalPx = TIMELINE_MIN * PX_PER_MIN;
  const ticks = [];
  for (let m = 0; m <= TIMELINE_MIN; m += increment) {
    ticks.push(m);
  }

  const playheadLeft = currentMinute * PX_PER_MIN;

  return (
    <div className="tl-wrap">
      {/* ruler + playhead container */}
      <div className="tl-scroll">
        <div className="tl-inner" style={{ width: totalPx + 80 }}>
          {/* Ruler */}
          <div className="tl-ruler">
            {ticks.map(m => {
              const isHour = m % 60 === 0;
              return (
                <div key={m} className={isHour ? 'tl-tick hour' : 'tl-tick'} style={{ left: m * PX_PER_MIN }}>
                  <span className="tl-tick-label">{fmtClock(m)}</span>
                </div>
              );
            })}
          </div>

          {/* Channel rows */}
          {channels.map(ch => {
            const items = playlists[ch.num] || [];
            // A row is "NES-locked" if it currently holds a NES rom (only one allowed)
            const nesInst = items.find(inst => {
              const v = lookupVideo(inst.videoId);
              return v && v.fileType === 'nes';
            });
            const isNesRow = !!nesInst;
            // Validate the current drag for this row
            const drag = dragRef.current;
            let dragInvalid = false;
            if (drag && drag.video) {
              const draggingNes = drag.video.fileType === 'nes';
              if (draggingNes && items.length > 0 && !(drag.source === 'timeline' && drag.originChan === ch.num)) {
                dragInvalid = true; // NES needs an empty row
              } else if (!draggingNes && isNesRow) {
                dragInvalid = true; // can't add normal clips to a NES-locked row
              }
            }
            return (
              <div key={ch.num} className="tl-row-wrap">
                <div
                  className={`tl-row${isNesRow ? ' nes-row' : ''}${dragInvalid ? ' drop-invalid' : ''}`}
                  style={{ width: totalPx }}
                  onDragOver={(e) => onDragOverRow(e, ch.num, items, isNesRow)}
                  onDrop={(e) => onDropRow(e, ch.num)}>
                  {items.map((inst, i) => {
                    const v = lookupVideo(inst.videoId);
                    if (!v) return null;
                    const isNesClip = v.fileType === 'nes';
                    const w = isNesClip ? totalPx : (v.duration / 60) * PX_PER_MIN;
                    const isSel = selected === inst.instId;
                    return (
                      <ClipBlock
                        key={inst.instId}
                        v={v}
                        width={w}
                        isNes={isNesClip}
                        selected={isSel}
                        onClick={() => setSelected(inst.instId)}
                        onRemove={() => onRemove(ch.num, inst.instId)}
                        onContextMenu={(e) => {
                          e.preventDefault();
                          e.stopPropagation();
                          setSelected(inst.instId);
                          setClipMenu({
                            x: e.clientX, y: e.clientY,
                            video: v, chan: ch.num, instId: inst.instId, idx: i,
                          });
                        }}
                        onDragStart={(e) => onDragStartCard(e, v, 'timeline', ch.num, i, inst.instId)}
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

          {/* Playhead overlay */}
          <div className="tl-playhead" style={{ left: playheadLeft }}>
            <div className="tl-playhead-cap">
              <span>{fmtClock(currentMinute)}</span>
            </div>
            <div className="tl-playhead-line" />
          </div>
        </div>
      </div>

      {clipMenu && (
        <ClipContextMenu
          x={clipMenu.x}
          y={clipMenu.y}
          video={clipMenu.video}
          chan={clipMenu.chan}
          onRemove={() => {
            onRemove(clipMenu.chan, clipMenu.instId);
            setClipMenu(null);
          }}
          onClose={() => setClipMenu(null)}
        />
      )}
    </div>
  );
}

function ClipBlock({ v, width, selected, isNes, onClick, onRemove, onContextMenu, onDragStart }) {
  const kindColor = {
    cartoon: { bg: '#3a2818', stripe: '#e8a838' },
    news:    { bg: '#0f2238', stripe: '#5a9fd4' },
    promo:   { bg: '#2a1428', stripe: '#c97cb8' },
    ad:      { bg: '#3a1f0c', stripe: '#d97740' },
    sitcom:  { bg: '#152812', stripe: '#7ab87a' },
    show:    { bg: '#231838', stripe: '#a884d4' },
    movie:   { bg: '#380c0c', stripe: '#d44a4a' },
    doc:     { bg: '#0c2e2a', stripe: '#5ab8a8' },
    game:    { bg: '#1c0f2e', stripe: '#c060ff' },
    tech:    { bg: '#1a1a1a', stripe: '#888' },
  };
  const c = kindColor[v.kind] || kindColor.tech;
  const tooSmall = width < 60;

  if (isNes) {
    return (
      <div
        className={`clip clip-nes${selected ? ' selected' : ''}`}
        draggable
        onDragStart={onDragStart}
        onClick={onClick}
        onDoubleClick={onRemove}
        onContextMenu={onContextMenu}
        style={{
          width,
          '--clip-bg': c.bg,
          '--clip-stripe': c.stripe,
        }}
        title={`${v.name} — interactive ROM (right-click for options)`}>
        <div className="clip-stripe" />
        <div className="clip-nes-body">
          <span className="clip-nes-badge">NES</span>
          <span className="clip-nes-name">{v.name}</span>
          <span className="clip-nes-tag">INTERACTIVE · FULL CHANNEL</span>
        </div>
        <div className="clip-edge" />
      </div>
    );
  }

  return (
    <div
      className={selected ? 'clip selected' : 'clip'}
      draggable
      onDragStart={onDragStart}
      onClick={onClick}
      onDoubleClick={onRemove}
      onContextMenu={onContextMenu}
      style={{
        width,
        '--clip-bg': c.bg,
        '--clip-stripe': c.stripe,
      }}
      title={`${v.name} — ${fmtDur(v.duration)} (right-click for options)`}>
      <div className="clip-stripe" />
      <div className="clip-body">
        {!tooSmall && <div className="clip-name">{v.name}</div>}
        {!tooSmall && <div className="clip-dur">{fmtDur(v.duration)}</div>}
        {tooSmall && <div className="clip-mini">{fmtDur(v.duration)}</div>}
      </div>
      <div className="clip-edge" />
    </div>
  );
}

// ─────────── Clip Context Menu ───────────
function ClipContextMenu({ x, y, video, chan, onRemove, onClose }) {
  const ref = React.useRef(null);
  const [pos, setPos] = React.useState({ left: x, top: y });

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
        CH {String(chan).padStart(2,'0')} · {video.kind.toUpperCase()} · {fmtDur(video.duration)}
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
window.ClipBlock = ClipBlock;
window.PX_PER_MIN = PX_PER_MIN;window.TIMELINE_MIN = TIMELINE_MIN;

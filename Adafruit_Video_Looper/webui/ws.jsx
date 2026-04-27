// WebSocket connection with auto-reconnect (exponential backoff capped at 5s).
// Subscribers register handlers by envelope type; the connection multiplexes.

function connectWebSocket() {
  const handlers = new Map();          // type → Set<fn>
  const anyHandlers = new Set();       // catch-all
  const stateListeners = new Set();    // 'open'|'close' subscribers

  let ws = null;
  let backoff = 250;
  let stopped = false;
  let reconnectTimer = null;

  const url = (location.protocol === 'https:' ? 'wss://' : 'ws://')
    + location.host + '/ws';

  const dispatch = (envelope) => {
    const t = envelope && envelope.type;
    if (t && handlers.has(t)) {
      for (const fn of handlers.get(t)) {
        try { fn(envelope); } catch (e) { console.error('ws handler', t, e); }
      }
    }
    for (const fn of anyHandlers) {
      try { fn(envelope); } catch (e) { console.error('ws any handler', e); }
    }
  };

  const notifyState = (state) => {
    for (const fn of stateListeners) {
      try { fn(state); } catch (e) { console.error('ws state', e); }
    }
  };

  const open = () => {
    if (stopped) return;
    try {
      ws = new WebSocket(url);
    } catch (e) {
      console.warn('ws ctor failed', e);
      schedule();
      return;
    }
    ws.addEventListener('open', () => {
      backoff = 250;
      notifyState('open');
    });
    ws.addEventListener('message', (evt) => {
      let envelope;
      try { envelope = JSON.parse(evt.data); }
      catch (e) { console.warn('ws bad json', e); return; }
      dispatch(envelope);
    });
    ws.addEventListener('close', () => {
      notifyState('close');
      schedule();
    });
    ws.addEventListener('error', () => {
      // 'close' will follow; nothing extra to do.
    });
  };

  const schedule = () => {
    if (stopped) return;
    if (reconnectTimer) return;
    const delay = Math.min(backoff, 5000);
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      open();
    }, delay);
    backoff = Math.min(backoff * 2, 5000);
  };

  open();

  return {
    subscribe(type, fn) {
      if (!handlers.has(type)) handlers.set(type, new Set());
      handlers.get(type).add(fn);
      return () => handlers.get(type).delete(fn);
    },
    onAny(fn) {
      anyHandlers.add(fn);
      return () => anyHandlers.delete(fn);
    },
    onState(fn) {
      stateListeners.add(fn);
      return () => stateListeners.delete(fn);
    },
    close() {
      stopped = true;
      if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
      if (ws) try { ws.close(); } catch {}
    },
  };
}

window.connectWebSocket = connectWebSocket;

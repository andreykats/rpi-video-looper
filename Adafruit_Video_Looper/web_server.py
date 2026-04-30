"""In-process Starlette + uvicorn web UI for the video looper.

Runs as a daemon thread alongside ProcessManager. Reads pm state (current
channel, playlists, players, config) and writes via the existing
LatestSlot pipeline (PlaylistIntent only — channel intents stay rotary-
only). Streams logs and playback position over a single /ws endpoint.
"""
import asyncio
import configparser
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from glob import glob
from pathlib import Path
from typing import Optional

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from . import playlist_io
from .model import Playlist

log = logging.getLogger('looper.web')


class NoCacheMiddleware(BaseHTTPMiddleware):
    """Force browsers to always re-validate static assets so the in-browser
    Babel pipeline picks up freshly-deployed JSX without a hard reload."""
    NO_CACHE_SUFFIXES = ('.html', '.jsx', '.css', '.js')

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.endswith(self.NO_CACHE_SUFFIXES) or path == '/':
            response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
        return response

RUNTIME_INI_PATH = '/boot/video_looper.ini'
WEBUI_DIR = os.path.join(os.path.dirname(__file__), 'webui')

# Process-startup asset version. Appended as ?v=<this> to local asset URLs
# in index.html so each `supervisorctl restart` invalidates browser caches
# that may have stored an older copy without no-cache headers.
ASSET_VERSION = str(int(time.time()))
_ASSET_REF_RE = re.compile(
    r'((?:href|src)=")((?!https?://|//)[^"?]+\.(?:css|js|jsx))(")'
)


async def index_handler(request: Request):
    path = os.path.join(WEBUI_DIR, 'index.html')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            html = f.read()
    except OSError as e:
        log.warning('failed to read index.html: %s', e)
        return HTMLResponse('index.html missing', status_code=500)

    def repl(m):
        return f'{m.group(1)}{m.group(2)}?v={ASSET_VERSION}{m.group(3)}'

    return HTMLResponse(_ASSET_REF_RE.sub(repl, html))

# Per the plan: only these INI keys are exposed to the UI. Anything else
# (encoder, web, usb_drive) stays SSH-only.
EDITABLE_CONFIG_KEYS = {
    'process_manager': ['is_random', 'wait_time'],
    'mpv': ['sound', 'video_stretch', 'hwdec'],
    'retroarch': ['verbose', 'audio_enable'],
    'logging': ['relay_debug'],
}

VIDEO_EXTS = (
    'avi', 'mov', 'mkv', 'mp4', 'm4v', 'webm', 'flv', 'ts',
    'nes', 'fds', 'nsf',
)
NES_EXTS = ('nes', 'fds', 'nsf')

# Folders to hide from the pool tree — filesystem/OS cruft the user
# doesn't curate.
HIDDEN_FOLDERS = frozenset({
    'System Volume Information',
    '$RECYCLE.BIN',
    'lost+found',
    '.Trashes',
    '.Spotlight-V100',
    '.fseventsd',
})


# ---------- USB / path helpers ----------

def _usb_roots():
    return sorted(glob('/mnt/usbdrive*'))


def _usb_roots_resolved():
    out = []
    for r in _usb_roots():
        try:
            out.append(Path(r).resolve(strict=True))
        except OSError:
            pass
    return out


def _safe_resolve(path_str: str) -> Optional[Path]:
    """Resolve `path_str` and ensure it lives under one of the USB mount roots.

    Returns None if the path is missing, escapes, or there are no mounts.
    """
    if not isinstance(path_str, str) or not path_str:
        return None
    roots = _usb_roots_resolved()
    if not roots:
        return None
    try:
        target = Path(path_str).resolve(strict=True)
    except (OSError, ValueError):
        return None
    for r in roots:
        try:
            target.relative_to(r)
            return target
        except ValueError:
            continue
    return None


def _usb_root_for(target: Path) -> Optional[Path]:
    """Return the USB mount root that contains `target`, or None."""
    for r in _usb_roots_resolved():
        try:
            target.relative_to(r)
            return r
        except ValueError:
            continue
    return None


def _rel_under_usb(abs_path: str, usb_root: str) -> str:
    """Strip `usb_root` from `abs_path`, returning a USB-relative POSIX path.

    Falls back to the basename when the path isn't under usb_root — that
    shouldn't happen for valid Movies but the fallback keeps the wire
    payload non-empty for diagnostics.
    """
    root = usb_root.rstrip('/')
    if abs_path == root:
        return ''
    prefix = root + '/'
    if abs_path.startswith(prefix):
        return abs_path[len(prefix):]
    return os.path.basename(abs_path)


def _is_protected(target: Path) -> bool:
    """A USB mount root itself — never delete or rename via the web UI.

    Numbered folders 1..13 used to be the source of channel definitions
    and were protected too. Under the central-playlist scheme they are
    just folders — the user may keep, rename, or delete them.
    """
    return target in _usb_roots_resolved()


def _upload_extensions(pm) -> tuple:
    """Resolve the upload allow-list. Reads `[web] upload_extensions`
    (comma- or whitespace-separated) and falls back to VIDEO_EXTS when
    the key is absent or empty. Always lowercased, dot-stripped, deduped.
    """
    try:
        raw = pm._config.get('web', 'upload_extensions', fallback='')
    except (configparser.NoSectionError, configparser.NoOptionError):
        raw = ''
    tokens = re.split(r'[,\s]+', raw or '')
    out = []
    seen = set()
    for t in tokens:
        ext = t.strip().lower().lstrip('.')
        if ext and ext not in seen:
            seen.add(ext)
            out.append(ext)
    return tuple(out) if out else VIDEO_EXTS


def _upload_target_root(pm) -> Optional[str]:
    """Pick the USB root for new uploads.

    Prefers `pm.active_usb_root` (where the central playlist.json
    lives) so uploads land on the volume the looper is reading. Falls
    back to the root with the most free space, then the first root.
    """
    if pm is not None and pm.active_usb_root is not None:
        if os.path.isdir(pm.active_usb_root):
            return pm.active_usb_root
    roots = _usb_roots()
    if not roots:
        return None
    best = None
    best_free = -1
    for root in roots:
        try:
            free = shutil.disk_usage(root).free
        except OSError:
            continue
        if free > best_free:
            best_free = free
            best = root
    return best or roots[0]


def _is_active_mount(pm, src_usb: Optional[Path]) -> bool:
    """True if `src_usb` (a resolved Path) points at pm.active_usb_root."""
    if src_usb is None or pm.active_usb_root is None:
        return False
    try:
        active = Path(pm.active_usb_root).resolve(strict=False)
    except OSError:
        return False
    return src_usb == active


# ---------- Pool tree ----------

def _scan_dir(path: str) -> dict:
    children = []
    files = []
    try:
        entries = sorted(os.scandir(path), key=lambda e: e.name.lower())
    except OSError:
        return {
            'name': os.path.basename(path) or path,
            'path': path,
            'type': 'folder',
            'children': [],
            'files': [],
        }
    for e in entries:
        if e.name.startswith('.'):
            continue
        if e.name in HIDDEN_FOLDERS:
            continue
        if e.is_dir(follow_symlinks=False):
            children.append(_scan_dir(e.path))
        elif e.is_file(follow_symlinks=False):
            ext = os.path.splitext(e.name)[1].lower().lstrip('.')
            if ext not in VIDEO_EXTS:
                continue
            try:
                size = e.stat().st_size
            except OSError:
                size = 0
            files.append({
                'name': e.name,
                'path': e.path,
                'sizeBytes': size,
                'fileType': 'nes' if ext in NES_EXTS else 'video',
            })
    return {
        'name': os.path.basename(path) or path,
        'path': path,
        'type': 'folder',
        'children': children,
        'files': files,
    }


def _build_pool_tree() -> dict:
    roots = _usb_roots()
    nodes = [_scan_dir(r) for r in roots]
    return {'mountRoots': roots, 'children': nodes}


# ---------- mpv IPC: time-pos query ----------

def query_mpv_position(socket_path: str = '/tmp/mpv-video-looper.sock',
                      timeout: float = 0.1):
    """Open a fresh AF_UNIX connection to mpv's IPC socket and ask for
    `time-pos` and `duration`. Returns (positionSec, durationSec) or
    (None, None) on any failure. mpv accepts multiple concurrent clients,
    so this never collides with the worker thread's loadfile writes.
    """
    if not os.path.exists(socket_path):
        return (None, None)
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(socket_path)
    except OSError:
        return (None, None)

    pos = None
    dur = None
    try:
        for i, prop in enumerate(('time-pos', 'duration'), 1):
            cmd = json.dumps({'command': ['get_property', prop],
                              'request_id': i})
            s.sendall((cmd + '\n').encode())
        buf = b''
        deadline = time.monotonic() + timeout
        seen = 0
        while seen < 2 and time.monotonic() < deadline:
            try:
                chunk = s.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            buf += chunk
            while b'\n' in buf:
                line, buf = buf.split(b'\n', 1)
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                rid = msg.get('request_id')
                data = msg.get('data')
                if rid == 1 and isinstance(data, (int, float)):
                    pos = float(data)
                    seen += 1
                elif rid == 2 and isinstance(data, (int, float)):
                    dur = float(data)
                    seen += 1
    except OSError:
        pass
    finally:
        try:
            s.close()
        except OSError:
            pass
    return (pos, dur)


# ---------- Event broker ----------

class Broker:
    """Fan-out broker for log lines and state events.

    Each subscriber owns an asyncio.Queue. The log handler thread pushes
    via call_soon_threadsafe (publish_threadsafe). State events are
    published from inside the event loop (publish).

    On per-queue overflow, the oldest entry is dropped and a one-off
    `log.dropped` envelope is enqueued so the client knows.
    """

    MAX_QUEUE = 500
    LOG_RING_SIZE = 100

    def __init__(self):
        self._loop = None
        self._subscribers = set()
        self._lock = threading.Lock()
        self._log_ring = []  # list of envelopes

    def attach_loop(self, loop):
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q = asyncio.Queue(maxsize=self.MAX_QUEUE)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            self._subscribers.discard(q)

    def recent_logs(self):
        with self._lock:
            return list(self._log_ring)

    def publish_threadsafe(self, envelope: dict):
        if self._loop is None:
            return
        try:
            self._loop.call_soon_threadsafe(self._publish_inline, envelope)
        except RuntimeError:
            # Loop closed during shutdown; drop.
            pass

    def publish(self, envelope: dict):
        self._publish_inline(envelope)

    def _publish_inline(self, envelope: dict):
        if envelope.get('type') == 'log.line':
            with self._lock:
                self._log_ring.append(envelope)
                if len(self._log_ring) > self.LOG_RING_SIZE:
                    self._log_ring = self._log_ring[-self.LOG_RING_SIZE:]
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(envelope)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait({
                        'type': 'log.dropped',
                        'ts': time.time(),
                        'count': 1,
                    })
                except asyncio.QueueFull:
                    pass


class WebSocketLogHandler(logging.Handler):
    """Routes log records into the broker for fan-out to websockets."""

    def __init__(self, broker: Broker):
        super().__init__()
        self.broker = broker

    def emit(self, record: logging.LogRecord):
        try:
            msg = record.getMessage()
        except Exception:
            return
        envelope = {
            'type': 'log.line',
            'ts': record.created,
            'level': record.levelname,
            'name': record.name,
            'msg': msg,
        }
        self.broker.publish_threadsafe(envelope)


# ---------- State snapshot ----------

def _active_kind(pm) -> Optional[str]:
    if pm._active_player is None:
        return None
    for kind, p in pm._players.items():
        if p is pm._active_player:
            return kind
    return None


def _read_central(pm) -> dict:
    """Read the active mount's central playlist.json. Returns
    `{'channels': {...}}`; an empty dict when no active mount or no
    central file. Cheap and lock-free — `os.replace` makes writes atomic
    so concurrent reads observe a consistent state.
    """
    if pm.active_usb_root is None:
        return {'channels': {}}
    data = playlist_io.read_central_playlist(pm.active_usb_root)
    return data if data is not None else {'channels': {}}


def _build_channel_playlist(pm, entries) -> Playlist:
    """Materialize a channel's entries against the active USB root."""
    if pm.active_usb_root is None or not entries:
        return Playlist([])
    movies = playlist_io.materialize_channel(
        pm.active_usb_root, entries, pm._get_video_duration,
    )
    return Playlist(movies)


def _build_state_snapshot(pm) -> dict:
    bm = pm._broadcast_manager
    channels = {}
    usb_root = pm.active_usb_root
    central_channels = _read_central(pm)['channels']

    if bm is not None:
        for ch_num, pl in bm._channel_playlists.items():
            entries = [{
                'filename': (_rel_under_usb(m.target, usb_root)
                             if usb_root else m.filename),
                'durationSec': m.duration,
                'fileType': m.content_type,
            } for m in pl._movies]
            cur = {
                'filename': None,
                'positionSec': None,
                'durationSec': None,
                'loopPositionSec': None,
            }
            loop_total = bm._channel_durations.get(ch_num, 0)
            if pl.length() > 0:
                selection = bm.calculate_broadcast_position(ch_num)
                movie = selection.movie
                if movie is not None and not selection.hidden:
                    cumulative = 0
                    for m in pl._movies:
                        if m is movie:
                            break
                        cumulative += m.duration
                    cur = {
                        'filename': movie.filename,
                        'positionSec': float(selection.seek_offset),
                        'durationSec': movie.duration,
                        'loopPositionSec': cumulative + float(selection.seek_offset),
                    }
            ch_meta = central_channels.get(ch_num) or {}
            channels[str(ch_num)] = {
                'totalDurationSec': loop_total,
                'current': cur,
                'entries': entries,
                'name': ch_meta.get('name'),
            }

    cfg = {}
    for section, keys in EDITABLE_CONFIG_KEYS.items():
        cfg[section] = {}
        for k in keys:
            try:
                cfg[section][k] = pm._config.get(section, k)
            except (configparser.NoSectionError, configparser.NoOptionError):
                cfg[section][k] = None

    broadcast_start = pm._broadcast_start_time
    return {
        'currentChannel': pm._current_channel,
        'playbackStopped': pm._playback_stopped,
        'activePlayer': _active_kind(pm),
        'broadcastStartTime': broadcast_start,
        'broadcastElapsedSec': max(0.0, time.time() - broadcast_start),
        'broadcast': {
            'startTime': broadcast_start,
            'channels': channels,
        },
        'mountRoots': _usb_roots(),
        'activeUsbRoot': usb_root,
        'config': cfg,
        'uploadExtensions': list(_upload_extensions(pm)),
    }


# ---------- Playback conflict check ----------

def _is_currently_playing(pm, target: Path) -> bool:
    bm = pm._broadcast_manager
    if bm is None:
        return False
    ch = pm._current_channel
    pl = bm._channel_playlists.get(ch)
    if pl is None or pl.length() == 0:
        return False
    selection = bm.calculate_broadcast_position(ch)
    movie = selection.movie
    if movie is None or selection.hidden:
        return False
    try:
        return Path(movie.target).resolve(strict=False) == target
    except OSError:
        return False


def _folder_contains_playing(pm, folder: Path) -> bool:
    """True if the currently-playing file lives anywhere under `folder`."""
    bm = pm._broadcast_manager
    if bm is None:
        return False
    pl = bm._channel_playlists.get(pm._current_channel)
    if pl is None or pl.length() == 0:
        return False
    selection = bm.calculate_broadcast_position(pm._current_channel)
    movie = selection.movie
    if movie is None or selection.hidden:
        return False
    try:
        active = Path(movie.target).resolve(strict=False)
        active.relative_to(folder)
        return True
    except (OSError, ValueError):
        return False


# ---------- REST handlers ----------

async def state_handler(request: Request):
    return JSONResponse(_build_state_snapshot(request.app.state.pm))


async def pool_handler(request: Request):
    return JSONResponse(_build_pool_tree())


async def storage_handler(request: Request):
    roots = _usb_roots()
    used = total = free = 0
    files = 0
    for r in roots:
        try:
            u = shutil.disk_usage(r)
            used += u.used
            total += u.total
            free += u.free
        except OSError:
            pass
        try:
            for _, _, fs in os.walk(r):
                files += sum(1 for f in fs if not f.startswith('.'))
        except OSError:
            pass
    return JSONResponse({
        'used': used, 'free': free, 'total': total, 'files': files,
    })


async def get_playlist_handler(request: Request):
    pm = request.app.state.pm
    bm = pm._broadcast_manager
    try:
        ch = int(request.path_params['ch'])
    except (KeyError, ValueError):
        return JSONResponse({'ok': False, 'error': 'bad-channel'}, status_code=400)
    if bm is None:
        return JSONResponse(
            {'ok': False, 'error': 'broadcast-mode-disabled'},
            status_code=409,
        )
    usb_root = pm.active_usb_root
    pl = bm._channel_playlists.get(ch)
    entries = []
    if pl is not None:
        for m in pl._movies:
            rel = (_rel_under_usb(m.target, usb_root)
                   if usb_root else m.filename)
            entries.append({
                'filename': rel,
                'durationSec': m.duration,
                'fileType': m.content_type,
            })
    name = None
    central_channels = _read_central(pm)['channels']
    ch_data = central_channels.get(ch)
    if ch_data is not None:
        name = ch_data.get('name')
    return JSONResponse({'channel': ch, 'entries': entries, 'name': name})


async def post_playlist_handler(request: Request):
    pm = request.app.state.pm
    bm = pm._broadcast_manager
    broker: Broker = request.app.state.broker
    lock: asyncio.Lock = request.app.state.playlist_lock
    try:
        ch = int(request.path_params['ch'])
    except (KeyError, ValueError):
        return JSONResponse({'ok': False, 'error': 'bad-channel'}, status_code=400)
    if bm is None:
        return JSONResponse(
            {'ok': False, 'error': 'broadcast-mode-disabled'},
            status_code=409,
        )
    if pm.active_usb_root is None:
        return JSONResponse({'ok': False, 'error': 'no-usb'}, status_code=503)
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({'ok': False, 'error': 'bad-body'}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({'ok': False, 'error': 'bad-body'}, status_code=400)
    raw_entries = body.get('entries')
    if not isinstance(raw_entries, list):
        return JSONResponse({'ok': False, 'error': 'bad-entries'}, status_code=400)

    # Pre-validate every filename. Reject 400 on any violation, naming
    # the offending entry so the UI can point the user at it.
    cleaned_entries = []
    for idx, raw in enumerate(raw_entries):
        if not isinstance(raw, dict):
            return JSONResponse(
                {'ok': False, 'error': 'bad-entry-shape', 'index': idx},
                status_code=400,
            )
        try:
            fn = playlist_io.clean_filename(raw.get('filename'))
        except ValueError as e:
            return JSONResponse(
                {'ok': False, 'error': 'bad-filename',
                 'index': idx, 'detail': str(e)},
                status_code=400,
            )
        cleaned_entries.append({'filename': fn})

    # Name handling — three cases the wire format must distinguish:
    #   key absent           → preserve existing name (drag-reorder path)
    #   '' or None           → clear the name
    #   non-empty string     → set
    has_name_key = 'name' in body
    new_name = None
    if has_name_key:
        try:
            new_name = playlist_io.clean_name(body.get('name'))
        except ValueError as e:
            return JSONResponse(
                {'ok': False, 'error': 'bad-name', 'detail': str(e)},
                status_code=400,
            )

    async with lock:
        central = _read_central(pm)
        existing = central['channels'].get(ch, {'name': None, 'entries': []})
        ch_name = new_name if has_name_key else existing.get('name')
        central['channels'][ch] = {
            'name': ch_name,
            'entries': cleaned_entries,
        }
        try:
            playlist_io.write_central_playlist(
                pm.active_usb_root, central['channels'],
            )
        except (ValueError, OSError) as e:
            log.exception('write_central_playlist failed: %s', e)
            return JSONResponse(
                {'ok': False, 'error': 'write-failed', 'detail': str(e)},
                status_code=500,
            )

    new_pl = _build_channel_playlist(pm, cleaned_entries)
    pm.publish_playlist_intent(ch, new_pl)
    broker.publish({
        'type': 'playlist.changed',
        'ts': time.time(),
        'channel': ch,
    })
    skipped = max(0, len(cleaned_entries) - new_pl.length())
    return JSONResponse({
        'ok': True,
        'applied': new_pl.length(),
        'skippedCount': skipped,
    })


async def post_file_rename_handler(request: Request):
    pm = request.app.state.pm
    broker: Broker = request.app.state.broker
    lock: asyncio.Lock = request.app.state.playlist_lock
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({'ok': False, 'error': 'bad-body'}, status_code=400)
    src_str = body.get('path', '') if isinstance(body, dict) else ''
    new_name = (body.get('newName') or '').strip() if isinstance(body, dict) else ''
    if not new_name or '/' in new_name or new_name in ('.', '..'):
        return JSONResponse({'ok': False, 'error': 'invalid-name'}, status_code=400)

    target = _safe_resolve(src_str)
    if target is None:
        return JSONResponse({'ok': False, 'error': 'path-escape'}, status_code=403)
    if _is_protected(target):
        return JSONResponse({'ok': False, 'error': 'protected-path'}, status_code=403)
    if not target.is_file():
        return JSONResponse({'ok': False, 'error': 'not-a-file'}, status_code=400)
    if _is_currently_playing(pm, target):
        return JSONResponse({
            'ok': False,
            'error': 'playback-conflict',
            'hint': 'Switch channels before renaming the active file.',
        }, status_code=409)
    new_path = target.parent / new_name
    if new_path.exists():
        return JSONResponse({'ok': False, 'error': 'name-exists'}, status_code=409)
    try:
        os.rename(target, new_path)
    except OSError as e:
        return JSONResponse(
            {'ok': False, 'error': 'rename-failed', 'detail': str(e)},
            status_code=500,
        )

    src_usb = _usb_root_for(target)
    updated_channels: list = []
    if src_usb is not None:
        try:
            old_rel = str(target.relative_to(src_usb))
            new_rel = str(new_path.relative_to(src_usb))
        except ValueError:
            old_rel = new_rel = None
        if old_rel is not None and new_rel is not None:
            updated_channels = await _rewrite_central_for_filename_change(
                pm, lock, src_usb, old_rel, new_rel,
            )
    broker.publish({'type': 'pool.changed', 'ts': time.time()})
    for ch in updated_channels:
        broker.publish({
            'type': 'playlist.changed',
            'ts': time.time(),
            'channel': ch,
        })
    return JSONResponse({
        'ok': True,
        'newPath': str(new_path),
        'playlistsUpdated': updated_channels,
    })


async def _rewrite_central_for_filename_change(
    pm, lock: asyncio.Lock, src_usb: Optional[Path],
    old_rel: str, new_rel: Optional[str],
) -> list:
    """Rewrite the central playlist after a file rename/delete/move.

    Walks every channel in the active mount's central file. Entries whose
    `filename` equals `old_rel` are replaced with `new_rel` (rename/move)
    or dropped (`new_rel is None`, delete).

    Skips when the modified file lives on a non-active mount: the central
    file there doesn't reference it.

    Returns the channel numbers whose entries actually changed.
    """
    if not _is_active_mount(pm, src_usb):
        return []
    updated = []
    async with lock:
        central = _read_central(pm)
        for ch_num, ch_data in central['channels'].items():
            ents = ch_data.get('entries', [])
            if new_rel is None:
                new_ents = [e for e in ents if e['filename'] != old_rel]
            else:
                new_ents = [
                    {'filename': (new_rel if e['filename'] == old_rel
                                  else e['filename'])}
                    for e in ents
                ]
            if new_ents == ents:
                continue
            ch_data['entries'] = new_ents
            updated.append(ch_num)
        if not updated:
            return []
        try:
            playlist_io.write_central_playlist(
                pm.active_usb_root, central['channels'],
            )
        except (ValueError, OSError) as e:
            log.exception('rewriting central playlist failed: %s', e)
            return []
    for ch in updated:
        ents = central['channels'][ch].get('entries', [])
        pm.publish_playlist_intent(ch, _build_channel_playlist(pm, ents))
    return updated


async def _rewrite_central_for_folder_change(
    pm, lock: asyncio.Lock, src_usb: Optional[Path],
    old_folder_rel: str, new_folder_rel: Optional[str],
) -> list:
    """Rewrite the central playlist after a folder rename/delete.

    Entries whose `filename` lives inside `old_folder_rel` (prefix match
    on `old_folder_rel + '/'`) are rewritten to the new prefix or dropped
    when `new_folder_rel is None`. The trailing slash on the prefix is
    load-bearing: renaming `videos` must not match `videos2/...` entries.
    """
    if not _is_active_mount(pm, src_usb):
        return []
    old_prefix = old_folder_rel.rstrip('/') + '/'
    new_prefix = (
        new_folder_rel.rstrip('/') + '/' if new_folder_rel else None
    )
    updated = []
    async with lock:
        central = _read_central(pm)
        for ch_num, ch_data in central['channels'].items():
            ents = ch_data.get('entries', [])
            if new_prefix is None:
                new_ents = [
                    e for e in ents if not e['filename'].startswith(old_prefix)
                ]
            else:
                new_ents = [
                    ({'filename': new_prefix + e['filename'][len(old_prefix):]}
                     if e['filename'].startswith(old_prefix) else e)
                    for e in ents
                ]
            if new_ents == ents:
                continue
            ch_data['entries'] = new_ents
            updated.append(ch_num)
        if not updated:
            return []
        try:
            playlist_io.write_central_playlist(
                pm.active_usb_root, central['channels'],
            )
        except (ValueError, OSError) as e:
            log.exception('rewriting central playlist failed: %s', e)
            return []
    for ch in updated:
        ents = central['channels'][ch].get('entries', [])
        pm.publish_playlist_intent(ch, _build_channel_playlist(pm, ents))
    return updated


async def post_file_delete_handler(request: Request):
    pm = request.app.state.pm
    broker: Broker = request.app.state.broker
    lock: asyncio.Lock = request.app.state.playlist_lock
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({'ok': False, 'error': 'bad-body'}, status_code=400)
    src_str = body.get('path', '') if isinstance(body, dict) else ''
    target = _safe_resolve(src_str)
    if target is None:
        return JSONResponse({'ok': False, 'error': 'path-escape'}, status_code=403)
    if _is_protected(target):
        return JSONResponse({'ok': False, 'error': 'protected-path'}, status_code=403)
    if not target.is_file():
        return JSONResponse({'ok': False, 'error': 'not-a-file'}, status_code=400)
    if _is_currently_playing(pm, target):
        return JSONResponse({
            'ok': False,
            'error': 'playback-conflict',
            'hint': 'Switch channels before deleting the active file.',
        }, status_code=409)
    src_usb = _usb_root_for(target)
    try:
        os.unlink(target)
    except OSError as e:
        return JSONResponse(
            {'ok': False, 'error': 'delete-failed', 'detail': str(e)},
            status_code=500,
        )
    updated_channels: list = []
    if src_usb is not None:
        try:
            old_rel = str(target.relative_to(src_usb))
        except ValueError:
            old_rel = None
        if old_rel is not None:
            updated_channels = await _rewrite_central_for_filename_change(
                pm, lock, src_usb, old_rel, None,
            )
    broker.publish({'type': 'pool.changed', 'ts': time.time()})
    for ch in updated_channels:
        broker.publish({
            'type': 'playlist.changed',
            'ts': time.time(),
            'channel': ch,
        })
    return JSONResponse({'ok': True, 'playlistsUpdated': updated_channels})


async def post_file_move_handler(request: Request):
    """Move a file between USB folders.

    Rewrites the central playlist's entries for the source mount to the
    new location. Cross-mount moves drop the entries from the source
    mount's central playlist (the destination mount's central file —
    if any — references files relative to its own root, which the
    source mount's data would never satisfy).
    """
    pm = request.app.state.pm
    broker: Broker = request.app.state.broker
    lock: asyncio.Lock = request.app.state.playlist_lock
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({'ok': False, 'error': 'bad-body'}, status_code=400)
    src_str = body.get('path', '') if isinstance(body, dict) else ''
    target_dir_str = body.get('targetDir', '') if isinstance(body, dict) else ''

    target_file = _safe_resolve(src_str)
    if target_file is None:
        return JSONResponse({'ok': False, 'error': 'path-escape'}, status_code=403)
    if _is_protected(target_file):
        return JSONResponse({'ok': False, 'error': 'protected-path'}, status_code=403)
    if not target_file.is_file():
        return JSONResponse({'ok': False, 'error': 'not-a-file'}, status_code=400)
    target_dir = _safe_resolve(target_dir_str)
    if target_dir is None or not target_dir.is_dir():
        return JSONResponse({'ok': False, 'error': 'bad-target-dir'},
                            status_code=400)
    if _is_currently_playing(pm, target_file):
        return JSONResponse({
            'ok': False,
            'error': 'playback-conflict',
            'hint': 'Switch channels before moving the active file.',
        }, status_code=409)

    new_path = target_dir / target_file.name
    if new_path.exists():
        return JSONResponse({'ok': False, 'error': 'name-exists'}, status_code=409)
    src_usb = _usb_root_for(target_file)
    dest_usb = _usb_root_for(target_dir)
    try:
        os.rename(target_file, new_path)
    except OSError as e:
        return JSONResponse(
            {'ok': False, 'error': 'move-failed', 'detail': str(e)},
            status_code=500,
        )

    updated_channels: list = []
    if src_usb is not None:
        try:
            old_rel = str(target_file.relative_to(src_usb))
        except ValueError:
            old_rel = None
        new_rel = None
        if dest_usb == src_usb:
            try:
                new_rel = str(new_path.relative_to(src_usb))
            except ValueError:
                new_rel = None
        if old_rel is not None:
            updated_channels = await _rewrite_central_for_filename_change(
                pm, lock, src_usb, old_rel, new_rel,
            )
    broker.publish({'type': 'pool.changed', 'ts': time.time()})
    for ch in updated_channels:
        broker.publish({
            'type': 'playlist.changed',
            'ts': time.time(),
            'channel': ch,
        })
    return JSONResponse({
        'ok': True,
        'newPath': str(new_path),
        'playlistsUpdated': updated_channels,
    })


async def post_file_upload_handler(request: Request):
    """Stream a raw request body to a new file at the root of the first
    USB drive. Filename comes from `?name=` so the body stays pure bytes
    — no multipart parser, no memory buffering, works for arbitrarily
    large videos.
    """
    pm = request.app.state.pm
    broker: Broker = request.app.state.broker
    name = (request.query_params.get('name') or '').strip()
    if not name or '/' in name or name in ('.', '..') or name.startswith('.'):
        return JSONResponse({'ok': False, 'error': 'invalid-name'}, status_code=400)
    ext = os.path.splitext(name)[1].lower().lstrip('.')
    allowed = _upload_extensions(pm)
    if ext not in allowed:
        return JSONResponse(
            {'ok': False, 'error': 'bad-extension', 'detail': ext or '(none)'},
            status_code=400,
        )

    root = _upload_target_root(pm)
    if root is None:
        return JSONResponse({'ok': False, 'error': 'no-usb'}, status_code=503)
    final_path = os.path.join(root, name)
    if os.path.exists(final_path):
        return JSONResponse({'ok': False, 'error': 'name-exists'}, status_code=409)

    tmp = tempfile.NamedTemporaryFile(
        dir=root, prefix='.upload-', suffix='.tmp', delete=False,
    )
    tmp_path = tmp.name
    written = 0
    try:
        try:
            async for chunk in request.stream():
                if chunk:
                    tmp.write(chunk)
                    written += len(chunk)
        finally:
            tmp.close()
        os.rename(tmp_path, final_path)
    except OSError as e:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        log.exception('upload failed for %s: %s', name, e)
        return JSONResponse(
            {'ok': False, 'error': 'write-failed', 'detail': str(e)},
            status_code=500,
        )
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    broker.publish({'type': 'pool.changed', 'ts': time.time()})
    return JSONResponse({
        'ok': True,
        'path': final_path,
        'sizeBytes': written,
    })


async def post_folder_rename_handler(request: Request):
    pm = request.app.state.pm
    broker: Broker = request.app.state.broker
    lock: asyncio.Lock = request.app.state.playlist_lock
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({'ok': False, 'error': 'bad-body'}, status_code=400)
    src_str = body.get('path', '') if isinstance(body, dict) else ''
    new_name = (body.get('newName') or '').strip() if isinstance(body, dict) else ''
    if not new_name or '/' in new_name or new_name in ('.', '..'):
        return JSONResponse({'ok': False, 'error': 'invalid-name'}, status_code=400)
    target = _safe_resolve(src_str)
    if target is None:
        return JSONResponse({'ok': False, 'error': 'path-escape'}, status_code=403)
    if _is_protected(target):
        return JSONResponse({'ok': False, 'error': 'protected-path'}, status_code=403)
    if not target.is_dir():
        return JSONResponse({'ok': False, 'error': 'not-a-dir'}, status_code=400)
    if _folder_contains_playing(pm, target):
        return JSONResponse({
            'ok': False,
            'error': 'playback-conflict',
            'hint': 'Switch channels before renaming a folder containing the active file.',
        }, status_code=409)
    new_path = target.parent / new_name
    if new_path.exists():
        return JSONResponse({'ok': False, 'error': 'name-exists'}, status_code=409)
    src_usb = _usb_root_for(target)
    try:
        os.rename(target, new_path)
    except OSError as e:
        return JSONResponse(
            {'ok': False, 'error': 'rename-failed', 'detail': str(e)},
            status_code=500,
        )
    updated_channels: list = []
    if src_usb is not None:
        try:
            old_folder_rel = str(target.relative_to(src_usb))
            new_folder_rel = str(new_path.relative_to(src_usb))
        except ValueError:
            old_folder_rel = new_folder_rel = None
        if old_folder_rel and new_folder_rel:
            updated_channels = await _rewrite_central_for_folder_change(
                pm, lock, src_usb, old_folder_rel, new_folder_rel,
            )
    broker.publish({'type': 'pool.changed', 'ts': time.time()})
    for ch in updated_channels:
        broker.publish({
            'type': 'playlist.changed',
            'ts': time.time(),
            'channel': ch,
        })
    return JSONResponse({
        'ok': True,
        'newPath': str(new_path),
        'playlistsUpdated': updated_channels,
    })


async def post_folder_delete_handler(request: Request):
    pm = request.app.state.pm
    broker: Broker = request.app.state.broker
    lock: asyncio.Lock = request.app.state.playlist_lock
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({'ok': False, 'error': 'bad-body'}, status_code=400)
    src_str = body.get('path', '') if isinstance(body, dict) else ''
    target = _safe_resolve(src_str)
    if target is None:
        return JSONResponse({'ok': False, 'error': 'path-escape'}, status_code=403)
    if _is_protected(target):
        return JSONResponse({'ok': False, 'error': 'protected-path'}, status_code=403)
    if not target.is_dir():
        return JSONResponse({'ok': False, 'error': 'not-a-dir'}, status_code=400)
    if _folder_contains_playing(pm, target):
        return JSONResponse({
            'ok': False,
            'error': 'playback-conflict',
            'hint': 'Switch channels before deleting a folder containing the active file.',
        }, status_code=409)
    src_usb = _usb_root_for(target)
    try:
        shutil.rmtree(target)
    except OSError as e:
        return JSONResponse(
            {'ok': False, 'error': 'delete-failed', 'detail': str(e)},
            status_code=500,
        )
    updated_channels: list = []
    if src_usb is not None:
        try:
            old_folder_rel = str(target.relative_to(src_usb))
        except ValueError:
            old_folder_rel = None
        if old_folder_rel:
            updated_channels = await _rewrite_central_for_folder_change(
                pm, lock, src_usb, old_folder_rel, None,
            )
    broker.publish({'type': 'pool.changed', 'ts': time.time()})
    for ch in updated_channels:
        broker.publish({
            'type': 'playlist.changed',
            'ts': time.time(),
            'channel': ch,
        })
    return JSONResponse({'ok': True, 'playlistsUpdated': updated_channels})


async def get_config_handler(request: Request):
    pm = request.app.state.pm
    cfg = {}
    for section, keys in EDITABLE_CONFIG_KEYS.items():
        cfg[section] = {}
        for k in keys:
            try:
                cfg[section][k] = pm._config.get(section, k)
            except (configparser.NoSectionError, configparser.NoOptionError):
                cfg[section][k] = None
    return JSONResponse(cfg)


async def post_config_handler(request: Request):
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({'ok': False, 'error': 'bad-body'}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({'ok': False, 'error': 'bad-body'}, status_code=400)

    cleaned = {}
    for section, keys in EDITABLE_CONFIG_KEYS.items():
        section_in = body.get(section)
        if not isinstance(section_in, dict):
            continue
        cleaned[section] = {}
        for k in keys:
            if k in section_in:
                cleaned[section][k] = str(section_in[k])

    if not cleaned:
        return JSONResponse({'ok': False, 'error': 'no-editable-keys'},
                            status_code=400)

    cp = configparser.ConfigParser()
    cp.read(RUNTIME_INI_PATH)
    for section, kv in cleaned.items():
        if not cp.has_section(section):
            cp.add_section(section)
        for k, v in kv.items():
            cp.set(section, k, v)

    try:
        fd, tmp = tempfile.mkstemp(
            prefix='.video_looper-', suffix='.ini',
            dir=os.path.dirname(RUNTIME_INI_PATH),
        )
        with os.fdopen(fd, 'w') as f:
            cp.write(f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, RUNTIME_INI_PATH)
    except OSError as e:
        try:
            os.unlink(tmp)
        except (OSError, NameError):
            pass
        log.exception('config write failed: %s', e)
        return JSONResponse(
            {'ok': False, 'error': 'write-failed', 'detail': str(e)},
            status_code=500,
        )

    log.info('config written via web UI; scheduling supervisor restart')
    _schedule_command(['sudo', 'supervisorctl', 'restart', 'video_looper'])
    return JSONResponse({'ok': True, 'restartScheduled': True}, status_code=202)


async def post_reboot_handler(request: Request):
    log.info('reboot requested via web UI')
    _schedule_command(['sudo', 'reboot'])
    return JSONResponse({'ok': True}, status_code=202)


async def post_broadcast_reset_handler(request: Request):
    """Snap T0 to now and re-launch the current channel from offset 0.

    Returns 409 in legacy/sequential mode (no broadcast manager).
    """
    pm = request.app.state.pm
    broker: Broker = request.app.state.broker
    T0 = pm.reset_broadcast()
    if T0 is None:
        return JSONResponse(
            {'ok': False, 'error': 'broadcast-mode-disabled'},
            status_code=409,
        )
    log.info('broadcast reset via web UI; new T0=%.3f', T0)
    broker.publish({'type': 'broadcast.reset', 'ts': time.time(),
                    'startTime': T0})
    return JSONResponse({'ok': True, 'startTime': T0})


def _schedule_command(cmd, delay_s: float = 0.25):
    def _run():
        time.sleep(delay_s)
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except OSError as e:
            log.exception('scheduled command failed: %s', e)

    threading.Thread(target=_run, daemon=True).start()


# ---------- WebSocket ----------

async def ws_endpoint(websocket: WebSocket):
    pm = websocket.app.state.pm
    broker: Broker = websocket.app.state.broker
    await websocket.accept()
    q = broker.subscribe()
    try:
        for envelope in broker.recent_logs():
            await websocket.send_json(envelope)
        await websocket.send_json({
            'type': 'state.snapshot',
            'ts': time.time(),
            'payload': _build_state_snapshot(pm),
        })
        while True:
            envelope = await q.get()
            await websocket.send_json(envelope)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.warning('ws error: %s', e)
    finally:
        broker.unsubscribe(q)


# ---------- Background pollers ----------

async def state_poller(app: Starlette):
    pm = app.state.pm
    broker: Broker = app.state.broker
    last_channel = None
    last_playback = None
    next_position_ts = 0.0
    while not app.state.shutdown.is_set():
        try:
            ch = pm._current_channel
            if ch != last_channel:
                broker.publish({
                    'type': 'state.channel',
                    'ts': time.time(),
                    'channel': ch,
                })
                last_channel = ch
            playback = (pm._playback_stopped, _active_kind(pm))
            if playback != last_playback:
                broker.publish({
                    'type': 'state.playback',
                    'ts': time.time(),
                    'stopped': playback[0],
                    'activePlayer': playback[1],
                })
                last_playback = playback
            now = time.monotonic()
            if now >= next_position_ts:
                next_position_ts = now + 0.5
                await _emit_positions(pm, broker)
        except Exception as e:
            log.exception('state_poller error: %s', e)
        try:
            await asyncio.wait_for(app.state.shutdown.wait(), timeout=0.1)
        except asyncio.TimeoutError:
            pass


async def _emit_positions(pm, broker: Broker):
    """Emit one envelope with broadcast positions for every channel.

    The active video channel's position is refined via mpv IPC; other
    channels use the broadcast manager's calculated position.
    """
    bm = pm._broadcast_manager
    if bm is None:
        return
    active_ch = pm._current_channel
    active_kind_v = _active_kind(pm)
    mpv_pos = None
    if active_kind_v == 'video':
        mpv_pos, _ = await asyncio.to_thread(query_mpv_position)

    channels_data = {}
    for ch_num, pl in bm._channel_playlists.items():
        if pl is None or pl.length() == 0:
            continue
        selection = bm.calculate_broadcast_position(ch_num)
        movie = selection.movie
        if movie is None or selection.hidden:
            continue
        loop_total = bm._channel_durations.get(ch_num, 0)
        cumulative = 0
        for m in pl._movies:
            if m is movie:
                break
            cumulative += m.duration
        seek = float(selection.seek_offset)
        if (ch_num == active_ch and mpv_pos is not None
                and movie.content_type == 'video'):
            seek = mpv_pos
        channels_data[str(ch_num)] = {
            'currentFilename': movie.filename,
            'positionSec': seek,
            'durationSec': movie.duration,
            'loopPositionSec': cumulative + seek,
            'loopDurationSec': loop_total,
        }
    broker.publish({
        'type': 'state.positions',
        'ts': time.time(),
        'channels': channels_data,
        'broadcastElapsedSec': max(0.0, time.time() - pm._broadcast_start_time),
    })


async def shutdown_watcher(app: Starlette, pm):
    while not pm._stop_event.is_set():
        await asyncio.sleep(0.5)
    log.info('looper stop_event observed; web shutting down')
    app.state.shutdown.set()


# ---------- App factory ----------

def create_app(pm) -> Starlette:
    routes = [
        Route('/', index_handler, methods=['GET']),
        Route('/index.html', index_handler, methods=['GET']),
        Route('/api/state', state_handler, methods=['GET']),
        Route('/api/pool', pool_handler, methods=['GET']),
        Route('/api/storage', storage_handler, methods=['GET']),
        Route('/api/playlist/{ch}', get_playlist_handler, methods=['GET']),
        Route('/api/playlist/{ch}', post_playlist_handler, methods=['POST']),
        Route('/api/file/rename', post_file_rename_handler, methods=['POST']),
        Route('/api/file/delete', post_file_delete_handler, methods=['POST']),
        Route('/api/file/move', post_file_move_handler, methods=['POST']),
        Route('/api/file/upload', post_file_upload_handler, methods=['POST']),
        Route('/api/folder/rename', post_folder_rename_handler, methods=['POST']),
        Route('/api/folder/delete', post_folder_delete_handler, methods=['POST']),
        Route('/api/config', get_config_handler, methods=['GET']),
        Route('/api/config', post_config_handler, methods=['POST']),
        Route('/api/system/reboot', post_reboot_handler, methods=['POST']),
        Route('/api/broadcast/reset', post_broadcast_reset_handler, methods=['POST']),
        WebSocketRoute('/ws', ws_endpoint),
    ]
    if os.path.isdir(WEBUI_DIR):
        routes.append(Mount('/', app=StaticFiles(directory=WEBUI_DIR, html=True)))
    else:
        log.warning('webui dir missing at %s; static files will not be served',
                    WEBUI_DIR)

    @asynccontextmanager
    async def lifespan(app: Starlette):
        app.state.pm = pm
        app.state.broker = Broker()
        app.state.shutdown = asyncio.Event()
        # Serializes read-modify-write of the central playlist.json.
        # Saves to different channels race against each other since the
        # whole file is rewritten on each save; the lock keeps them sane.
        app.state.playlist_lock = asyncio.Lock()
        loop = asyncio.get_running_loop()
        app.state.broker.attach_loop(loop)

        handler = WebSocketLogHandler(app.state.broker)
        handler.setLevel(logging.INFO)
        logging.getLogger().addHandler(handler)
        app.state._log_handler = handler

        poll_task = asyncio.create_task(state_poller(app))
        watch_task = asyncio.create_task(shutdown_watcher(app, pm))
        try:
            yield
        finally:
            app.state.shutdown.set()
            for t in (poll_task, watch_task):
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
            logging.getLogger().removeHandler(handler)

    return Starlette(
        routes=routes,
        lifespan=lifespan,
        middleware=[Middleware(NoCacheMiddleware)],
    )


def start_web_thread(pm) -> Optional[threading.Thread]:
    """Launch uvicorn in a daemon thread.

    Returns the Thread (already started) or None if `[web] enabled` is
    false. Reads bind/port from the looper's config.
    """
    enabled = pm._config.getboolean('web', 'enabled', fallback=False)
    if not enabled:
        log.info('web UI disabled via config')
        return None
    port = pm._config.getint('web', 'port', fallback=80)
    bind = pm._config.get('web', 'bind', fallback='0.0.0.0')

    app = create_app(pm)

    def _run():
        config = uvicorn.Config(
            app,
            host=bind,
            port=port,
            log_config=None,
            lifespan='on',
        )
        server = uvicorn.Server(config)
        # Suppress uvicorn's own SIGTERM/SIGINT registration — we're not
        # on the main thread, and the looper already owns those signals.
        server.install_signal_handlers = lambda: None
        try:
            asyncio.run(server.serve())
        except Exception as e:
            log.exception('web server exited: %s', e)

    t = threading.Thread(target=_run, name='web', daemon=True)
    t.start()
    log.info('web UI listening on http://%s:%d/', bind, port)
    return t

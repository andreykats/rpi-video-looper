"""Per-channel playlist persistence.

Each channel folder may contain a `playlist.json` describing the explicit
play order. When present, it is the source of truth for that channel;
when absent, the channel falls back to the existing alphabetical scan of
`_get_movies_from_path`.

Schema (version 4):

    {
      "version": 4,
      "name": "EVENING NEWS",
      "entries": [
        {"path": "videos/EVENING_NEWS_0600.mp4"},
        {"path": "ads/AD_DETERGENT_30S.mp4"}
      ]
    }

`path` is USB-root-relative — resolved against the parent of the
channel folder (the USB drive mount). Paths may point anywhere on the
USB drive, not just inside the channel folder. The same path may
appear multiple times to repeat a clip.

Optional top-level `name` is a user-facing channel label shown in the
web UI.

v1/v2/v3 files are rejected on read; the channel falls back to the
alphabetical scan as if no playlist file existed. Saving once via the UI
upgrades it.

Missing files (path not found on disk) are skipped with a logged warning
during materialize, never raised.
"""
import json
import logging
import os
import tempfile
from typing import Callable, Iterable, Optional

from .model import Movie

log = logging.getLogger('looper.playlist')

PLAYLIST_FILENAME = 'playlist.json'
PLAYLIST_VERSION = 4
SUPPORTED_PLAYLIST_VERSIONS = (4,)
MAX_NAME_LEN = 64


def clean_name(raw) -> Optional[str]:
    """Validate a channel-name value from JSON or HTTP input.

    Returns a stripped string when valid and non-empty, or None when the
    value is missing/empty/blank. Raises ValueError on type mismatch,
    excess length, or control characters.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError('name must be a string, got {0!r}'.format(type(raw)))
    cleaned = raw.strip()
    if not cleaned:
        return None
    if len(cleaned) > MAX_NAME_LEN:
        raise ValueError('name exceeds {0} chars'.format(MAX_NAME_LEN))
    for ch in cleaned:
        if ord(ch) < 0x20 or ord(ch) == 0x7f:
            raise ValueError('name contains control character')
    return cleaned


def _clean_path(raw) -> Optional[str]:
    """Validate and normalize a USB-root-relative entry path.

    Returns the normalized POSIX path (forward slashes, no leading slash,
    no `..` segments) on success, or None when the input is unusable.
    Does not check the filesystem — that happens at materialize time.
    """
    if not isinstance(raw, str) or not raw:
        return None
    if '\x00' in raw:
        return None
    # Normalize slashes; reject Windows-style backslashes outright.
    if '\\' in raw:
        return None
    norm = os.path.normpath(raw)
    # normpath('foo/../bar') → 'bar', but normpath('../foo') → '../foo';
    # reject anything that escapes the USB root or is absolute.
    if norm.startswith('..') or norm.startswith('/') or norm in ('.', ''):
        return None
    # Defense in depth — split and reject any '..' segment surviving normpath.
    if '..' in norm.split('/'):
        return None
    return norm


def read_playlist_meta(channel_dir: str) -> Optional[dict]:
    """Read playlist.json from a channel directory.

    Returns `{'name': str|None, 'entries': [{'path': str}, ...]}` on
    success, or None if the file is absent, unparseable, or written under
    a non-supported schema version. Malformed individual entries are
    dropped with a warning; the file as a whole is rejected when the
    top-level shape or version is wrong.
    """
    path = os.path.join(channel_dir, PLAYLIST_FILENAME)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, 'r') as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        log.warning('failed to read %s: %s', path, e)
        return None

    if not isinstance(data, dict):
        log.warning('%s is not a JSON object', path)
        return None
    if data.get('version') not in SUPPORTED_PLAYLIST_VERSIONS:
        log.warning('%s unknown version %r', path, data.get('version'))
        return None
    raw_entries = data.get('entries')
    if not isinstance(raw_entries, list):
        log.warning('%s missing entries list', path)
        return None

    try:
        name = clean_name(data.get('name'))
    except ValueError as e:
        log.warning('%s: ignoring invalid name (%s)', path, e)
        name = None

    cleaned = []
    for raw in raw_entries:
        if not isinstance(raw, dict):
            continue
        cleaned_path = _clean_path(raw.get('path'))
        if cleaned_path is None:
            log.warning('%s: ignoring entry with invalid path: %r',
                        path, raw.get('path'))
            continue
        cleaned.append({'path': cleaned_path})
    return {'name': name, 'entries': cleaned}


def read_playlist_json(channel_dir: str) -> Optional[list]:
    """Read playlist.json entries (channel name discarded).

    Thin wrapper around `read_playlist_meta` for callers that only need
    the playback order. Returns the entries list, or None when the file
    is absent or unparseable.
    """
    meta = read_playlist_meta(channel_dir)
    return None if meta is None else meta['entries']


def write_playlist_json(channel_dir: str, entries: Iterable, *,
                        name: Optional[str] = None) -> None:
    """Atomically write playlist.json to a channel directory.

    Validates each entry. `path` must be a USB-root-relative path with
    no leading slash and no `..` traversal. The optional `name` is the
    user-facing channel label; pass None (or a blank string) to omit it.

    Raises FileNotFoundError if channel_dir doesn't exist; ValueError on
    a malformed entry or invalid name; OSError on filesystem failures.
    """
    if not os.path.isdir(channel_dir):
        raise FileNotFoundError(channel_dir)

    cleaned_name = clean_name(name)

    cleaned = []
    for raw in entries:
        if not isinstance(raw, dict):
            raise ValueError('entry must be a dict, got {0!r}'.format(type(raw)))
        cleaned_path = _clean_path(raw.get('path'))
        if cleaned_path is None:
            raise ValueError('invalid entry path: {0!r}'.format(raw.get('path')))
        cleaned.append({'path': cleaned_path})

    payload = {'version': PLAYLIST_VERSION}
    if cleaned_name is not None:
        payload['name'] = cleaned_name
    payload['entries'] = cleaned

    final_path = os.path.join(channel_dir, PLAYLIST_FILENAME)
    fd, tmp_path = tempfile.mkstemp(prefix='.playlist-', suffix='.tmp',
                                    dir=channel_dir)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(payload, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, final_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    log.info('wrote %s with %d entries', final_path, len(cleaned))


def materialize(channel_dir: str, json_entries: list, usb_root: str,
                duration_for_path: Callable[[str], float]) -> list:
    """Build a list of Movies in JSON order from USB-root-relative paths.

    Each entry's `path` is joined with `usb_root` to form an absolute path.
    Missing files are dropped with a logged warning — they may have been
    deleted or moved; we don't crash. `duration_for_path` is invoked per
    surviving entry to obtain a duration in seconds (callers typically
    cache or short-circuit so identical files aren't probed twice).

    Each entry plays exactly once per loop iteration; users add a
    duplicate entry to repeat a clip.
    """
    out = []
    for entry in json_entries:
        rel = entry['path']
        abs_path = os.path.join(usb_root, rel)
        if not os.path.isfile(abs_path):
            log.warning('playlist.json in %s references missing file: %s',
                        channel_dir, rel)
            continue
        title = os.path.splitext(os.path.basename(abs_path))[0]
        duration = duration_for_path(abs_path)
        out.append(Movie(abs_path, title, 1, duration))
    return out

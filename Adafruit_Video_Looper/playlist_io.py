"""Per-channel playlist persistence.

Each channel folder may contain a `playlist.json` describing the explicit
play order. When present, it is the source of truth for that channel;
when absent, the channel falls back to the existing alphabetical scan of
`_get_movies_from_path`.

Schema (version 3):

    {
      "version": 3,
      "name": "EVENING NEWS",
      "entries": [
        {"filename": "EVENING_NEWS_0600.mp4"},
        {"filename": "AD_DETERGENT_30S.mp4"}
      ]
    }

Each entry plays exactly once per loop iteration. Users repeat a clip by
adding it twice. Optional top-level `name` is a user-facing channel label
shown in the web UI.

v1/v2 files are rejected on read; the channel falls back to the
alphabetical scan as if no playlist file existed. Saving once via the UI
upgrades it.

Same filename can appear multiple times. Missing files (filename not
present in folder) are skipped with a logged warning, never raised.
"""
import json
import logging
import os
import tempfile
from typing import Iterable, Optional

from .model import Movie

log = logging.getLogger('looper.playlist')

PLAYLIST_FILENAME = 'playlist.json'
PLAYLIST_VERSION = 3
SUPPORTED_PLAYLIST_VERSIONS = (3,)
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


def read_playlist_meta(channel_dir: str) -> Optional[dict]:
    """Read playlist.json from a channel directory.

    Returns `{'name': str|None, 'entries': [{'filename': str}, ...]}` on
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
        filename = raw.get('filename')
        if not isinstance(filename, str) or not filename:
            continue
        if '/' in filename or filename in ('.', '..'):
            log.warning('%s: ignoring entry with non-basename filename: %r',
                        path, filename)
            continue
        cleaned.append({'filename': filename})
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

    Validates each entry. Filename must be a basename (no path separators).
    The optional `name` is the user-facing channel label; pass None (or a
    blank string) to omit it from the file.

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
        filename = raw.get('filename')
        if not isinstance(filename, str) or not filename:
            raise ValueError('entry missing filename')
        if '/' in filename or filename in ('.', '..'):
            raise ValueError('filename must be a basename: {0!r}'.format(filename))
        cleaned.append({'filename': filename})

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


def materialize(channel_dir: str, scanned_movies: list, json_entries: list) -> list:
    """Build a list of Movies in JSON order from the alphabetical scan.

    Looks up each JSON entry by basename in `scanned_movies` and clones
    the Movie so each playlist instance is independent. Each entry plays
    exactly once per loop iteration; users add a duplicate entry to
    repeat a clip.

    Filenames not found in the scan are dropped with a logged warning —
    they may have been deleted or the JSON may be stale; we don't crash.
    """
    by_filename = {m.filename: m for m in scanned_movies}
    out = []
    for entry in json_entries:
        fn = entry['filename']
        src = by_filename.get(fn)
        if src is None:
            log.warning('playlist.json in %s references missing file: %s',
                        channel_dir, fn)
            continue
        out.append(Movie(src.target, src.title, 1, src.duration))
    return out

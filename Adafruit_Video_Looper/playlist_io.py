"""Per-channel playlist persistence.

Each channel folder may contain a `playlist.json` describing the explicit
play order and per-instance repeat counts. When present, it is the source
of truth for that channel; when absent, the channel falls back to the
existing alphabetical scan of `_get_movies_from_path`.

Schema (version 1):

    {
      "version": 1,
      "entries": [
        {"filename": "EVENING_NEWS_0600.mp4", "repeat": 1},
        {"filename": "AD_DETERGENT_30S.mp4",  "repeat": 2}
      ]
    }

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
PLAYLIST_VERSION = 1


def read_playlist_json(channel_dir: str) -> Optional[list]:
    """Read playlist.json from a channel directory.

    Returns a cleaned list of `{"filename": str, "repeat": int}` dicts on
    success, or None if the file is absent or unparseable. Malformed
    individual entries are dropped with a warning; the file as a whole is
    only rejected when the top-level shape or version is wrong.
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
    if data.get('version') != PLAYLIST_VERSION:
        log.warning('%s unknown version %r', path, data.get('version'))
        return None
    raw_entries = data.get('entries')
    if not isinstance(raw_entries, list):
        log.warning('%s missing entries list', path)
        return None

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
        repeat_raw = raw.get('repeat', 1)
        try:
            repeat = max(1, int(repeat_raw))
        except (TypeError, ValueError):
            repeat = 1
        cleaned.append({'filename': filename, 'repeat': repeat})
    return cleaned


def write_playlist_json(channel_dir: str, entries: Iterable) -> None:
    """Atomically write playlist.json to a channel directory.

    Validates each entry. Filename must be a basename (no path separators).
    Repeat is coerced to a positive int (defaults to 1).

    Raises FileNotFoundError if channel_dir doesn't exist; ValueError on
    a malformed entry; OSError on filesystem failures.
    """
    if not os.path.isdir(channel_dir):
        raise FileNotFoundError(channel_dir)

    cleaned = []
    for raw in entries:
        if not isinstance(raw, dict):
            raise ValueError('entry must be a dict, got {0!r}'.format(type(raw)))
        filename = raw.get('filename')
        if not isinstance(filename, str) or not filename:
            raise ValueError('entry missing filename')
        if '/' in filename or filename in ('.', '..'):
            raise ValueError('filename must be a basename: {0!r}'.format(filename))
        repeat_raw = raw.get('repeat', 1)
        try:
            repeat = max(1, int(repeat_raw))
        except (TypeError, ValueError):
            raise ValueError('repeat must be a positive int, got {0!r}'.format(repeat_raw))
        cleaned.append({'filename': filename, 'repeat': repeat})

    payload = {'version': PLAYLIST_VERSION, 'entries': cleaned}

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
    the Movie so each playlist instance has its own playcount. The JSON
    `repeat` value overrides any `_repeat_Nx` filename suffix already
    captured in the scan.

    Filenames not found in the scan are dropped with a logged warning —
    they may have been deleted or the JSON may be stale; we don't crash.
    """
    by_filename = {m.filename: m for m in scanned_movies}
    out = []
    for entry in json_entries:
        fn = entry['filename']
        repeat = entry.get('repeat', 1)
        src = by_filename.get(fn)
        if src is None:
            log.warning('playlist.json in %s references missing file: %s',
                        channel_dir, fn)
            continue
        out.append(Movie(src.target, src.title, repeat, src.duration))
    return out

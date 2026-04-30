"""Central USB playlist persistence.

A single `playlist.json` at the USB drive root maps each broadcast
channel (1..13; channel 1 is unmapped per existing convention) to a
list of file entries. Entries reference files anywhere on the USB by
USB-root-relative path.

Schema (version 4):

    {
      "version": 4,
      "channels": {
        "2": {
          "name": "EVENING NEWS",
          "entries": [
            {"filename": "shows/news/evening.mp4"},
            {"filename": "ads/detergent.mp4"}
          ]
        }
      }
    }

Repeats are encoded by listing the same `filename` twice. ROM-vs-video
is implicit from the file extension. Channels not present in the
`channels` object are empty.

`filename` is a path relative to the USB drive root, forward-slash
separated, with no `..` segments and no leading slash. Missing files
on disk are dropped at materialize time with a logged warning, never
raised.

Pre-v4 per-channel `<channel>/playlist.json` files (an earlier in-tree
format that never shipped) are ignored — users rebuild via the web UI.
"""
import json
import logging
import os
import tempfile
from typing import Callable, List, Optional

from .model import Movie

log = logging.getLogger('looper.playlist')

CENTRAL_PLAYLIST_FILENAME = 'playlist.json'
PLAYLIST_VERSION = 4
MAX_NAME_LEN = 64
MAX_FILENAME_LEN = 512
MAX_FILE_SIZE = 1 * 1024 * 1024  # 1 MB — sanity cap so a garbage file can't OOM us.


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


def clean_filename(raw) -> str:
    """Validate and normalize a USB-root-relative filename.

    Returns the normalized POSIX path on success; raises ValueError on
    any rule violation. Does not check the filesystem — that happens at
    materialize time.

    Rules:
    - Non-empty UTF-8 string, ≤MAX_FILENAME_LEN chars.
    - No NUL bytes or control characters.
    - No backslashes (Linux-only deploy; reject preemptively).
    - No leading slash (must be relative).
    - No `..` segment after normalization.
    """
    if not isinstance(raw, str):
        raise ValueError('filename must be a string')
    if not raw:
        raise ValueError('filename is empty')
    if len(raw) > MAX_FILENAME_LEN:
        raise ValueError(
            'filename exceeds {0} chars'.format(MAX_FILENAME_LEN)
        )
    if '\x00' in raw:
        raise ValueError('filename contains NUL byte')
    for ch in raw:
        if ord(ch) < 0x20 or ord(ch) == 0x7f:
            raise ValueError('filename contains control character')
    if '\\' in raw:
        raise ValueError('filename contains backslash')
    if raw.startswith('/'):
        raise ValueError('filename must be relative (no leading slash)')
    norm = os.path.normpath(raw)
    # normpath('foo/../bar') → 'bar', but '../foo' → '../foo'.
    if norm in ('.', '') or norm.startswith('..') or '..' in norm.split('/'):
        raise ValueError('filename escapes USB root')
    if norm.startswith('/'):
        raise ValueError('filename normalized to absolute path')
    return norm


def _validate_entries(raw_entries) -> List[dict]:
    out = []
    for raw in raw_entries:
        if not isinstance(raw, dict):
            raise ValueError(
                'entry must be an object, got {0!r}'.format(type(raw))
            )
        out.append({'filename': clean_filename(raw.get('filename'))})
    return out


def read_central_playlist(usb_root: str) -> Optional[dict]:
    """Read <usb_root>/playlist.json.

    Returns
        {'channels': {channel_num: int -> {'name': str|None,
                                            'entries': [{'filename': str}]}}}
    on success, or None when the file is absent, oversized, malformed,
    or under a non-supported version. Per-entry validation drops bad
    entries with a warning; the file as a whole is rejected only on
    top-level shape or version mismatch.
    """
    path = os.path.join(usb_root, CENTRAL_PLAYLIST_FILENAME)
    if not os.path.isfile(path):
        return None
    try:
        size = os.path.getsize(path)
    except OSError as e:
        log.warning('failed to stat %s: %s', path, e)
        return None
    if size > MAX_FILE_SIZE:
        log.warning('%s rejected: %d bytes exceeds %d-byte cap',
                    path, size, MAX_FILE_SIZE)
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
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
    raw_channels = data.get('channels')
    if not isinstance(raw_channels, dict):
        log.warning('%s missing channels object', path)
        return None

    channels = {}
    for key, raw in raw_channels.items():
        try:
            ch_num = int(key)
        except (TypeError, ValueError):
            log.warning('%s: ignoring non-integer channel key %r', path, key)
            continue
        if ch_num < 1 or ch_num > 13:
            log.warning('%s: channel %d out of range 1..13', path, ch_num)
            continue
        if not isinstance(raw, dict):
            log.warning('%s: channel %d is not an object', path, ch_num)
            continue
        try:
            name = clean_name(raw.get('name'))
        except ValueError as e:
            log.warning('%s: ignoring invalid channel %d name (%s)',
                        path, ch_num, e)
            name = None
        raw_entries = raw.get('entries')
        if not isinstance(raw_entries, list):
            log.warning('%s: channel %d missing entries list', path, ch_num)
            continue
        cleaned = []
        for entry in raw_entries:
            if not isinstance(entry, dict):
                log.warning('%s: ch %d ignoring non-object entry: %r',
                            path, ch_num, entry)
                continue
            try:
                fn = clean_filename(entry.get('filename'))
            except ValueError as e:
                log.warning('%s: ch %d ignoring invalid filename %r (%s)',
                            path, ch_num, entry.get('filename'), e)
                continue
            cleaned.append({'filename': fn})
        channels[ch_num] = {'name': name, 'entries': cleaned}
    return {'channels': channels}


def write_central_playlist(usb_root: str, channels: dict) -> None:
    """Atomically write <usb_root>/playlist.json.

    `channels` is keyed by integer channel number (1..13) with values of
    shape `{'name': str|None, 'entries': [{'filename': str}, ...]}`.
    Validates every entry; raises ValueError on bad input, OSError on
    filesystem failures. Channels are sorted by integer key for stable
    diffs across writes.
    """
    if not isinstance(channels, dict):
        raise ValueError('channels must be a dict')
    if not os.path.isdir(usb_root):
        raise FileNotFoundError(usb_root)

    cleaned_channels = {}
    for key, val in channels.items():
        try:
            ch_num = int(key)
        except (TypeError, ValueError):
            raise ValueError(
                'channel key must be an integer, got {0!r}'.format(key)
            )
        if ch_num < 1 or ch_num > 13:
            raise ValueError('channel {0} out of range 1..13'.format(ch_num))
        if not isinstance(val, dict):
            raise ValueError(
                'channel {0} value must be an object'.format(ch_num)
            )
        name = clean_name(val.get('name'))
        raw_entries = val.get('entries', [])
        if not isinstance(raw_entries, list):
            raise ValueError(
                'channel {0} entries must be a list'.format(ch_num)
            )
        ch_payload = {'entries': _validate_entries(raw_entries)}
        if name is not None:
            ch_payload['name'] = name
        cleaned_channels[ch_num] = ch_payload

    serial_channels = {
        str(ch): cleaned_channels[ch] for ch in sorted(cleaned_channels)
    }
    payload = {'version': PLAYLIST_VERSION, 'channels': serial_channels}

    final_path = os.path.join(usb_root, CENTRAL_PLAYLIST_FILENAME)
    fd, tmp_path = tempfile.mkstemp(prefix='.playlist-', suffix='.tmp',
                                    dir=usb_root)
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
    populated = sum(1 for v in serial_channels.values() if v.get('entries'))
    log.info('wrote %s (%d channels populated)', final_path, populated)


def materialize_channel(
    usb_root: str,
    entries: list,
    duration_for_path: Callable[[str], float],
) -> List[Movie]:
    """Resolve each entry's filename against `usb_root` and return Movies.

    Drops missing files with a logged warning. Defends against symlink
    escape: realpath of the resolved file must stay under realpath of
    the USB root. `duration_for_path` is invoked per surviving entry
    (callers typically cache so identical paths aren't probed twice).
    """
    out = []
    try:
        root_real = os.path.realpath(usb_root)
    except OSError as e:
        log.warning('usb_root %s does not resolve: %s', usb_root, e)
        return out
    root_prefix = root_real.rstrip('/') + os.sep

    for entry in entries:
        rel = entry.get('filename') if isinstance(entry, dict) else None
        if not isinstance(rel, str) or not rel:
            log.warning('skipping entry without filename: %r', entry)
            continue
        abs_path = os.path.join(usb_root, rel)
        try:
            real = os.path.realpath(abs_path)
        except OSError as e:
            log.warning('failed to resolve %s: %s', abs_path, e)
            continue
        if real != root_real and not real.startswith(root_prefix):
            log.warning('refusing to materialize %s: escapes %s',
                        abs_path, root_real)
            continue
        if not os.path.isfile(real):
            log.warning('missing file %s skipped', rel)
            continue
        title = os.path.splitext(os.path.basename(real))[0]
        duration = duration_for_path(real)
        out.append(Movie(real, title, 1, duration))
    return out

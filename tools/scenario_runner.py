#!/usr/bin/env python3
"""Drive the looper's mock encoder from a scenario file.

Scenarios are plain text. Header lines starting with `# expect:` /
`# expect-fail:` / `# setup:` are read by tools/verify_run.py and ignored
here. `# setup:` directives are run by this script before any encoder
writes (e.g. wiping the persisted band/frequency state). Body lines are
`delta_seconds  channel`.

Run with the looper already up under supervisor and `[encoder] backend = mock`
set in the config. The looper creates the FIFO at startup; this script
opens it for write and pushes channel numbers at the requested cadence.

Usage:
    python3 tools/scenario_runner.py tools/scenarios/slow.txt
    python3 tools/scenario_runner.py tools/scenarios/slow.txt --fifo /tmp/mock_encoder.fifo
"""
import argparse
import os
import pickle
import re
import sys
import time

DEFAULT_FIFO = '/tmp/mock_encoder.fifo'
DEFAULT_STATE_FILE = '/var/lib/video_looper/previous_values.pkl'
LEGACY_STATE_FILE = 'previous_values.pkl'


def parse_scenario(path):
    """Return (setup_directives, events).

    setup_directives: list[str] from `# setup: ...` lines.
    events: list[(delta, channel)].
    """
    setups = []
    events = []
    with open(path) as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            m = re.match(r'#\s*setup\s*:\s*(.+)', line, re.IGNORECASE)
            if m:
                setups.append(m.group(1).strip())
                continue
            if line.startswith('#'):
                continue
            parts = line.split()
            if len(parts) < 2:
                raise ValueError(f'bad scenario line: {line!r}')
            try:
                delta = float(parts[0])
                channel = int(parts[1])
            except ValueError:
                raise ValueError(f'bad scenario line: {line!r}')
            events.append((delta, channel))
    return setups, events


def apply_setups(setups, state_files):
    for directive in setups:
        if directive == 'reset-state':
            for sf in state_files:
                if os.path.exists(sf):
                    os.remove(sf)
                    print(f'[setup] removed {sf}', flush=True)
            continue
        m = re.match(r'pin-band\s*=\s*(\d+)\s+pin-freq\s*=\s*(\d+)', directive)
        if m:
            band = int(m.group(1))
            freq = int(m.group(2))
            # Mirror the format ChannelSwitcher.save_previous_values uses.
            frequency_by_band = {1: 2, 2: 16}
            frequency_by_band[band] = freq
            target = state_files[0]
            os.makedirs(os.path.dirname(target) or '.', exist_ok=True)
            with open(target, 'wb') as f:
                pickle.dump((frequency_by_band, band), f)
            print(f'[setup] pinned state band={band} freq={freq} -> {target}',
                  flush=True)
            continue
        print(f'[setup] WARNING unknown directive: {directive!r}', flush=True)


def run(scenario_path, fifo_path, state_files):
    setups, events = parse_scenario(scenario_path)
    apply_setups(setups, state_files)

    if not os.path.exists(fifo_path):
        # Looper should create it; create here too as a fallback so the
        # runner doesn't crash if started in the wrong order.
        try:
            os.mkfifo(fifo_path, 0o666)
            print(f'[runner] created fifo {fifo_path}', flush=True)
        except OSError as e:
            print(f'[runner] could not create fifo {fifo_path}: {e}',
                  flush=True)
            sys.exit(1)

    print(f'[runner] opening fifo {fifo_path} for write '
          f'(blocks until looper opens for read)...', flush=True)
    with open(fifo_path, 'w', buffering=1) as fifo:
        print('[runner] fifo open', flush=True)
        start = time.monotonic()
        for delta, channel in events:
            target_time = start + delta
            now = time.monotonic()
            if target_time > now:
                time.sleep(target_time - now)
            t_rel = time.monotonic() - start
            fifo.write(f'{channel}\n')
            fifo.flush()
            print(f'[runner] t={t_rel:6.3f}s channel={channel}', flush=True)
        print('[runner] scenario complete', flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('scenario')
    p.add_argument('--fifo', default=DEFAULT_FIFO)
    p.add_argument('--state-file', action='append',
                   help='path to previous_values.pkl; can be given more than '
                        'once. Defaults to common locations on the Pi.')
    args = p.parse_args()

    state_files = args.state_file or [DEFAULT_STATE_FILE, LEGACY_STATE_FILE]
    run(args.scenario, args.fifo, state_files)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Verify a looper run against scenario expectations.

Reads /tmp/video_looper.log and the `# expect:` / `# expect-fail:` headers
from a scenario file, then runs each predicate. Exits 0 only if every
`# expect:` predicate passes AND every `# expect-fail:` predicate fails.

The verifier is generic — adding a new scenario does not require editing
this file. Adding a new *predicate* does.

Usage:
    python3 tools/verify_run.py tools/scenarios/slow.txt
    python3 tools/verify_run.py tools/scenarios/slow.txt --log /tmp/video_looper.log
"""
import argparse
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

DEFAULT_LOG_PATH = '/tmp/video_looper.log'

# Log line format from process_manager._configure_logging:
#   HH:MM:SS.mmm threadname looper.<name> LEVEL message
LOG_LINE_RE = re.compile(
    r'^(?P<time>\d{2}:\d{2}:\d{2}\.\d{3})\s+'
    r'(?P<thread>\S+)\s+'
    r'(?P<logger>\S+)\s+'
    r'(?P<level>\S+)\s+'
    r'(?P<msg>.*)$'
)


@dataclass
class Event:
    t: float           # seconds since 00:00:00 (rolls over at midnight; OK for single-day runs)
    logger: str
    level: str
    msg: str
    raw: str           # original line for error reporting

    # Decoded fields populated by classify().
    kind: Optional[str] = None
    fields: dict = field(default_factory=dict)


def parse_time(hms: str) -> float:
    h, m, rest = hms.split(':')
    s = float(rest)
    return int(h) * 3600 + int(m) * 60 + s


def classify(ev: Event):
    """Tag an event with a kind and decoded fields if it's relevant."""
    msg = ev.msg
    if ev.logger == 'looper.encoder' and msg.startswith('publish '):
        m = re.search(r'target=(\d+)\s+prev=(\d+)', msg)
        if m:
            ev.kind = 'publish'
            ev.fields = {'target': int(m.group(1)), 'prev': int(m.group(2))}
    elif ev.logger == 'looper.mpv':
        if msg.startswith('start '):
            m = re.search(r'pid=(\d+)\s+file=(\S+)', msg)
            ev.kind = 'mpv-start'
            ev.fields = {'pid': int(m.group(1)), 'file': m.group(2)} if m else {}
        elif msg.startswith('stop '):
            m = re.search(r'reason=(\S+)', msg)
            ev.kind = 'mpv-stop'
            ev.fields = {'reason': m.group(1)} if m else {}
        elif msg.startswith('ipc-load '):
            ev.kind = 'mpv-ipc-load'
    elif ev.logger == 'looper.retroarch':
        if msg.startswith('start '):
            m = re.search(r'pid=(\d+)\s+file=(\S+)', msg)
            ev.kind = 'retroarch-start'
            ev.fields = {'pid': int(m.group(1)), 'file': m.group(2)} if m else {}
        elif msg.startswith('stop '):
            m = re.search(r'reason=(\S+)', msg)
            ev.kind = 'retroarch-stop'
            ev.fields = {'reason': m.group(1)} if m else {}
        elif msg.startswith('network-load '):
            ev.kind = 'retroarch-network-load'
    elif ev.logger == 'looper.relay' and msg.startswith('pulse-execute '):
        # "pulse-execute kind=up gpio=27 high" or "...low"
        m = re.match(r'pulse-execute\s+kind=(\S+)\s+gpio=\d+\s+(high|low)', msg)
        if m:
            ev.kind = 'pulse-execute'
            ev.fields = {'kind': m.group(1), 'edge': m.group(2)}


def load_log(path):
    events = []
    with open(path) as f:
        for raw in f:
            raw = raw.rstrip('\n')
            m = LOG_LINE_RE.match(raw)
            if not m:
                continue
            ev = Event(
                t=parse_time(m.group('time')),
                logger=m.group('logger'),
                level=m.group('level'),
                msg=m.group('msg'),
                raw=raw,
            )
            classify(ev)
            events.append(ev)
    return events


def parse_scenario_predicates(path):
    """Return list of (mode, predicate_string) where mode is 'expect' or 'expect-fail'."""
    out = []
    with open(path) as f:
        for raw in f:
            line = raw.strip()
            m = re.match(r'#\s*(expect|expect-fail)\s*:\s*(.+?)\s*(#.*)?$',
                         line, re.IGNORECASE)
            if m:
                out.append((m.group(1).lower(), m.group(2).strip()))
    return out


# ============================================================================
# Predicates
# ============================================================================
# Each predicate returns (passed: bool, message: str, offending: list[str]).

def pred_no_double_spawn(events):
    failures = []
    for player in ('mpv', 'retroarch'):
        last_was_start = False
        last_start = None
        for ev in events:
            if ev.kind == f'{player}-start':
                if last_was_start:
                    failures.append(
                        f'two {player} starts without a stop between: '
                        f'{last_start.raw} ; {ev.raw}'
                    )
                last_was_start = True
                last_start = ev
            elif ev.kind == f'{player}-stop':
                last_was_start = False
    if failures:
        return False, f'no-double-spawn FAILED ({len(failures)})', failures
    return True, 'no-double-spawn ok', []


def pred_cleanup(events, *, allow_trailing):
    failures = []
    for player in ('mpv', 'retroarch'):
        open_count = 0
        last_open = None
        for ev in events:
            if ev.kind == f'{player}-start':
                open_count += 1
                last_open = ev
            elif ev.kind == f'{player}-stop':
                open_count = max(0, open_count - 1)
        if open_count == 0:
            continue
        if open_count == 1 and allow_trailing:
            continue
        failures.append(
            f'{player} has {open_count} unclosed start(s); last: '
            f'{last_open.raw if last_open else "?"}'
        )
    label = 'cleanup-final-player' if allow_trailing else 'cleanup-strict'
    if failures:
        return False, f'{label} FAILED', failures
    return True, f'{label} ok', []


def pred_relay_pulses(events, kind, expected):
    # Each pulse logs two pulse-execute lines (high then low). Count high edges only.
    actual = sum(
        1 for ev in events
        if ev.kind == 'pulse-execute'
        and ev.fields.get('kind') == kind
        and ev.fields.get('edge') == 'high'
    )
    if actual == expected:
        return True, f'relay-pulses-{kind} = {expected} ok', []
    return False, f'relay-pulses-{kind} expected={expected} actual={actual}', []


def pred_unmapped_no_relay(events, channel, window=1.0):
    failures = []
    for pub in events:
        if pub.kind != 'publish':
            continue
        if pub.fields.get('target') != channel:
            continue
        for ev in events:
            if ev.kind != 'pulse-execute':
                continue
            if ev.fields.get('edge') != 'high':
                continue
            if pub.t <= ev.t <= pub.t + window:
                failures.append(
                    f'pulse within {window}s after publish channel={channel}: '
                    f'{ev.raw}'
                )
    if failures:
        return False, f'unmapped-no-relay channel={channel} FAILED', failures
    return True, f'unmapped-no-relay channel={channel} ok', []


def pred_coalesce(events, window, max_launches):
    publishes = [ev for ev in events if ev.kind == 'publish']
    launches = [ev for ev in events
                if ev.kind in ('mpv-start', 'retroarch-start')]
    failures = []
    # Slide a `window`-second window starting at each publish; if the window
    # contains >=3 publishes, check that launches in the same window <= max.
    for i, p in enumerate(publishes):
        win_pubs = [q for q in publishes[i:] if q.t <= p.t + window]
        if len(win_pubs) < 3:
            continue
        win_launches = [l for l in launches if p.t <= l.t <= p.t + window]
        if len(win_launches) > max_launches:
            failures.append(
                f'window starting t={p.t:.3f}s had {len(win_pubs)} publishes '
                f'and {len(win_launches)} launches (max {max_launches})'
            )
            break  # one failure is enough
    if failures:
        return False, f'coalesce window={window} max-launches={max_launches} FAILED', failures
    return True, f'coalesce window={window} max-launches={max_launches} ok', []


# Predicate dispatch.
def run_predicate(predicate_str, events):
    s = predicate_str
    if s == 'no-double-spawn':
        return pred_no_double_spawn(events)
    if s == 'cleanup-final-player':
        return pred_cleanup(events, allow_trailing=True)
    if s == 'cleanup-strict':
        return pred_cleanup(events, allow_trailing=False)
    m = re.match(r'relay-pulses-(band|up|down)\s*=\s*(\d+)$', s)
    if m:
        return pred_relay_pulses(events, m.group(1), int(m.group(2)))
    m = re.match(r'unmapped-no-relay\s+channel\s*=\s*(\d+)$', s)
    if m:
        return pred_unmapped_no_relay(events, int(m.group(1)))
    m = re.match(r'coalesce\s+window\s*=\s*([\d.]+)\s+max-launches\s*=\s*(\d+)$', s)
    if m:
        return pred_coalesce(events, float(m.group(1)), int(m.group(2)))
    return False, f'unknown predicate: {s!r}', []


def main():
    p = argparse.ArgumentParser()
    p.add_argument('scenario')
    p.add_argument('--log', default=DEFAULT_LOG_PATH)
    args = p.parse_args()

    events = load_log(args.log)
    predicates = parse_scenario_predicates(args.scenario)

    if not predicates:
        print(f'no predicates in {args.scenario}', file=sys.stderr)
        sys.exit(2)

    overall_ok = True
    print(f'log: {args.log} ({len(events)} events parsed)')
    print(f'scenario: {args.scenario} ({len(predicates)} predicates)')
    print('-' * 70)
    for mode, pred_str in predicates:
        passed, summary, offending = run_predicate(pred_str, events)
        if mode == 'expect':
            ok = passed
            tag = 'PASS' if ok else 'FAIL'
        else:  # expect-fail
            ok = not passed
            tag = 'PASS (expected fail)' if ok else 'FAIL (unexpected pass)'
        if not ok:
            overall_ok = False
        print(f'[{tag}] {mode}: {summary}')
        if offending and not ok:
            for line in offending[:5]:
                print(f'   {line}')
            if len(offending) > 5:
                print(f'   ... and {len(offending) - 5} more')
    print('-' * 70)
    print('OVERALL:', 'PASS' if overall_ok else 'FAIL')
    sys.exit(0 if overall_ok else 1)


if __name__ == '__main__':
    main()

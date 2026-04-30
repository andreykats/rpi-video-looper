# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Summary
A Raspberry Pi video looper for **retro TV simulation**. Based on Adafruit Video Looper, heavily customized with:
- 13-channel broadcast TV simulation with synchronized playback
- RF modulator hardware control via GPIO relays
- Rotary encoder channel switching via I2C
- Multi-player support: MPV for video, RetroArch for NES ROMs
- In-process web UI (Starlette + uvicorn) for content/playlist/settings management — read-only for channel state; the encoder remains the only way to switch channels.

**Target Hardware**: Raspberry Pi 5 (runs on remote Pi, NOT this development machine)
**OS**: DietPi (requires `dtoverlay=vc4-kms-v3d` in `/boot/config.txt` for DRM/KMS)

> `README.md` is upstream Adafruit `pi_video_looper` documentation and is **outdated** (references omxplayer, no mention of MPV / RetroArch / broadcast mode / rotary encoder). Treat this CLAUDE.md as the source of truth.

## Commands

Everything below runs **on the Pi**, not on the development machine.

### SSH into the Pi
```bash
ssh root@192.168.1.195
```

### Install
```bash
sudo ./install.sh
```
Installs system packages (`mpv`, `retroarch`, `libretro-nestopia`, `i2c-tools`, `python3-smbus`), Python deps via `pip3 install .`, registers the supervisor config, copies the default config to `/boot/video_looper.ini`, and ensures I2C and `dtoverlay=vc4-kms-v3d` are enabled.

### Run / control
```bash
./run.sh                                  # foreground run (debugging) — calls process_manager directly
sudo ./enable.sh                          # supervisor autostart on
sudo ./disable.sh                         # supervisor autostart off
sudo ./reload.sh                          # supervisorctl restart video_looper
sudo supervisorctl tail -f video_looper   # live logs
```

### Deploying code changes to the Pi
Always deploy via git, never `scp` into the Pi's working tree:
1. Edit and commit locally
2. `git push origin <branch>`
3. On the Pi: `git pull` in `/root/rpi-channel-surfer`
4. `sudo supervisorctl restart video_looper`

`scp` works mechanically but leaves the Pi's working tree dirty, has no history, and a future `git pull` will refuse to merge or clobber the changes. Commit + push + pull keeps the Pi reproducible. The Pi runs **dropbear**, not OpenSSH, so `scp` requires the legacy `-O` flag — another reason to avoid it.

Supervisor config lives at `assets/video_looper.conf` and is installed to `/etc/supervisor/conf.d/video_looper.conf` by `install.sh`.

### Tests
There is no pytest/unittest suite, but there is a scenario-driven harness under `tools/` that runs against a live looper on the Pi:

- `[encoder] backend = mock` in `/boot/video_looper.ini` makes the looper read channel numbers from `/tmp/mock_encoder.fifo` instead of I2C. The looper auto-creates the FIFO on init.
- `python3 tools/scenario_runner.py tools/scenarios/<name>.txt` writes channel numbers to the FIFO at scheduled deltas. Honors `# setup: reset-state` directives by deleting `/var/lib/video_looper/previous_values.pkl` before the run.
- `python3 tools/verify_run.py tools/scenarios/<name>.txt` parses `/tmp/video_looper.log` and runs the `# expect:` / `# expect-fail:` predicates declared in the scenario header. Predicates: `no-double-spawn`, `cleanup-{strict,final-player}`, `relay-pulses-{band|up|down}=N`, `unmapped-no-relay channel=N`, `coalesce window=Ts max-launches=K`.
- **Always restore `backend = i2c` and restart the looper afterwards** — otherwise the physical encoder is dead. A wrapper script `tools/test-on-pi.sh` is on the way (scheduled agent) to automate the flip.

Five scenarios live under `tools/scenarios/`: `slow`, `rapid_spin`, `cross_band`, `unmapped_channel`, `eov_during_change`.

## Key Files

| File | Purpose |
|------|---------|
| `Adafruit_Video_Looper/process_manager.py` | Main orchestrator — `ProcessManager`, main loop, playlist build, two intent slots, `_channel_worker` (sole player launcher) |
| `Adafruit_Video_Looper/model.py` | Data models: `Movie`, `Playlist`, `BroadcastChannelManager` |
| `Adafruit_Video_Looper/rotary.py` | `ChannelSwitcher` — encoder polling, relay executor (consumes `_relay_target_slot`), GPIO output |
| `Adafruit_Video_Looper/encoder.py` | `EncoderBackend` interface + `I2CEncoderBackend` (lazy `smbus`) + `MockEncoderBackend` (FIFO-driven) |
| `Adafruit_Video_Looper/latest_slot.py` | `LatestSlot` — single-value publisher with optional shared wake event; replaces queue.Queue patterns |
| `Adafruit_Video_Looper/mpv.py` | MPV video player wrapper (DRM output, IPC for fast switching) |
| `Adafruit_Video_Looper/retroarch.py` | RetroArch emulator wrapper (NES ROMs via Nestopia core) |
| `Adafruit_Video_Looper/usb_drive.py` | USB drive file reader with pyudev monitoring |
| `Adafruit_Video_Looper/directory.py` | Local directory file reader |
| `Adafruit_Video_Looper/playlist_io.py` | Atomic read/write of `<channel>/playlist.json` |
| `Adafruit_Video_Looper/web_server.py` | Starlette app + uvicorn thread; REST + `/ws` for the UI |
| `Adafruit_Video_Looper/webui/` | Static frontend (HTML + JSX in-browser Babel + CSS) |
| `assets/video_looper.ini` | Default configuration template |
| `assets/video_looper.conf` | Supervisor program definition (installed to `/etc/supervisor/conf.d/`) |
| `/boot/video_looper.ini` | **Runtime config location** (on Pi) |
| `/tmp/video_looper.log` | Structured log (truncated each run) — fed to `tools/verify_run.py` |
| `/var/lib/video_looper/previous_values.pkl` | Persisted band + per-band frequency state |
| `/mnt/usbdrive*/<N>/playlist.json` | **Source of truth** for channel N's playlist when present (UI writes it). Falls back to alphabetical scan when absent. |
| `install.sh` | One-shot Pi setup — system pkgs, pip install, supervisor, I2C, DRM overlay, state dir |
| `run.sh` / `enable.sh` / `disable.sh` / `reload.sh` | Foreground run / supervisor autostart toggles / restart |
| `tools/scenario_runner.py` | Drives the mock encoder FIFO from a scenario file |
| `tools/verify_run.py` | Generic predicate engine; reads `/tmp/video_looper.log` and asserts `# expect:` headers |
| `tools/scenarios/*.txt` | Scenario files with `# setup:` and `# expect:` headers |

## Two Operating Modes

### Broadcast Mode (Primary)
- **Trigger**: USB drive contains numbered folders `1/` through `13/`
- Each folder = one channel with its own playlist
- **Synchronized playback**: All channels share a global start time
- Switching channels seeks to correct position based on elapsed broadcast time
- Uses `BroadcastChannelManager` class to calculate positions
- **Playlist source**: if `<channel>/playlist.json` exists, channel order + repeats come from it. Otherwise the folder is scanned alphabetically (legacy behavior). The web UI is the editor — no manual JSON editing required.

### Legacy Mode (Fallback)
- Single playlist of all videos
- Channel number (1-13) = video index in playlist
- Sequential or random playback

## Players

`ProcessManager` picks a player per file by extension: video (`avi, mov, mkv, mp4, m4v, webm, flv, ts`) → MPV; NES (`nes, fds, nsf`) → RetroArch. Extension lists are configurable in `[mpv]` / `[retroarch]` sections of `video_looper.ini`.

### MPVPlayer (mpv.py)
Video playback using MPV with DRM/KMS output:
- `--vo=drm` for direct framebuffer output (no X11/Wayland needed)
- IPC socket (`/tmp/mpv-video-looper.sock`) for fast video switching
- 2-second startup grace period to prevent duplicate launches
- Supports seek position for broadcast mode synchronization

### RetroArchPlayer (retroarch.py)
NES emulation using RetroArch:
- Uses `gl` video driver with `kms` context for framebuffer output
- Nestopia libretro core for NES/FDS/NSF files
- ROM switching is kill+respawn — RetroArch's Network Control Interface has no `LOAD_CONTENT` (or equivalent) command, so a different ROM cannot be loaded into a running process. `_current_rom` tracks the active ROM path so a redundant `play()` for the same ROM is a no-op.
- 2-second startup grace period
- `stop()` is **SIGTERM-then-SIGKILL** with a 1.0s grace window (configurable via `[retroarch] stop_grace_seconds`). The graceful term is what makes `savestate_auto_save = true` actually flush a `.auto` file on channel switch — pair with `savestate_auto_load = true` (default) for resume-on-return.
- **Critical**: the pkill sweep after the kill must use blocking `subprocess.run()` (not `Popen`) to avoid race condition where pkill kills newly-started process
- `pkill` is PID-scoped (`pkill -P <looper_pid>`) so unrelated retroarch processes on the box aren't disturbed
- Save-state controller bindings: optional `[retroarch] save_state_btn` / `load_state_btn` / `enable_hotkey_btn` (joypad button indices). Empty by default; emitted into the override config only when set.

Config written to `/tmp/retroarch-video-looper.cfg`:
```ini
video_driver = "gl"
video_context_driver = "kms"
audio_driver = "alsa"
input_driver = "udev"
```

## Hardware Integration

### Rotary Encoder (Channel Selection)
- **Arduino** reads physical rotary encoder
- Sends channel (0-13) over **I2C bus** at address `0x8`
- Channel 0 = dead zone (ignored)
- `ChannelSwitcher` polls via an `EncoderBackend` (I2C in production, FIFO-backed mock in tests). Backend selected by `[encoder] backend = i2c|mock`.
- Startup default channel is **2** but `run()` reads the encoder synchronously before threads start, so the actual initial channel matches the dial position.

### GPIO Relays (RF Modulator Control)
| GPIO Pin | Purpose |
|----------|---------|
| 17 | Band selector relay |
| 22 | Frequency DOWN relay |
| 27 | Frequency UP relay |

- **Active-HIGH** logic; pulse width 30 ms with 30 ms gap between pulses
- Encoder thread publishes `(target_band, target_freq)` to `_relay_target_slot` (a `LatestSlot`); the **relay executor** thread takes the latest target and computes the minimum pulse train against tracked hardware state. Burst spins coalesce — no queued backlog.
- 2-second band-settle is a literal `time.sleep(2.0)` inside the executor between band-pulse and freq-pulse phases.
- State persisted to `/var/lib/video_looper/previous_values.pkl` (configurable via `[encoder] state_file`).

### Band System
- **Band 1**: Channels 2-6 (RF frequencies 2-6)
- **Band 2**: Channels 7-13 (RF frequencies 16-22)
- Channel 1 is unmapped (no relay activation)
- Modulator cycles through 5 bands (1→2→3→4→5→1)
- Code calculates minimum pulses to reach target band

## Important Classes

### ProcessManager (process_manager.py)
Main application class. Owns the worker; **main thread does not launch players**.
- Loads config; initializes MPV and RetroArch players (`_players`).
- Builds playlists (broadcast or legacy mode).
- Owns two `LatestSlot`s sharing one wake event:
  - `_channel_intent_slot: ChannelIntent` (encoder publishes; reasons `'startup' | 'channel'`)
  - `_eov_intent_slot: EovIntent` (main publishes when no player is playing)
- `_channel_worker` is the **sole owner of `self._active_player`** and the only caller of `_play_movie`. Drains channel-first so it always wins over eov on contention. Channel intents pass through a 150 ms settle window — fast encoder spins fold into one launch on the final value.
- Main loop just polls `is_playing()`, posts eov intents (idempotent via `_eov_pending` flag cleared by worker on consume), and watches the file reader for USB inserts.
- `run()` wraps the loop in `try/finally` that signals `_stop_event`, calls `ChannelSwitcher.stop()`, drains players, then `GPIO.cleanup()`.

### ChannelSwitcher (rotary.py)
Encoder + relay control.
- `read_remote_rotary_encoder()` delegates to `self._backend.read()` (I2C or mock).
- `change_channel()` reads the backend; if the channel changed and is mapped, publishes a relay target to `_relay_target_slot` and invokes `on_channel_change(channel, prev)` (which posts a `ChannelIntent` to the `ProcessManager`).
- `_relay_executor` thread: waits on `_relay_wake`, takes the latest target, computes a minimum delta from `previous_band` + `frequency_by_band`, drives GPIO pulses with the band-settle sleep between phases.
- `stop()` sets `_stop_event` so both the encoder loop and the relay executor exit cleanly.

### BroadcastChannelManager (model.py)
Synchronized playback:
- `calculate_broadcast_position(channel)` → returns `(movie, seek_offset)`
- Computes `position_in_loop = broadcast_time % total_channel_duration`
- Finds which video contains that position

### Playlist (model.py)
Video sequencing:
- `get_next(is_random, resume)` - next video (random or sequential)
- `set_next(index/filename)` - jump to specific video
- Supports repeat counts via filename pattern `_repeat_Nx`

## Entry Points

The single entry point is `Adafruit_Video_Looper.process_manager`:

```bash
python3 -m Adafruit_Video_Looper.process_manager                    # default /boot/video_looper.ini
python3 -m Adafruit_Video_Looper.process_manager /path/to/config.ini
```

This is what `run.sh` calls. The supervisor config in `assets/video_looper.conf` runs the same module.

## Configuration (video_looper.ini)

Key sections:
- `[process_manager]`: `file_reader`, `is_random`, `wait_time` (the `console_output` flag was dropped — logging now goes to `/tmp/video_looper.log` and stdout unconditionally)
- `[encoder]`: `backend` (`i2c` | `mock`), `mock_fifo`, `state_file`
- `[logging]`: `relay_debug` (when true, relay-pulse events log at DEBUG to the file handler)
- `[mpv]`: `extensions`, `sound`, `hwdec`, `drm_connector`, `extra_args`
- `[retroarch]`: `extensions`, `core_path`, `video_driver`, `video_context_driver`, `audio_driver`, ...
- `[directory]`: path for local directory mode
- `[usb_drive]`: `mount_path`, `readonly` (default `false` — required for the web UI to write `playlist.json`. Existing mounts won't pick up the change without remount/replug.)
- `[web]`: `enabled`, `port` (default 80), `bind` (default 0.0.0.0). When enabled, the looper starts a Starlette+uvicorn server in a daemon thread.

## Threading Model

Single Python process, five threads (with web UI enabled). GIL means only one thread runs Python bytecode at a time; multi-core utilization comes from subprocesses (mpv, retroarch).

- **`main`** (`process_manager.run`): polls `is_playing()`, posts eov intents to `_eov_intent_slot`, watches file reader. **Never launches players.**
- **`encoder`** (`ChannelSwitcher.start`): polls the encoder backend at ~5 ms, publishes `ChannelIntent` to `_channel_intent_slot` and `(band, freq)` to `_relay_target_slot` on changes.
- **`channel_worker`** (`ProcessManager._channel_worker`): waits on the shared worker wake event, takes channel-intent first then eov-intent then playlist-intent, applies the 150 ms settle window for channel intents, then calls `_play_movie`. Sole owner of subprocess spawns.
- **`relay_executor`** (`ChannelSwitcher._relay_executor`): waits on `_relay_wake`, takes latest target, computes min pulse train, drives GPIO. Re-takes after each train so mid-train target changes are absorbed.
- **`web`** (`web_server.start_web_thread`): runs uvicorn for the Starlette app. Reads `pm` state, publishes `PlaylistIntent` to `_playlist_intent_slot` after a successful playlist write. Never publishes channel intents — the encoder is authoritative.

All cross-thread communication is via `LatestSlot`s. **No `queue.Queue`, no module-global queues.** No `Lock` outside the slots' internal locks and the small `_eov_pending_lock`.

## DietPi Setup Notes

### Required /boot/config.txt setting:
```ini
dtoverlay=vc4-kms-v3d
```
**WARNING**: Common typo is `vc4-ksm-v3d` (letters swapped) - this will cause `/dev/dri/` to not exist and both MPV and RetroArch will fail.

### Verify DRM is working:
```bash
ls -la /dev/dri/
# Should show card0, card1, renderD128, etc.
```

## Dependencies
- `pyudev` - USB drive monitoring
- `smbus` - I2C communication
- `RPi.GPIO` - GPIO control
- `mpv` - video playback (system package)
- `retroarch` - emulation (system package)
- `nestopia_libretro.so` - NES core at `/usr/lib/aarch64-linux-gnu/libretro/`

## Web UI

Reachable at `http://<pi-ip>/` (port 80) on the LAN. **No authentication** — the network is trusted. Disable with `[web] enabled = false` in `/boot/video_looper.ini`.

What it can do:
- Read-only: current channel, per-channel playback position, log stream, USB storage usage.
- Edit: per-channel playlist (drag-drop reorder, repeat counts), file/folder rename + delete on USB, whitelisted looper INI keys (`is_random`, `wait_time`, `mpv.{sound,video_stretch,hwdec}`, `retroarch.{verbose,audio_enable}`, `logging.relay_debug`).
- Trigger: reboot the Pi.

What it does **not** do: switch channels (rotary encoder is the only input), edit network config, manage hostname/IP.

Saving config writes `/boot/video_looper.ini` then runs `sudo supervisorctl restart video_looper` after a 250 ms delay. The web server is in-process, so the UI's WebSocket disconnects during the restart and reconnects on the way back up. The UI shows a "RESTARTING" overlay during this window.

## Common Issues

### RetroArch restart loop
- **Cause**: Race condition where `pkill -9 retroarch` runs async and kills newly-started process
- **Fix**: Use `subprocess.run()` (blocking) instead of `subprocess.Popen()` for pkill

### XDG_RUNTIME_DIR errors
- **Cause**: Missing DRM/KMS kernel drivers
- **Fix**: Ensure `dtoverlay=vc4-kms-v3d` in `/boot/config.txt` (check for typos!)

### Channel switching not working
- Check I2C: `i2cget -y 1 0x8` and turn knob.
- Check `[encoder] backend` in `/boot/video_looper.ini` — if it's left at `mock` from a test run, the I2C path is bypassed and the looper is reading from a FIFO no one is writing to.
- Tail the structured log: `tail -f /tmp/video_looper.log | grep -E 'publish|handle|start|stop'`. Expect a `looper.encoder publish target=N prev=M` followed by `looper.worker handle channel=N ...` and a player `start` line.


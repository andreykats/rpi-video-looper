# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Summary
A Raspberry Pi video looper for **retro TV simulation**. Based on Adafruit Video Looper, heavily customized with:
- 13-channel broadcast TV simulation with synchronized playback
- RF modulator hardware control via GPIO relays
- Rotary encoder channel switching via I2C
- Multi-player support: MPV for video, RetroArch for NES ROMs

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
This project has **no test suite** — no pytest, unittest, or `tests/` directory. Verification is done on-device.

## Key Files

| File | Purpose |
|------|---------|
| `Adafruit_Video_Looper/process_manager.py` | Main orchestrator - `ProcessManager` class, main loop, playlist building |
| `Adafruit_Video_Looper/model.py` | Data models: `Movie`, `Playlist`, `BroadcastChannelManager` |
| `Adafruit_Video_Looper/rotary.py` | `ChannelSwitcher` class, relay control, I2C communication |
| `Adafruit_Video_Looper/mpv.py` | MPV video player wrapper (DRM output, IPC for fast switching) |
| `Adafruit_Video_Looper/retroarch.py` | RetroArch emulator wrapper (NES ROMs via Nestopia core) |
| `Adafruit_Video_Looper/usb_drive.py` | USB drive file reader with pyudev monitoring |
| `Adafruit_Video_Looper/directory.py` | Local directory file reader |
| `assets/video_looper.ini` | Default configuration template |
| `assets/video_looper.conf` | Supervisor program definition (installed to `/etc/supervisor/conf.d/`) |
| `/boot/video_looper.ini` | **Runtime config location** (on Pi) |
| `install.sh` | One-shot Pi setup — system pkgs, pip install, supervisor, I2C, DRM overlay |
| `run.sh` / `enable.sh` / `disable.sh` / `reload.sh` | Foreground run / supervisor autostart toggles / restart |

## Two Operating Modes

### Broadcast Mode (Primary)
- **Trigger**: USB drive contains numbered folders `1/` through `13/`
- Each folder = one channel with its own playlist
- **Synchronized playback**: All channels share a global start time
- Switching channels seeks to correct position based on elapsed broadcast time
- Uses `BroadcastChannelManager` class to calculate positions

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
- UDP network commands (port 55355) for ROM switching
- 2-second startup grace period
- **Critical**: `stop()` must use blocking `subprocess.run()` (not `Popen`) to avoid race condition where pkill kills newly-started process

Config written to `/tmp/retroarch-video-looper.cfg`:
```ini
video_driver = "gl"
video_context_driver = "kms"
audio_driver = "alsa"
input_driver = "udev"
network_cmd_enable = "true"
network_cmd_port = "55355"
```

## Hardware Integration

### Rotary Encoder (Channel Selection)
- **Arduino** reads physical rotary encoder
- Sends channel (0-13) over **I2C bus** at address `0x8`
- Channel 0 = dead zone (ignored)
- `ChannelSwitcher` class polls I2C continuously
- Startup default channel is **2** (`process_manager.py`, `self._current_channel = 2`)

### GPIO Relays (RF Modulator Control)
| GPIO Pin | Purpose |
|----------|---------|
| 17 | Band selector relay |
| 22 | Frequency DOWN relay |
| 27 | Frequency UP relay |

- **Active-HIGH** logic
- Thread-safe queue with 30ms delays between pulses
- State persisted to `previous_values.pkl`

### Band System
- **Band 1**: Channels 2-6 (RF frequencies 2-6)
- **Band 2**: Channels 7-13 (RF frequencies 16-22)
- Channel 1 is unmapped (no relay activation)
- Modulator cycles through 5 bands (1→2→3→4→5→1)
- Code calculates minimum pulses to reach target band

## Important Classes

### ProcessManager (process_manager.py)
Main application class:
- Loads config from INI file
- Initializes MPV and RetroArch players
- Selects player based on file extension (content_type)
- Builds playlists (broadcast or legacy mode)
- Main loop: checks player status, handles channel changes, USB insertion

### ChannelSwitcher (rotary.py)
Hardware control:
- `read_remote_rotary_encoder()` - polls I2C at address 0x8
- `on_channel_change` callback - triggers player switch
- `relay_channel_up/down()` - queue frequency relay pulses
- `relay_band_press()` - queue band relay pulses
- `execute_relay_commands()` - daemon thread consuming relay queue

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

This is what `run.sh` calls.

The supervisor config (`assets/video_looper.conf`) currently runs `python3 -u -m Adafruit_Video_Looper.video_looper`, but no `video_looper.py` exists in `Adafruit_Video_Looper/` — only `process_manager.py`. If autostart is broken on the Pi, the supervisor command likely needs updating to `Adafruit_Video_Looper.process_manager`.

## Configuration (video_looper.ini)

Key sections:
- `[process_manager]`: file_reader, console_output, is_random, wait_time
- `[mpv]`: extensions, sound, hwdec, drm_connector, extra_args
- `[retroarch]`: extensions, core_path, video_driver, video_context_driver
- `[directory]`: path for local directory mode
- `[usb_drive]`: mount path for USB drive mode

## Threading Model
- **Main thread**: main loop checking player status
- **Channel switcher**: daemon thread polling I2C
- **Relay executor**: daemon thread processing relay queue

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

## Common Issues

### RetroArch restart loop
- **Cause**: Race condition where `pkill -9 retroarch` runs async and kills newly-started process
- **Fix**: Use `subprocess.run()` (blocking) instead of `subprocess.Popen()` for pkill

### XDG_RUNTIME_DIR errors
- **Cause**: Missing DRM/KMS kernel drivers
- **Fix**: Ensure `dtoverlay=vc4-kms-v3d` in `/boot/config.txt` (check for typos!)

### Channel switching not working
- Check I2C: `i2cget -y 1 0x8` and turn knob
- Check logs for "Switching from channel X to Y" messages

## Known Bugs (Backlog)

### Modulator desync on first entry to band 2 (rotary.py)
- **Symptom**: After cycling channels including channel 7+, the TV's actual RF channel no longer matches the looper's intended channel. Looper logs `Tuning UP from 7 to 16 on band 2 (9 pulses)` on first band-2 entry — band 2 only has 7 RF positions (16–22), so 9 UP pulses overshoot and wrap.
- **Root cause** (`Adafruit_Video_Looper/rotary.py`): `band_start_frequencies = {1: 2, 2: 7}` resets `frequency_by_band[2]` to 7 on first entry, but `CHANNEL_MAP[7] = (2, 16)` targets RF 16. The numbering scheme used in `band_start_frequencies` (and the initial `frequency_by_band` value, and `default_frequencies` in `load_previous_values`) is inconsistent with the RF numbering in `CHANNEL_MAP` for band 2.
- **Fix**: Change band 2 starts to 16 in three places — `__init__` (`self.frequency_by_band = {1: 2, 2: 16}`), `_switch_to_band` (`band_start_frequencies = {1: 2, 2: 16}`), and `load_previous_values` (`default_frequencies = {1: 2, 2: 16}`). The misleading "Band 2: RF 7-13 (hardware internal), maps to RF 16-22" comment should be removed; band 2 is RF 16–22 throughout.
- **Trigger to repro**: Boot, navigate to any band-1 channel (2–6), then to a band-2 channel (7–13). Subsequent rotation across both bands will display the wrong TV channel even though the looper plays the right video.

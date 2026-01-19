# RPI Video Looper - Claude Reference

## Project Summary
A Raspberry Pi video looper for **retro TV simulation**. Based on Adafruit Video Looper, heavily customized with:
- 13-channel broadcast TV simulation with synchronized playback
- RF modulator hardware control via GPIO relays
- Rotary encoder channel switching via I2C
- Multi-player support: MPV for video, RetroArch for NES ROMs

**Target Hardware**: Raspberry Pi 5 (runs on remote Pi, NOT this development machine)
**OS**: DietPi (requires `dtoverlay=vc4-kms-v3d` in `/boot/config.txt` for DRM/KMS)

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
| `/boot/video_looper.ini` | **Runtime config location** (on Pi) |

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

```bash
# Start the process manager
python3 -m Adafruit_Video_Looper.process_manager

# Or with custom config
python3 -m Adafruit_Video_Looper.process_manager /path/to/config.ini
```

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

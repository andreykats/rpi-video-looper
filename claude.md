# RPI Video Looper - Claude Reference

## Project Summary
A Raspberry Pi video looper for **retro TV simulation**. Based on Adafruit Video Looper, heavily customized with:
- 13-channel broadcast TV simulation with synchronized playback
- RF modulator hardware control via GPIO relays
- Rotary encoder channel switching via I2C

**Target Hardware**: Raspberry Pi (runs on remote Pi, NOT this development machine)
**OS Requirement**: Raspberry Pi OS Legacy (Buster) - omxplayer is deprecated on newer versions

## Key Files

| File | Purpose |
|------|---------|
| `Adafruit_Video_Looper/video_looper.py` | Main orchestrator - `VideoLooper` class, main loop, playlist building |
| `Adafruit_Video_Looper/model.py` | Data models: `Movie`, `Playlist`, `BroadcastChannelManager` |
| `Adafruit_Video_Looper/rotary.py` | `ChannelSwitcher` class, relay control, I2C communication |
| `Adafruit_Video_Looper/omxplayer.py` | Primary video player wrapper (subprocess-based) |
| `Adafruit_Video_Looper/hello_video.py` | Lightweight H264-only player alternative |
| `Adafruit_Video_Looper/image_player.py` | Static image slideshow player |
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
| 22 | Frequency UP relay |
| 27 | Frequency DOWN relay |

- **Active-HIGH** logic
- Thread-safe queue with 30ms delays between pulses
- State persisted to `previous_values.pkl`

### Band System
- **Band 1**: Channels 1-6
- **Band 2**: Channels 7-13
- Modulator cycles through 4 bands (1→2→3→4→1)
- Code calculates minimum pulses to reach target band

## Important Classes

### VideoLooper (video_looper.py)
Main application class:
- Loads config from INI file
- Initializes pygame display
- Dynamically loads player and file reader modules
- Builds playlists (broadcast or legacy mode)
- Main loop: checks player status, handles channel changes, USB insertion

### ChannelSwitcher (rotary.py)
Hardware control:
- `read_remote_rotary_encoder()` - polls I2C
- `_handle_channel_change()` - callback on channel change
- `relay_channel_up/down()` - queue frequency relay pulses
- `relay_band_press()` - queue band relay pulses
- `execute_relay_commands()` - thread consuming relay queue

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
# Start the looper
./run.sh  # runs: python3 -m Adafruit_Video_Looper.video_looper

# Or directly
python3 -m Adafruit_Video_Looper.video_looper
```

## Configuration (video_looper.ini)

Key sections:
- `[video_looper]`: player, file_reader, osd, wait_time, bgcolor, etc.
- `[control]`: keyboard_control, gpio_pin_map
- `[omxplayer]`: extensions, sound output, extra_args
- `[directory]`: path for local directory mode
- `[playlist]`: M3U playlist path

## Keyboard Controls (if enabled)
| Key | Action |
|-----|--------|
| ESC | Quit |
| K | Skip to next video |
| B | Go back one video |
| SPACE | Pause/resume |
| S | Stop/resume playback |
| P | Shutdown system |
| O/I | Next/previous chapter |

## Threading Model
- **Main thread**: pygame display loop, video playback
- **Keyboard handler**: daemon thread (if enabled)
- **Channel switcher**: daemon thread polling I2C
- **Relay executor**: daemon thread processing relay queue

## Recent Custom Features
1. **Broadcast TV mode** with time-synchronized 13 channels
2. **Band relay system** for RF modulator frequency control
3. **Channel-to-frequency mapping** with selective tuning
4. **State persistence** via pickle file for relay state

## Dependencies
- `pygame` - display and input
- `pyudev` - USB drive monitoring
- `smbus` - I2C communication
- `RPi.GPIO` - GPIO control (on Pi only)
- `omxplayer` - video playback (legacy Pi OS only)

# Copyright 2024
# License: GNU GPLv2, see LICENSE.txt
import logging
import os
import select
import subprocess
import time
import socket
import json

SOCKET_PATH = '/tmp/mpv-video-looper.sock'

log = logging.getLogger('looper.mpv')


def _blank_console():
    """Blank the console to hide TTY during player transitions."""
    try:
        with open('/dev/tty1', 'w') as tty:
            tty.write('\033[?25l')  # Hide cursor
            tty.write('\033[2J')     # Clear screen
            tty.write('\033[H')      # Home cursor
    except (IOError, PermissionError):
        pass  # Not running on console or no permission


class MPVPlayer:

    def __init__(self, config):
        """Create an instance of a video player that runs mpv in the background."""
        self._process = None
        self._ipc_sock = None
        self._play_requested_time = 0  # Prevent duplicate plays during startup
        self._load_config(config)

    def _load_config(self, config):
        """Load configuration from INI file."""
        self._extensions = config.get('mpv', 'extensions') \
                                 .translate(str.maketrans('', '', ' \t\r\n.')) \
                                 .split(',')
        self._extra_args = config.get('mpv', 'extra_args').split()
        self._sound = config.get('mpv', 'sound').lower()
        self._hwdec = config.get('mpv', 'hwdec', fallback='auto')
        self._drm_connector = config.get('mpv', 'drm_connector', fallback='')
        self._video_stretch = config.getboolean('mpv', 'video_stretch', fallback=False)

    def supported_extensions(self):
        """Return list of supported file extensions."""
        return self._extensions

    def _convert_volume(self, millibels):
        """Convert omxplayer-style millibels to mpv percentage (0-100).

        omxplayer uses millibels where 0 = full volume, -6000 = silent.
        mpv uses 0-100 percentage scale.
        """
        if millibels >= 0:
            return 100
        elif millibels <= -6000:
            return 0
        # Linear approximation: -6000mB = 0%, 0mB = 100%
        return int(100 + (millibels / 60))

    def _cleanup_socket(self):
        """Remove stale IPC socket file."""
        if os.path.exists(SOCKET_PATH):
            try:
                os.remove(SOCKET_PATH)
            except OSError:
                pass

    def _connect_ipc(self, timeout=1.0):
        """Connect to mpv IPC socket with retry.

        MPV takes a moment to create the IPC socket after starting.
        """
        start = time.time()
        while time.time() - start < timeout:
            if os.path.exists(SOCKET_PATH):
                try:
                    self._ipc_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    self._ipc_sock.connect(SOCKET_PATH)
                    self._ipc_sock.setblocking(False)
                    return True
                except (socket.error, OSError):
                    self._ipc_sock = None
            time.sleep(0.05)
        return False

    def _send_ipc_command(self, command, *args):
        """Send command to mpv via IPC socket."""
        if self._ipc_sock is None:
            return False
        try:
            msg = {"command": [command] + list(args)}
            self._ipc_sock.send((json.dumps(msg) + '\n').encode())
            return True
        except (socket.error, OSError, BrokenPipeError):
            return False

    def _send_ipc_command_verified(self, command, *args):
        """Send command to mpv via IPC and verify response.

        Returns True only if MPV confirms command was received and processed.
        MPV sends async events on the same socket, so we skip those and wait
        for the actual command response.
        """
        if self._ipc_sock is None:
            return False
        try:
            msg = {"command": [command] + list(args)}
            self._ipc_sock.send((json.dumps(msg) + '\n').encode())

            # Read responses until we get command result (skip async events)
            # MPV events have 'event' key, command responses have 'error' key
            deadline = time.time() + 0.1
            buffer = b''

            while time.time() < deadline:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break

                ready, _, _ = select.select([self._ipc_sock], [], [], min(remaining, 0.1))
                if not ready:
                    continue

                try:
                    chunk = self._ipc_sock.recv(4096)
                    if not chunk:
                        break
                    buffer += chunk
                except (socket.error, BlockingIOError):
                    continue

                # Process complete lines
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    if not line.strip():
                        continue
                    try:
                        result = json.loads(line.decode())
                        # Skip async events (they have 'event' key)
                        if 'event' in result:
                            continue
                        # This is a command response
                        if result.get('error') == 'success':
                            return True
                        else:
                            log.warning('ipc command failed: %s', result)
                            return False
                    except json.JSONDecodeError:
                        continue

            log.warning('ipc timeout waiting for response')
            return False
        except (socket.error, OSError, BrokenPipeError) as e:
            log.warning('ipc socket error: %s', e)
            self._ipc_sock = None  # Mark socket as dead
            return False

    def _load_via_ipc(self, movie, seek_position=None):
        """Load new video via IPC without restarting mpv.

        Returns True if successful, False if IPC failed.
        """
        try:
            # Use loadfile command to replace current video
            # MPV JSON IPC format: loadfile <url> [<flags> [<index> [<options>]]]
            # Options must be a dict, not a string
            if seek_position and seek_position > 0:
                # With seek position: pass options as dict with index=-1
                success = self._send_ipc_command_verified(
                    'loadfile', movie.target, 'replace', -1,
                    {'start': str(int(seek_position))}
                )
            else:
                success = self._send_ipc_command_verified('loadfile', movie.target, 'replace')

            if success:
                log.info('ipc-load file=%s', movie.filename)
            else:
                log.warning('ipc-load failed, will restart')
            return success
        except Exception as e:
            log.exception('ipc exception: %s', e)
            return False

    def play(self, movie, loop=None, vol=0, seek_position=None):
        """Play the provided movie file, optionally looping it repeatedly.

        Args:
            movie: Movie object to play (has .target for file path, .repeats for loop count)
            loop: Loop count (-1 for infinite). If None, uses movie.repeats
            vol: Volume in millibels (omxplayer compatibility, converted to percentage)
            seek_position: Seek to this position in seconds (for broadcast mode)
        """
        # Try IPC if mpv is already running (fast channel switching)
        if self.is_playing():
            if self._ipc_sock:
                if self._load_via_ipc(movie, seek_position):
                    return  # Success - no need to restart
            else:
                # Socket not ready - try to connect (process may have created it late)
                if self._connect_ipc(timeout=0.5):
                    if self._load_via_ipc(movie, seek_position):
                        return  # Success after reconnect
                # Socket still not available - fall through to kill and restart
                log.info('ipc socket unavailable, restarting player')

        # Fallback: kill and restart mpv
        self.stop()
        self._cleanup_socket()

        # Mark play as requested to prevent duplicate calls during startup
        self._play_requested_time = time.time()

        # Build command arguments
        args = ['mpv']
        args.extend(['--vo=drm'])
        args.extend(['--hwdec={}'.format(self._hwdec)])
        args.extend(['--fullscreen'])
        args.extend(['--input-ipc-server={}'.format(SOCKET_PATH)])
        args.extend(['--no-osc'])
        args.extend(['--no-input-default-bindings'])
        args.extend(['--keep-open=no'])

        # Force ALSA when the config selects an HDMI/local/alsa output. mpv's
        # default autodetection probes PipeWire and JACK first; on this Pi
        # neither is running, and PipeWire's failure path can wedge the ALSA
        # device in EOWNERDEAD until reboot. Going straight to ALSA avoids it.
        if self._sound in ('hdmi', 'local', 'alsa'):
            args.extend(['--ao=alsa'])

        # If audio init still fails for any reason, fall back to silent
        # playback instead of exiting — keeps the looper from spinning on a
        # transient audio problem.
        args.extend(['--audio-fallback-to-null=yes'])

        # Video stretch - ignore aspect ratio to fill screen (no black bars)
        if self._video_stretch:
            args.extend(['--no-keepaspect'])

        # Handle DRM connector if specified
        if self._drm_connector:
            args.extend(['--drm-connector={}'.format(self._drm_connector)])

        # Handle seek position (broadcast mode)
        if seek_position is not None and seek_position > 0:
            args.extend(['--start={}'.format(int(seek_position))])

        # Handle volume (convert millibels to percentage)
        volume_pct = self._convert_volume(vol)
        args.extend(['--volume={}'.format(volume_pct)])

        # Handle looping
        if loop is None:
            loop = movie.repeats
        if loop <= -1:
            args.extend(['--loop-file=inf'])
        elif loop > 1:
            args.extend(['--loop-file={}'.format(loop)])

        # Add extra args from config
        if self._extra_args:
            args.extend(self._extra_args)

        # Add movie file path
        args.append(movie.target)

        log.debug('mpv command: %s', ' '.join(args))

        # MPV's audio backends and DRM context use XDG_RUNTIME_DIR for
        # runtime sockets; supervisor's environment doesn't include it.
        # Pass an explicit env so the value is guaranteed to reach the child.
        env = os.environ.copy()
        env.setdefault('XDG_RUNTIME_DIR', '/run/user/{0}'.format(os.geteuid()))

        # Start mpv process (show stderr for debugging)
        self._process = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=None,  # Show errors for debugging
            stdin=subprocess.DEVNULL,
            close_fds=True,
            env=env,
        )
        log.info('start pid=%d file=%s seek=%s',
                 self._process.pid, movie.filename,
                 int(seek_position) if seek_position else 0)

        # IPC socket is connected lazily on first use (see _load_via_ipc /
        # reconnect path). Skipping the synchronous wait here keeps the worker
        # responsive to the next queued channel change.

    def pause(self):
        """Toggle pause/resume."""
        self._send_ipc_command('cycle', 'pause')

    def sendKey(self, key: str):
        """Send control key for chapter navigation.

        Args:
            key: 'o' for next chapter, 'i' for previous chapter
        """
        if key.lower() == 'o':
            self._send_ipc_command('add', 'chapter', 1)
        elif key.lower() == 'i':
            self._send_ipc_command('add', 'chapter', -1)

    def is_playing(self):
        """Return true if the video player is running, false otherwise.

        Called frequently in main loop - must be lightweight!
        Also returns True during startup grace period to prevent duplicate plays.
        """
        # During startup, return True to prevent duplicate play calls
        if time.time() - self._play_requested_time < 2.0:
            return True

        # Capture local reference to avoid race condition with stop()
        process = self._process
        if process is None:
            return False
        process.poll()
        return process.returncode is None

    def stop(self, block=True):
        """Stop the video player. Always non-blocking for fast channel switching."""
        if self._process is None and self._ipc_sock is None:
            return  # nothing to stop

        reason = 'kill'
        # Reset play request time so new plays can happen immediately
        self._play_requested_time = 0

        # Blank console to hide TTY during transition
        _blank_console()

        # Try graceful quit via IPC
        if self._ipc_sock:
            try:
                self._send_ipc_command('quit')
                reason = 'ipc'
            except:
                pass
            try:
                self._ipc_sock.close()
            except:
                pass
            self._ipc_sock = None

        # Kill all mpv processes (non-blocking like omxplayer)
        subprocess.Popen(['pkill', '-9', 'mpv'],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        log.info('stop reason=%s', reason)

        # Let the process reference be garbage collected
        self._process = None

    @staticmethod
    def can_loop_count():
        """MPV handles loop counting internally."""
        return True


def create_player(config, **kwargs):
    """Create new video player based on mpv."""
    return MPVPlayer(config)

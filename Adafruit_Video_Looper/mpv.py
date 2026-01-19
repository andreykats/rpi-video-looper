# Copyright 2024
# License: GNU GPLv2, see LICENSE.txt
import os
import subprocess
import time
import socket
import json

SOCKET_PATH = '/tmp/mpv-video-looper.sock'


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

    def _load_via_ipc(self, movie, seek_position=None):
        """Load new video via IPC without restarting mpv.

        Returns True if successful, False if IPC failed.
        """
        try:
            # Use loadfile command to replace current video
            # Format: loadfile <path> [replace|append] [options]
            if seek_position and seek_position > 0:
                # With seek position: loadfile path replace start=N
                options = 'start={}'.format(int(seek_position))
                success = self._send_ipc_command('loadfile', movie.target, 'replace', options)
            else:
                success = self._send_ipc_command('loadfile', movie.target, 'replace')

            if success:
                print('MPV: Loaded {} via IPC'.format(movie.filename))
            return success
        except Exception:
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
        if self.is_playing() and self._ipc_sock:
            if self._load_via_ipc(movie, seek_position):
                return  # Success - no need to restart

        # Fallback: kill and restart mpv
        self.stop()
        self._cleanup_socket()

        # Build command arguments
        args = ['mpv']
        args.extend(['--vo=drm'])
        args.extend(['--hwdec={}'.format(self._hwdec)])
        args.extend(['--fullscreen'])
        args.extend(['--input-ipc-server={}'.format(SOCKET_PATH)])
        args.extend(['--no-osc'])
        args.extend(['--no-input-default-bindings'])
        args.extend(['--keep-open=no'])
        args.extend(['--video-aspect-override=no'])  # Stretch to fill (like omxplayer)

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

        # Debug: print the command being run
        print("MPV command: {}".format(' '.join(args)))

        # Start mpv process (show stderr for debugging)
        self._process = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=None,  # Show errors for debugging
            stdin=subprocess.DEVNULL,
            close_fds=True
        )

        # Connect to IPC socket (non-blocking, best effort)
        # Playback starts even if IPC connection fails
        self._connect_ipc(timeout=1.0)

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

        Called every 2ms in main loop - must be lightweight!
        """
        # Capture local reference to avoid race condition with stop()
        process = self._process
        if process is None:
            return False
        process.poll()
        return process.returncode is None

    def stop(self):
        """Stop the video player. Non-blocking for fast channel switching."""
        # Blank console to hide TTY during transition
        _blank_console()

        # Try graceful quit via IPC
        if self._ipc_sock:
            try:
                self._send_ipc_command('quit')
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

        # Let the process reference be garbage collected
        self._process = None

    @staticmethod
    def can_loop_count():
        """MPV handles loop counting internally."""
        return True


def create_player(config, **kwargs):
    """Create new video player based on mpv."""
    return MPVPlayer(config)

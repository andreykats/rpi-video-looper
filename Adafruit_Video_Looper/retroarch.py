import socket
import subprocess
import time

OVERRIDE_CONFIG_PATH = '/tmp/retroarch-video-looper.cfg'
RETROARCH_CMD_PORT = 55355


def _blank_console():
    """Blank the console to hide TTY during player transitions."""
    try:
        with open('/dev/tty1', 'w') as tty:
            tty.write('\033[?25l')  # Hide cursor
            tty.write('\033[2J')     # Clear screen
            tty.write('\033[H')      # Home cursor
    except (IOError, PermissionError):
        pass  # Not running on console or no permission


class RetroArchPlayer:
    """Player for NES ROMs using RetroArch."""

    def __init__(self, config):
        """Create an instance of a player that runs RetroArch in the background."""
        self._process = None
        self._play_requested_time = 0  # Prevent duplicate plays during startup
        self._load_config(config)

    def _load_config(self, config):
        """Load configuration from INI file."""
        self._extensions = config.get('retroarch', 'extensions') \
                                 .translate(str.maketrans('', '', ' \t\r\n.')) \
                                 .split(',')
        self._core_path = config.get('retroarch', 'core_path', fallback='').strip()
        self._video_driver = config.get('retroarch', 'video_driver', fallback='gl').strip()
        self._video_context_driver = config.get('retroarch', 'video_context_driver', fallback='kms').strip()
        self._audio_driver = config.get('retroarch', 'audio_driver', fallback='alsa').strip()
        self._input_driver = config.get('retroarch', 'input_driver', fallback='udev').strip()
        self._fullscreen = config.getboolean('retroarch', 'fullscreen', fallback=True)
        self._video_scale_integer = config.getboolean('retroarch', 'video_scale_integer', fallback=False)
        self._video_force_aspect = config.getboolean('retroarch', 'video_force_aspect', fallback=False)
        self._audio_enable = config.getboolean('retroarch', 'audio_enable', fallback=True)
        self._autosave_interval = config.getint('retroarch', 'autosave_interval', fallback=0)
        self._savestate_auto_load = config.getboolean('retroarch', 'savestate_auto_load', fallback=True)
        self._extra_args = config.get('retroarch', 'extra_args', fallback='').split()

    def supported_extensions(self):
        """Return list of supported file extensions."""
        return self._extensions

    def _send_udp_command(self, command):
        """Send command to RetroArch via UDP network interface."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(0.1)
            sock.sendto(command.encode(), ('127.0.0.1', RETROARCH_CMD_PORT))
            sock.close()
            return True
        except Exception:
            return False

    def _load_via_network(self, rom_path, filename):
        """Load new ROM via network command without restarting RetroArch.

        Returns True if successful, False if command failed.
        """
        try:
            if self._send_udp_command('LOAD_CONTENT {}'.format(rom_path)):
                print('RetroArch: Loaded {} via network command'.format(filename))
                return True
            return False
        except Exception:
            return False

    def _in_grace_period(self):
        """Check if we're still in the startup grace period."""
        return time.time() - self._play_requested_time < 2.0

    def play(self, movie, loop=None, vol=0, seek_position=None):
        """Start RetroArch with the provided ROM file.

        Note: loop, vol, and seek_position are ignored for ROMs.
        """
        # During grace period, don't try to restart or send commands
        if self._in_grace_period():
            print('RetroArch: Ignoring play() during startup grace period')
            return

        # Try network command if RetroArch is already running (fast ROM switching)
        if self.is_playing():
            if self._load_via_network(movie.target, movie.filename):
                return  # Success - no need to restart
            # Network command failed - fall through to kill and restart
            print('RetroArch: Network command failed, will restart')

        # Kill and restart RetroArch
        self.stop()

        # Mark play as requested to prevent duplicate calls during startup
        self._play_requested_time = time.time()

        if not self._core_path:
            print("RetroArch error: core_path is not set in [retroarch].")
            return

        args = ['retroarch']
        args.extend(['--appendconfig', self._write_override_config()])

        args.extend(['-L', self._core_path])

        if self._extra_args:
            args.extend(self._extra_args)

        args.append(movie.target)

        print("RetroArch command: {}".format(' '.join(args)))

        # Capture stderr to file for debugging
        stderr_file = open('/tmp/retroarch-stderr.log', 'w')
        self._process = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=stderr_file,
            stdin=subprocess.DEVNULL,
            close_fds=True
        )

    def _write_override_config(self):
        """Write a minimal RetroArch override config for this launch."""
        fullscreen = 'true' if self._fullscreen else 'false'
        scale_integer = 'true' if self._video_scale_integer else 'false'
        force_aspect = 'true' if self._video_force_aspect else 'false'
        audio_enable = 'true' if self._audio_enable else 'false'
        savestate_auto_load = 'true' if self._savestate_auto_load else 'false'
        lines = [
            'video_driver = "{}"'.format(self._video_driver),
            'video_context_driver = "{}"'.format(self._video_context_driver),
            'audio_driver = "{}"'.format(self._audio_driver),
            'input_driver = "{}"'.format(self._input_driver),
            'video_fullscreen = "{}"'.format(fullscreen),
            # Video scaling options for full screen stretch
            'video_scale_integer = "{}"'.format(scale_integer),
            'video_force_aspect = "{}"'.format(force_aspect),
            # aspect_ratio_index: 22 = Full (stretch to fill screen)
            'aspect_ratio_index = 22',
            # Audio
            'audio_enable = "{}"'.format(audio_enable),
            # Save states
            'savestate_auto_load = "{}"'.format(savestate_auto_load),
            'autosave_interval = {}'.format(self._autosave_interval),
            # Enable network commands for fast ROM switching
            'network_cmd_enable = "true"',
            'network_cmd_port = "{}"'.format(RETROARCH_CMD_PORT),
        ]
        with open(OVERRIDE_CONFIG_PATH, 'w') as handle:
            handle.write('\n'.join(lines) + '\n')
        return OVERRIDE_CONFIG_PATH

    def pause(self):
        """Pause not directly supported for emulator."""
        pass

    def sendKey(self, key: str):
        """Key sending not supported."""
        pass

    def is_playing(self):
        """Return true if RetroArch is running, false otherwise.

        Called frequently in main loop - must be lightweight!
        Also returns True during startup grace period to prevent duplicate plays.
        """
        # During startup, return True to prevent duplicate play calls
        grace_remaining = 2.0 - (time.time() - self._play_requested_time)
        if grace_remaining > 0:
            return True

        process = self._process
        if process is None:
            return False
        process.poll()
        if process.returncode is not None:
            print(f"RetroArch exited with code: {process.returncode}")
        return process.returncode is None

    def stop(self, block_timeout_sec=0):
        """Stop RetroArch."""
        # Reset play request time so new plays can happen immediately
        self._play_requested_time = 0

        # Blank console to hide TTY during transition
        _blank_console()

        # Kill all retroarch processes - MUST be blocking to avoid race condition
        # where pkill kills the newly started retroarch
        subprocess.run(['pkill', '-9', 'retroarch'],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._process = None

    @staticmethod
    def can_loop_count():
        """Emulator doesn't handle loop counting."""
        return False


def create_player(config, **kwargs):
    """Create new RetroArch player."""
    return RetroArchPlayer(config)

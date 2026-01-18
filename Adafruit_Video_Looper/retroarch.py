import subprocess

OVERRIDE_CONFIG_PATH = '/tmp/retroarch-video-looper.cfg'


class RetroArchPlayer:
    """Player for NES ROMs using RetroArch."""

    def __init__(self, config):
        """Create an instance of a player that runs RetroArch in the background."""
        self._process = None
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
        self._extra_args = config.get('retroarch', 'extra_args', fallback='').split()

    def supported_extensions(self):
        """Return list of supported file extensions."""
        return self._extensions

    def play(self, movie, loop=None, vol=0, seek_position=None):
        """Start RetroArch with the provided ROM file.

        Note: loop, vol, and seek_position are ignored for ROMs.
        """
        self.stop()

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

        self._process = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True
        )

    def _write_override_config(self):
        """Write a minimal RetroArch override config for this launch."""
        fullscreen = 'true' if self._fullscreen else 'false'
        lines = [
            'video_driver = "{}"'.format(self._video_driver),
            'video_context_driver = "{}"'.format(self._video_context_driver),
            'audio_driver = "{}"'.format(self._audio_driver),
            'input_driver = "{}"'.format(self._input_driver),
            'video_fullscreen = "{}"'.format(fullscreen),
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
        """
        process = self._process
        if process is None:
            return False
        process.poll()
        return process.returncode is None

    def stop(self, block_timeout_sec=0):
        """Stop RetroArch. Non-blocking for fast channel switching."""
        subprocess.Popen(['pkill', '-9', 'retroarch'],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._process = None

    @staticmethod
    def can_loop_count():
        """Emulator doesn't handle loop counting."""
        return False


def create_player(config, **kwargs):
    """Create new RetroArch player."""
    return RetroArchPlayer(config)

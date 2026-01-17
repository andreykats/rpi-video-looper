# Copyright 2024
# License: GNU GPLv2, see LICENSE.txt
import subprocess


class FCEUXPlayer:
    """Player for NES ROMs using fceux emulator."""

    def __init__(self, config):
        """Create an instance of a player that runs fceux in the background."""
        self._process = None
        self._load_config(config)

    def _load_config(self, config):
        """Load configuration from INI file."""
        self._extensions = config.get('fceux', 'extensions') \
                                 .translate(str.maketrans('', '', ' \t\r\n.')) \
                                 .split(',')
        self._extra_args = config.get('fceux', 'extra_args', fallback='').split()
        self._fullscreen = config.getboolean('fceux', 'fullscreen', fallback=True)

    def supported_extensions(self):
        """Return list of supported file extensions."""
        return self._extensions

    def play(self, movie, loop=None, vol=0, seek_position=None):
        """Start fceux with the provided ROM file.

        Note: seek_position is ignored for ROMs (can't seek into emulation)
        """
        self.stop()

        # Build command arguments
        args = ['fceux']

        if self._fullscreen:
            args.extend(['--fullscreen', '1'])

        # fceux-specific options
        args.extend(['--nogui', '1'])
        args.extend(['--sound', '1'])

        # Add extra args from config
        if self._extra_args:
            args.extend(self._extra_args)

        # Add ROM file path
        args.append(movie.target)

        # Debug: print the command being run
        print("FCEUX command: {}".format(' '.join(args)))

        # Start fceux process
        self._process = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True
        )

    def pause(self):
        """Pause not directly supported for emulator."""
        pass

    def sendKey(self, key: str):
        """Key sending not supported."""
        pass

    def is_playing(self):
        """Return true if fceux is running, false otherwise.

        Called frequently in main loop - must be lightweight!
        """
        process = self._process
        if process is None:
            return False
        process.poll()
        return process.returncode is None

    def stop(self, block_timeout_sec=0):
        """Stop fceux. Non-blocking for fast channel switching."""
        subprocess.Popen(['pkill', '-9', 'fceux'],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._process = None

    @staticmethod
    def can_loop_count():
        """Emulator doesn't handle loop counting."""
        return False


def create_player(config, **kwargs):
    """Create new fceux player."""
    return FCEUXPlayer(config)

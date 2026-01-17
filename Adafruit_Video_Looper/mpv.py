# Copyright 2015 Adafruit Industries.
# Author: Tony DiCola
# License: GNU GPLv2, see LICENSE.txt
#
# mpv player implementation (replacement for deprecated omxplayer)
import json
import math
import os
import shutil
import socket
import subprocess
import tempfile
import time
import datetime


class MPVPlayer:

    def __init__(self, config):
        """Create an instance of a video player that runs mpv in the background."""
        self._process = None
        self._temp_directory = None
        self._socket_path = '/tmp/mpv-looper-{}.sock'.format(os.getpid())
        self._load_config(config)
        self._start_time = datetime.datetime.now()

    def __del__(self):
        if self._temp_directory:
            shutil.rmtree(self._temp_directory)
        # Clean up socket file
        if os.path.exists(self._socket_path):
            try:
                os.unlink(self._socket_path)
            except OSError:
                pass

    def _get_temp_directory(self):
        if not self._temp_directory:
            self._temp_directory = tempfile.mkdtemp()
        return self._temp_directory

    def _load_config(self, config):
        self._extensions = config.get('mpv', 'extensions') \
                                 .translate(str.maketrans('', '', ' \t\r\n.')) \
                                 .split(',')
        self._extra_args = config.get('mpv', 'extra_args').split()
        self._sound = config.get('mpv', 'sound').lower()
        self._audio_device = config.get('mpv', 'audio_device').strip()
        self._default_volume = config.getint('mpv', 'volume')
        self._hwdec = config.get('mpv', 'hwdec').strip()
        self._vo = config.get('mpv', 'vo').strip() if config.has_option('mpv', 'vo') else ''
        self._show_titles = config.getboolean('mpv', 'show_titles')
        if self._show_titles:
            title_duration = config.getint('mpv', 'title_duration')
            if title_duration >= 0:
                m, s = divmod(title_duration, 60)
                h, m = divmod(m, 60)
                self._subtitle_header = '00:00:00,00 --> {:d}:{:02d}:{:02d},00\n'.format(h, m, s)
            else:
                self._subtitle_header = '00:00:00,00 --> 99:59:59,00\n'

    def supported_extensions(self):
        """Return list of supported file extensions."""
        return self._extensions

    def extract_video_length(self, movie):
        """Extract the length of the movie from the filename."""
        # Filename example:
        # 01-12-23_Name.mp4
        filename = os.path.basename(movie.target)
        length_str = filename.split('_')[0]  # Assuming the length is before the first underscore
        hours, minutes, seconds = map(int, length_str.split('-'))
        # return length in seconds
        return hours * 3600 + minutes * 60 + seconds

    def _convert_millibels_to_percent(self, millibels):
        """Convert omxplayer millibels to mpv volume percentage.

        omxplayer: 0 mB = 0 dB = full volume, negative = quieter
        mpv: 100 = full volume, 0 = mute

        Formula: volume_percent = 100 * 10^(millibels/2000)
        """
        if millibels >= 0:
            return self._default_volume
        # millibels is negative
        volume = 100 * math.pow(10, millibels / 2000)
        return max(0, min(100, int(volume)))

    def _get_audio_args(self):
        """Build audio output arguments for mpv."""
        args = []

        # If a specific audio device is configured, use it
        if self._audio_device:
            args.extend(['--audio-device=' + self._audio_device])
        elif self._sound == 'hdmi':
            # Try common HDMI device names for Raspberry Pi
            args.extend(['--audio-device=alsa/hdmi:CARD=vc4hdmi0,DEV=0'])
        elif self._sound == 'local':
            # Analog audio output
            args.extend(['--audio-device=alsa/plughw:CARD=Headphones,DEV=0'])
        elif self._sound == 'both':
            # For 'both', let mpv use auto detection
            # Note: True simultaneous output requires PulseAudio config
            pass
        # 'auto' or anything else - let mpv decide

        return args

    def assemble_args(self, movie, loop=None, vol=0, seek_position=None):
        """Assemble the list of arguments for the mpv command.

        Args:
            movie: Movie object to play
            loop: Loop count (-1 for infinite)
            vol: Volume level (millibels, for compatibility)
            seek_position: Seek to this position in seconds (for broadcast mode)
        """
        args = ['mpv']

        # Fullscreen and no terminal output
        args.extend(['--fs', '--really-quiet'])

        # Hardware decoding
        if self._hwdec:
            args.extend(['--hwdec=' + self._hwdec])

        # Video output driver
        if self._vo:
            args.extend(['--vo=' + self._vo])

        # Video scaling - fill screen (equivalent to omxplayer --aspect-mode stretch)
        args.extend(['--keepaspect=no'])

        # IPC socket for control
        args.extend(['--input-ipc-server=' + self._socket_path])

        # Audio output
        args.extend(self._get_audio_args())

        # Volume (convert from millibels if provided)
        if vol != 0:
            volume_percent = self._convert_millibels_to_percent(vol)
        else:
            volume_percent = self._default_volume
        args.extend(['--volume=' + str(volume_percent)])

        # Determine seek position
        if seek_position is not None:
            # Broadcast mode: Use provided seek position
            elapsed_time_in_seconds = seek_position
        else:
            # Legacy mode: Calculate from elapsed time
            video_length_in_seconds = self.extract_video_length(movie)
            elapsed_time_in_seconds = self.get_elapsed_time_in_seconds()
            if elapsed_time_in_seconds >= video_length_in_seconds:
                elapsed_time_in_seconds = elapsed_time_in_seconds % video_length_in_seconds

        # Convert the elapsed time to HH:MM:SS format for --start
        hours, remainder = divmod(elapsed_time_in_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        elapsed_time = '{:02}:{:02}:{:02}'.format(int(hours), int(minutes), int(seconds))
        args.extend(['--start=' + elapsed_time])

        # Extra args from config
        args.extend(self._extra_args)

        # Loop handling
        if loop is None:
            loop = movie.repeats
        if loop <= -1:
            args.append('--loop-file=inf')

        # Subtitles for titles
        if self._show_titles and movie.title:
            srt_path = os.path.join(self._get_temp_directory(), 'video_looper.srt')
            with open(srt_path, 'w') as f:
                f.write('1\n')  # SRT requires sequence number
                f.write(self._subtitle_header)
                f.write(movie.title + '\n')
            args.extend(['--sub-file=' + srt_path])

        # Movie file path (must be last)
        args.append(movie.target)
        return args

    def play(self, movie, loop=None, vol=0, seek_position=None):
        """Play the provided movie file, optionally looping it repeatedly.

        Args:
            movie: Movie object to play
            loop: Loop count (-1 for infinite)
            vol: Volume level (millibels, for compatibility)
            seek_position: Seek to this position in seconds (for broadcast mode)
        """
        self.stop()  # Non-blocking stop for faster channel switching

        # Clean up old socket if it exists
        if os.path.exists(self._socket_path):
            try:
                os.unlink(self._socket_path)
            except OSError:
                pass

        args = self.assemble_args(movie, loop, vol, seek_position)

        # Debug: print the command being run
        print("MPV command: " + " ".join(args))

        # Run mpv process
        self._process = subprocess.Popen(args,
                                        stdout=subprocess.DEVNULL,
                                        stderr=subprocess.PIPE,
                                        stdin=subprocess.DEVNULL,
                                        close_fds=True)

        # Give mpv a moment to start, then check if it crashed
        time.sleep(0.1)
        if self._process.poll() is not None:
            stderr_output = self._process.stderr.read().decode('utf-8', errors='ignore')
            print(f"MPV exited immediately with code {self._process.returncode}")
            print(f"MPV stderr: {stderr_output}")

    def _send_ipc_command(self, command, timeout=1.0):
        """Send a command to mpv via IPC socket.

        Args:
            command: List of command arguments, e.g., ["cycle", "pause"]
            timeout: Socket timeout in seconds

        Returns:
            True if command was sent successfully, False otherwise
        """
        if not os.path.exists(self._socket_path):
            return False

        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect(self._socket_path)

            # Send command as JSON line
            msg = json.dumps({"command": command}) + "\n"
            sock.sendall(msg.encode('utf-8'))

            sock.close()
            return True
        except (socket.error, OSError):
            return False

    def pause(self):
        """Toggle pause state."""
        self._send_ipc_command(["cycle", "pause"])

    def sendKey(self, key: str):
        """Send a key command to the player.

        Maps omxplayer key commands to mpv IPC commands:
        - 'p' -> toggle pause
        - 'o' -> next chapter
        - 'i' -> previous chapter
        """
        if not self.is_playing():
            return

        key_map = {
            'p': ["cycle", "pause"],
            'o': ["add", "chapter", 1],
            'i': ["add", "chapter", -1],
        }

        command = key_map.get(key)
        if command:
            self._send_ipc_command(command)

    def is_playing(self):
        """Return true if the video player is running, false otherwise."""
        process = self._process
        if process is None:
            return False
        process.poll()
        return process.returncode is None

    def stop(self):
        """Stop the video player."""
        # Kill only our specific mpv process, not all mpv processes
        if self._process is not None:
            try:
                self._process.kill()
                self._process.wait(timeout=0.5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                pass

        # Clean up socket
        if os.path.exists(self._socket_path):
            try:
                os.unlink(self._socket_path)
            except OSError:
                pass

        self._process = None

    @staticmethod
    def can_loop_count():
        return False

    def get_elapsed_time_in_seconds(self):
        elapsed_time = datetime.datetime.now() - self._start_time
        return elapsed_time.seconds


def create_player(config, **kwargs):
    """Create new video player based on mpv."""
    return MPVPlayer(config)

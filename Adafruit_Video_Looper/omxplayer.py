# Copyright 2015 Adafruit Industries.
# Author: Tony DiCola
# License: GNU GPLv2, see LICENSE.txt
import os
import shutil
import subprocess
import tempfile
import time
import datetime

from .alsa_config import parse_hw_device

class OMXPlayer:

    def __init__(self, config):
        """Create an instance of a video player that runs omxplayer in the
        background.
        """
        self._process = None
        self._temp_directory = None
        self._load_config(config)
        self._start_time = datetime.datetime.now()

    def __del__(self):
        if self._temp_directory:
            shutil.rmtree(self._temp_directory)

    def _get_temp_directory(self):
        if not self._temp_directory:
            self._temp_directory = tempfile.mkdtemp()
        return self._temp_directory

    def _load_config(self, config):
        self._extensions = config.get('omxplayer', 'extensions') \
                                 .translate(str.maketrans('', '', ' \t\r\n.')) \
                                 .split(',')
        self._extra_args = config.get('omxplayer', 'extra_args').split()
        self._sound = config.get('omxplayer', 'sound').lower()
        assert self._sound in ('hdmi', 'local', 'both', 'alsa'), 'Unknown omxplayer sound configuration value: {0} Expected hdmi, local, both or alsa.'.format(self._sound)
        self._alsa_hw_device = parse_hw_device(config.get('alsa', 'hw_device'))
        if self._alsa_hw_device != None and self._sound == 'alsa':
            self._sound = 'alsa:hw:{},{}'.format(self._alsa_hw_device[0], self._alsa_hw_device[1])
        self._show_titles = config.getboolean('omxplayer', 'show_titles')
        if self._show_titles:
            title_duration = config.getint('omxplayer', 'title_duration')
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

    def assemble_args(self, movie, loop=None, vol=0):
        """Assemble the list of arguments for the omxplayer command."""
        # Assemble list of arguments.
        args = ['omxplayer']
        args.extend(['-o', self._sound])  # Add sound arguments.

        # Get the length of the video in seconds
        video_length_in_seconds = self.extract_video_length(movie)

        # Get the elapsed playback time in seconds
        elapsed_time_in_seconds = self.get_elapsed_time_in_seconds()

        # If the elapsed time is longer than the video length, calculate the remainder
        if elapsed_time_in_seconds >= video_length_in_seconds:
            elapsed_time_in_seconds = elapsed_time_in_seconds % video_length_in_seconds

        # Convert the elapsed time to 00:00:00 format
        hours, remainder = divmod(elapsed_time_in_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        elapsed_time = '{:02}:{:02}:{:02}'.format(hours, minutes, seconds)

        args.extend(['-l', elapsed_time])  # Add starting position.
        args.extend(self._extra_args)   
        if vol != 0:
            args.extend(['--vol', str(vol)])
        if loop is None:
            loop = movie.repeats
        if loop <= -1:
            args.append('--loop')  # Add loop parameter if necessary.
        if self._show_titles and movie.title:
            srt_path = os.path.join(self._get_temp_directory(), 'video_looper.srt')
            with open(srt_path, 'w') as f:
                f.write(self._subtitle_header)
                f.write(movie.title)
            args.extend(['--subtitles', srt_path])
        args.append(movie.target)       # Add movie file path.
        return args
    
    def play(self, movie, loop=None, vol=0):
        """Play the provided movie file, optionally looping it repeatedly."""
        self.stop(3)  # Up to 3 second delay to let the old player stop.
        args = self.assemble_args(movie, loop, vol)
        # Run omxplayer process and direct standard output to /dev/null.
        # Establish input pipe for commands
        self._process = subprocess.Popen(args,
                                        stdout=open(os.devnull, 'wb'),
                                        stdin=subprocess.PIPE,
                                        close_fds=True)
    
    def pause(self):
        self.sendKey("p")
    
    def sendKey(self, key: str):
        if self.is_playing():
            self._process.stdin.write(key.encode())
            self._process.stdin.flush()

    def is_playing(self):
        """Return true if the video player is running, false otherwise."""
        if self._process is None:
            return False
        self._process.poll()
        return self._process.returncode is None

    def stop(self, block_timeout_sec=0):
        """Stop the video player.  block_timeout_sec is how many seconds to
        block waiting for the player to stop before moving on.
        """
        # Stop the player if it's running.
        if self._process is not None and self._process.returncode is None:
            # There are a couple processes used by omxplayer, so kill both
            # with a pkill command.
            subprocess.call(['pkill', '-9', 'omxplayer'])
        # If a blocking timeout was specified, wait up to that amount of time
        # for the process to stop.
        start = time.time()
        while self._process is not None and self._process.returncode is None:
            if (time.time() - start) >= block_timeout_sec:
                break
            time.sleep(0)
        # Let the process be garbage collected.
        self._process = None

    @staticmethod
    def can_loop_count():
        return False
    
    def get_elapsed_time_in_seconds(self):
        elapsed_time = datetime.datetime.now() - self._start_time
        return elapsed_time.seconds

    def test_get_elapsed_time(self):
        """Return the elapsed time since the movie started in the format 00:00:00."""
        if self._start_time is None:
            print('Start time is None')
            return '00:00:00'
        elapsed_time = datetime.datetime.now() - self._start_time
        # hours, remainder = divmod(elapsed_time.seconds, 3600)
        # minutes, seconds = divmod(remainder, 60)
        # return '{:02}:{:02}:{:02}'.format(hours, minutes + 20, seconds)
        # return elapsed_time as an integer in seconds
        return elapsed_time.seconds + 1200


def create_player(config, **kwargs):
    """Create new video player based on omxplayer."""
    return OMXPlayer(config)

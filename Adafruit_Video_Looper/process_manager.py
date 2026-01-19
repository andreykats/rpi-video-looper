import configparser
import importlib
import os
import re
import subprocess
import sys
import signal
import time
import threading
from datetime import datetime

from .model import Playlist, Movie, BroadcastChannelManager
from .rotary import ChannelSwitcher
from .mpv import MPVPlayer
from .retroarch import RetroArchPlayer


class ProcessManager:
    """Manages video and emulator processes based on channel input."""

    def __init__(self, config_path):
        """Create an instance of the process manager.

        Args:
            config_path: Path to configuration INI file
        """
        # Load configuration
        self._config = configparser.ConfigParser()
        if len(self._config.read(config_path)) == 0:
            raise RuntimeError('Failed to find configuration file at {0}'.format(config_path))

        self._console_output = self._config.getboolean('process_manager', 'console_output', fallback=True)
        self._is_random = self._config.getboolean('process_manager', 'is_random', fallback=False)
        self._wait_time = self._config.getint('process_manager', 'wait_time', fallback=0)

        # Initialize players
        self._players = {
            'video': MPVPlayer(self._config),
            'nes': RetroArchPlayer(self._config),
        }
        self._active_player = None

        # Load file reader
        self._reader = self._load_file_reader()

        # Playlist and broadcast state
        self._playlist = None
        self._broadcast_manager = None
        self._current_channel = 2  # Start on channel 2
        self._broadcast_start_time = time.time()

        # Build combined extensions from all players
        all_extensions = []
        for player in self._players.values():
            all_extensions.extend(player.supported_extensions())
        self._extensions = '|'.join(all_extensions)

        # Runtime state
        self._running = True
        self._playback_stopped = False

        # Volume settings
        self._sound_vol = 0
        self._sound_vol_file = self._config.get('mpv', 'sound_vol_file', fallback='')

        # Initialize channel switcher (starts after playlist is built)
        self._channel_switcher = ChannelSwitcher(self._handle_channel_change)
        self._channel_switcher_thread = threading.Thread(
            target=self._channel_switcher.start,
            daemon=True
        )

    def _print(self, message):
        """Print message to console if enabled."""
        if self._console_output:
            now = datetime.now()
            print("[{}] {}".format(now, message))

    def _load_file_reader(self):
        """Load the configured file reader."""
        module = self._config.get('process_manager', 'file_reader', fallback='usb_drive')
        return importlib.import_module('.' + module, 'Adafruit_Video_Looper').create_file_reader(self._config, None)

    def _get_player_for_movie(self, movie):
        """Get the appropriate player for a movie based on content type."""
        content_type = getattr(movie, 'content_type', 'video')
        return self._players.get(content_type, self._players['video'])

    def _stop_all_players(self):
        """Stop all players."""
        for player in self._players.values():
            player.stop()
        time.sleep(0.1)  # Brief settle time

    def _is_number(self, s):
        try:
            float(s)
            return True
        except ValueError:
            return False

    def _get_video_duration(self, video_path):
        """Extract video duration in seconds using ffprobe."""
        try:
            result = subprocess.run(
                ['ffprobe', '-v', 'quiet', '-print_format', 'json',
                 '-show_format', video_path],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                import json
                data = json.loads(result.stdout)
                return float(data['format']['duration'])
        except Exception as e:
            self._print(f"Warning: Could not extract duration for {video_path}: {e}")

        # Fallback: parse from filename
        return self._parse_duration_from_filename(os.path.basename(video_path))

    def _parse_duration_from_filename(self, filename):
        """Parse duration from filename format: HH-MM-SS_Name.mp4"""
        match = re.match(r'(\d{2})-(\d{2})-(\d{2})_', filename)
        if match:
            hours = int(match.group(1))
            minutes = int(match.group(2))
            seconds = int(match.group(3))
            return hours * 3600 + minutes * 60 + seconds
        return 0

    def _get_movies_from_path(self, path):
        """Extract movies from a directory with duration extraction."""
        movies = []

        if not os.path.exists(path) or not os.path.isdir(path):
            return movies

        for x in os.listdir(path):
            if x[0] != '.' and re.search(r'\.({0})$'.format(self._extensions), x, flags=re.IGNORECASE):
                repeatsetting = re.search(r'_repeat_([0-9]*)x', x, flags=re.IGNORECASE)
                repeat = repeatsetting.group(1) if repeatsetting else 1
                basename, extension = os.path.splitext(x)

                file_path = '{0}/{1}'.format(path.rstrip('/'), x)

                # Extract duration for video files
                ext = extension.lower().lstrip('.')
                if ext in ('nes', 'fds', 'nsf'):
                    duration = 0  # No duration for ROMs
                else:
                    duration = self._get_video_duration(file_path)

                movie = Movie(file_path, basename, repeat, duration)
                movies.append(movie)

                if duration > 0:
                    self._print(f"  {x}: {int(duration)}s")
                else:
                    self._print(f"  {x}: (no duration)")

        # Handle volume file
        if self._sound_vol_file:
            vol_path = '{0}/{1}'.format(path.rstrip('/'), self._sound_vol_file)
            if os.path.exists(vol_path):
                with open(vol_path, 'r') as f:
                    vol_str = f.readline()
                    if self._is_number(vol_str):
                        self._sound_vol = int(float(vol_str))

        return movies

    def _build_playlist(self):
        """Build playlist, trying broadcast mode first."""
        self._broadcast_manager = self._build_broadcast_channels()

        if self._broadcast_manager is not None:
            self._print("Using broadcast TV mode (time-synchronized channels)")
            return None

        # Fallback to all files mode
        return self._build_playlist_from_all_files()

    def _build_playlist_from_all_files(self):
        """Build playlist from all files in search paths."""
        paths = self._reader.search_paths()
        movies = []

        for path in paths:
            if os.path.exists(path) and os.path.isdir(path):
                movies.extend(self._get_movies_from_path(path))

        return Playlist(sorted(movies))

    def _build_broadcast_channels(self):
        """Build broadcast TV-style channels."""
        if not hasattr(self._reader, 'search_channel_paths'):
            self._print("Channel mode not supported by file reader")
            return None

        channel_paths = self._reader.search_channel_paths()

        if len(channel_paths) == 0:
            self._print("No channel folders found")
            return None

        manager = BroadcastChannelManager(self._broadcast_start_time)

        self._print("Building broadcast channels...")

        for channel_num in range(1, 14):
            if channel_num in channel_paths:
                self._print(f"Channel {channel_num}:")
                movies = self._get_movies_from_path(channel_paths[channel_num])
                playlist = Playlist(sorted(movies))
                manager.set_channel_playlist(channel_num, playlist)

                total_duration = sum(m.duration for m in movies)
                self._print(f"  Total: {len(movies)} files, {int(total_duration)}s loop")

        manager.set_default_playlist(Playlist([]))
        return manager

    def _handle_channel_change(self, channel, previous_channel):
        """Handle rotary encoder channel changes."""
        if not self._running:
            return

        if channel < 1 or channel > 13:
            self._print(f"Channel {channel} out of range (1-13)")
            return

        self._print(f"Switching from channel {previous_channel} to {channel}")

        # Note: Don't stop players here - let _play_movie() handle it
        # This allows same-player-type transitions to use IPC (fast switching)

        if self._broadcast_manager is not None:
            # Broadcast mode
            self._current_channel = channel
            movie, seek_offset = self._broadcast_manager.calculate_broadcast_position(channel)

            if movie is None:
                self._print(f"Channel {channel} is empty")
                self._playback_stopped = True
                return

            self._print(f"Channel {channel}: Playing {movie.filename}")
            self._play_movie(movie, seek_offset)
            self._playback_stopped = False
        else:
            # Legacy mode
            video_index = channel - 1
            if self._playlist and video_index < self._playlist.length():
                self._playlist.set_next(video_index)
                self._playback_stopped = False

    def _play_movie(self, movie, seek_offset=None):
        """Play a movie with the appropriate player."""
        player = self._get_player_for_movie(movie)

        # If switching to a different player type, stop the old one first
        # Same-player-type transitions are handled by the player's IPC (fast)
        if self._active_player is not None and self._active_player != player:
            if isinstance(self._active_player, RetroArchPlayer):
                self._active_player.pause()
            else:
                self._active_player.stop()

        # Only pass seek_offset for video content
        if movie.content_type == 'video' and seek_offset:
            player.play(movie, vol=self._sound_vol, seek_position=seek_offset)
        else:
            player.play(movie, vol=self._sound_vol)

        self._active_player = player

    def run(self):
        """Main program loop."""
        self._playlist = self._build_playlist()

        # Get initial movie
        if self._broadcast_manager is not None:
            self._print("Starting broadcast TV mode")
            movie, seek_offset = self._broadcast_manager.calculate_broadcast_position(self._current_channel)
        else:
            movie = self._playlist.get_next(self._is_random) if self._playlist else None
            seek_offset = None

        # Start channel switcher thread
        self._channel_switcher_thread.start()

        while self._running:
            # Check if any player is playing
            any_playing = any(p.is_playing() for p in self._players.values())

            if not any_playing and not self._playback_stopped:
                if movie is not None:
                    # Get next movie based on mode
                    if self._broadcast_manager is not None:
                        movie, seek_offset = self._broadcast_manager.calculate_broadcast_position(
                            self._current_channel
                        )
                    else:
                        if self._playlist:
                            movie = self._playlist.get_next(self._is_random)
                        seek_offset = None

                    if self._wait_time > 0:
                        time.sleep(self._wait_time)

                    if movie:
                        self._print(f'Playing: {movie.filename} ({movie.content_type})')
                        self._play_movie(movie, seek_offset)

            # Check for file reader changes (USB insert/remove)
            if self._reader.is_changed() and not self._playback_stopped:
                self._print("Media changed, rebuilding playlists")
                self._stop_all_players()

                self._broadcast_start_time = time.time()
                self._playlist = self._build_playlist()

                if self._broadcast_manager is not None:
                    movie, seek_offset = self._broadcast_manager.calculate_broadcast_position(
                        self._current_channel
                    )
                else:
                    movie = self._playlist.get_next(self._is_random) if self._playlist else None
                    seek_offset = None

            time.sleep(0.1)  # Main loop delay

        self._print("Process manager stopped")

    def quit(self, shutdown=False):
        """Shut down the program."""
        self._print("Quitting process manager")

        if shutdown:
            os.system("sudo shutdown now")

        self._playback_stopped = True
        self._running = False
        self._stop_all_players()

    def signal_quit(self, signal, frame):
        """Signal handler for quit."""
        self._print("Received signal to quit")
        self.quit()


# Main entry point
if __name__ == '__main__':
    print('Starting Process Manager.')
    config_path = '/boot/video_looper.ini'
    if len(sys.argv) == 2:
        config_path = sys.argv[1]

    manager = ProcessManager(config_path)
    signal.signal(signal.SIGTERM, manager.signal_quit)
    signal.signal(signal.SIGINT, manager.signal_quit)
    manager.run()

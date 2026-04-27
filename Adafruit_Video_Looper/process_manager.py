import configparser
import importlib
import logging
import os
import re
import subprocess
import sys
import signal
import time
import threading
from dataclasses import dataclass
from typing import Optional

import RPi.GPIO as GPIO

from .latest_slot import LatestSlot
from .model import Playlist, Movie, BroadcastChannelManager
from .rotary import ChannelSwitcher
from .mpv import MPVPlayer
from .retroarch import RetroArchPlayer

log = logging.getLogger('looper.main')
wlog = logging.getLogger('looper.worker')


@dataclass
class ChannelIntent:
    channel: int
    previous_channel: Optional[int]
    reason: str  # 'startup' | 'channel'


@dataclass
class EovIntent:
    channel: int


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

        # Channel-change pipeline. Two LatestSlots share one wake event.
        # Producers (encoder thread, main thread) overwrite each other
        # within their slot — only the latest target is acted on. Worker
        # drains channel-intent first, then eov-intent: channel always
        # wins on contention without needing priority logic in the slot.
        self._worker_wake = threading.Event()
        self._channel_intent_slot = LatestSlot(self._worker_wake)
        self._eov_intent_slot = LatestSlot(self._worker_wake)
        self._eov_pending = False
        self._eov_pending_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._channel_worker_thread = threading.Thread(
            target=self._channel_worker,
            name='channel_worker',
            daemon=True,
        )

        # Initialize channel switcher (starts after playlist is built)
        self._channel_switcher = ChannelSwitcher(
            on_channel_change=self._publish_channel_intent,
            config=self._config,
        )
        self._channel_switcher_thread = threading.Thread(
            target=self._channel_switcher.start,
            name='encoder',
            daemon=True,
        )

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
            log.warning('ffprobe failed path=%s err=%s', video_path, e)

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
                    log.info('  %s: %ds', x, int(duration))
                else:
                    log.info('  %s: (no duration)', x)

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
            log.info('using broadcast TV mode (time-synchronized channels)')
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
            log.info('channel mode not supported by file reader')
            return None

        channel_paths = self._reader.search_channel_paths()

        if len(channel_paths) == 0:
            log.info('no channel folders found')
            return None

        manager = BroadcastChannelManager(self._broadcast_start_time)

        log.info('building broadcast channels')

        for channel_num in range(1, 14):
            if channel_num in channel_paths:
                log.info('channel %d:', channel_num)
                movies = self._get_movies_from_path(channel_paths[channel_num])
                playlist = Playlist(sorted(movies))
                manager.set_channel_playlist(channel_num, playlist)

                total_duration = sum(m.duration for m in movies)
                log.info('  total: %d files, %ds loop', len(movies), int(total_duration))

        manager.set_default_playlist(Playlist([]))
        return manager

    def _publish_channel_intent(self, channel, previous_channel):
        """Called from the encoder polling thread. Publishes a channel
        intent and returns immediately so the encoder loop stays
        responsive while the worker swaps players."""
        self._channel_intent_slot.publish(
            ChannelIntent(channel=channel, previous_channel=previous_channel,
                          reason='channel')
        )

    def _publish_eov_intent(self):
        """Called from the main loop when no player is active. Idempotent
        while a previous eov is in flight — set-once-per-need."""
        with self._eov_pending_lock:
            if self._eov_pending:
                return
            self._eov_pending = True
        log.info('advance reason=eov channel=%d', self._current_channel)
        self._eov_intent_slot.publish(EovIntent(channel=self._current_channel))

    # Reconcile window — after a launch, watch for newer publishes for
    # this long. Subsequent intents during the window switch the player
    # again (fast: MPV uses IPC loadfile, RetroArch uses LOAD_CONTENT
    # over UDP). Cost is one extra launch per channel touched during a
    # spin; benefit is zero added latency on isolated clicks.
    _CHANNEL_RECONCILE_S = 0.15

    def _channel_worker(self):
        """Single owner of player launches.

        Wakes on either intent slot; drains channel-intent first so it
        always wins on contention, then eov-intent. Acts on the first
        intent immediately, then opens a brief reconcile window so a
        spin keeps switching to the latest target without paying any
        pre-launch latency on isolated clicks.
        """
        while not self._stop_event.is_set():
            if not self._worker_wake.wait(timeout=0.2):
                continue
            channel_intent = self._channel_intent_slot.take()
            eov_intent = self._eov_intent_slot.take()
            self._worker_wake.clear()

            if eov_intent is not None:
                with self._eov_pending_lock:
                    self._eov_pending = False

            # Channel beats eov when both are pending.
            intent = channel_intent or eov_intent
            if intent is None:
                continue

            try:
                self._handle_intent(intent)
            except Exception as e:
                wlog.exception('worker error: %s', e)

            # Reconcile only for encoder-driven channel intents. Startup
            # and eov are one-shots; nothing follows them in a burst.
            if isinstance(intent, ChannelIntent) and intent.reason == 'channel':
                self._reconcile_after_launch()

    def _reconcile_after_launch(self):
        """After a launch, briefly watch for newer channel intents and
        switch again if any arrive. Each newer intent restarts the
        window so a continuous spin keeps re-targeting until quiet.
        """
        deadline = time.monotonic() + self._CHANNEL_RECONCILE_S
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            if not self._worker_wake.wait(timeout=remaining):
                return
            self._worker_wake.clear()
            newer = self._channel_intent_slot.take()
            # eov can arrive during the window; channel still wins, but
            # we clear the pending flag so main can publish more later.
            extra_eov = self._eov_intent_slot.take()
            if extra_eov is not None:
                with self._eov_pending_lock:
                    self._eov_pending = False
            if newer is None:
                continue
            try:
                self._handle_intent(newer)
            except Exception as e:
                wlog.exception('worker error: %s', e)
            deadline = time.monotonic() + self._CHANNEL_RECONCILE_S

    def _handle_intent(self, intent):
        if isinstance(intent, ChannelIntent):
            self._handle_channel_intent(intent)
        elif isinstance(intent, EovIntent):
            self._handle_eov_intent(intent)

    def _handle_channel_intent(self, intent):
        channel = intent.channel
        if channel < 1 or channel > 13:
            wlog.warning('channel %d out of range (1-13)', channel)
            return

        wlog.info('handle channel=%d prev=%s reason=%s',
                  channel, intent.previous_channel, intent.reason)

        if self._broadcast_manager is not None:
            self._current_channel = channel
            movie, seek_offset = self._broadcast_manager.calculate_broadcast_position(channel)
            if movie is None:
                wlog.info('channel %d empty', channel)
                self._playback_stopped = True
                return
            wlog.info('channel %d playing %s', channel, movie.filename)
            self._play_movie(movie, seek_offset)
            self._playback_stopped = False
        else:
            video_index = channel - 1
            if self._playlist and video_index < self._playlist.length():
                self._playlist.set_next(video_index)
                self._playback_stopped = False
                movie = self._playlist.get_next(self._is_random)
                if movie is not None:
                    self._play_movie(movie)

    def _handle_eov_intent(self, intent):
        """End-of-video advance — same path as a channel change but driven
        by the main loop noticing no active player rather than the encoder."""
        wlog.info('handle eov channel=%d', intent.channel)
        if self._broadcast_manager is not None:
            movie, seek_offset = self._broadcast_manager.calculate_broadcast_position(
                intent.channel
            )
            if movie is None:
                self._playback_stopped = True
                return
            self._play_movie(movie, seek_offset)
            self._playback_stopped = False
        elif self._playlist is not None:
            movie = self._playlist.get_next(self._is_random)
            if movie is not None:
                self._play_movie(movie)
                self._playback_stopped = False

    def _play_movie(self, movie, seek_offset=None):
        """Play a movie with the appropriate player."""
        player = self._get_player_for_movie(movie)

        # If switching to a different player type, stop the old one first
        # Same-player-type transitions are handled by the player's IPC (fast)
        # Use non-blocking stop since we're switching to a different player
        if self._active_player is not None and self._active_player != player:
            self._active_player.stop(block=False)

        # Only pass seek_offset for video content
        if movie.content_type == 'video' and seek_offset:
            player.play(movie, vol=self._sound_vol, seek_position=seek_offset)
        else:
            player.play(movie, vol=self._sound_vol)

        self._active_player = player

    def run(self):
        """Main program loop.

        Main becomes a pure poller/publisher: it watches `is_playing()`
        and the file-reader, and posts intents. The worker thread is the
        only writer of `self._active_player` and the only caller of
        `_play_movie`.
        """
        self._playlist = self._build_playlist()

        # Read the encoder synchronously so the initial movie matches the
        # actual hardware position. Without this the worker would race
        # the switcher: main posts channel=2 startup intent while the
        # switcher reads channel=8 — whichever finishes last owns DRM.
        initial = self._channel_switcher.read_remote_rotary_encoder()
        if 1 <= initial <= 13:
            self._current_channel = initial
            self._channel_switcher.previous_channel = initial

        if self._broadcast_manager is not None:
            log.info('starting broadcast TV mode')

        # Start worker before switcher so the consumer is ready before
        # any producer publishes.
        self._channel_worker_thread.start()
        self._channel_switcher_thread.start()

        # Initial player launch goes through the worker like every other
        # launch — keeps the single-owner invariant from minute zero.
        self._channel_intent_slot.publish(
            ChannelIntent(channel=self._current_channel,
                          previous_channel=None,
                          reason='startup')
        )
        self._worker_wake.set()

        try:
            while self._running:
                any_playing = any(p.is_playing() for p in self._players.values())

                if not any_playing and not self._playback_stopped:
                    self._publish_eov_intent()

                # Check for file reader changes (USB insert/remove)
                if self._reader.is_changed() and not self._playback_stopped:
                    log.info('media changed, rebuilding playlists')
                    self._stop_all_players()

                    self._broadcast_start_time = time.time()
                    self._playlist = self._build_playlist()

                    # Trigger a fresh launch via the worker.
                    self._publish_eov_intent()

                time.sleep(0.1)  # Main loop delay
        finally:
            log.info('shutting down')
            self._stop_event.set()
            self._channel_switcher.stop()
            self._worker_wake.set()  # unblock worker
            self._stop_all_players()
            try:
                GPIO.cleanup()
            except Exception as e:
                log.warning('GPIO cleanup failed: %s', e)
            log.info('process manager stopped')

    def quit(self, shutdown=False):
        """Shut down the program."""
        log.info('quit')

        if shutdown:
            os.system("sudo shutdown now")

        self._playback_stopped = True
        self._running = False

    def signal_quit(self, signal, frame):
        """Signal handler for quit."""
        log.info('received signal to quit')
        self.quit()


def _ensure_xdg_runtime_dir():
    """Make sure XDG_RUNTIME_DIR points at an existing directory.

    Supervisor launches the looper with no login session, so systemd-logind
    has never created /run/user/<uid>. RetroArch's KMS context handoff
    refuses to start without XDG_RUNTIME_DIR, and several audio backends
    use it for socket paths. Provide one ourselves and inherit it into
    every subprocess (mpv, retroarch, ...) via the default Popen env.
    """
    runtime_dir = os.environ.get('XDG_RUNTIME_DIR')
    if not runtime_dir:
        runtime_dir = '/run/user/{0}'.format(os.geteuid())
        os.environ['XDG_RUNTIME_DIR'] = runtime_dir
    try:
        os.makedirs(runtime_dir, mode=0o700, exist_ok=True)
        os.chmod(runtime_dir, 0o700)
    except OSError as e:
        print('Warning: could not prepare XDG_RUNTIME_DIR={0}: {1}'.format(runtime_dir, e))


def _configure_logging(config):
    """Set up structured logging.

    File at /tmp/video_looper.log (truncated each run) for the verifier;
    stdout for supervisor's existing tail-based debug workflow.
    """
    relay_debug = False
    if config is not None:
        relay_debug = config.getboolean('logging', 'relay_debug', fallback=False)

    fmt = '%(asctime)s.%(msecs)03d %(threadName)s %(name)s %(levelname)s %(message)s'
    datefmt = '%H:%M:%S'

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # Wipe any handlers a library may have installed at import.
    root.handlers.clear()

    formatter = logging.Formatter(fmt, datefmt=datefmt)

    file_handler = logging.FileHandler('/tmp/video_looper.log', mode='w')
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG if relay_debug else logging.INFO)
    root.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(logging.INFO)
    root.addHandler(stream_handler)

    if relay_debug:
        logging.getLogger('looper.relay').setLevel(logging.DEBUG)


# Main entry point
if __name__ == '__main__':
    threading.current_thread().name = 'main'

    config_path = '/boot/video_looper.ini'
    if len(sys.argv) == 2:
        config_path = sys.argv[1]

    bootstrap_config = configparser.ConfigParser()
    bootstrap_config.read(config_path)
    _configure_logging(bootstrap_config)

    log.info('starting process manager config=%s', config_path)
    _ensure_xdg_runtime_dir()

    manager = ProcessManager(config_path)
    signal.signal(signal.SIGTERM, manager.signal_quit)
    signal.signal(signal.SIGINT, manager.signal_quit)
    manager.run()

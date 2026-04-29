# Copyright 2015 Adafruit Industries.
# Author: Tony DiCola
# License: GNU GPLv2, see LICENSE.txt
import os
import random
from dataclasses import dataclass
from os.path import basename
from typing import Optional, Union

random.seed()

class Movie:
    """Representation of a movie"""

    def __init__(self, target:str , title: Optional[str] = None,
                 repeats: int = 1, duration: float = 0,
                 hidden: bool = False):
        """Create a playlist from the provided list of movies."""
        self.target = target
        self.filename = basename(target)
        self.title = title
        self.repeats = int(repeats)
        self.playcount = 0
        self.duration = float(duration)  # Duration in seconds for broadcast mode
        self.hidden = bool(hidden)
        self.content_type = self._detect_content_type()

    def _detect_content_type(self):
        """Detect content type from file extension."""
        ext = os.path.splitext(self.target)[1].lower().lstrip('.')
        if ext in ('nes', 'fds', 'nsf'):
            return 'nes'
        return 'video'

    def was_played(self):
        if self.repeats > 1:
            # only count up if its necessary, to prevent memory exhaustion if player runs a long time
            self.playcount += 1
        else:
            self.playcount = 1

    def clear_playcount(self):
        self.playcount = 0
        
    def finish_playing(self):
        self.playcount = self.repeats+1

    def has_duration(self):
        """Check if duration has been set."""
        return self.duration > 0

    def __lt__(self, other):
        return self.target < other.target

    def __eq__(self, other):
        if isinstance(other, str):
            return self.filename == other
        if isinstance(other, Movie):
            return self.target == other.target
        return False

    def __str__(self):
        return "{0} ({1})".format(self.filename, self.title) if self.title else self.filename

    def __repr__(self):
        return repr((self.target, self.filename, self.title, self.repeats, self.playcount))

class Playlist:
    """Representation of a playlist of movies."""

    def __init__(self, movies):
        """Create a playlist from the provided list of movies."""
        self._movies = movies
        self._index = None
        self._next = None

    def get_next(self, is_random, resume = False) -> Movie:
        """Get the next movie in the playlist. Will loop to start of playlist
        after reaching end.
        """
        # Check if no movies are in the playlist and return nothing.
        if len(self._movies) == 0:
            return None
        
        # Check if next movie is set and jump directly there:
        if self._next is not None:
            next=self._next
            self._next = None # reset next
            self._index=self._movies.index(next)
            return next
        
        # Start Random movie
        if is_random:
            self._index = random.randrange(0, self.length())
        else:
            # Start at the first movie or resume and increment through them in order.
            if self._index is None:
                if resume:
                    try:
                        with open('playlist_index.txt', 'r') as f:
                            self._index = int(f.read())
                    except FileNotFoundError:
                        self._index = 0
                else:
                    self._index = 0
            else:
                self._index += 1
                
            # Wrap around to the start after finishing.
            if self._index >= self.length():
                self._index = 0

        if resume:
            with open('playlist_index.txt','w') as f:
                f.write(str(self._index))

        return self._movies[self._index]
    
    # sets next by filename or Movie object or index
    def set_next(self, thing: Union[Movie, str, int]):
        if isinstance(thing, Movie):
            if (thing in self._movies):
                self._next = thing
        elif isinstance(thing, str):
            if thing in self._movies:
                self._next = self._movies[self._movies.index(thing)]
            elif thing[0:1] in ("+","-"):
                self._next = self._movies[(self._index+int(thing))%self.length()]
        elif isinstance(thing, int):
            if thing >= 0 and thing <= self.length():
                self._next = self._movies[thing]
        else:
            self._next = None
        self.clear_all_playcounts()
        self._movies[self._index].finish_playing() #set the current to max playcount so it will not get played again
       
    # sets next relative to current index
    def seek(self, amount:int):
        self.set_next((self._index+amount)%self.length())

    def length(self):
        """Return the number of movies in the playlist."""
        return len(self._movies)

    def clear_all_playcounts(self):
        for movie in self._movies:
            movie.clear_playcount()


@dataclass
class BroadcastSelection:
    movie: Optional[Movie]
    seek_offset: float = 0
    play_length: Optional[float] = None
    loop: Optional[int] = None
    hidden: bool = False
    loop_position: Optional[float] = None


class BroadcastChannelManager:
    """Manages broadcast TV-style channels with synchronized playback."""

    def __init__(self, broadcast_start_time, num_channels=13):
        self._channel_playlists = {}  # Dict: channel_num -> Playlist
        self._channel_durations = {}  # Dict: channel_num -> real total_duration
        self._channel_playback_schedules = {}  # Dict: channel_num -> Playlist
        self._channel_effective_durations = {}  # Includes hidden fillers
        self._broadcast_start_time = broadcast_start_time  # time.time() when app started
        self._default_playlist = None
        self._default_movie = None
        self._default_duration = 0
        self._broadcast_block_duration = 0

    def set_default_movie(self, movie):
        """Set runtime-only filler movie for short video channels."""
        self._default_movie = movie
        self._default_duration = movie.duration if movie is not None else 0
        self._rebuild_playback_schedules()

    def set_channel_playlist(self, channel_num, playlist):
        """Associate a user-visible playlist with a channel number (1-13)."""
        self._channel_playlists[channel_num] = playlist
        # Real user duration only; hidden default fillers are kept in the
        # separate playback schedule and never exposed to the web UI.
        total_duration = sum(movie.duration for movie in playlist._movies)
        self._channel_durations[channel_num] = total_duration
        self._rebuild_playback_schedules()

    def set_default_playlist(self, playlist):
        """Set playlist to use for empty/missing channels."""
        self._default_playlist = playlist

    def get_playlist(self, channel_num):
        """Get playlist for channel, or default if empty/missing."""
        if channel_num in self._channel_playlists:
            playlist = self._channel_playlists[channel_num]
            if playlist.length() > 0:
                return playlist
        return self._default_playlist if self._default_playlist else Playlist([])

    def get_playback_playlist(self, channel_num):
        """Get the runtime playback schedule for a channel."""
        playlist = self._channel_playback_schedules.get(channel_num)
        if playlist is not None and playlist.length() > 0:
            return playlist
        return self._default_playlist if self._default_playlist else Playlist([])

    def _rebuild_playback_schedules(self):
        """Build hidden default-fill schedules from real playlist durations.

        `T_max` is computed from real user content only. Short video
        channels get hidden default.mp4 segments appended to their runtime
        schedule, but their persisted/user-facing playlist stays unchanged.
        """
        self._broadcast_block_duration = max(
            self._channel_durations.values(), default=0
        )
        self._channel_playback_schedules = {}
        self._channel_effective_durations = {}

        for channel_num, playlist in self._channel_playlists.items():
            real_duration = self._channel_durations.get(channel_num, 0)
            movies = list(playlist._movies)

            if (real_duration > 0
                    and self._broadcast_block_duration > real_duration
                    and self._default_movie is not None
                    and self._default_duration > 0):
                remaining = self._broadcast_block_duration - real_duration
                while remaining > 0.001:
                    segment_duration = min(self._default_duration, remaining)
                    movies.append(Movie(
                        self._default_movie.target,
                        self._default_movie.title,
                        1,
                        segment_duration,
                        hidden=True,
                    ))
                    remaining -= segment_duration

            self._channel_playback_schedules[channel_num] = Playlist(movies)
            self._channel_effective_durations[channel_num] = sum(
                movie.duration for movie in movies
            )

    def calculate_broadcast_position(self, channel_num):
        """Calculate what should be playing on this channel at current broadcast time.

        Returns:
            BroadcastSelection, with movie=None if channel empty.
        """
        import time

        playlist = self.get_playback_playlist(channel_num)
        if playlist.length() == 0:
            return BroadcastSelection(None, 0)

        # Get elapsed broadcast time
        broadcast_time = time.time() - self._broadcast_start_time

        # Get total loop duration for this channel
        total_duration = self._channel_effective_durations.get(channel_num, 0)
        if total_duration == 0:
            # Fallback if durations not set
            movie = playlist._movies[0]
            return BroadcastSelection(
                movie, 0, hidden=getattr(movie, 'hidden', False),
                loop_position=0
            )

        # Calculate position within the loop
        position_in_loop = broadcast_time % total_duration

        # Find which video contains this position
        cumulative_time = 0
        for movie in playlist._movies:
            if cumulative_time + movie.duration > position_in_loop:
                # Found the video!
                seek_offset = position_in_loop - cumulative_time
                hidden = getattr(movie, 'hidden', False)
                play_length = None
                if hidden:
                    # Hidden filler segments are virtual playlist entries.
                    # Limit playback to the remaining segment so the next
                    # EOV lands exactly on the next hidden/full/real entry.
                    play_length = max(0.001, movie.duration - seek_offset)
                return BroadcastSelection(
                    movie, seek_offset, play_length=play_length,
                    hidden=hidden, loop_position=position_in_loop
                )
            cumulative_time += movie.duration

        # Fallback (shouldn't reach here)
        movie = playlist._movies[0]
        return BroadcastSelection(
            movie, 0, hidden=getattr(movie, 'hidden', False), loop_position=0
        )

    def has_channel(self, channel_num):
        """Check if channel has videos."""
        return (channel_num in self._channel_playlists and
                self._channel_playlists[channel_num].length() > 0)

# Copyright 2015 Adafruit Industries.
# Author: Tony DiCola
# License: GNU GPLv2, see LICENSE.txt
import random
from enum import Enum
from os.path import basename
from typing import Optional, Union

random.seed()


class ChannelType(Enum):
    """Type of content on a broadcast channel."""
    VIDEO = "video"
    GAME = "game"
    EMPTY = "empty"

class Movie:
    """Representation of a movie"""

    def __init__(self, target:str , title: Optional[str] = None, repeats: int = 1, duration: float = 0):
        """Create a playlist from the provided list of movies."""
        self.target = target
        self.filename = basename(target)
        self.title = title
        self.repeats = int(repeats)
        self.playcount = 0
        self.duration = float(duration)  # Duration in seconds for broadcast mode

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
                self._next(thing)
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


class BroadcastChannelManager:
    """Manages broadcast TV-style channels with synchronized playback."""

    def __init__(self, broadcast_start_time, num_channels=13):
        self._channel_playlists = {}  # Dict: channel_num -> Playlist
        self._channel_durations = {}  # Dict: channel_num -> total_duration
        self._broadcast_start_time = broadcast_start_time  # time.time() when app started
        self._default_playlist = None
        self._channel_types = {}  # Dict: channel_num -> ChannelType
        self._game_roms = {}      # Dict: channel_num -> rom_path

    def set_channel_playlist(self, channel_num, playlist):
        """Associate a playlist with a channel number (1-13)."""
        self._channel_playlists[channel_num] = playlist
        # Calculate total duration for this channel
        total_duration = sum(movie.duration for movie in playlist._movies)
        self._channel_durations[channel_num] = total_duration

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

    def calculate_broadcast_position(self, channel_num):
        """Calculate what should be playing on this channel at current broadcast time.

        Returns:
            (movie, seek_offset) tuple, or (None, 0) if channel empty
        """
        import time

        playlist = self.get_playlist(channel_num)
        if playlist.length() == 0:
            return (None, 0)

        # Get elapsed broadcast time
        broadcast_time = time.time() - self._broadcast_start_time

        # Get total loop duration for this channel
        total_duration = self._channel_durations.get(channel_num, 0)
        if total_duration == 0:
            # Fallback if durations not set
            return (playlist._movies[0], 0)

        # Calculate position within the loop
        position_in_loop = broadcast_time % total_duration

        # Find which video contains this position
        cumulative_time = 0
        for movie in playlist._movies:
            if cumulative_time + movie.duration > position_in_loop:
                # Found the video!
                seek_offset = position_in_loop - cumulative_time
                return (movie, seek_offset)
            cumulative_time += movie.duration

        # Fallback (shouldn't reach here)
        return (playlist._movies[0], 0)

    def has_channel(self, channel_num):
        """Check if channel has videos."""
        return (channel_num in self._channel_playlists and
                self._channel_playlists[channel_num].length() > 0)

    def set_channel_type(self, channel_num, channel_type, rom_path=None):
        """Set the type of content for a channel.

        Args:
            channel_num: Channel number (1-13)
            channel_type: ChannelType enum value
            rom_path: Path to ROM file (for game channels only)
        """
        self._channel_types[channel_num] = channel_type
        if rom_path:
            self._game_roms[channel_num] = rom_path

    def get_channel_type(self, channel_num):
        """Get the type of content for a channel.

        Returns:
            ChannelType enum value, defaults to EMPTY if not set
        """
        return self._channel_types.get(channel_num, ChannelType.EMPTY)

    def get_game_rom(self, channel_num):
        """Get ROM path for a game channel.

        Returns:
            ROM file path string, or None if not a game channel
        """
        return self._game_roms.get(channel_num)

    def is_game_channel(self, channel_num):
        """Check if channel is a game channel.

        Returns:
            True if channel type is GAME, False otherwise
        """
        return self._channel_types.get(channel_num) == ChannelType.GAME
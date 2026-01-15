# Copyright 2015 Adafruit Industries.
# Author: Tony DiCola
# License: GNU GPLv2, see LICENSE.txt
import glob

from .usb_drive_mounter import USBDriveMounter


class USBDriveReader:

    def __init__(self, config):
        """Create an instance of a file reader that uses the USB drive mounter
        service to keep track of attached USB drives and automatically mount
        them for reading videos.
        """
        self._load_config(config)
        self._mounter = USBDriveMounter(root=self._mount_path,
                                        readonly=self._readonly)
        self._mounter.start_monitor()


    def _load_config(self, config):
        self._mount_path = config.get('usb_drive', 'mount_path')
        self._readonly = config.getboolean('usb_drive', 'readonly')

    def search_paths(self):
        """Return a list of paths to search for files. Will return a list of all
        mounted USB drives.
        """
        self._mounter.mount_all()
        return glob.glob(self._mount_path + '*')

    def search_channel_paths(self):
        """Return dict mapping channel numbers to their folder info and content type.

        Returns:
            dict: {channel_num: {'path': str, 'type': 'video'|'game', 'rom': str|None}}
            Only includes channels that exist on USB drive.
        """
        import os

        self._mounter.mount_all()
        usb_drives = glob.glob(self._mount_path + '*')

        channel_info = {}
        for drive in usb_drives:
            # Search for channel folders 1-13
            for channel_num in range(1, 14):
                channel_path = os.path.join(drive, str(channel_num))
                if os.path.exists(channel_path) and os.path.isdir(channel_path):
                    # Check for NES ROM files (.nes extension)
                    rom_files = [f for f in os.listdir(channel_path)
                                if f.lower().endswith('.nes') and not f.startswith('.')]

                    if rom_files:
                        # Game channel - use first ROM found alphabetically
                        rom_files.sort()
                        channel_info[channel_num] = {
                            'path': channel_path,
                            'type': 'game',
                            'rom': os.path.join(channel_path, rom_files[0])
                        }
                    else:
                        # Video channel (original behavior)
                        channel_info[channel_num] = {
                            'path': channel_path,
                            'type': 'video',
                            'rom': None
                        }

        return channel_info

    def is_changed(self):
        """Return true if the file search paths have changed, like when a new
        USB drive is inserted.
        """
        return self._mounter.poll_changes()

    def idle_message(self):
        """Return a message to display when idle and no files are found."""
        return 'Insert USB drive with compatible movies.'


def create_file_reader(config, screen):
    """Create new file reader based on mounting USB drives."""
    return USBDriveReader(config)

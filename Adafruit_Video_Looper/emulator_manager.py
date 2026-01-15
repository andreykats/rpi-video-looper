"""
EmulatorManager - Manages NES emulator (nestopia) for game channel support.

Responsibilities:
- Start nestopia subprocess with ROM file
- Track nestopia window ID via xdotool
- Show/hide emulator window via wmctrl
- Clean shutdown of emulator process
"""

import subprocess
import os
import time


class EmulatorManager:
    """Manages the nestopia NES emulator process and window visibility."""

    def __init__(self, config):
        self._process = None
        self._window_id = None
        self._rom_path = None
        self._is_visible = False
        self._load_config(config)

    def _load_config(self, config):
        """Load emulator settings from [emulator] section of INI file."""
        self._fullscreen = config.getboolean('emulator', 'fullscreen', fallback=True)
        self._emulator_path = config.get('emulator', 'emulator_path', fallback='nestopia')

    def set_rom(self, rom_path):
        """Set the ROM file path to load."""
        self._rom_path = rom_path

    def start(self):
        """Start nestopia emulator in background (initially hidden).

        Returns:
            bool: True if emulator started successfully, False otherwise.
        """
        if self._rom_path is None or not os.path.exists(self._rom_path):
            return False

        if self._process is not None and self._process.poll() is None:
            return True  # Already running

        # Build nestopia command with options
        args = [self._emulator_path]
        if self._fullscreen:
            args.append('-f')  # nestopia fullscreen flag
        args.append(self._rom_path)

        # Start process with output suppressed
        try:
            self._process = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except FileNotFoundError:
            print(f"Error: emulator not found at '{self._emulator_path}'")
            return False

        # Wait for window to appear, then find and hide it
        time.sleep(1.5)
        self._window_id = self._find_emulator_window()

        if self._window_id:
            self.hide()  # Start hidden
            return True
        return False

    def _find_emulator_window(self):
        """Find emulator window ID using xdotool.

        Returns:
            str: Window ID if found, None otherwise.
        """
        # Try multiple window name patterns
        search_patterns = ['nestopia', 'Nestopia', 'NES']
        for pattern in search_patterns:
            try:
                result = subprocess.run(
                    ['xdotool', 'search', '--name', pattern],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0 and result.stdout.strip():
                    return result.stdout.strip().split('\n')[0]
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass
        return None

    def show(self):
        """Bring emulator window to foreground."""
        if self._window_id is None:
            self._window_id = self._find_emulator_window()

        if self._window_id:
            # Remove hidden state
            subprocess.run(
                ['wmctrl', '-i', '-r', self._window_id, '-b', 'remove,hidden'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            # Activate (focus) window
            subprocess.run(
                ['wmctrl', '-i', '-a', self._window_id],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            self._is_visible = True

    def hide(self):
        """Hide emulator window (send to background)."""
        if self._window_id:
            subprocess.run(
                ['wmctrl', '-i', '-r', self._window_id, '-b', 'add,hidden'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            self._is_visible = False

    def is_visible(self):
        """Return True if emulator is currently visible."""
        return self._is_visible

    def is_running(self):
        """Return True if emulator process is running."""
        if self._process is None:
            return False
        return self._process.poll() is None

    def stop(self):
        """Terminate emulator process."""
        if self._process:
            self._process.terminate()
            try:
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None
            self._window_id = None
            self._is_visible = False

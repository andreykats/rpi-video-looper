import logging
import os
import pickle
import queue
import sys
import threading
import time

import RPi.GPIO as GPIO

from .encoder import create_backend

log = logging.getLogger('looper.relay')
elog = logging.getLogger('looper.encoder')

# Create a queue
relay_queue = queue.Queue()

# GPIO pins for relay control (modify as needed)
RELAY_UP_PIN = 27  # Frequency up relay
RELAY_DOWN_PIN = 22  # Frequency down relay
RELAY_BAND_PIN = 17  # Band selector relay

# Set up GPIO for Relays
GPIO.setmode(GPIO.BCM)  # Use Broadcom pin numbering
GPIO.setwarnings(False)  # Disable warnings if pins are already in use

# Channel mapping: TV channel -> (band, rf_frequency)
# Arduino sends channel number (0-13) directly via I2C
# 0 = no position (dead zone), 1-13 = channel numbers
# Band 1: RF channels 2-6 correspond to TV channels 2-6
# Band 2: RF channels 16-22 correspond to TV channels 7-13
CHANNEL_MAP = {
    1: None,
    2: (1, 2),
    3: (1, 3),
    4: (1, 4),
    5: (1, 5),
    6: (1, 6),
    7: (2, 16),
    8: (2, 17),
    9: (2, 18),
    10: (2, 19),
    11: (2, 20),
    12: (2, 21),
    13: (2, 22),
}

DEFAULT_STATE_FILE = 'previous_values.pkl'

class ChannelSwitcher:
    def __init__(self, on_channel_change=None, config=None, state_file=None):
        self.previous_channel = 0
        self.previous_band = 1  # Default to band 1
        # Track frequency per band (each band has independent RF frequency range)
        # Band 1: RF 2-6, Band 2: RF 16-22
        self.frequency_by_band = {1: 2, 2: 16}  # Default to starting frequency per band
        self.on_channel_change = on_channel_change
        self._state_file = state_file or DEFAULT_STATE_FILE
        self._stop_event = threading.Event()

        # Build encoder backend (i2c by default; mock available via config).
        self._backend = create_backend(config) if config is not None else _build_default_backend()

        # Load previously set frequencies and band from file
        self.frequency_by_band, self.previous_band = self.load_previous_values()

        # Track which bands we've visited this session (hardware remembers per-band frequency)
        # Start with previous_band since we're already on it
        self.visited_bands = {self.previous_band}

        elog.info('switcher init frequency_by_band=%s previous_band=%s',
                  self.frequency_by_band, self.previous_band)

        self.initialize_relays()
        log.info('relays init up=%d down=%d band=%d', RELAY_UP_PIN, RELAY_DOWN_PIN, RELAY_BAND_PIN)

        # Start a thread to execute the relay commands
        self._relay_thread = threading.Thread(
            target=self.execute_relay_commands, name='relay_executor', daemon=True
        )
        self._relay_thread.start()
        log.info('relay executor thread started')

    def stop(self):
        """Signal the switcher and relay-executor loops to exit."""
        self._stop_event.set()

    def start(self):
        while not self._stop_event.is_set():
            self.change_channel()
            # Tiny sleep so the poll cadence is predictable; without it the
            # loop runs as fast as the I2C bus allows and the mock backend's
            # FIFO drain coalesces every burst into one read.
            time.sleep(0.005)

    def get_channel_info(self, channel_number):
        """Get band and frequency for a channel number (0-13).
        Returns (channel, band, frequency) tuple.
        Channel 0 or unmapped channels return (None, None, None)."""
        if channel_number == 0 or channel_number not in CHANNEL_MAP:
            return (None, None, None)
        mapping = CHANNEL_MAP[channel_number]
        if mapping is None:
            return (channel_number, None, None)  # Channel exists but not used
        band, frequency = mapping
        return (channel_number, band, frequency)

    def get_band_for_channel(self, channel):
        """Get the band number for a channel from CHANNEL_MAP."""
        if channel not in CHANNEL_MAP or CHANNEL_MAP[channel] is None:
            return None
        return CHANNEL_MAP[channel][0]  # Return band from (band, frequency) tuple

    def _tune_to_frequency(self, band, target_frequency):
        """Tune to target frequency within a band by pulsing relays."""
        current_frequency = self.frequency_by_band.get(band, target_frequency)

        if target_frequency == current_frequency:
            log.info('tune skip band=%d freq=%d already-tuned', band, target_frequency)
            return

        if target_frequency > current_frequency:
            # Frequency UP
            pulses = target_frequency - current_frequency
            log.info('tune up band=%d from=%d to=%d pulses=%d',
                     band, current_frequency, target_frequency, pulses)
            for _ in range(pulses):
                self.relay_channel_up()
        else:
            # Frequency DOWN
            pulses = current_frequency - target_frequency
            log.info('tune down band=%d from=%d to=%d pulses=%d',
                     band, current_frequency, target_frequency, pulses)
            for _ in range(pulses):
                self.relay_channel_down()

        self.frequency_by_band[band] = target_frequency
        self.save_previous_values()

    def change_channel(self):
        # Read channel number from Arduino (0-13)
        channel_number = self.read_remote_rotary_encoder()

        # Get band and frequency for this channel
        channel, band, frequency = self.get_channel_info(channel_number)

        # Channel 0 or invalid = do nothing
        if channel is None:
            return None

        # Only act if channel has changed
        if channel == self.previous_channel:
            return None

        elog.info('publish target=%d prev=%d', channel, self.previous_channel)

        # Channel exists but not mapped (e.g., channel 1)
        if band is None:
            log.info('channel %d unmapped: skipping relays', channel)
            # Still call callback and update state
            if self.on_channel_change is not None:
                self.on_channel_change(channel, self.previous_channel)
            self.previous_channel = channel
            return None

        # Handle band switching first (before frequency tuning)
        self._switch_to_band(band)

        # Handle relay tuning
        self._tune_to_frequency(band, frequency)

        # Call callback with both current and previous channel
        if self.on_channel_change is not None:
            self.on_channel_change(channel, self.previous_channel)

        # Update state
        self.previous_channel = channel

    def read_remote_rotary_encoder(self):
        return self._backend.read()

    def relay_channel_up(self):
        log.debug('pulse up queued gpio=%d', RELAY_UP_PIN)
        def engage():
            log.debug('pulse-execute kind=up gpio=%d high', RELAY_UP_PIN)
            GPIO.output(RELAY_UP_PIN, GPIO.HIGH)  # Turn on the relay

        def disengage():
            log.debug('pulse-execute kind=up gpio=%d low', RELAY_UP_PIN)
            GPIO.output(RELAY_UP_PIN, GPIO.LOW)  # Turn off the relay

        relay_queue.put(engage)  # Add function to queue
        relay_queue.put(disengage)  # Add function to queue

    def relay_channel_down(self):
        log.debug('pulse down queued gpio=%d', RELAY_DOWN_PIN)
        def engage():
            log.debug('pulse-execute kind=down gpio=%d high', RELAY_DOWN_PIN)
            GPIO.output(RELAY_DOWN_PIN, GPIO.HIGH)  # Turn on the relay

        def disengage():
            log.debug('pulse-execute kind=down gpio=%d low', RELAY_DOWN_PIN)
            GPIO.output(RELAY_DOWN_PIN, GPIO.LOW)  # Turn off the relay

        relay_queue.put(engage)  # Add function to queue
        relay_queue.put(disengage)  # Add function to queue

    def relay_band_press(self):
        log.debug('pulse band queued gpio=%d', RELAY_BAND_PIN)
        def engage():
            log.debug('pulse-execute kind=band gpio=%d high', RELAY_BAND_PIN)
            GPIO.output(RELAY_BAND_PIN, GPIO.HIGH)  # Turn on the relay

        def disengage():
            log.debug('pulse-execute kind=band gpio=%d low', RELAY_BAND_PIN)
            GPIO.output(RELAY_BAND_PIN, GPIO.LOW)  # Turn off the relay

        relay_queue.put(engage)  # Add function to queue
        relay_queue.put(disengage)  # Add function to queue

    def _switch_to_band(self, target_band):
        """Switch to target band by pulsing the band relay.
        The modulator has 5 bands that cycle: 1 → 2 → 3 → 4 → 5 → 1..."""
        if target_band == self.previous_band:
            log.info('band skip target=%d already-on-band', target_band)
            return

        # Calculate pulses needed to get from current band to target band (cycling through 5 bands)
        if target_band > self.previous_band:
            pulses = target_band - self.previous_band
        else:
            # Wrap around: e.g., from band 2 to band 1 = 5 - 2 + 1 = 4 pulses
            pulses = 5 - self.previous_band + target_band

        log.info('band switch from=%d to=%d pulses=%d',
                 self.previous_band, target_band, pulses)
        for _ in range(pulses):
            self.relay_band_press()

        # Add 2 second delay for modulator to settle on new band
        def band_settle_delay():
            log.info('band settle waiting=2s')
            time.sleep(2.0)
            log.info('band settle done')
        relay_queue.put(band_settle_delay)

        # Only reset frequency on FIRST entry to a band (hardware remembers per-band)
        if target_band not in self.visited_bands:
            # First entry - hardware is at band's starting position
            band_start_frequencies = {1: 2, 2: 16}
            self.frequency_by_band[target_band] = band_start_frequencies[target_band]
            self.visited_bands.add(target_band)
            log.info('band first-entry target=%d freq=%d',
                     target_band, band_start_frequencies[target_band])
        else:
            log.info('band returning target=%d freq=%d',
                     target_band, self.frequency_by_band[target_band])

        self.previous_band = target_band
        self.save_previous_values()

    def execute_relay_commands(self):
        log.info('relay executor running')
        while not self._stop_event.is_set():
            try:
                relay_function = relay_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                relay_function()
            except Exception as e:
                log.warning('relay exec error: %s', e)
            finally:
                relay_queue.task_done()
            # Add a delay before processing the next item
            time.sleep(0.03)

    def save_previous_values(self):
        """Save frequency_by_band and previous_band to a file."""
        try:
            with open(self._state_file, 'wb') as f:
                pickle.dump((self.frequency_by_band, self.previous_band), f)
        except OSError as e:
            log.warning('state save failed path=%s err=%s', self._state_file, e)

    def load_previous_values(self):
        """Load frequency_by_band and previous_band from a file.
        Returns (frequency_by_band dict, band) tuple."""
        default_frequencies = {1: 2, 2: 16}  # Default to starting frequency per band
        try:
            with open(self._state_file, 'rb') as f:
                data = pickle.load(f)
                # Handle current format: (frequency_by_band dict, band)
                if isinstance(data, tuple) and isinstance(data[0], dict):
                    return (data[0], data[1])
                # Handle old format: (single_frequency, band)
                elif isinstance(data, tuple):
                    old_freq, old_band = data
                    # Migrate: put old frequency in current band
                    frequencies = default_frequencies.copy()
                    if old_band in frequencies:
                        frequencies[old_band] = old_freq
                    return (frequencies, old_band)
                else:
                    # Very old format: just frequency
                    frequencies = default_frequencies.copy()
                    frequencies[1] = data
                    return (frequencies, 1)
        except FileNotFoundError:
            return (default_frequencies, 1)
        except (EOFError, pickle.UnpicklingError):
            return (default_frequencies, 1)

    def initialize_relays(self):
        # Active-HIGH relays: HIGH = on, LOW = off
        GPIO.setup(RELAY_UP_PIN, GPIO.OUT, initial=GPIO.LOW)  # Start LOW (relay off)
        GPIO.setup(RELAY_DOWN_PIN, GPIO.OUT, initial=GPIO.LOW)  # Start LOW (relay off)
        GPIO.setup(RELAY_BAND_PIN, GPIO.OUT, initial=GPIO.LOW)  # Start LOW (relay off)


def _build_default_backend():
    """Used when ChannelSwitcher is constructed without a config (e.g.
    the __main__ debug entry point below). Defaults to I2C."""
    from .encoder import I2CEncoderBackend
    return I2CEncoderBackend()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s.%(msecs)03d %(threadName)s %(name)s %(levelname)s %(message)s',
        datefmt='%H:%M:%S',
    )

    def handle_switch(channel, prev):
        log.info('callback channel=%d prev=%d', channel, prev)

    try:
        controller = ChannelSwitcher(handle_switch)
        controller.start()
    except KeyboardInterrupt:
        log.info('exiting; cleaning up GPIO')
        controller.stop()
        GPIO.cleanup()
        sys.exit(0)

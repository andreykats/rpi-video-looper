import logging
import os
import pickle
import sys
import threading
import time

import RPi.GPIO as GPIO

from .encoder import create_backend
from .latest_slot import LatestSlot

log = logging.getLogger('looper.relay')
elog = logging.getLogger('looper.encoder')

# GPIO pins for relay control (modify as needed)
RELAY_UP_PIN = 27  # Frequency up relay
RELAY_DOWN_PIN = 22  # Frequency down relay
RELAY_BAND_PIN = 17  # Band selector relay

PULSE_WIDTH_S = 0.03   # how long a relay stays HIGH
PULSE_GAP_S = 0.03     # gap between successive pulses
BAND_SETTLE_S = 2.0    # modulator's own settle delay after a band cycle

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
    6: (1, 5),
    7: (2, 16),
    8: (2, 17),
    9: (2, 18),
    10: (2, 19),
    11: (2, 20),
    12: (2, 21),
    13: (2, 22),
}

DEFAULT_STATE_FILE = '/var/lib/video_looper/previous_values.pkl'


class ChannelSwitcher:
    def __init__(self, on_channel_change=None, config=None, state_file=None):
        self.previous_channel = 0
        self.previous_band = 1  # Default to band 1
        # Per-band frequency tracking. Each band has its own RF range and
        # the modulator hardware remembers the last freq tuned per band.
        self.frequency_by_band = {1: 2, 2: 16}
        self.on_channel_change = on_channel_change

        if state_file is None and config is not None:
            state_file = config.get('encoder', 'state_file',
                                    fallback=DEFAULT_STATE_FILE).strip()
        self._state_file = state_file or DEFAULT_STATE_FILE

        self._stop_event = threading.Event()

        # Encoder backend (i2c by default; mock available via config).
        self._backend = create_backend(config) if config is not None else _build_default_backend()

        # Load previously set frequencies and band from file
        self.frequency_by_band, self.previous_band = self.load_previous_values()

        # Track which bands we've visited this session (hardware remembers
        # per-band frequency only after first entry).
        self.visited_bands = {self.previous_band}

        elog.info('switcher init frequency_by_band=%s previous_band=%s',
                  self.frequency_by_band, self.previous_band)

        self.initialize_relays()
        log.info('relays init up=%d down=%d band=%d',
                 RELAY_UP_PIN, RELAY_DOWN_PIN, RELAY_BAND_PIN)

        # Latest-target slot. The encoder thread publishes (band, freq)
        # tuples; the executor takes the *latest* and computes a minimum
        # pulse train against current hardware state. Burst spins
        # coalesce automatically — no queued pulse backlog.
        self._relay_wake = threading.Event()
        self._relay_target_slot = LatestSlot(self._relay_wake)

        self._relay_thread = threading.Thread(
            target=self._relay_executor, name='relay_executor', daemon=True
        )
        self._relay_thread.start()
        log.info('relay executor thread started')

    def stop(self):
        """Signal the switcher and relay-executor loops to exit."""
        self._stop_event.set()
        self._relay_wake.set()  # unblock executor

    def start(self):
        while not self._stop_event.is_set():
            self.change_channel()
            # Predictable poll cadence — also lets the mock encoder's
            # FIFO drain meaningfully between reads in scenario tests.
            time.sleep(0.005)

    def get_channel_info(self, channel_number):
        """Get (channel, band, frequency) for a channel number (0-13).
        Channel 0 or unmapped channels return (None, None, None) /
        (channel, None, None) respectively."""
        if channel_number == 0 or channel_number not in CHANNEL_MAP:
            return (None, None, None)
        mapping = CHANNEL_MAP[channel_number]
        if mapping is None:
            return (channel_number, None, None)
        band, frequency = mapping
        return (channel_number, band, frequency)

    def get_band_for_channel(self, channel):
        if channel not in CHANNEL_MAP or CHANNEL_MAP[channel] is None:
            return None
        return CHANNEL_MAP[channel][0]

    def change_channel(self):
        channel_number = self.read_remote_rotary_encoder()
        channel, band, frequency = self.get_channel_info(channel_number)

        if channel is None:
            return None
        if channel == self.previous_channel:
            return None

        elog.info('publish target=%d prev=%d', channel, self.previous_channel)

        if self.on_channel_change is not None:
            self.on_channel_change(channel, self.previous_channel)

        if band is None:
            log.info('channel %d unmapped: skipping relays', channel)
        else:
            self._relay_target_slot.publish((band, frequency))

        self.previous_channel = channel

    def read_remote_rotary_encoder(self):
        return self._backend.read()

    # ------------------------------------------------------------------
    # Relay executor
    # ------------------------------------------------------------------

    def _relay_executor(self):
        """Single owner of relay GPIO. Coalesces published targets:
        wakes once on a publish, then drains the slot — taking the
        *latest* target each time. A burst of publishes during a pulse
        train is handled by re-taking after the train completes; the
        next train computes a fresh minimum delta from the new state.
        """
        log.info('relay executor running')
        while not self._stop_event.is_set():
            if not self._relay_wake.wait(timeout=0.2):
                continue
            while not self._stop_event.is_set():
                target = self._relay_target_slot.take()
                if target is None:
                    break
                try:
                    self._execute_target(target)
                except Exception as e:
                    log.exception('relay executor error: %s', e)
            self._relay_wake.clear()

    def _execute_target(self, target):
        target_band, target_freq = target

        # Band phase
        if target_band != self.previous_band:
            pulses = (target_band - self.previous_band) % 5
            if pulses == 0:
                pulses = 5
            log.info('band switch from=%d to=%d pulses=%d',
                     self.previous_band, target_band, pulses)
            for _ in range(pulses):
                self._pulse(RELAY_BAND_PIN, 'band')

            log.info('band settle waiting=%.1fs', BAND_SETTLE_S)
            time.sleep(BAND_SETTLE_S)
            log.info('band settle done')

            # Hardware resets per-band freq on first entry only.
            if target_band not in self.visited_bands:
                band_start_frequencies = {1: 2, 2: 16}
                start = band_start_frequencies.get(target_band, target_freq)
                self.frequency_by_band[target_band] = start
                self.visited_bands.add(target_band)
                log.info('band first-entry target=%d freq=%d', target_band, start)
            else:
                log.info('band returning target=%d freq=%d',
                         target_band, self.frequency_by_band[target_band])

            self.previous_band = target_band
            self.save_previous_values()
        else:
            log.info('band skip target=%d already-on-band', target_band)

        # Frequency phase
        current_freq = self.frequency_by_band.get(target_band, target_freq)
        if target_freq == current_freq:
            log.info('tune skip band=%d freq=%d already-tuned',
                     target_band, target_freq)
            return

        if target_freq > current_freq:
            pulses = target_freq - current_freq
            log.info('tune up band=%d from=%d to=%d pulses=%d',
                     target_band, current_freq, target_freq, pulses)
            for _ in range(pulses):
                self._pulse(RELAY_UP_PIN, 'up')
        else:
            pulses = current_freq - target_freq
            log.info('tune down band=%d from=%d to=%d pulses=%d',
                     target_band, current_freq, target_freq, pulses)
            for _ in range(pulses):
                self._pulse(RELAY_DOWN_PIN, 'down')

        self.frequency_by_band[target_band] = target_freq
        self.save_previous_values()

    def _pulse(self, pin, kind):
        """Drive one pulse on the given GPIO pin: HIGH for PULSE_WIDTH_S,
        LOW, then a PULSE_GAP_S gap before the caller sends the next."""
        log.debug('pulse-execute kind=%s gpio=%d high', kind, pin)
        GPIO.output(pin, GPIO.HIGH)
        time.sleep(PULSE_WIDTH_S)
        log.debug('pulse-execute kind=%s gpio=%d low', kind, pin)
        GPIO.output(pin, GPIO.LOW)
        time.sleep(PULSE_GAP_S)

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def save_previous_values(self):
        try:
            d = os.path.dirname(self._state_file)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(self._state_file, 'wb') as f:
                pickle.dump((self.frequency_by_band, self.previous_band), f)
        except OSError as e:
            log.warning('state save failed path=%s err=%s', self._state_file, e)

    def load_previous_values(self):
        """Load (frequency_by_band, band) from state file, with format migrations."""
        default_frequencies = {1: 2, 2: 16}
        try:
            with open(self._state_file, 'rb') as f:
                data = pickle.load(f)
                if isinstance(data, tuple) and isinstance(data[0], dict):
                    return (data[0], data[1])
                elif isinstance(data, tuple):
                    old_freq, old_band = data
                    frequencies = default_frequencies.copy()
                    if old_band in frequencies:
                        frequencies[old_band] = old_freq
                    return (frequencies, old_band)
                else:
                    frequencies = default_frequencies.copy()
                    frequencies[1] = data
                    return (frequencies, 1)
        except FileNotFoundError:
            return (default_frequencies, 1)
        except (EOFError, pickle.UnpicklingError):
            return (default_frequencies, 1)

    def initialize_relays(self):
        # Active-HIGH relays: HIGH = on, LOW = off
        GPIO.setup(RELAY_UP_PIN, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(RELAY_DOWN_PIN, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(RELAY_BAND_PIN, GPIO.OUT, initial=GPIO.LOW)


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

import smbus
import time
import sys
import RPi.GPIO as GPIO
import queue
import threading
import pickle

# Create a queue
relay_queue = queue.Queue()

# Rotary encoder's I2C address
I2C_ADDRESS = 0x8

# GPIO pins for relay control (modify as needed)
RELAY_UP_PIN = 27  # Frequency up relay
RELAY_DOWN_PIN = 22  # Frequency down relay
RELAY_BAND_PIN = 17  # Band selector relay

# Initialize I2C bus
bus = smbus.SMBus(1)  # Use bus 1 (check your specific Pi model)

# Set up GPIO for Relays
GPIO.setmode(GPIO.BCM)  # Use Broadcom pin numbering
GPIO.setwarnings(False)  # Disable warnings if pins are already in use

# Channel mapping: TV channel -> (band, rf_frequency)
# Arduino sends channel number (0-13) directly via I2C
# 0 = no position (dead zone), 1-13 = channel numbers
# Band 1: RF channels 2-6 correspond to TV channels 2-6
# Band 2: RF channels 16-22 correspond to TV channels 7-13
CHANNEL_MAP = {
    1: None,  # Channel 1 not used
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

class ChannelSwitcher:
    def __init__(self, on_channel_change=None):
        self.previous_channel = 0
        self.previous_band = 1  # Default to band 1
        # Track frequency per band (each band has independent RF frequency range)
        # Band 1: RF 2-6, Band 2: RF 16-22
        self.frequency_by_band = {1: 2, 2: 16}  # Default to lowest frequency per band
        self.on_channel_change = on_channel_change

        # Load previously set frequencies and band from file
        self.frequency_by_band, self.previous_band = self.load_previous_values()
        print(f"ChannelSwitcher initialized: frequency_by_band = {self.frequency_by_band}, previous_band = {self.previous_band}")

        self.initialize_relays()
        print(f"Relays initialized on GPIO pins: UP={RELAY_UP_PIN}, DOWN={RELAY_DOWN_PIN}, BAND={RELAY_BAND_PIN}")

        # Start a thread to execute the relay commands
        threading.Thread(target=self.execute_relay_commands, daemon=True).start()
        print("Starting relay command executor thread...")

    def start(self):
        while True:
            self.change_channel()

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
            print(f"Already at frequency {target_frequency} on band {band}, skipping relay pulses")
            return

        if target_frequency > current_frequency:
            # Frequency UP
            pulses = target_frequency - current_frequency
            print(f"Tuning UP from {current_frequency} to {target_frequency} on band {band} ({pulses} pulses)")
            for _ in range(pulses):
                self.relay_channel_up()
        else:
            # Frequency DOWN
            pulses = current_frequency - target_frequency
            print(f"Tuning DOWN from {current_frequency} to {target_frequency} on band {band} ({pulses} pulses)")
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

        # Channel exists but not mapped (e.g., channel 1)
        if band is None:
            print(f"Channel {channel} is not mapped - relays will not activate")
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
        return int(bus.read_byte(I2C_ADDRESS))

    def relay_channel_up(self):
        print(f"  → Relay UP queued (GPIO {RELAY_UP_PIN})")
        def engage():
            print(f"    → GPIO {RELAY_UP_PIN} HIGH (relay on)")
            GPIO.output(RELAY_UP_PIN, GPIO.HIGH)  # Turn on the relay

        def disengage():
            print(f"    → GPIO {RELAY_UP_PIN} LOW (relay off)")
            GPIO.output(RELAY_UP_PIN, GPIO.LOW)  # Turn off the relay

        relay_queue.put(engage)  # Add function to queue
        relay_queue.put(disengage)  # Add function to queue

    def relay_channel_down(self):
        print(f"  → Relay DOWN queued (GPIO {RELAY_DOWN_PIN})")
        def engage():
            print(f"    → GPIO {RELAY_DOWN_PIN} HIGH (relay on)")
            GPIO.output(RELAY_DOWN_PIN, GPIO.HIGH)  # Turn on the relay

        def disengage():
            print(f"    → GPIO {RELAY_DOWN_PIN} LOW (relay off)")
            GPIO.output(RELAY_DOWN_PIN, GPIO.LOW)  # Turn off the relay

        relay_queue.put(engage)  # Add function to queue
        relay_queue.put(disengage)  # Add function to queue

    def relay_band_press(self):
        print(f"  → Relay BAND queued (GPIO {RELAY_BAND_PIN})")
        def engage():
            print(f"    → GPIO {RELAY_BAND_PIN} HIGH (relay on)")
            GPIO.output(RELAY_BAND_PIN, GPIO.HIGH)  # Turn on the relay

        def disengage():
            print(f"    → GPIO {RELAY_BAND_PIN} LOW (relay off)")
            GPIO.output(RELAY_BAND_PIN, GPIO.LOW)  # Turn off the relay

        relay_queue.put(engage)  # Add function to queue
        relay_queue.put(disengage)  # Add function to queue

    def _switch_to_band(self, target_band):
        """Switch to target band by pulsing the band relay.
        The modulator has 5 bands that cycle: 1 → 2 → 3 → 4 → 5 → 1..."""
        if target_band == self.previous_band:
            print(f"Already at band {target_band}, skipping band relay pulses")
            return

        # Calculate pulses needed to get from current band to target band (cycling through 5 bands)
        if target_band > self.previous_band:
            pulses = target_band - self.previous_band
        else:
            # Wrap around: e.g., from band 2 to band 1 = 5 - 2 + 1 = 4 pulses
            pulses = 5 - self.previous_band + target_band

        print(f"Switching from band {self.previous_band} to band {target_band} ({pulses} pulses)")
        for _ in range(pulses):
            self.relay_band_press()

        self.previous_band = target_band
        self.save_previous_values()

    def execute_relay_commands(self):
        print("Relay command executor thread started")
        while True:
            # Get a function from the queue and execute it
            relay_function = relay_queue.get()
            relay_function()
            relay_queue.task_done()

            # Add a delay before processing the next item
            time.sleep(0.03)  # Adjust the delay as needed

    def save_previous_values(self):
        """Save frequency_by_band and previous_band to a file."""
        with open('previous_values.pkl', 'wb') as f:
            pickle.dump((self.frequency_by_band, self.previous_band), f)

    def load_previous_values(self):
        """Load frequency_by_band and previous_band from a file.
        Returns (frequency_by_band dict, band) tuple."""
        default_frequencies = {1: 2, 2: 16}  # Default to lowest frequency per band
        try:
            with open('previous_values.pkl', 'rb') as f:
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


if __name__ == "__main__":
    def handle_switch(channel, direction):
        print(f"DEBUG: channel changed {direction} to: {channel}")

    try:
        controller = ChannelSwitcher(handle_switch)
        controller.start()

    except KeyboardInterrupt:
        print("\nExiting. Cleanup GPIO...")
        GPIO.cleanup()
        sys.exit(0)

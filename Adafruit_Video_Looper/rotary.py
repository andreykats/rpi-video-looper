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
RELAY_UP_PIN = 22  # Frequency up relay
RELAY_DOWN_PIN = 27  # Frequency down relay

# Initialize I2C bus
bus = smbus.SMBus(1)  # Use bus 1 (check your specific Pi model)

# Set up GPIO for Relays
GPIO.setmode(GPIO.BCM)  # Use Broadcom pin numbering

# Channel to frequency mapping
# Arduino sends channel number (0-13) directly via I2C
# 0 = no position (dead zone), 1-13 = channel numbers
CHANNEL_FREQUENCY_MAP = {
    1: None, 2: None, 3: None, 4: None, 5: None, 6: None,
    7: 16, 8: 18, 9: None, 10: 20, 11: 21, 12: 21, 13: 22
}

class ChannelSwitcher:
    def __init__(self, on_channel_change=None):
        self.previous_channel = 0
        self.previous_frequency = 0
        self.on_channel_change = on_channel_change

        # Load previously set frequency from file
        self.previous_frequency = self.load_previous_values()

        self.initialize_relays()

        # Start a thread to execute the relay commands
        threading.Thread(target=self.execute_relay_commands, daemon=True).start()

    def start(self):
        while True:
            self.change_channel()

    def get_channel_and_frequency(self, channel_number):
        """Get frequency for a channel number (0-13).
        Returns (channel, frequency) tuple.
        Channel 0 returns (None, None) to indicate no action."""
        if channel_number == 0 or channel_number not in CHANNEL_FREQUENCY_MAP:
            return (None, None)
        return (channel_number, CHANNEL_FREQUENCY_MAP[channel_number])

    def _tune_to_frequency(self, target_frequency):
        """Tune to target frequency by pulsing relays."""
        if target_frequency == self.previous_frequency:
            return

        if target_frequency > self.previous_frequency:
            # Frequency UP
            pulses = target_frequency - self.previous_frequency
            for _ in range(pulses):
                self.relay_channel_up()
        else:
            # Frequency DOWN
            pulses = self.previous_frequency - target_frequency
            for _ in range(pulses):
                self.relay_channel_down()

        self.previous_frequency = target_frequency
        self.save_previous_values(target_frequency)

    def change_channel(self):
        # Read channel number from Arduino (0-13)
        channel_number = self.read_remote_rotary_encoder()

        # Get frequency for this channel
        channel, frequency = self.get_channel_and_frequency(channel_number)

        # Channel 0 or invalid = do nothing
        if channel is None:
            return None

        # Only act if channel has changed
        if channel == self.previous_channel:
            return None

        # Handle relay tuning if frequency is specified
        if frequency is not None:
            self._tune_to_frequency(frequency)

        # Call callback with both current and previous channel
        if self.on_channel_change is not None:
            self.on_channel_change(channel, self.previous_channel)

        # Update state
        self.previous_channel = channel

    def read_remote_rotary_encoder(self):
        return int(bus.read_byte(I2C_ADDRESS))

    def relay_channel_up(self):
        def engage():
            GPIO.output(RELAY_UP_PIN, GPIO.HIGH)  # Turn on the relay

        def disengage():
            GPIO.output(RELAY_UP_PIN, GPIO.LOW)  # Turn off the relay

        relay_queue.put(engage)  # Add function to queue
        relay_queue.put(disengage)  # Add function to queue

    def relay_channel_down(self):
        def engage():
            GPIO.output(RELAY_DOWN_PIN, GPIO.HIGH)  # Turn on the relay

        def disengage():
            GPIO.output(RELAY_DOWN_PIN, GPIO.LOW)  # Turn off the relay

        relay_queue.put(engage)  # Add function to queue
        relay_queue.put(disengage)  # Add function to queue

    def execute_relay_commands(self):
        while True:
            # Get a function from the queue and execute it
            relay_function = relay_queue.get()
            relay_function()
            relay_queue.task_done()

            # Add a delay before processing the next item
            time.sleep(0.03)  # Adjust the delay as needed

    # Save previous_frequency and previous_source to a file
    def save_previous_values(self, previous_frequency):
        with open('previous_values.pkl', 'wb') as f:
            pickle.dump((previous_frequency), f)

    # Load previous_frequency and previous_source from a file
    def load_previous_values(self):
        try:
            with open('previous_values.pkl', 'rb') as f:
                return pickle.load(f)
        except FileNotFoundError:
            return 0  # Return 0 and None if file does not exist

    def initialize_relays(self):
        GPIO.setup(RELAY_UP_PIN, GPIO.OUT, initial=GPIO.LOW)  # Set relay pin as output and start in a low state (relay off)
        GPIO.setup(RELAY_DOWN_PIN, GPIO.OUT, initial=GPIO.LOW)  # Set relay pin as output and start in a low state (relay off)  


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

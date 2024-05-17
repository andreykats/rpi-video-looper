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

# GPIO pin for the crelay (modify as needed)
RELAY_UP_PIN = 17  # Use GPIO 17 (Pin 11) or any other available pin
RELAY_DOWN_PIN = 27  # Use GPIO 17 (Pin 11) or any other available pin
RELAY_SOURCE_PIN = 22  # Use GPIO 17 (Pin 11) or any other available pin

# Initialize I2C bus
bus = smbus.SMBus(1)  # Use bus 1 (check your specific Pi model)

# Set up GPIO for Relays
GPIO.setmode(GPIO.BCM)  # Use Broadcom pin numbering

# Channel list with rotary encoder position and frequency
# (channel, rotary_position, modulator frequency)
CHANNEL_LIST = [
    (1, 39, None),
    (1, 0, None),
    (2, 2, None),
    (2, 3, None),
    (3, 6, None),
    (4, 8, None),
    (4, 9, None),
    (5, 12, None),
    (6, 15, None),
    (6, 16, None),
    (7, 18, 16),
    (8, 21, 18),
    (8, 22, 18),
    (9, 24, None),
    (10, 27, 20),
    (10, 28, 20),
    (11, 30, 21),
    (11, 31, 21),
    (12, 33, 21),
    (12, 34, 21),
    (13, 36, 22),
    (13, 37, 22)
]

class ChannelSwitcher:
    def __init__(self):
        self.previous_channel = 0
        self.previous_frequency = 0
        self.current_source = 'hdmi'
        self.initialize_relays()

    def get_channel_from_position(self, position):
        for channel, rotary_position, frequency in CHANNEL_LIST:
            if position == int(rotary_position):
                return (channel, frequency)
        return (None, None)

    def change_channel(self, callback=None):
        # Read the rotary encoder position
        rotary_position = self.read_remote_rotary_encoder()
        # Get coresponding channel
        channel, frequency = self.get_channel_from_position(rotary_position)

        # Print the angle (for debugging)
        #print(f"Encoder Position: {rotary_position}, Channel: {channel}, Frequency: {frequency}")

        # If the channel has changed, send change channel command
        # global previous_channel
        # global previous_frequency
        # global previous_source

        if channel is None:
            return None

        if channel != previous_channel:
            if channel == 13:
                if self.current_source != 'hdmi':
                    self.relay_source_hdmi()
            else:
                if self.current_source != 'composite':
                    self.relay_source_composite()

            if channel > previous_channel:
                print(f"Channel UP: {channel}")
                if frequency is not None:
                    print(f"Switching to frequency: {frequency}")
                    if frequency is not None and frequency < previous_frequency:
                        for _ in range(previous_frequency - frequency):
                            self.relay_channel_down()
                    else:
                        for _ in range(frequency - previous_frequency):
                            self.relay_channel_up()

                    previous_frequency = frequency
                    self.save_previous_values(previous_frequency, self.current_source)

                    # Call the callback if it's provided
                    if callback is not None:
                        callback(channel)

            if channel < previous_channel:
                print(f"Channel DOWN: {channel}")
                if frequency is not None:
                    print(f"Switching to frequency: {frequency}")
                    if frequency > previous_frequency:
                        for _ in range(frequency - previous_frequency):
                            self.relay_channel_up()
                    else:
                        for _ in range(previous_frequency - frequency):
                            self.relay_channel_down()

                    previous_frequency = frequency
                    self.save_previous_values(previous_frequency, self.current_source)

                    # Call the callback if it's provided
                    if callback is not None:
                        callback(channel)
                        
            previous_channel = channel

    def read_remote_rotary_encoder(self):
        return int(bus.read_byte(I2C_ADDRESS))

    def relay_source_hdmi(self):
        print("Switching to HDMI")
        GPIO.output(RELAY_SOURCE_PIN, GPIO.LOW) 
        # global current_source
        self.current_source = 'hdmi'

    def relay_source_composite(self):
        print("Switching to Composite")
        GPIO.output(RELAY_SOURCE_PIN, GPIO.HIGH) 
        # global current_source
        self.current_source = 'composite'

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
            time.sleep(0.02)  # Adjust the delay as needed

    # Save previous_frequency and previous_source to a file
    def save_previous_values(self, previous_frequency, current_source):
        with open('previous_values.pkl', 'wb') as f:
            pickle.dump((previous_frequency, current_source), f)

    # Load previous_frequency and previous_source from a file
    def load_previous_values(self):
        try:
            with open('previous_values.pkl', 'rb') as f:
                return pickle.load(f)
        except FileNotFoundError:
            return 0, None  # Return 0 and None if file does not exist

    def initialize_relays(self):
        GPIO.setup(RELAY_UP_PIN, GPIO.OUT, initial=GPIO.LOW)  # Set relay pin as output and start in a low state (relay off)
        GPIO.setup(RELAY_DOWN_PIN, GPIO.OUT, initial=GPIO.LOW)  # Set relay pin as output and start in a low state (relay off)

        if self.current_source == 'hdmi':
            GPIO.setup(RELAY_SOURCE_PIN, GPIO.OUT, initial=GPIO.LOW)  
        else:
            GPIO.setup(RELAY_SOURCE_PIN, GPIO.OUT, initial=GPIO.HIGH)  

    def on_channel_change(self, channel):
        print(f"Channel changed to {channel}")

    def main(self):
        # Start a thread to execute the relay commands
        threading.Thread(target=self.execute_relay_commands, daemon=True).start()

        # Load previously set frequency from file
        self.previous_frequency, self.current_source = self.load_previous_values()
        
        # Initialize the relays
        self.initialize_relays()

        while True:
            self.change_channel(self.on_channel_change)
            # channel = change_channel()
            # if channel is not None:
            #     print(f"Channel: {channel}")

if __name__ == "__main__":
    try:
        controller = ChannelSwitcher()
        controller.main()

    except KeyboardInterrupt:
        print("\nExiting. Cleanup GPIO...")
        GPIO.cleanup()
        sys.exit(0)

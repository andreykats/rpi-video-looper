#!/usr/bin/env python3
"""Test relay methods in isolation to debug cross-activation issue"""

import RPi.GPIO as GPIO
import time
import queue
import threading

# GPIO pins
RELAY_UP_PIN = 22
RELAY_DOWN_PIN = 27

# Create a queue
relay_queue = queue.Queue()

# Setup GPIO
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

# Initialize relays (active-LOW)
GPIO.setup(RELAY_UP_PIN, GPIO.OUT, initial=GPIO.HIGH)
GPIO.setup(RELAY_DOWN_PIN, GPIO.OUT, initial=GPIO.HIGH)

print("Testing Relay Isolation")
print("=" * 60)
print(f"RELAY_UP_PIN = {RELAY_UP_PIN}")
print(f"RELAY_DOWN_PIN = {RELAY_DOWN_PIN}")
print()

def execute_relay_commands():
    """Execute relay commands from queue"""
    while True:
        relay_function = relay_queue.get()
        relay_function()
        relay_queue.task_done()
        time.sleep(0.03)

# Start relay executor thread
threading.Thread(target=execute_relay_commands, daemon=True).start()

def relay_channel_up():
    print(f"→ Relay UP queued (GPIO {RELAY_UP_PIN})")
    def engage():
        print(f"  → GPIO {RELAY_UP_PIN} LOW")
        GPIO.output(RELAY_UP_PIN, GPIO.LOW)

    def disengage():
        print(f"  → GPIO {RELAY_UP_PIN} HIGH")
        GPIO.output(RELAY_UP_PIN, GPIO.HIGH)

    relay_queue.put(engage)
    relay_queue.put(disengage)

def relay_channel_down():
    print(f"→ Relay DOWN queued (GPIO {RELAY_DOWN_PIN})")
    def engage():
        print(f"  → GPIO {RELAY_DOWN_PIN} LOW")
        GPIO.output(RELAY_DOWN_PIN, GPIO.LOW)

    def disengage():
        print(f"  → GPIO {RELAY_DOWN_PIN} HIGH")
        GPIO.output(RELAY_DOWN_PIN, GPIO.HIGH)

    relay_queue.put(engage)
    relay_queue.put(disengage)

try:
    print("Test 1: Activating ONLY relay UP (GPIO 22)")
    print("-" * 60)
    relay_channel_up()
    time.sleep(0.5)

    print()
    input("Did ONLY the UP relay click? Press Enter to continue...")
    print()

    print("Test 2: Activating ONLY relay DOWN (GPIO 27)")
    print("-" * 60)
    relay_channel_down()
    time.sleep(0.5)

    print()
    input("Did ONLY the DOWN relay click? Press Enter to continue...")
    print()

    print("Test 3: Activating relay DOWN 3 times in sequence")
    print("-" * 60)
    for i in range(3):
        print(f"Pulse {i+1}:")
        relay_channel_down()
        time.sleep(0.3)

    print()
    input("Did ONLY the DOWN relay click 3 times? Press Enter to finish...")

except KeyboardInterrupt:
    print("\nTest interrupted")
finally:
    GPIO.cleanup()
    print("\nGPIO cleanup done")

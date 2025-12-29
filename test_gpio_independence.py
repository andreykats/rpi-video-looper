#!/usr/bin/env python3
"""Test if GPIO 22 and 27 can be controlled independently"""

import RPi.GPIO as GPIO
import time

# GPIO pins
RELAY_UP_PIN = 22
RELAY_DOWN_PIN = 27

# Setup GPIO
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

print("GPIO Independence Test")
print("=" * 60)
print(f"Testing GPIO {RELAY_UP_PIN} and GPIO {RELAY_DOWN_PIN}")
print()

# Initialize both pins to HIGH (relay off for active-LOW)
GPIO.setup(RELAY_UP_PIN, GPIO.OUT, initial=GPIO.HIGH)
GPIO.setup(RELAY_DOWN_PIN, GPIO.OUT, initial=GPIO.HIGH)

print("Both GPIOs initialized to HIGH (relays should be OFF)")
print()

try:
    print("Test 1: Setting ONLY GPIO 22 to LOW")
    print("-" * 60)
    print(f"Setting GPIO {RELAY_UP_PIN} = LOW")
    GPIO.output(RELAY_UP_PIN, GPIO.LOW)

    # Read back the states
    state_22 = GPIO.input(RELAY_UP_PIN)
    state_27 = GPIO.input(RELAY_DOWN_PIN)
    print(f"GPIO {RELAY_UP_PIN} reads as: {state_22} (should be 0/LOW)")
    print(f"GPIO {RELAY_DOWN_PIN} reads as: {state_27} (should be 1/HIGH)")
    print()
    input("Did ONLY relay on GPIO 22 activate? Press Enter...")

    # Reset
    print("\nResetting both to HIGH...")
    GPIO.output(RELAY_UP_PIN, GPIO.HIGH)
    GPIO.output(RELAY_DOWN_PIN, GPIO.HIGH)
    time.sleep(1)
    print()

    print("Test 2: Setting ONLY GPIO 27 to LOW")
    print("-" * 60)
    print(f"Setting GPIO {RELAY_DOWN_PIN} = LOW")
    GPIO.output(RELAY_DOWN_PIN, GPIO.LOW)

    # Read back the states
    state_22 = GPIO.input(RELAY_UP_PIN)
    state_27 = GPIO.input(RELAY_DOWN_PIN)
    print(f"GPIO {RELAY_UP_PIN} reads as: {state_22} (should be 1/HIGH)")
    print(f"GPIO {RELAY_DOWN_PIN} reads as: {state_27} (should be 0/LOW)")
    print()
    input("Did ONLY relay on GPIO 27 activate? Press Enter...")

    # Reset
    print("\nResetting both to HIGH...")
    GPIO.output(RELAY_UP_PIN, GPIO.HIGH)
    GPIO.output(RELAY_DOWN_PIN, GPIO.HIGH)
    print()

    print("Test 3: Check for electrical bridging")
    print("-" * 60)
    print("Setting GPIO 22 LOW, waiting 0.5s, reading GPIO 27...")
    GPIO.output(RELAY_UP_PIN, GPIO.LOW)
    time.sleep(0.5)
    state_27 = GPIO.input(RELAY_DOWN_PIN)
    print(f"GPIO {RELAY_DOWN_PIN} reads as: {state_27} (should be 1/HIGH if independent)")
    if state_27 == 0:
        print("⚠️  WARNING: GPIO 27 is LOW when it should be HIGH!")
        print("   This indicates the pins are electrically connected!")

    GPIO.output(RELAY_UP_PIN, GPIO.HIGH)
    print()

except KeyboardInterrupt:
    print("\nTest interrupted")
finally:
    GPIO.cleanup()
    print("GPIO cleanup done")

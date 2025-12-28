#!/usr/bin/env python3
"""Quick relay test script to verify GPIO control"""

import RPi.GPIO as GPIO
import time

# GPIO pins
RELAY_UP_PIN = 22
RELAY_DOWN_PIN = 27

# Setup
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
GPIO.setup(RELAY_UP_PIN, GPIO.OUT, initial=GPIO.LOW)
GPIO.setup(RELAY_DOWN_PIN, GPIO.OUT, initial=GPIO.LOW)

print("Relay Test Script")
print("=" * 50)
print(f"UP Relay on GPIO {RELAY_UP_PIN}")
print(f"DOWN Relay on GPIO {RELAY_DOWN_PIN}")
print()

try:
    print("Test 1: Active HIGH logic (current setup)")
    print("-" * 50)

    print("Setting UP relay HIGH (should activate)...")
    GPIO.output(RELAY_UP_PIN, GPIO.HIGH)
    time.sleep(2)
    print("Setting UP relay LOW (should deactivate)...")
    GPIO.output(RELAY_UP_PIN, GPIO.LOW)
    time.sleep(1)

    print("Setting DOWN relay HIGH (should activate)...")
    GPIO.output(RELAY_DOWN_PIN, GPIO.HIGH)
    time.sleep(2)
    print("Setting DOWN relay LOW (should deactivate)...")
    GPIO.output(RELAY_DOWN_PIN, GPIO.LOW)
    time.sleep(1)

    print()
    input("Did the relays click? Press Enter to test inverted logic...")
    print()

    print("Test 2: Active LOW logic (inverted)")
    print("-" * 50)

    print("Setting UP relay LOW (should activate)...")
    GPIO.output(RELAY_UP_PIN, GPIO.LOW)
    time.sleep(2)
    print("Setting UP relay HIGH (should deactivate)...")
    GPIO.output(RELAY_UP_PIN, GPIO.HIGH)
    time.sleep(1)

    print("Setting DOWN relay LOW (should activate)...")
    GPIO.output(RELAY_DOWN_PIN, GPIO.LOW)
    time.sleep(2)
    print("Setting DOWN relay HIGH (should deactivate)...")
    GPIO.output(RELAY_DOWN_PIN, GPIO.HIGH)
    time.sleep(1)

    print()
    print("Test complete!")
    print("If the relays clicked in Test 2, your module is ACTIVE-LOW")

except KeyboardInterrupt:
    print("\nTest interrupted")
finally:
    GPIO.cleanup()
    print("GPIO cleanup done")

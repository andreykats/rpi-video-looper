#!/usr/bin/env python3
"""Test GPIO 27 specifically"""

import RPi.GPIO as GPIO
import time

PIN = 27

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

print("Testing GPIO 27 (RELAY_DOWN_PIN)")
print("=" * 50)

# Try to set it up
try:
    GPIO.setup(PIN, GPIO.OUT, initial=GPIO.HIGH)
    print(f"✓ GPIO {PIN} setup successful")
except Exception as e:
    print(f"✗ GPIO {PIN} setup failed: {e}")
    GPIO.cleanup()
    exit(1)

print()
print("Cycling relay 5 times...")
for i in range(5):
    print(f"  Cycle {i+1}: Setting LOW (should activate)...")
    GPIO.output(PIN, GPIO.LOW)
    time.sleep(1)
    print(f"  Cycle {i+1}: Setting HIGH (should deactivate)...")
    GPIO.output(PIN, GPIO.HIGH)
    time.sleep(1)

print()
print("Test complete. Did the relay click?")
print()
print("If NOT:")
print("  1. Check wiring to GPIO 27 (Pin 13 on header)")
print("  2. Try a different GPIO pin")
print("  3. Check if pin 27 is used by something else")
print("  4. Check relay module - is the relay working?")

GPIO.cleanup()

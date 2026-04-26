"""Single-value publisher with optional shared wake event.

Used to coalesce bursty inputs (encoder spins, end-of-video advances,
relay tuning targets) into "latest target wins" semantics: producers
overwrite each other; consumers wake once and act on the latest value.

Replaces unbounded queue.Queue patterns where intermediate events would
each trigger expensive work (subprocess spawns, relay pulse trains) that
gets immediately invalidated by the next event.
"""
import threading
from typing import Optional


class LatestSlot:
    """Single-value publisher; reader gets the latest value or nothing.

    A `wake_event` may be passed in so multiple slots share a single
    Event — the consumer wakes on any publish and drains every slot that
    feeds it. With no wake_event the slot owns its own Event.
    """

    def __init__(self, wake_event: Optional[threading.Event] = None):
        self._value = None
        self._has_value = False
        self._event = wake_event or threading.Event()
        self._lock = threading.Lock()

    def publish(self, value):
        with self._lock:
            self._value = value
            self._has_value = True
        self._event.set()

    def take(self):
        """Non-blocking read; returns latest value or None.

        Does not clear the wake event — when slots share an Event, only
        the consumer knows when it's safe to clear (all slots drained).
        """
        with self._lock:
            if not self._has_value:
                return None
            v = self._value
            self._value = None
            self._has_value = False
            return v

    def wait(self, timeout=None):
        """Block until a value is published or timeout. Returns the value
        or None on timeout. Clears the slot's own wake event on take.
        Only safe when this slot owns its Event (no fan-in).
        """
        if not self._event.wait(timeout):
            return None
        with self._lock:
            if not self._has_value:
                # Spurious wake (event set by something else); fine.
                self._event.clear()
                return None
            v = self._value
            self._value = None
            self._has_value = False
            self._event.clear()
            return v

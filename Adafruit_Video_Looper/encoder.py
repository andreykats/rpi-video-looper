"""Encoder backends for ChannelSwitcher.

Production reads from a rotary encoder over I2C. The mock backend reads
from a FIFO so scenarios can drive channel changes without hardware.
"""
import errno
import logging
import os
import time

I2C_ADDRESS = 0x8

log = logging.getLogger('looper.encoder')


class EncoderBackend:
    def read(self) -> int:
        raise NotImplementedError

    def close(self) -> None:
        pass


class I2CEncoderBackend(EncoderBackend):
    """Reads channel (0-13) from the Arduino over I2C bus 1."""

    def __init__(self):
        import smbus
        self._bus = smbus.SMBus(1)
        self._last_value = 0

    def read(self) -> int:
        try:
            value = int(self._bus.read_byte(I2C_ADDRESS))
            self._last_value = value
            return value
        except (TimeoutError, OSError) as e:
            log.debug('i2c read failed: %s', e)
            time.sleep(0.1)
            return self._last_value


class MockEncoderBackend(EncoderBackend):
    """Reads channel (0-13) from a FIFO, one int per line.

    Matches real-encoder semantics: read() always returns the *latest*
    value the writer dialed in, never an event stream. Bursts of writes
    that arrive between two read() calls coalesce to the last one.
    Scenarios that want to exercise per-publish behavior must space writes
    wider than the switcher's poll cadence.

    The looper starts before any test runner under supervisor, so the
    backend creates the FIFO if missing and opens it non-blocking.
    """

    def __init__(self, fifo_path: str = '/tmp/mock_encoder.fifo'):
        self._fifo_path = fifo_path
        self._fd = None
        self._buf = b''
        self._last_value = 0
        self._ensure_fifo()
        self._open_nonblocking()

    def _ensure_fifo(self):
        if os.path.exists(self._fifo_path):
            return
        try:
            os.mkfifo(self._fifo_path, 0o666)
            log.info('mock fifo created path=%s', self._fifo_path)
        except OSError as e:
            log.warning('mock fifo create failed path=%s err=%s', self._fifo_path, e)

    def _open_nonblocking(self):
        try:
            self._fd = os.open(self._fifo_path, os.O_RDONLY | os.O_NONBLOCK)
            log.info('mock fifo opened fd=%d', self._fd)
        except OSError as e:
            log.warning('mock fifo open failed: %s', e)
            self._fd = None

    def read(self) -> int:
        if self._fd is None:
            self._open_nonblocking()
            if self._fd is None:
                return self._last_value

        # Drain whatever is pending; latest complete line wins.
        try:
            while True:
                chunk = os.read(self._fd, 4096)
                if not chunk:
                    break
                self._buf += chunk
        except OSError as e:
            if e.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                log.warning('mock fifo read error: %s', e)
                self._reopen()
                return self._last_value

        if b'\n' in self._buf:
            lines = self._buf.split(b'\n')
            # Last element is partial (no trailing \n) or empty; keep as remainder.
            self._buf = lines[-1]
            for raw in reversed(lines[:-1]):
                line = raw.strip()
                if not line:
                    continue
                try:
                    value = int(line)
                except ValueError:
                    log.warning('mock fifo got non-int line: %r', line)
                    continue
                if 0 <= value <= 13:
                    self._last_value = value
                    break
                log.warning('mock fifo got out-of-range value: %d', value)

        return self._last_value

    def _reopen(self):
        self.close()
        self._open_nonblocking()

    def close(self):
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None


def create_backend(config) -> EncoderBackend:
    """Build the configured backend. Falls back to i2c if section missing."""
    backend = config.get('encoder', 'backend', fallback='i2c').strip().lower()
    if backend == 'mock':
        fifo = config.get('encoder', 'mock_fifo', fallback='/tmp/mock_encoder.fifo').strip()
        log.info('using mock encoder backend fifo=%s', fifo)
        return MockEncoderBackend(fifo)
    log.info('using i2c encoder backend')
    return I2CEncoderBackend()

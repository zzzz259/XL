"""A single, bounded gateway connection for explicitly allowlisted reads."""

from __future__ import annotations

import math
import struct
import time
from types import MappingProxyType

from . import gacha

REQUEST_RESPONSES = MappingProxyType({
    2301: 102301,
    2306: 102306,
    2307: 102307,
    10033: 110033,
    2001: 102001,
    2002: 102002,
    2003: 102003,
    3001: 103001,
    3004: 103004,
    10504: 110504,
})


class GameGatewaySession:
    """Authenticate once; count every business RPC, with no retries.

    Duration includes authentication and login. Blocking authentication and
    connection timeouts receive the remaining duration; the global deadline is
    checked immediately after each, and bounds every subsequent socket read.
    HTTP/DNS operations retain their underlying per-operation timeout semantics,
    rather than providing a process-level absolute interruption guarantee.
    A failed session cannot be reopened.
    An optional response_observer callable receives (command, raw_body) for every
    accepted business RPC response; authentication and the enter frame are never
    observed, and an observer failure stops the session with OBSERVER_FAILED.
    """

    def __init__(self, credentials, *, max_requests=64, max_duration=180,
                 request_delay=2, timeout=12, response_observer=None):
        if (type(max_requests) is not int or max_requests < 0
                or any(type(value) not in (int, float) or not math.isfinite(value)
                       for value in (max_duration, request_delay, timeout))
                or max_duration <= 0 or timeout <= 0 or request_delay < 0):
            raise gacha.GachaError('INVALID_SESSION_LIMITS')
        if response_observer is not None and not callable(response_observer):
            raise gacha.GachaError('INVALID_OBSERVER')
        self._credentials = credentials
        self._max_requests = max_requests
        self._max_duration = max_duration
        self._request_delay = request_delay
        self._timeout = timeout
        self._response_observer = response_observer
        self._requests_used = 0
        self._sequence = 1
        self._sock = None
        self._deadline = None
        self._last_sent = None
        self._entered = False
        self._started = False

    @property
    def requests_used(self):
        return self._requests_used

    def _remaining(self):
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise gacha.GachaError('SESSION_TIMEOUT')
        return remaining

    def close(self):
        sock, self._sock = self._sock, None
        self._credentials = None
        self._entered = False
        if sock is not None:
            try:
                sock.close()
            except Exception:  # noqa: BLE001, S110 - close is best-effort; no secret-bearing logs
                pass

    def __enter__(self):
        if self._started:
            raise gacha.GachaError('SESSION_CLOSED')
        self._started = True
        self._deadline = time.monotonic() + self._max_duration
        try:
            try:
                host, port = gacha.authenticate(
                    self._credentials, timeout=min(self._timeout, self._remaining()))
            except gacha.GachaError as error:
                safe = {'AUTH_NETWORK_ERROR', 'INVALID_AUTH_RESPONSE',
                        'AUTH_RESPONSE_TOO_LARGE', 'SERVER_MAINTENANCE',
                        'NO_ALLOWED_GATEWAY'}
                code = error.code if error.code in safe else 'AUTH_REJECTED'
                raise gacha.GachaError(code) from None
            except Exception:  # noqa: BLE001 - sanitize untrusted transport errors
                raise gacha.GachaError('AUTH_ERROR') from None
            self._remaining()
            try:
                self._sock = gacha.gateway_connection(
                    host, port, timeout=min(self._timeout, self._remaining()))
            except Exception:  # noqa: BLE001 - sanitize untrusted transport errors
                raise gacha.GachaError('GATEWAY_NETWORK_ERROR') from None
            self._remaining()
            self._sock.settimeout(min(self._timeout, self._remaining()))
            self._last_sent = time.monotonic()
            self._sock.sendall(gacha.enter_frame(self._credentials))
            body = self._response(101002, 1, 'ENTER_REJECTED')
            if gacha.numeric(body).get(1, 0) != 1:
                raise gacha.GachaError('ACTOR_NOT_FOUND')
            self._credentials = None
            self._entered = True
            return self
        except gacha.GachaError:
            self.close()
            raise
        except TimeoutError:
            self.close()
            raise gacha.GachaError('RESPONSE_TIMEOUT') from None
        except Exception:  # noqa: BLE001 - sanitize untrusted transport errors
            self.close()
            raise gacha.GachaError('GATEWAY_NETWORK_ERROR') from None

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False

    def _exact(self, length, deadline):
        if not 0 < length <= 65535:
            raise gacha.GachaError('INVALID_FRAME')
        result = bytearray()
        while len(result) < length:
            remaining = min(self._remaining(), deadline - time.monotonic())
            if remaining <= 0:
                raise gacha.GachaError('RESPONSE_TIMEOUT')
            self._sock.settimeout(remaining)
            part = self._sock.recv(length - len(result))
            self._remaining()
            if time.monotonic() >= deadline:
                raise gacha.GachaError('RESPONSE_TIMEOUT')
            if not part:
                raise gacha.GachaError('CONNECTION_CLOSED')
            if len(part) > length - len(result):
                raise gacha.GachaError('INVALID_FRAME')
            result.extend(part)
        return bytes(result)

    def _response(self, expected, sequence, rejection):
        deadline = min(self._deadline, time.monotonic() + self._timeout)
        consumed = 0
        known_responses = set(REQUEST_RESPONSES.values()) | {101002}
        for _ in range(128):
            head, body, size = self._read_frame(deadline, 1048576 - consumed)
            consumed += size
            command = head[1]
            if command not in known_responses:
                # Notifications are never executed or returned to the caller.
                # They share the same frame, byte and absolute read deadlines.
                continue
            if command != expected or head.get(3) != sequence:
                raise gacha.GachaError('RESPONSE_MISMATCH')
            if gacha.signed(head.get(4, 0)) != 0:
                raise gacha.GachaError(rejection)
            return body
        raise gacha.GachaError('RESPONSE_LIMIT')

    def _read_frame(self, deadline, byte_budget):
        if byte_budget < 8:
            raise gacha.GachaError('RESPONSE_SIZE_LIMIT')
        magic, length, _, wire_command = struct.unpack('!HHHH', self._exact(8, deadline))
        if magic != 0 or not length:
            raise gacha.GachaError('INVALID_FRAME')
        if length + 8 > byte_budget:
            raise gacha.GachaError('RESPONSE_SIZE_LIMIT')
        message = gacha.fields(self._exact(length, deadline))
        heads = [value for number, wire, value in message if number == 1 and wire == 2]
        bodies = [value for number, wire, value in message if number == 2 and wire == 2]
        if len(heads) != 1 or len(bodies) > 1:
            raise gacha.GachaError('INVALID_FRAME')
        head_fields = gacha.fields(heads[0])
        for number in (1, 3, 4):
            values = [(wire, value) for field, wire, value in head_fields if field == number]
            if len(values) > 1 or any(wire != 0 for wire, _ in values):
                raise gacha.GachaError('INVALID_FRAME')
        head = gacha.numeric(heads[0])
        if 1 not in head or wire_command != head[1] & 65535:
            raise gacha.GachaError('INVALID_FRAME')
        return head, bodies[0] if bodies else b'', length + 8

    def request(self, command, body=b''):
        try:
            if type(command) is not int or command not in REQUEST_RESPONSES:
                raise gacha.GachaError('RPC_NOT_ALLOWED')
            if not self._entered or self._sock is None:
                raise gacha.GachaError('SESSION_CLOSED')
            if not isinstance(body, bytes):
                raise gacha.GachaError('INVALID_REQUEST_BODY')
            self._remaining()
            if self._requests_used >= self._max_requests:
                raise gacha.GachaError('REQUEST_LIMIT')
            wait = max(0, self._last_sent + self._request_delay - time.monotonic())
            if wait >= self._remaining():
                raise gacha.GachaError('SESSION_TIMEOUT')
            if wait:
                time.sleep(wait)
            self._remaining()
            sequence = self._sequence + 1
            packet = gacha.frame(command, body, sequence)
            self._sock.settimeout(min(self._timeout, self._remaining()))
            self._sequence = sequence
            self._requests_used += 1
            self._last_sent = time.monotonic()
            self._sock.sendall(packet)
            body = self._response(REQUEST_RESPONSES[command], sequence, 'RPC_REJECTED')
            if self._response_observer is not None:
                try:
                    self._response_observer(command, body)
                except Exception:  # noqa: BLE001 - never retain observer exception text
                    raise gacha.GachaError('OBSERVER_FAILED') from None
            return body
        except gacha.GachaError:
            self.close()
            raise
        except TimeoutError:
            self.close()
            raise gacha.GachaError('RESPONSE_TIMEOUT') from None
        except Exception:  # noqa: BLE001 - sanitize untrusted transport errors
            self.close()
            raise gacha.GachaError('GATEWAY_NETWORK_ERROR') from None

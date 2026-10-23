"""Offline transport tests: never open a real connection."""

import json
import struct
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

from gacha_core import gacha
from gacha_core.session import GameGatewaySession


def reply(command, sequence, body=b'', code=0):
    head = gacha.integer(1, command) + gacha.integer(3, sequence)
    head += gacha.integer(4, code)
    message = gacha.blob(1, head) + gacha.blob(2, body)
    return struct.pack('!HHHH', 0, len(message), 0, command & 65535) + message


class Clock:
    value = 0.0

    def now(self):
        return self.value

    def sleep(self, duration):
        self.value += duration


class Wire:
    def __init__(self, data, clock):
        self.data = data
        self.clock = clock
        self.sent = []
        self.timeouts = []
        self.closed = False
        self.recv_cost = 0
        self.chunk = 65536

    def settimeout(self, duration):
        self.timeouts.append(duration)

    def sendall(self, data):
        self.sent.append((self.clock.value, data))

    def recv(self, size):
        self.clock.value += self.recv_cost
        size = min(size, self.chunk)
        result, self.data = self.data[:size], self.data[size:]
        return result

    def close(self):
        self.closed = True


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.wire = Wire(reply(101002, 1, gacha.integer(1, 1)), self.clock)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch('gacha_core.session.time.monotonic', self.clock.now))
        self.stack.enter_context(patch('gacha_core.session.time.sleep', self.clock.sleep))
        self.auth = self.stack.enter_context(patch.object(gacha, 'authenticate', return_value=('gw', 8801)))
        self.connect = self.stack.enter_context(patch.object(gacha, 'gateway_connection', return_value=self.wire))
        self.credentials = gacha.WebCredentials('secret-token', 'private-id', 'channel', 'device')

    def session(self, **kwargs):
        return GameGatewaySession(self.credentials, **kwargs)

    def assert_code(self, code, action):
        with self.assertRaises(gacha.GachaError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)

    def test_context_login_and_monotonic_sequences(self):
        self.wire.data += reply(102301, 2, b'first') + reply(110504, 3, b'history')
        with self.session() as client:
            self.assertEqual(client.requests_used, 0)
            self.assertEqual(client.request(2301), b'first')
            self.assertEqual(client.request(10504, gacha.integer(1, 4)), b'history')
            self.assertEqual(client.requests_used, 2)
        self.assertTrue(self.wire.closed)
        for expected, (_, frame) in enumerate(self.wire.sent, 1):
            head = gacha.numeric(gacha.fields(frame[8:])[0][2])
            self.assertEqual(head[3], expected)

    def test_all_fixed_readonly_commands(self):
        commands = (2301, 2307, 10033, 2001, 2002, 3001, 3004, 10504)
        for sequence, command in enumerate(commands, 2):
            self.wire.data += reply(command + 100000, sequence, bytes([sequence]))
        with self.session(request_delay=0) as client:
            for sequence, command in enumerate(commands, 2):
                self.assertEqual(client.request(command), bytes([sequence]))

    def test_unsafe_rpc_rejected_before_send_and_closes(self):
        with self.session() as client:
            self.assert_code('RPC_NOT_ALLOWED', lambda: client.request(9999))
            self.assertEqual(client.requests_used, 0)
        self.assertEqual(len(self.wire.sent), 1)
        self.assertTrue(self.wire.closed)

    def test_mismatched_response_command_closes(self):
        self.wire.data += reply(102307, 2)
        with self.session() as client:
            self.assert_code('RESPONSE_MISMATCH', lambda: client.request(2301))
            self.assertTrue(self.wire.closed)

    def test_mismatched_sequence_closes(self):
        self.wire.data += reply(102301, 3)
        with self.session() as client:
            self.assert_code('RESPONSE_MISMATCH', lambda: client.request(2301))
        self.assertEqual(len(self.wire.sent), 2)

    def test_notifications_are_bounded_and_skipped_before_matching_reply(self):
        self.wire.data += reply(9000, 0, b'notification') + reply(102301, 2, b'valid')
        with self.session(request_delay=0) as client:
            self.assertEqual(client.request(2301), b'valid')
            self.assertEqual(client.requests_used, 1)

    def test_notification_frame_limit_closes_without_accepting_later_reply(self):
        self.wire.data += reply(9000, 0) * 128 + reply(102301, 2)
        with self.session(request_delay=0) as client:
            self.assert_code('RESPONSE_LIMIT', lambda: client.request(2301))
            self.assertTrue(self.wire.closed)

    def test_notification_byte_limit_closes(self):
        self.wire.data += reply(9000, 0, b'x' * 65000) * 17 + reply(102301, 2)
        with self.session(request_delay=0) as client:
            self.assert_code('RESPONSE_SIZE_LIMIT', lambda: client.request(2301))
            self.assertTrue(self.wire.closed)

    def test_notification_stream_does_not_reset_response_deadline(self):
        self.wire.data += reply(9000, 0) * 10 + reply(102301, 2)
        with self.session(request_delay=0, timeout=1) as client:
            self.wire.recv_cost = 0.25
            self.assert_code('RESPONSE_TIMEOUT', lambda: client.request(2301))

    def test_budget_counts_metadata_and_rechecks_but_not_login(self):
        self.wire.data += reply(102301, 2)
        with self.session(max_requests=1) as client:
            client.request(2301)
            self.assert_code('REQUEST_LIMIT', lambda: client.request(2301))
            self.assertEqual(client.requests_used, 1)
        self.assertEqual(len(self.wire.sent), 2)

    def test_spacing_applies_after_login_and_between_requests(self):
        self.wire.data += reply(102301, 2) + reply(102307, 3)
        with self.session(request_delay=2) as client:
            client.request(2301)
            client.request(2307)
        self.assertEqual([stamp for stamp, _ in self.wire.sent], [0, 2, 4])

    def test_spacing_cannot_exceed_total_deadline(self):
        with self.session(max_duration=1, request_delay=2) as client:
            self.assert_code('SESSION_TIMEOUT', lambda: client.request(2301))
        self.assertEqual(len(self.wire.sent), 1)

    def test_slow_fragmented_reads_enforce_total_deadline(self):
        self.wire.data += reply(102301, 2, b'x' * 40)
        with self.session(max_duration=3, request_delay=0, timeout=10) as client:
            self.wire.chunk = 1
            self.wire.recv_cost = 0.5
            self.assert_code('SESSION_TIMEOUT', lambda: client.request(2301))
        self.assertTrue(self.wire.closed)

    def test_response_deadline_covers_header_and_body(self):
        self.wire.data += reply(102301, 2, b'x' * 40)
        with self.session(request_delay=0, timeout=1) as client:
            self.wire.chunk = 1
            self.wire.recv_cost = 0.25
            self.assert_code('RESPONSE_TIMEOUT', lambda: client.request(2301))

    def test_login_rejection_closes_without_retry(self):
        self.wire.data = reply(101002, 1, code=-2)
        self.assert_code('ENTER_REJECTED', lambda: self.session().__enter__())
        self.assertTrue(self.wire.closed)
        self.assertEqual(len(self.wire.sent), 1)

    def test_actor_missing_closes(self):
        self.wire.data = reply(101002, 1)
        self.assert_code('ACTOR_NOT_FOUND', lambda: self.session().__enter__())
        self.assertTrue(self.wire.closed)

    def test_auth_failure_hides_external_exception(self):
        self.auth.side_effect = RuntimeError('secret-token')
        self.assert_code('AUTH_ERROR', lambda: self.session().__enter__())
        self.connect.assert_not_called()

    def test_auth_consumes_total_duration(self):
        def authenticate(_, **kwargs):
            self.clock.value = 10
            return 'gw', 8801
        self.auth.side_effect = authenticate
        self.assert_code('SESSION_TIMEOUT', lambda: self.session(max_duration=5).__enter__())
        self.connect.assert_not_called()

    def test_auth_and_connect_receive_remaining_duration(self):
        def authenticate(_, **kwargs):
            self.clock.value += 2
            return 'gw', 8801
        self.auth.side_effect = authenticate
        with self.session(max_duration=5, timeout=12):
            pass
        self.auth.assert_called_once_with(self.credentials, timeout=5)
        self.connect.assert_called_once_with('gw', 8801, timeout=3)

    def test_per_operation_timeout_bounds_auth_and_connect(self):
        with self.session(max_duration=60, timeout=4):
            pass
        self.auth.assert_called_once_with(self.credentials, timeout=4)
        self.connect.assert_called_once_with('gw', 8801, timeout=4)

    def test_original_gateway_helper_accepts_timeout_override(self):
        self.stack.close()
        with patch.object(gacha.socket, 'create_connection') as connect:
            gacha.gateway_connection('gw.haoplay.com.cn', 8801, timeout=0.5)
        connect.assert_called_once_with(('gw.haoplay.com.cn', 8801), timeout=0.5)

    def test_original_auth_helper_accepts_timeout_override(self):
        self.stack.close()
        response = Mock()
        response.read.return_value = json.dumps({'retCode': 0, 'data': {
            'addresses': ['gw.haoplay.com.cn:8801']}}).encode()
        context = Mock(__enter__=Mock(return_value=response), __exit__=Mock(return_value=False))
        opener = Mock()
        opener.open.return_value = context
        with patch.object(gacha.urllib.request, 'build_opener', return_value=opener):
            self.assertEqual(gacha.authenticate(self.credentials, timeout=0.5),
                             ('gw.haoplay.com.cn', 8801))
        self.assertEqual(opener.open.call_args.kwargs['timeout'], 0.5)

    def test_truncated_frame_fails_closed(self):
        self.wire.data += b'\0\0\0'
        with self.session(request_delay=0) as client:
            self.assert_code('CONNECTION_CLOSED', lambda: client.request(2301))

    def test_invalid_frame_length_rejected(self):
        self.wire.data += struct.pack('!HHHH', 0, 0, 0, 0)
        with self.session() as client:
            self.assert_code('INVALID_FRAME', lambda: client.request(2301))

    def test_rejected_rpc_closes(self):
        self.wire.data += reply(102301, 2, code=99)
        with self.session() as client:
            self.assert_code('RPC_REJECTED', lambda: client.request(2301))

    def test_socket_timeout_is_safe(self):
        with self.session() as client:
            self.wire.recv = lambda _: (_ for _ in ()).throw(TimeoutError('secret-token'))
            self.assert_code('RESPONSE_TIMEOUT', lambda: client.request(2301))

    def test_socket_send_failure_counts_attempt_and_closes(self):
        with self.session() as client:
            self.wire.sendall = lambda _: (_ for _ in ()).throw(OSError('secret-token'))
            self.assert_code('GATEWAY_NETWORK_ERROR', lambda: client.request(2301))
            self.assertEqual(client.requests_used, 1)
            self.assertTrue(self.wire.closed)

    def test_unopened_or_closed_session_cannot_request(self):
        client = self.session()
        self.assert_code('SESSION_CLOSED', lambda: client.request(2301))

    def test_invalid_limits_fail_before_authentication(self):
        for kwargs in ({'max_requests': -1}, {'timeout': 0}, {'request_delay': -1}, {'max_duration': float('nan')}):
            with self.subTest(kwargs=kwargs):
                self.assert_code('INVALID_SESSION_LIMITS', lambda kwargs=kwargs: self.session(**kwargs))
        self.auth.assert_not_called()


if __name__ == '__main__':
    unittest.main()

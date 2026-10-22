"""Small bounded CDP transport for the application's own loopback browser only."""

import base64
import hashlib
import json
import os
import socket
import struct
from urllib.parse import urlsplit


class CdpError(Exception):
    pass


class CDP:
    LIMIT = 1024 * 1024

    def __init__(self, url, port):
        self.sock = None
        self.sequence = 0
        try:
            parsed = urlsplit(url)
            if (
                parsed.scheme != "ws"
                or parsed.hostname != "127.0.0.1"
                or parsed.port != port
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or not parsed.path.startswith("/devtools/")
            ):
                raise CdpError("CDP_ENDPOINT_INVALID")
            self.sock = socket.create_connection(("127.0.0.1", port), timeout=4)
            self.sock.settimeout(4)
            nonce = base64.b64encode(os.urandom(16)).decode()
            self.sock.sendall(
                (
                    f"GET {parsed.path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                    f"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {nonce}\r\nSec-WebSocket-Version: 13\r\n\r\n"
                ).encode()
            )
            header = bytearray()
            while not header.endswith(b"\r\n\r\n"):
                if len(header) > 16384:
                    raise CdpError("CDP_HANDSHAKE_FAILED")
                header.extend(self._exact(1))
            lines = header.decode("ascii").split("\r\n")
            values = {
                k.lower(): v.strip()
                for k, v in (line.split(":", 1) for line in lines[1:] if ":" in line)
            }
            expected = base64.b64encode(
                hashlib.sha1(
                    (nonce + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()
                ).digest()
            ).decode()
            if (
                lines[0].split()[1] != "101"
                or values.get("sec-websocket-accept") != expected
            ):
                raise CdpError("CDP_HANDSHAKE_FAILED")
        except Exception:  # noqa: BLE001 - UI/background boundary hides unexpected private error details.
            self.close()
            raise CdpError("CDP_UNAVAILABLE") from None

    def _exact(self, length):
        out = bytearray()
        while len(out) < length:
            part = self.sock.recv(length - len(out))
            if not part:
                raise CdpError("CDP_CLOSED")
            out.extend(part)
        return bytes(out)

    def _send(self, opcode, raw):
        if len(raw) > self.LIMIT:
            raise CdpError("CDP_MESSAGE_TOO_LARGE")
        mask = os.urandom(4)
        header = bytes([0x80 | opcode])
        n = len(raw)
        header += (
            bytes([0x80 | n])
            if n < 126
            else b"\xfe" + struct.pack("!H", n)
            if n < 65536
            else b"\xff" + struct.pack("!Q", n)
        )
        self.sock.sendall(
            header + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(raw))
        )

    def _receive(self):
        message = bytearray()
        started = False
        for _ in range(256):
            a, b = self._exact(2)
            opcode = a & 15
            if a & 0x70 or b & 0x80:
                raise CdpError("CDP_FRAME_INVALID")
            n = b & 127
            if n == 126:
                n = struct.unpack("!H", self._exact(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._exact(8))[0]
            if n > self.LIMIT or len(message) + n > self.LIMIT:
                raise CdpError("CDP_MESSAGE_TOO_LARGE")
            raw = self._exact(n)
            if opcode == 8:
                raise CdpError("CDP_CLOSED")
            if opcode == 9:
                if n > 125 or not a & 0x80:
                    raise CdpError("CDP_FRAME_INVALID")
                self._send(10, raw)
                continue
            if opcode == 10:
                continue
            if opcode == 1 and not started:
                started = True
            elif opcode != 0 or not started:
                raise CdpError("CDP_FRAME_INVALID")
            message.extend(raw)
            if a & 0x80:
                return json.loads(message)
        raise CdpError("CDP_MESSAGE_TOO_LARGE")

    def command(self, method, params=None):
        self.sequence += 1
        try:
            self._send(
                1,
                json.dumps(
                    {"id": self.sequence, "method": method, "params": params or {}}
                ).encode(),
            )
            for _ in range(128):
                result = self._receive()
                if result.get("id") == self.sequence:
                    if result.get("error"):
                        raise CdpError("CDP_COMMAND_FAILED")
                    return result.get("result", {})
            raise CdpError("CDP_RESPONSE_MISSING")
        except Exception:  # noqa: BLE001 - UI/background boundary hides unexpected private error details.
            raise CdpError("CDP_COMMAND_FAILED") from None

    def close(self):
        if self.sock:
            self.sock.close()
            self.sock = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

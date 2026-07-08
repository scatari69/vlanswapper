"""Minimal Telnet client on a raw socket.

Reason it exists: Python 3.13 removed ``telnetlib`` from the standard library,
and external libraries (netmiko/telnetlib3) are unavailable in the target
environment. This class implements just enough of the Telnet protocol (RFC 854)
to talk to network-gear CLIs: parsing IAC sequences and refusing every offered
option. That is enough for the line mode all supported switches run in.
"""

from __future__ import annotations

import re
import socket
import time

# Telnet control bytes (RFC 854).
IAC = 255   # Interpret As Command
DONT = 254
DO = 253
WONT = 252
WILL = 251
SB = 250    # start of subnegotiation
SE = 240    # end of subnegotiation
# Option codes seen in negotiations (needed for tests/reference).
ECHO = 1
SGA = 3


class TelnetError(Exception):
    """A Telnet transport error (timeout, dropped connection, etc.)."""


class TelnetClient:
    """A simple blocking Telnet client.

    Keeps an internal buffer of text already stripped of IAC. The read methods
    work with that buffer, while protocol negotiations are handled transparently
    in ``_feed``.
    """

    def __init__(self, host: str, port: int = 23, timeout: float = 10.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._buf = bytearray()      # IAC-stripped stream (what the switch "printed")
        self._raw = bytearray()      # unfinished tail of an IAC sequence

    # -- lifecycle ----------------------------------------------------------
    def open(self) -> None:
        try:
            self._sock = socket.create_connection((self.host, self.port), self.timeout)
        except OSError as exc:  # pragma: no cover - network dependent
            raise TelnetError(f"could not connect to {self.host}:{self.port}: {exc}") from exc
        self._sock.settimeout(self.timeout)

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def __enter__(self) -> "TelnetClient":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- writing ------------------------------------------------------------
    def write(self, data: str) -> None:
        if self._sock is None:
            raise TelnetError("connection not open")
        payload = data.encode("ascii", errors="replace")
        # Escape a bare IAC in user data (unlikely, but correct).
        payload = payload.replace(bytes([IAC]), bytes([IAC, IAC]))
        self._sock.sendall(payload)

    def write_line(self, data: str = "", newline: str = "\r\n") -> None:
        self.write(data + newline)

    # -- reading ------------------------------------------------------------
    def _recv(self) -> bytes:
        assert self._sock is not None
        try:
            chunk = self._sock.recv(4096)
        except socket.timeout:
            return b""
        except OSError as exc:  # pragma: no cover - network dependent
            raise TelnetError(f"read error: {exc}") from exc
        if chunk == b"":
            raise TelnetError("connection closed by the remote side")
        return chunk

    def _feed(self, data: bytes) -> None:
        """Parse the raw stream: answer negotiations, drop IAC, append the text."""
        buf = self._raw + data
        self._raw = bytearray()
        out = bytearray()
        i = 0
        n = len(buf)
        while i < n:
            b = buf[i]
            if b != IAC:
                out.append(b)
                i += 1
                continue
            # Found IAC — we need at least one more byte.
            if i + 1 >= n:
                self._raw = bytearray(buf[i:])
                break
            cmd = buf[i + 1]
            if cmd == IAC:  # escaped 0xFF -> a single data byte
                out.append(IAC)
                i += 2
            elif cmd in (DO, DONT, WILL, WONT):
                if i + 2 >= n:
                    self._raw = bytearray(buf[i:])
                    break
                self._refuse(cmd, buf[i + 2])
                i += 3
            elif cmd == SB:  # subnegotiation up to IAC SE — just swallow it
                end = buf.find(bytes([IAC, SE]), i + 2)
                if end == -1:
                    self._raw = bytearray(buf[i:])
                    break
                i = end + 2
            else:  # other two-byte commands (NOP, DM, ...)
                i += 2
        self._buf += out

    def _refuse(self, cmd: int, option: int) -> None:
        """Reject every offered option (keep a dumb line mode)."""
        if self._sock is None:
            return
        if cmd == DO:
            resp = bytes([IAC, WONT, option])
        elif cmd == WILL:
            resp = bytes([IAC, DONT, option])
        else:  # DONT/WONT need no reply
            return
        try:
            self._sock.sendall(resp)
        except OSError:  # pragma: no cover
            pass

    def read_until_re(self, patterns, timeout: float | None = None) -> tuple[str, int]:
        """Read until one of the regex ``patterns`` appears in the buffer.

        Returns ``(accumulated_text, index_of_matched_pattern)``. On timeout it
        raises :class:`TelnetError` with the already-read text in the message so
        the caller can log it.
        """
        compiled = [p if hasattr(p, "search") else re.compile(p, re.IGNORECASE) for p in patterns]
        deadline = time.monotonic() + (timeout if timeout is not None else self.timeout)
        while True:
            text = self._buf.decode("latin-1", errors="replace")
            for idx, pat in enumerate(compiled):
                if pat.search(text):
                    return text, idx
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TelnetError(
                    f"timed out waiting for {[p.pattern for p in compiled]}; received:\n{text}"
                )
            if self._sock is not None:
                self._sock.settimeout(min(remaining, self.timeout))
            self._feed(self._recv())

    def drain(self, idle: float = 0.5) -> str:
        """Read whatever has arrived during a short quiet interval."""
        assert self._sock is not None
        end = time.monotonic() + idle
        self._sock.settimeout(idle)
        while time.monotonic() < end:
            try:
                data = self._sock.recv(4096)
            except socket.timeout:
                break
            except OSError:
                break
            if not data:
                break
            self._feed(data)
        text = self._buf.decode("latin-1", errors="replace")
        self._buf = bytearray()
        return text

    def take_buffer(self) -> str:
        """Take and clear the accumulated text."""
        text = self._buf.decode("latin-1", errors="replace")
        self._buf = bytearray()
        return text

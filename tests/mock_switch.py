"""Mini Telnet-switch emulator (Eltex-like) for integration tests.

Starts a TCP server in a separate thread, plays back the login and answers the
main commands. It deliberately sends IAC negotiations in the banner to exercise
their parsing in TelnetClient.
"""

from __future__ import annotations

import socket
import threading

IAC, WILL, DO, ECHO, SGA = 255, 251, 253, 1, 3


class MockSwitch:
    #: pager line D-Link smart switches stop on when paging can't be disabled
    PAGER = b"CTRL+C ESC q Quit SPACE n Next Page ENTER Next Entry a All"

    def __init__(self, mac_port: dict[str, int] | None = None,
                 uplink_ports: set[int] | None = None,
                 page_size: int | None = None):
        self.mac_port = mac_port or {"aabb.ccdd.eeff": 7}
        self.uplink_ports = uplink_ports or set()  # ports trunking VLAN 253
        # When set, replies longer than this many lines are sent page by page,
        # each page followed by PAGER and a wait for a single keypress byte.
        self.page_size = page_size
        self.received: list[str] = []       # every command received (for assertions)
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(1)
        self.port = self._srv.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self) -> "MockSwitch":
        self._thread.start()
        return self

    # -- server ------------------------------------------------------------
    def _serve(self) -> None:
        try:
            conn, _ = self._srv.accept()
            with conn:
                self._handle(conn)
        finally:
            self._srv.close()

    def _readline(self, conn: socket.socket) -> str:
        buf = bytearray()
        while not buf.endswith(b"\n"):
            data = conn.recv(1)
            if not data:
                break
            if data[0] == IAC:            # swallow our own negotiation (client replies WONT/DONT)
                conn.recv(2)
                continue
            buf += data
        return buf.decode("latin-1").strip("\r\n")

    def _handle(self, conn: socket.socket) -> None:
        # Banner with IAC negotiations — verifies the client filters them out.
        conn.sendall(bytes([IAC, WILL, ECHO, IAC, WILL, SGA]))
        conn.sendall(b"\r\nWelcome\r\nUser Name:")
        self._readline(conn)                      # username
        conn.sendall(b"Password:")
        self._readline(conn)                      # password
        conn.sendall(b"\r\nswitch>")

        prompt = b"switch>"
        while True:
            line = self._readline(conn)
            if line == "":
                if not self._alive(conn):
                    break
                conn.sendall(b"\r\n" + prompt)
                continue
            self.received.append(line)
            reply, prompt = self._reply(line, prompt)
            if self.page_size and reply.count(b"\r\n") + 1 > self.page_size:
                self._send_paged(conn, reply, prompt)
                continue
            conn.sendall(b"\r\n" + reply + b"\r\n" + prompt)

    def _send_paged(self, conn: socket.socket, reply: bytes, prompt: bytes) -> None:
        """Send a long reply page by page, waiting for a keypress between pages."""
        lines = reply.split(b"\r\n")
        conn.sendall(b"\r\n")
        for i in range(0, len(lines), self.page_size):
            conn.sendall(b"\r\n".join(lines[i:i + self.page_size]))
            if i + self.page_size < len(lines):
                conn.sendall(b"\r\n" + self.PAGER)
                conn.recv(1)          # the page key arrives as a bare byte, no newline
                conn.sendall(b"\r\n")
            else:
                conn.sendall(b"\r\n" + prompt)

    @staticmethod
    def _alive(conn: socket.socket) -> bool:
        try:
            conn.getpeername()
            return True
        except OSError:
            return False

    def _reply(self, line: str, prompt: bytes) -> tuple[bytes, bytes]:
        low = line.lower()
        if low == "enable":
            return b"", b"switch#"
        if low.startswith("show version"):
            return b"SW version Eltex MES2308 4.0.9", prompt
        if low.startswith("show mac address-table address"):
            wanted = line.split()[-1]
            for mac, port in self.mac_port.items():
                if mac.replace(":", "").replace(".", "").lower() == wanted.replace(":", "").lower():
                    return f"100  {mac}  dynamic  gi1/0/{port}".encode(), prompt
            return b"", prompt
        if low.startswith("show mac address-table"):
            rows = [f"100  {mac}  dynamic  gi1/0/{port}"
                    for mac, port in self.mac_port.items()]
            return ("\r\n".join(rows)).encode(), prompt
        if low.startswith("show interfaces status") or low.startswith("show interface status"):
            rows = ["Port      Status",
                    "gi1/0/1   connected",
                    "gi1/0/2   notconnect",
                    "gi1/0/3   disabled"]
            return ("\r\n".join(rows)).encode(), prompt
        if low.startswith("show interfaces switchport"):
            nums = [int(n) for n in line.split("/")[-1].split() if n.isdigit()] \
                if "/" in line else []
            portnum = nums[-1] if nums else None
            if portnum in self.uplink_ports:
                return (b"Administrative Mode: trunk\r\n"
                        b"Trunking Native Mode VLAN: 1\r\n"
                        b"Trunking VLANs Enabled: 253,100-105"), prompt
            return (b"Administrative Mode: access\r\n"
                    b"Access Mode VLAN: 1\r\n"
                    b"Trunking VLANs Enabled: "), prompt
        if low == "write":
            return b"Overwrite file [startup-config]? [Y/N]", prompt
        # config commands, exit/end, terminal datadump, etc. — just echo/ack.
        return b"", prompt

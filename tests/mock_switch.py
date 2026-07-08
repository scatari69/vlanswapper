"""Мини-эмулятор Telnet-свитча (Eltex-подобный) для интеграционных тестов.

Поднимает TCP-сервер в отдельном потоке, проигрывает логин и отвечает на
основные команды. Специально шлёт IAC-негации в баннере, чтобы проверить их
разбор в TelnetClient.
"""

from __future__ import annotations

import socket
import threading

IAC, WILL, DO, ECHO, SGA = 255, 251, 253, 1, 3


class MockSwitch:
    def __init__(self, mac_port: dict[str, int] | None = None):
        self.mac_port = mac_port or {"aabb.ccdd.eeff": 7}
        self.received: list[str] = []       # все полученные команды (для проверок)
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(1)
        self.port = self._srv.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self) -> "MockSwitch":
        self._thread.start()
        return self

    # -- сервер ------------------------------------------------------------
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
            if data[0] == IAC:            # проглотить нашу же негацию (клиент отвечает WONT/DONT)
                conn.recv(2)
                continue
            buf += data
        return buf.decode("latin-1").strip("\r\n")

    def _handle(self, conn: socket.socket) -> None:
        # Баннер с IAC-переговорами — проверяем их фильтрацию клиентом.
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
            conn.sendall(b"\r\n" + reply + b"\r\n" + prompt)

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
        if low == "write":
            return b"Overwrite file [startup-config]? [Y/N]", prompt
        # config-команды, exit/end, terminal datadump и т.п. — просто эхо-подтверждение.
        return b"", prompt

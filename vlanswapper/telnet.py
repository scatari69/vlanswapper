"""Минимальный Telnet-клиент на голом сокете.

Причина существования: в Python 3.13 из стандартной библиотеки удалён
``telnetlib``, а внешние библиотеки (netmiko/telnetlib3) в целевом окружении
недоступны. Класс реализует ровно столько протокола Telnet (RFC 854), сколько
нужно для общения с CLI сетевого железа: разбор IAC-последовательностей и отказ
от всех предлагаемых опций. Этого достаточно для строкового режима, в котором
работают все поддерживаемые свитчи.
"""

from __future__ import annotations

import re
import socket
import time

# Управляющие байты Telnet (RFC 854).
IAC = 255   # Interpret As Command
DONT = 254
DO = 253
WONT = 252
WILL = 251
SB = 250    # начало подпереговоров
SE = 240    # конец подпереговоров
# Коды опций, встречающиеся в переговорах (нужны в тестах/справочно).
ECHO = 1
SGA = 3


class TelnetError(Exception):
    """Ошибка транспорта Telnet (таймаут, разрыв соединения и т.п.)."""


class TelnetClient:
    """Простой блокирующий Telnet-клиент.

    Держит внутренний буфер уже «очищенного» от IAC текста. Методы чтения
    работают именно с этим буфером, а негации протокола обрабатываются
    прозрачно в ``_feed``.
    """

    def __init__(self, host: str, port: int = 23, timeout: float = 10.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._buf = bytearray()      # очищенный от IAC поток (то, что «напечатал» свитч)
        self._raw = bytearray()      # незавершённый хвост IAC-последовательности

    # -- жизненный цикл -----------------------------------------------------
    def open(self) -> None:
        try:
            self._sock = socket.create_connection((self.host, self.port), self.timeout)
        except OSError as exc:  # pragma: no cover - зависит от сети
            raise TelnetError(f"не удалось подключиться к {self.host}:{self.port}: {exc}") from exc
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

    # -- запись -------------------------------------------------------------
    def write(self, data: str) -> None:
        if self._sock is None:
            raise TelnetError("соединение не открыто")
        payload = data.encode("ascii", errors="replace")
        # Экранируем одиночный IAC в пользовательских данных (маловероятно, но корректно).
        payload = payload.replace(bytes([IAC]), bytes([IAC, IAC]))
        self._sock.sendall(payload)

    def write_line(self, data: str = "", newline: str = "\r\n") -> None:
        self.write(data + newline)

    # -- чтение -------------------------------------------------------------
    def _recv(self) -> bytes:
        assert self._sock is not None
        try:
            chunk = self._sock.recv(4096)
        except socket.timeout:
            return b""
        except OSError as exc:  # pragma: no cover - зависит от сети
            raise TelnetError(f"ошибка чтения: {exc}") from exc
        if chunk == b"":
            raise TelnetError("соединение закрыто удалённой стороной")
        return chunk

    def _feed(self, data: bytes) -> None:
        """Разобрать сырой поток: ответить на негации, выкинуть IAC, дописать текст."""
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
            # Нашли IAC — нужен минимум ещё один байт.
            if i + 1 >= n:
                self._raw = bytearray(buf[i:])
                break
            cmd = buf[i + 1]
            if cmd == IAC:  # экранированный 0xFF -> один байт данных
                out.append(IAC)
                i += 2
            elif cmd in (DO, DONT, WILL, WONT):
                if i + 2 >= n:
                    self._raw = bytearray(buf[i:])
                    break
                self._refuse(cmd, buf[i + 2])
                i += 3
            elif cmd == SB:  # подпереговоры до IAC SE — просто проглатываем
                end = buf.find(bytes([IAC, SE]), i + 2)
                if end == -1:
                    self._raw = bytearray(buf[i:])
                    break
                i = end + 2
            else:  # прочие двухбайтовые команды (NOP, DM, ...)
                i += 2
        self._buf += out

    def _refuse(self, cmd: int, option: int) -> None:
        """Отклонить любую предложенную опцию (держим «немой» строковый режим)."""
        if self._sock is None:
            return
        if cmd == DO:
            resp = bytes([IAC, WONT, option])
        elif cmd == WILL:
            resp = bytes([IAC, DONT, option])
        else:  # на DONT/WONT отвечать не требуется
            return
        try:
            self._sock.sendall(resp)
        except OSError:  # pragma: no cover
            pass

    def read_until_re(self, patterns, timeout: float | None = None) -> tuple[str, int]:
        """Читать, пока в буфере не встретится один из regex ``patterns``.

        Возвращает ``(накопленный_текст, индекс_сработавшего_паттерна)``.
        При таймауте бросает :class:`TelnetError` с уже прочитанным текстом в
        аргументе, чтобы вызывающий код мог его залогировать.
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
                    f"таймаут ожидания {[p.pattern for p in compiled]}; получено:\n{text}"
                )
            if self._sock is not None:
                self._sock.settimeout(min(remaining, self.timeout))
            self._feed(self._recv())

    def drain(self, idle: float = 0.5) -> str:
        """Дочитать всё, что успело прийти, за короткий «тихий» интервал."""
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
        """Забрать и очистить накопленный текст."""
        text = self._buf.decode("latin-1", errors="replace")
        self._buf = bytearray()
        return text

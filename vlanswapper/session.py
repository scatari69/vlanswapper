"""Высокоуровневая CLI-сессия поверх :class:`TelnetClient`.

Отвечает за вход в систему (логин/пароль/enable) и за выполнение команд с
ожиданием промпта. Специфика вендоров живёт в драйверах, а здесь — общий
механизм «отправить строку и дочитать до приглашения».
"""

from __future__ import annotations

import re

from .telnet import TelnetClient, TelnetError

# Что считаем приглашением ввода имени/пароля на разных прошивках.
LOGIN_RE = re.compile(r"(user\s*name|login|username)\s*[:>]", re.IGNORECASE)
PASSWORD_RE = re.compile(r"pass\s*word\s*[:>]", re.IGNORECASE)
# Приглашение командной строки: заканчивается на > (обычный режим) или # (enable).
PROMPT_RE = re.compile(r"[\r\n]?[\w.\-\[\]/@()<>]+[>#]\s*$")
# Строки-«нажмите что-нибудь», встречающиеся до логина.
PRESS_ANY_RE = re.compile(r"press\s+(any\s+key|enter|return)", re.IGNORECASE)


class LoginError(Exception):
    """Не удалось авторизоваться на устройстве."""


class Session:
    def __init__(self, client: TelnetClient, dry_run: bool = False, log=None):
        self.c = client
        self.dry_run = dry_run
        self._log = log or (lambda msg: None)
        self.prompt_re = PROMPT_RE

    def log(self, msg: str) -> None:
        self._log(msg)

    # -- вход --------------------------------------------------------------
    def login(self, username: str, password: str, enable_password: str | None = None) -> str:
        """Пройти логин и, при необходимости, войти в enable-режим.

        Возвращает текст последнего приглашения (пригодится для детекта).
        """
        patterns = [LOGIN_RE, PASSWORD_RE, PROMPT_RE, PRESS_ANY_RE]
        sent_user = sent_pass = False
        for _ in range(8):  # защита от бесконечного цикла на нестандартных баннерах
            text, idx = self.c.read_until_re(patterns, timeout=self.c.timeout)
            pat = patterns[idx]
            if pat is LOGIN_RE and not sent_user:
                self.c.write_line(username)
                sent_user = True
            elif pat is PASSWORD_RE and not sent_pass:
                self.c.write_line(password)
                sent_pass = True
            elif pat is PRESS_ANY_RE:
                self.c.write_line("")
            elif pat is PROMPT_RE:
                prompt = text.strip().splitlines()[-1] if text.strip() else ""
                if prompt.endswith(">") and enable_password is not None:
                    prompt = self._enter_enable(enable_password)
                return prompt
            self.c.take_buffer()
        raise LoginError("не удалось дойти до приглашения командной строки после логина")

    def _enter_enable(self, enable_password: str) -> str:
        self.c.take_buffer()  # выбросить уже прочитанный '>'-промпт, иначе сматчим его повторно
        self.c.write_line("enable")
        text, idx = self.c.read_until_re([PASSWORD_RE, PROMPT_RE])
        if idx == 0:
            self.c.take_buffer()
            self.c.write_line(enable_password)
            text, _ = self.c.read_until_re([PROMPT_RE])
        return text.strip().splitlines()[-1] if text.strip() else ""

    # -- выполнение команд -------------------------------------------------
    def run(self, command: str, expect=None, timeout: float | None = None) -> str:
        """Отправить команду и вернуть вывод без эхо-строки и без промпта.

        В режиме ``dry_run`` команда только логируется, на устройство ничего не
        уходит, возвращается пустая строка.
        """
        self.log(f"$ {command}")
        if self.dry_run:
            return ""
        self.c.take_buffer()
        self.c.write_line(command)
        expect = expect or [self.prompt_re]
        text, _ = self.c.read_until_re(expect, timeout=timeout)
        return self._clean(text, command)

    def run_expect(self, command: str, expect, timeout: float | None = None) -> tuple[str, int]:
        """Как :meth:`run`, но вернуть ещё и индекс сработавшего паттерна.

        Нужно для интерактивных подтверждений (например, ``Save? [Y/N]``).
        """
        self.log(f"$ {command}")
        if self.dry_run:
            return "", 0
        self.c.take_buffer()
        self.c.write_line(command)
        text, idx = self.c.read_until_re(expect, timeout=timeout)
        return text, idx

    @staticmethod
    def _clean(text: str, command: str) -> str:
        lines = text.replace("\r", "").split("\n")
        if lines and command.strip() and command.strip() in lines[0]:
            lines = lines[1:]              # убрать эхо команды
        if lines and PROMPT_RE.search(lines[-1] + "\n"):
            lines = lines[:-1]            # убрать финальный промпт
        return "\n".join(lines).strip("\n")


__all__ = ["Session", "LoginError", "PROMPT_RE", "TelnetError"]

"""High-level CLI session on top of :class:`TelnetClient`.

Responsible for logging in (username/password/enable) and for running commands
while waiting for the prompt. Vendor specifics live in the drivers; here is the
shared "send a line and read up to the prompt" mechanism.
"""

from __future__ import annotations

import re

from .telnet import TelnetClient, TelnetError

# What counts as a username/password prompt across firmwares.
LOGIN_RE = re.compile(r"(user\s*name|login|username)\s*[:>]", re.IGNORECASE)
PASSWORD_RE = re.compile(r"pass\s*word\s*[:>]", re.IGNORECASE)
# Command-line prompt: ends with > (normal mode) or # (enable).
PROMPT_RE = re.compile(r"[\r\n]?[\w.\-\[\]/@()<>]+[>#]\s*$")
# "press something" lines seen before login.
PRESS_ANY_RE = re.compile(r"press\s+(any\s+key|enter|return)", re.IGNORECASE)


class LoginError(Exception):
    """Failed to authenticate on the device."""


class Session:
    def __init__(self, client: TelnetClient, dry_run: bool = False, log=None):
        self.c = client
        self.dry_run = dry_run
        self._log = log or (lambda msg: None)
        self.prompt_re = PROMPT_RE

    def log(self, msg: str) -> None:
        self._log(msg)

    # -- login -------------------------------------------------------------
    def login(self, username: str, password: str, enable_password: str | None = None) -> str:
        """Complete login and, if needed, enter enable mode.

        Returns the text of the last prompt (useful for detection).
        """
        patterns = [LOGIN_RE, PASSWORD_RE, PROMPT_RE, PRESS_ANY_RE]
        sent_user = sent_pass = False
        for _ in range(8):  # guard against an infinite loop on odd banners
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
        raise LoginError("could not reach the command-line prompt after login")

    def _enter_enable(self, enable_password: str) -> str:
        self.c.take_buffer()  # drop the already-read '>' prompt, or we'd match it again
        self.c.write_line("enable")
        text, idx = self.c.read_until_re([PASSWORD_RE, PROMPT_RE])
        if idx == 0:
            self.c.take_buffer()
            self.c.write_line(enable_password)
            text, _ = self.c.read_until_re([PROMPT_RE])
        return text.strip().splitlines()[-1] if text.strip() else ""

    # -- running commands --------------------------------------------------
    def run(self, command: str, expect=None, timeout: float | None = None) -> str:
        """Send a command and return the output without the echo line or the prompt.

        In ``dry_run`` mode the command is only logged, nothing is sent to the
        device, and an empty string is returned.
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
        """Like :meth:`run`, but also return the index of the matched pattern.

        Needed for interactive confirmations (e.g. ``Save? [Y/N]``).
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
            lines = lines[1:]              # drop the command echo
        if lines and PROMPT_RE.search(lines[-1] + "\n"):
            lines = lines[:-1]            # drop the trailing prompt
        return "\n".join(lines).strip("\n")


__all__ = ["Session", "LoginError", "PROMPT_RE", "TelnetError"]

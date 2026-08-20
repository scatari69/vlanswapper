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
# Pager line shown when paged output can't be disabled: classic '--More--' and the
# D-Link smart-switch variant 'CTRL+C ESC q Quit SPACE n Next Page ENTER Next Entry a All'.
MORE_RE = re.compile(r"--\s*more\s*--|next\s+page", re.IGNORECASE)
# Some pagers advertise a key that dumps everything left in one go, e.g. D-Link's
# 'CTRL+C ESC q Quit SPACE n Next Page ENTER Next Entry a ALL'. Using it beats
# walking page by page: one round trip, and no page boundaries to stitch back
# together (a boundary landing mid-block is how a listing gets misparsed).
ALL_KEY_RE = re.compile(r"\b(\w)\s+ALL\b", re.IGNORECASE)


def _page_fingerprint(page: str) -> str:
    """Whitespace-insensitive identity of a page, for spotting a redraw."""
    return "\n".join(line.strip() for line in page.splitlines() if line.strip())


#: ANSI CSI sequences ('\x1b[K', '\x1b[2J', ...) a pager uses to erase its legend.
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
#: control bytes that are not line structure — they land mid-line at a page seam
#: and would otherwise split or corrupt the record being read.
CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


#: the tokens a pager legend is built from, so the legend can be excised without
#: taking any output that got glued onto the same line with it.
LEGEND_RE = re.compile(
    r"(?:CTRL\s*\+\s*C|ESC|SPACE|ENTER|Next\s+Page|Next\s+Entry|Previous\s+Page|"
    r"Refresh|Quit|ALL|--\s*More\s*--|\b\w\b|[\s.]+)+", re.IGNORECASE)


def _strip_pager(text: str, more: re.Pattern[str]) -> str:
    """Remove the pager legend, keeping everything else on its line.

    Dropping the whole line is not safe: after a keypress the device often
    continues on the same row, so the next record can be glued onto the legend
    ('... a ALLVID : 118  VLAN NAME : vlan118'). Deleting that line loses a VLAN
    header, and the port lists that follow then get charged to the VLAN above it.
    So cut out exactly the legend's own tokens and keep the remainder.

    ANSI escapes and stray control bytes go too — a pager erases itself with
    them, and left in place they split or corrupt the record being parsed.
    """
    cleaned = CTRL_RE.sub("", ANSI_RE.sub("", text))
    kept: list[str] = []
    for line in cleaned.splitlines():
        m = more.search(line)
        if not m:
            kept.append(line)
            continue
        # Cut the whole legend run, not just from the marker: the legend starts
        # earlier on the line ('CTRL+C ESC q Quit SPACE n Next Page ...').
        span = next((s for s in LEGEND_RE.finditer(line)
                     if s.start() <= m.start() < s.end()), None)
        rest = line[:span.start()] + line[span.end():] if span else line[:m.start()]
        if rest.strip():
            kept.append(rest)
    return "\n".join(kept)


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

    def run_paged(self, command: str, more_re=MORE_RE, page_key: str = " ",
                  quit_key: str = "q", timeout: float | None = None,
                  max_pages: int = 500, settle: float = 0.2) -> str:
        """Run a command whose output the device pages, and collect every page.

        Some firmware ignores the "disable paging" command for certain views (the
        D-Link 1100 series and its MAC/port listings). There the device stops on a
        ``--More--`` style line and waits for a keypress instead of returning to the
        prompt, so we answer with ``page_key`` until the prompt comes back. When no
        pager appears this behaves exactly like :meth:`run`.

        Two kinds of pager exist and both end here. A plain listing pager runs out
        of pages and hands the prompt back. An *interactive screen* — D-Link's
        ``show ports``, whose legend advertises ``p Previous Page r Refresh`` —
        never does: once everything fits, ``page_key`` merely redraws it, so it
        would loop forever. We therefore stop as soon as a page repeats one we
        have already collected (a redraw or a wrap-around) and leave the pager
        with ``quit_key``, which is also what restores the prompt for whatever
        command runs next.
        """
        self.log(f"$ {command}")
        if self.dry_run:
            return ""
        more = more_re if hasattr(more_re, "search") else re.compile(more_re, re.IGNORECASE)
        self.c.take_buffer()
        self.c.write_line(command)
        pages: list[str] = []
        seen: set[str] = set()
        for _ in range(max_pages):
            text, idx = self.c.read_until_re([more, self.prompt_re], timeout=timeout)
            self.c.take_buffer()
            if idx == 1:                       # prompt: last page, output complete
                pages.append(text)
                break
            # The match fires on 'Next Page', mid-legend; without waiting for the
            # rest of the line we would miss the 'a ALL' key it advertises and
            # walk the output page by page instead — one seam per page.
            text += self.c.drain(settle)
            page = _strip_pager(text, more)
            # If the pager offers a "dump everything" key, take it: fewer round
            # trips and, more importantly, no further page seams in the output.
            legend = next((ln for ln in text.splitlines() if more.search(ln)), "")
            all_key = ALL_KEY_RE.search(legend)
            step_key = all_key.group(1) if all_key else page_key
            fingerprint = _page_fingerprint(page)
            if fingerprint in seen:            # redrawing, not advancing
                self._leave_pager(quit_key, timeout)
                break
            seen.add(fingerprint)
            pages.append(page)
            self.c.write(step_key)             # bare key, no newline — that's what the pager wants
        else:
            self._leave_pager(quit_key, timeout)
            raise TelnetError(f"pager did not finish after {max_pages} pages: {command!r}")
        return self._clean("".join(pages), command)

    def _leave_pager(self, quit_key: str, timeout: float | None = None) -> None:
        """Quit an interactive pager so the device is back at its prompt.

        Best effort: if the device doesn't answer, the caller still keeps the
        output it collected, and the next command's read starts from a cleared
        buffer either way.
        """
        try:
            self.c.write(quit_key)
            self.c.read_until_re([self.prompt_re], timeout=timeout)
        except TelnetError as exc:
            self.log(f"[pager] no prompt after quit key: {exc}")
        self.c.take_buffer()

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

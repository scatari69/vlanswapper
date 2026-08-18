"""Vendor autodetection from version-command output and the prompt.

Logic: run neutral show-version commands (each vendor has its own), collect the
replies and look for vendor-specific markers in them. The prompt text is also
taken into account — sometimes it already contains the device name.
"""

from __future__ import annotations

from .drivers import REGISTRY, BaseDriver, DriverError
from .session import Session

# Commands that are safe for showing the version/platform across CLIs.
PROBE_COMMANDS = (
    "show version",      # Eltex, BDCOM, Zyxel
    "display version",   # Huawei
    "show switch",       # D-Link
)


def detect_vendor(session: Session, prompt: str = "") -> str:
    """Return a vendor name from :data:`REGISTRY` or raise :class:`DriverError`."""
    haystack = prompt.lower()
    for cmd in PROBE_COMMANDS:
        try:
            # Paged read: on some firmware an *unsupported* probe prints a long
            # help listing that pages (seen on the DGS-1100/ME). A plain read
            # would burn the whole timeout and, worse, leave the device sitting
            # in the pager so every later command is typed into it instead.
            out = session.run_paged(cmd, timeout=session.c.timeout)
        except Exception as exc:  # timeout/error on an unsupported command — skip
            session.log(f"[detect] '{cmd}' failed: {exc}")
            continue
        haystack += "\n" + out.lower()
        vendor = _match(haystack)
        if vendor:
            session.log(f"[detect] vendor identified as {vendor} via '{cmd}'")
            return vendor
    vendor = _match(haystack)
    if vendor:
        return vendor
    raise DriverError(
        "could not detect the vendor automatically; specify it with --vendor"
    )


def _match(text: str) -> str | None:
    best_name: str | None = None
    best_len = 0
    for name, cls in REGISTRY.items():
        for marker in cls.detect_markers:
            # The longest matching marker = the most specific: 'des-1210'
            # must beat the generic 'des-' of the base D-Link.
            if marker in text and len(marker) > best_len:
                best_name, best_len = name, len(marker)
    return best_name


def build_driver(session: Session, vendor: str) -> BaseDriver:
    from .drivers import get_driver
    return get_driver(vendor)(session)

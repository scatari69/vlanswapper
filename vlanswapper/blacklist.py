"""Switch access blacklist: refuse to touch hosts listed in blacklist.txt.

The file holds one entry per line: an IP address, a hostname, or a CIDR
network (e.g. ``192.0.2.0/24``). Blank lines and ``#`` comments (whole-line or
inline) are ignored. A missing file means nothing is blocked.

There is deliberately no bypass flag — the whole point is to keep restricted
switches out of reach; edit the file itself to lift a restriction.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path

DEFAULT_BLACKLIST_PATHS = (
    Path("blacklist.txt"),
    Path.home() / ".config" / "vlanswapper" / "blacklist.txt",
)


def load_blacklist(path: Path | None = None) -> list[str]:
    """Read blacklist entries from ``path`` or the first existing default file.

    Missing file → empty list (nothing is blocked). An explicitly given ``path``
    that doesn't exist also yields an empty list, matching config.ini behavior.
    """
    candidates = (path,) if path is not None else DEFAULT_BLACKLIST_PATHS
    for candidate in candidates:
        if candidate.exists():
            entries: list[str] = []
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.split("#", 1)[0].strip()
                if line:
                    entries.append(line)
            return entries
    return []


def is_blacklisted(host: str, entries: list[str]) -> bool:
    """``True`` if ``host`` matches an entry: exact (case-insensitive) or CIDR.

    CIDR entries (``10.0.0.0/8``) match only when ``host`` is an IP address;
    hostname entries match by exact string comparison.
    """
    host_l = host.strip().lower()
    try:
        addr = ipaddress.ip_address(host_l)
    except ValueError:
        addr = None
    for entry in entries:
        e = entry.lower()
        if e == host_l:
            return True
        if addr is not None and "/" in e:
            try:
                if addr in ipaddress.ip_network(e, strict=False):
                    return True
            except ValueError:
                continue  # a malformed entry must not break the check
    return False

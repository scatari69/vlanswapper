"""D-Link DES-1210 driver (Smart Managed, -10/-28/-52 series).

Differences from the base :class:`DlinkDriver` (which targets "full" managed
switches like the DES-3200):

* This is a **Smart Managed** switch. A Telnet CLI exists only on firmware
  revisions **C1/F** and newer; on earlier revisions management is web/
  SmartConsole/SNMP only — the driver cannot work there.
* The port is moved purely by untagged VLAN membership; no PVID command is
  issued (neither ``config port_vlan ... pvid`` nor the DES-3200's ``config
  gvrp ports ... pvid``).
* ``disable clipaging`` may be accepted and still leave ``show ports``/``show
  vlan`` paging (seen on a DES-1210-28/ME), so listings are read through the
  pager-aware path inherited from :class:`DlinkDriver`.

All templates are **best-effort**, verified against docs rather than hardware.
Before production use, check the output with ``--dry-run --vendor dlink_des1210``.
"""

from __future__ import annotations

import re

from .base import DriverError, parse_int_ranges
from .dlink import DlinkDriver

# D-Link port lists ('1-3,5,7-9') are parsed by the same range parser.
_parse_port_list = parse_int_ranges


class DlinkDes1210Driver(DlinkDriver):
    name = "dlink_des1210"
    # 'des-1210' is longer than the generic 'des-' → autodetect prefers this driver.
    detect_markers = ("des-1210",)

    #: One scan for both record kinds, in the order they appear. Whitespace
    #: classes span newlines on purpose: a page seam can drop a stray line break
    #: inside a record ('VID\n : 118'), and a line-by-line reader would lose the
    #: header and charge the port lists that follow to the VLAN above it.
    _RECORD_RE = re.compile(
        r"\bVID\b\s*:\s*(\d+)"
        r"|\b(member|untagged|tagged|forbidden)\s+ports\b\s*:([^\r\n]*)",
        re.IGNORECASE)

    def _vlan_blocks(self, text: str) -> tuple[list[dict] | None, str]:
        """Split a ``show vlan`` dump into blocks; report why if it can't be trusted.

        Returns ``(blocks, "")`` or ``(None, reason)``. The reason names the exact
        defect and is surfaced to the operator, because these listings come back
        through a pager: damage at a page seam can drop or fuse lines, after which
        a port list is attributed to the wrong VID and the caller would delete the
        port from a VLAN it was never in.
        """
        blocks: list[dict] = []
        cur: dict | None = None
        for m in self._RECORD_RE.finditer(text):
            lineno = text.count("\n", 0, m.start()) + 1
            if m.group(1) is not None:
                cur = {"vid": int(m.group(1))}
                blocks.append(cur)
                continue
            if cur is None:
                return None, (f"line {lineno} is a port list before any VID "
                              f"header: {m.group(0).strip()[:60]!r}")
            key = m.group(2).lower()
            if key in cur:
                return None, (f"VLAN {cur['vid']} lists '{key} ports' twice "
                              f"(line {lineno}) — a VID header went missing")
            cur[key] = parse_int_ranges(m.group(3))
        if not blocks:
            return None, f"no VLAN blocks in {len(text.splitlines())} lines of output"
        return blocks, ""

    @staticmethod
    def _require(blocks: list[dict], keys: tuple[str, ...],
                 only_vid: int | None = None) -> str:
        """Check that the blocks carry the port lists an answer depends on.

        Demanding every line of every block is too strict: a page seam can eat a
        single ``Member Ports`` line, which says nothing about *untagged*
        membership and must not stop a port being moved. So each caller asks only
        for the lists it actually reads, and only for the VLANs it reads them from.
        """
        for b in blocks:
            if only_vid is not None and b["vid"] != only_vid:
                continue
            for key in keys:
                if key not in b:
                    return (f"VLAN {b['vid']} has no '{key} ports' line — the "
                            f"listing is missing it ({len(blocks)} blocks read)")
        return ""

    def _read_vlan_blocks(self) -> tuple[list[dict] | None, str]:
        out = self._run_view("show vlan")
        blocks, reason = self._vlan_blocks(out)
        if blocks is None:
            self._log_raw(reason, out)
        return blocks, reason

    def _log_raw(self, reason: str, out: str) -> None:
        """Dump the listing escaped, so a paste keeps the invisible characters.

        Page-seam damage is usually control bytes or stray breaks — exactly what
        a terminal hides and a copy/paste drops, which is why the plain text is
        not enough to tell what went wrong.
        """
        shown = "\n".join(f"{n:4}| {line!r}"
                           for n, line in enumerate(out.splitlines(), 1))
        self.s.log(f"[{self.name}] unusable 'show vlan' ({reason}); raw output:\n{shown}")

    def _current_untagged_vlans(self, port_number: int) -> list[int]:
        """VIDs where the port is currently an untagged member.

        The DES-1210 has no ``show vlan ports <n>``; membership comes from the
        block-style ``show vlan``. Raises rather than returning a guess, since the
        caller deletes the port from whatever this reports.
        """
        blocks, reason = self._read_vlan_blocks()
        # Only the untagged lists matter here, but every block needs one: a port
        # could be hiding in the line that went missing.
        reason = reason or self._require(blocks, ("untagged",)) if blocks else reason
        if blocks is None or reason:
            raise DriverError(
                f"unusable 'show vlan' listing — {reason}. Refusing to guess "
                f"which VLAN port {port_number} is in; re-run with -v to see "
                f"the raw output")
        return [b["vid"] for b in blocks if port_number in b["untagged"]]

    def port_vlans(self, port_number: int) -> set[int] | None:
        # Membership counts tagged *and* untagged, so a trunked uplink (a port in
        # 'Member Ports' with an empty 'Untagged Ports') is caught by the guard.
        blocks, reason = self._read_vlan_blocks()
        # Both lists are read here, so both must be present everywhere; otherwise
        # the guard reports "don't know" and the caller warns instead of trusting.
        if blocks is None or self._require(blocks, ("member", "untagged")):
            return None
        return {b["vid"] for b in blocks
                if port_number in (b["member"] | b["untagged"])}

    def find_uplink_ports(self, uplink_vlan: int) -> list[int]:
        blocks, reason = self._read_vlan_blocks()
        # Only the uplink VLAN's own block is read, so only it has to be intact.
        reason = reason or self._require(blocks, ("member", "untagged"),
                                         only_vid=uplink_vlan) if blocks else reason
        if blocks is None or reason:
            self.s.log(f"[{self.name}] unusable 'show vlan' ({reason}) — tagging no uplink")
            return []
        ports: set[int] = set()
        for b in blocks:
            if b["vid"] == uplink_vlan:
                ports |= b["member"] | b["untagged"]
        return sorted(ports)

    def set_access_vlan(self, port_number: int, vlan_id: int) -> None:
        # Remove the port from its old untagged VLANs and add it to the target
        # (same as the base driver).
        for old in self._current_untagged_vlans(port_number):
            if old != vlan_id:
                self._run(f"config vlan vlanid {old} delete {port_number}")
        # No PVID command on purpose (see DlinkDriver.set_access_vlan).
        out = self._run(f"config vlan vlanid {vlan_id} add untagged {port_number}")
        self._check_untagged_accepted(out, port_number, vlan_id)

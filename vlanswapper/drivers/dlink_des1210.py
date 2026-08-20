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

    #: a VLAN block header, e.g. 'VID                : 253       VLAN NAME : mgmt'
    _VID_RE = re.compile(r"\bVID\b\s*:\s*(\d+)")
    #: a port-list line inside a block ('Member/Untagged/Tagged/Forbidden Ports')
    _PORTS_RE = re.compile(r"\b(member|untagged|tagged|forbidden)\s+ports\b\s*:(.*)$",
                           re.IGNORECASE)

    def _vlan_blocks(self, text: str) -> tuple[list[dict] | None, str]:
        """Split a ``show vlan`` dump into blocks; report why if it can't be trusted.

        Returns ``(blocks, "")`` or ``(None, reason)``. The reason names the exact
        defect and is surfaced to the operator, because these listings come back
        through a pager: a page seam landing inside a block drops or fuses lines,
        after which a port list is attributed to the wrong VID and the caller
        would delete the port from a VLAN it was never in.
        """
        blocks: list[dict] = []
        cur: dict | None = None
        for lineno, line in enumerate(text.splitlines(), 1):
            vid_m = self._VID_RE.search(line)
            ports_m = self._PORTS_RE.search(line)
            if vid_m and ports_m:
                return None, (f"line {lineno} has a VID header glued to a port "
                              f"list: {line.strip()[:70]!r}")
            if vid_m:
                cur = {"vid": int(vid_m.group(1))}
                blocks.append(cur)
                continue
            if ports_m:
                if cur is None:
                    return None, (f"line {lineno} is a port list before any VID "
                                  f"header: {line.strip()[:70]!r}")
                key = ports_m.group(1).lower()
                if key in cur:
                    return None, (f"VLAN {cur['vid']} lists '{key} ports' twice "
                                  f"(line {lineno}) — a VID header went missing")
                cur[key] = parse_int_ranges(ports_m.group(2))
        if not blocks:
            return None, f"no VLAN blocks in {len(text.splitlines())} lines of output"
        for b in blocks:
            for key in ("member", "untagged"):
                if key not in b:
                    return None, (f"VLAN {b['vid']} has no '{key} ports' line — the "
                                  f"listing stops there ({len(blocks)} blocks read)")
        return blocks, ""

    def _read_vlan_blocks(self) -> tuple[list[dict] | None, str]:
        out = self._run_view("show vlan")
        blocks, reason = self._vlan_blocks(out)
        if blocks is None:
            # Only visible with -v: long, but it is what pins down where the
            # pager truncated the listing.
            self.s.log(f"[{self.name}] unusable 'show vlan' ({reason}); raw output:\n{out}")
        return blocks, reason

    def _current_untagged_vlans(self, port_number: int) -> list[int]:
        """VIDs where the port is currently an untagged member.

        The DES-1210 has no ``show vlan ports <n>``; membership comes from the
        block-style ``show vlan``. Raises rather than returning a guess, since the
        caller deletes the port from whatever this reports.
        """
        blocks, reason = self._read_vlan_blocks()
        if blocks is None:
            raise DriverError(
                f"unusable 'show vlan' listing — {reason}. Refusing to guess "
                f"which VLAN port {port_number} is in; re-run with -v to see "
                f"the raw output")
        return [b["vid"] for b in blocks if port_number in b["untagged"]]

    def port_vlans(self, port_number: int) -> set[int] | None:
        # Membership counts tagged *and* untagged, so a trunked uplink (a port in
        # 'Member Ports' with an empty 'Untagged Ports') is caught by the guard.
        blocks, _ = self._read_vlan_blocks()
        if blocks is None:
            return None
        return {b["vid"] for b in blocks
                if port_number in (b["member"] | b["untagged"])}

    def find_uplink_ports(self, uplink_vlan: int) -> list[int]:
        blocks, reason = self._read_vlan_blocks()
        if blocks is None:
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

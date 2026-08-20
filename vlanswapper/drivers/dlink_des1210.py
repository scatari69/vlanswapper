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

from .base import parse_int_ranges
from .dlink import DlinkDriver

# D-Link port lists ('1-3,5,7-9') are parsed by the same range parser.
_parse_port_list = parse_int_ranges


class DlinkDes1210Driver(DlinkDriver):
    name = "dlink_des1210"
    # 'des-1210' is longer than the generic 'des-' → autodetect prefers this driver.
    detect_markers = ("des-1210",)

    def _current_untagged_vlans(self, port_number: int) -> list[int]:
        """Find the VIDs where the port is currently an untagged member.

        The DES-1210-28 (Smart Managed) **does not support** ``show vlan ports
        <n>`` — only the full ``show vlan`` works, which prints one block per
        VLAN with a line like ``Current Untagged ports : 1-28``. We parse those
        blocks and look for VLANs whose untagged list contains the port number.
        """
        out = self._run_view("show vlan")
        vids: list[int] = []
        cur_vid: int | None = None
        for line in out.splitlines():
            m = re.search(r"\bVID\b\s*:\s*(\d+)", line)
            if m:
                cur_vid = int(m.group(1))
            if cur_vid is None:
                continue
            if re.search(r"untagged\s+ports\b", line, re.IGNORECASE) and ":" in line:
                spec = line.split(":", 1)[1]
                if port_number in _parse_port_list(spec) and cur_vid not in vids:
                    vids.append(cur_vid)
        return vids

    def port_vlans(self, port_number: int) -> set[int] | None:
        # The DES-1210 doesn't know 'show vlan ports <n>' — membership comes from
        # the block-style 'show vlan'. Full membership (both tagged and untagged)
        # is printed on the 'Member Ports' line; 'Untagged Ports' is a subset of
        # it, and 'Forbidden Ports' is NOT membership and is ignored. This also
        # catches a trunked uplink (a port in 'Member Ports' with empty 'Untagged').
        out = self._run_view("show vlan")
        if not out.strip():
            return None
        vlans: set[int] = set()
        cur_vid: int | None = None
        for line in out.splitlines():
            m = re.search(r"\bVID\b\s*:\s*(\d+)", line)
            if m:
                cur_vid = int(m.group(1))
            if cur_vid is None or ":" not in line:
                continue
            if re.search(r"(?:member|(?:un)?tagged)\s+ports\b", line, re.IGNORECASE):
                if port_number in _parse_port_list(line.split(":", 1)[1]):
                    vlans.add(cur_vid)
        return vlans

    def find_uplink_ports(self, uplink_vlan: int) -> list[int]:
        # One 'show vlan' parse instead of the generic per-port enumeration:
        # return the member ports of the uplink VID (both tagged and untagged).
        out = self._run_view("show vlan")
        if not out.strip():
            return []
        ports: set[int] = set()
        cur_vid: int | None = None
        for line in out.splitlines():
            m = re.search(r"\bVID\b\s*:\s*(\d+)", line)
            if m:
                cur_vid = int(m.group(1))
            if cur_vid != uplink_vlan or ":" not in line:
                continue
            if re.search(r"(?:member|(?:un)?tagged)\s+ports\b", line, re.IGNORECASE):
                ports |= _parse_port_list(line.split(":", 1)[1])
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

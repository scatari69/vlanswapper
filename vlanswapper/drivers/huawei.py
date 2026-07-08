"""Huawei VRP driver (S-series: Quidway/S2xxx/S5xxx).

Key differences from Cisco-like devices: system-view instead of configure
terminal, port default vlan instead of switchport access vlan, display instead
of show.
"""

from __future__ import annotations

import re

from .. import mac as macfmt
from .base import BaseDriver, parse_int_ranges


class HuaweiDriver(BaseDriver):
    name = "huawei"
    detect_markers = ("huawei", "versatile routing platform", "vrp", "quidway")
    mac_table_cmd = "display mac-address"
    port_status_cmd = "display interface brief"

    def disable_paging(self) -> None:
        # Applies to the current session, no configuration mode required.
        self._run("screen-length 0 temporary")

    def enter_config(self) -> None:
        self._run("system-view")

    def exit_config(self) -> None:
        self._run("return")

    def iface(self, port_number: int) -> str:
        # TODO: on some models the interface is Ethernet0/0/x, not GigabitEthernet.
        return f"GigabitEthernet0/0/{port_number}"

    def create_vlan(self, vlan_id: int) -> None:
        self._run(f"vlan {vlan_id}")
        self._run("quit")

    def set_access_vlan(self, port_number: int, vlan_id: int) -> None:
        self._run(f"interface {self.iface(port_number)}")
        self._run("port link-type access")
        # port default vlan replaces the previous access PVID.
        self._run(f"port default vlan {vlan_id}")
        self._run("quit")

    def parse_port_status(self, output: str) -> list[tuple[int, str]]:
        # 'display interface brief': "GE0/0/1  up  up ...". PHY '*down' = admin-down.
        rows: list[tuple[int, str]] = []
        for line in output.splitlines():
            toks = line.split()
            if len(toks) < 2:
                continue
            port = self._last_port_number(toks[0])
            if port is None:
                continue
            phy = toks[1].lower()
            if phy.startswith("*down"):
                rows.append((port, "disabled"))
            elif phy in ("up", "down"):
                rows.append((port, phy))
        return rows

    def port_vlans(self, port_number: int) -> set[int] | None:
        # 'display port vlan <iface>': "GE0/0/5  trunk  1  1 100 253"
        # (columns: interface, type, PVID, trunk VLAN list).
        out = self._run(f"display port vlan {self.iface(port_number)}")
        if not out.strip():
            return None
        vlans: set[int] = set()
        found = False
        for line in out.splitlines():
            m = re.search(r"(?:Ethernet|GE|XGE|Eth-Trunk)\S*\s+\w[\w-]*\s+(\d+)\s+(.*)$",
                          line, re.IGNORECASE)
            if not m:
                continue
            vlans.add(int(m.group(1)))            # PVID
            vlans |= parse_int_ranges(m.group(2))  # trunk VLAN list ('-' is ignored)
            found = True
        return vlans if found else None

    def find_port_by_mac(self, mac: str) -> int | None:
        out = self._run(f"display mac-address {macfmt.huawei(mac)}")
        # Example: aabb-ccdd-eeff 100/-  GigabitEthernet0/0/5  dynamic
        m = re.search(r"(?:GigabitEthernet|Ethernet|XGigabitEthernet)\S*?(\d+)\b",
                      out, re.IGNORECASE)
        return int(m.group(1)) if m else None

    def save(self) -> None:
        # save is issued from the user view; it asks Y/N.
        self._run_confirm("save", yes="Y")

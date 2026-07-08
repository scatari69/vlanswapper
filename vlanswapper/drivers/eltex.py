"""Eltex MES driver (Cisco-like, VRP-style CLI).

Verified against MES23xx/MES2xxx syntax. The interface name and slot/stack may
differ on other series — see :meth:`iface`.
"""

from __future__ import annotations

import re

from .. import mac as macfmt
from .base import BaseDriver


class EltexDriver(BaseDriver):
    name = "eltex"
    detect_markers = ("eltex", "mes")
    mac_table_cmd = "show mac address-table"
    port_status_cmd = "show interfaces status"

    def disable_paging(self) -> None:
        self._run("terminal datadump")

    def enter_config(self) -> None:
        self._run("configure terminal")

    def exit_config(self) -> None:
        self._run("end")

    def iface(self, port_number: int) -> str:
        # TODO: on stackable models the slot/stack may not be 1/0 — move to config.
        return f"gigabitethernet 1/0/{port_number}"

    def create_vlan(self, vlan_id: int) -> None:
        self._run("vlan database")
        self._run(f"vlan {vlan_id}")
        self._run("exit")

    def set_access_vlan(self, port_number: int, vlan_id: int) -> None:
        self._run(f"interface {self.iface(port_number)}")
        self._run("switchport mode access")
        # switchport access vlan replaces the previous access VLAN — no need to clear the old one.
        self._run(f"switchport access vlan {vlan_id}")
        self._run("exit")

    def find_port_by_mac(self, mac: str) -> int | None:
        out = self._run(f"show mac address-table address {macfmt.colon(mac)}")
        # Example: 100  aa:bb:cc:dd:ee:ff  dynamic  gi1/0/5
        m = re.search(r"(?:gi|te|fa|gigabitethernet|tengigabitethernet)\S*?(\d+)\s*$",
                      out, re.IGNORECASE | re.MULTILINE)
        return int(m.group(1)) if m else None

    def save(self) -> None:
        self._run_confirm("write")

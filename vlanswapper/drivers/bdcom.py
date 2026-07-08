"""Драйвер BDCOM (Cisco-подобный CLI, коммутаторы/OLT).

Близок к IOS, но доступ к порту задаётся через ``switchport pvid`` вместо
``switchport access vlan``. Имя интерфейса — ``GigaEthernet0/N``.
"""

from __future__ import annotations

import re

from .. import mac as macfmt
from .base import BaseDriver


class BdcomDriver(BaseDriver):
    name = "bdcom"
    detect_markers = ("bdcom", "gpon", "epon olt")
    mac_table_cmd = "show mac address-table"
    port_status_cmd = "show interface status"  # TODO: сверить формат на серии

    def disable_paging(self) -> None:
        self._run("terminal length 0")

    def enter_config(self) -> None:
        self._run("config terminal")

    def exit_config(self) -> None:
        self._run("end")

    def iface(self, port_number: int) -> str:
        # TODO: на некоторых платформах TGigaEthernet/GigaEthernet0/0/N.
        return f"GigaEthernet0/{port_number}"

    def create_vlan(self, vlan_id: int) -> None:
        self._run(f"vlan {vlan_id}")
        self._run("exit")

    def set_access_vlan(self, port_number: int, vlan_id: int) -> None:
        self._run(f"interface {self.iface(port_number)}")
        self._run("switchport mode access")
        # switchport pvid заменяет прежний access-VLAN.
        self._run(f"switchport pvid {vlan_id}")
        self._run("exit")

    def find_port_by_mac(self, mac: str) -> int | None:
        out = self._run(f"show mac address-table address {macfmt.colon(mac)}")
        m = re.search(r"(?:GigaEthernet|TGigaEthernet|Ethernet)\S*?(\d+)\b",
                      out, re.IGNORECASE)
        return int(m.group(1)) if m else None

    def save(self) -> None:
        self._run_confirm("write")

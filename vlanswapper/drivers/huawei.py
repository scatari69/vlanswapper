"""Драйвер Huawei VRP (S-серии: Quidway/S2xxx/S5xxx).

Ключевые отличия от Cisco-подобных: system-view вместо configure terminal,
port default vlan вместо switchport access vlan, display вместо show.
"""

from __future__ import annotations

import re

from .. import mac as macfmt
from .base import BaseDriver


class HuaweiDriver(BaseDriver):
    name = "huawei"
    detect_markers = ("huawei", "versatile routing platform", "vrp", "quidway")

    def disable_paging(self) -> None:
        # Действует на текущую сессию, не требует режима конфигурации.
        self._run("screen-length 0 temporary")

    def enter_config(self) -> None:
        self._run("system-view")

    def exit_config(self) -> None:
        self._run("return")

    def iface(self, port_number: int) -> str:
        # TODO: на части моделей интерфейс Ethernet0/0/x, а не GigabitEthernet.
        return f"GigabitEthernet0/0/{port_number}"

    def create_vlan(self, vlan_id: int) -> None:
        self._run(f"vlan {vlan_id}")
        self._run("quit")

    def set_access_vlan(self, port_number: int, vlan_id: int) -> None:
        self._run(f"interface {self.iface(port_number)}")
        self._run("port link-type access")
        # port default vlan заменяет прежний PVID доступа.
        self._run(f"port default vlan {vlan_id}")
        self._run("quit")

    def find_port_by_mac(self, mac: str) -> int | None:
        out = self._run(f"display mac-address {macfmt.huawei(mac)}")
        # Пример: aabb-ccdd-eeff 100/-  GigabitEthernet0/0/5  dynamic
        m = re.search(r"(?:GigabitEthernet|Ethernet|XGigabitEthernet)\S*?(\d+)\b",
                      out, re.IGNORECASE)
        return int(m.group(1)) if m else None

    def save(self) -> None:
        # save задаётся из пользовательского вида; спросит Y/N.
        self._run_confirm("save", yes="Y")

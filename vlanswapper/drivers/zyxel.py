"""Драйвер Zyxel (управляемые коммутаторы GS/XGS-серий).

У Zyxel членство порта в VLAN и PVID задаются раздельно: сначала в контексте
``vlan <id>`` добавляем порт как untagged, затем в контексте порта ставим PVID.
"""

from __future__ import annotations

import re

from .. import mac as macfmt
from .base import BaseDriver


class ZyxelDriver(BaseDriver):
    name = "zyxel"
    detect_markers = ("zyxel", "gs1", "gs2", "xgs")
    mac_table_cmd = "show mac address-table"
    port_status_cmd = "show interfaces status"  # TODO: сверить формат на серии

    def disable_paging(self) -> None:
        # TODO: не все прошивки Zyxel поддерживают отключение пейджинга по CLI.
        # Если вывод режется на --More--, увеличьте idle/timeout сессии.
        pass

    def enter_config(self) -> None:
        self._run("configure")

    def exit_config(self) -> None:
        self._run("exit")

    def iface(self, port_number: int) -> str:
        return str(port_number)

    def create_vlan(self, vlan_id: int) -> None:
        # На Zyxel создание VLAN совмещено с добавлением порта (см. set_access_vlan).
        pass

    def set_access_vlan(self, port_number: int, vlan_id: int) -> None:
        # Добавляем порт untagged в целевой VLAN (VLAN создаётся, если его не было).
        self._run(f"vlan {vlan_id}")
        self._run(f"fixed {port_number}")
        self._run(f"untagged {port_number}")
        self._run("exit")
        # Ставим PVID порта — прежний access-VLAN замещается новым PVID.
        self._run(f"interface port-channel {port_number}")
        self._run(f"pvid {vlan_id}")
        self._run("exit")

    def find_port_by_mac(self, mac: str) -> int | None:
        out = self._run(f"show mac address-table mac {macfmt.colon(mac)}")
        # Формат: MAC  VID  Port  Type  →  берём столбец Port.
        m = re.search(r"[0-9a-fA-F:]{17}\s+\d+\s+(\d+)", out)
        return int(m.group(1)) if m else None

    def save(self) -> None:
        self._run_confirm("write memory")

"""Драйвер D-Link (традиционный CLI: DES-3200/DGS-3xxx и подобные).

Особенность D-Link: нет режима конфигурации и нет ``switchport access vlan`` —
членство в VLAN настраивается однострочными командами ``config vlan ... add
untagged`` плюс отдельная установка PVID. Поэтому «снять старый VLAN» приходится
делать явно: найти текущий untagged-VLAN порта и удалить его оттуда.

Синтаксис сильно зависит от серии/прошивки (особенно команда PVID) — отмечено
TODO. Проверяйте на конкретной модели.
"""

from __future__ import annotations

import re

from .. import mac as macfmt
from .base import BaseDriver, DriverError


class DlinkDriver(BaseDriver):
    name = "dlink"
    detect_markers = ("d-link", "des-", "dgs-", "dlink")

    def disable_paging(self) -> None:
        self._run("disable clipaging")

    # У D-Link нет отдельного режима конфигурации.
    def enter_config(self) -> None:
        pass

    def exit_config(self) -> None:
        pass

    def iface(self, port_number: int) -> str:
        return str(port_number)

    def create_vlan(self, vlan_id: int) -> None:
        # Если VLAN уже есть — команда вернёт ошибку, это не фатально.
        out = self._run(f"create vlan vlan{vlan_id} tag {vlan_id}")
        if "fail" in out.lower() and "already" not in out.lower():
            self.s.log(f"[dlink] create vlan вернул: {out.strip()}")

    def _current_untagged_vlans(self, port_number: int) -> list[int]:
        """Найти VID'ы, где порт сейчас untagged-член (обычно один)."""
        out = self._run(f"show vlan ports {port_number}")
        vids: list[int] = []
        for line in out.splitlines():
            # Формат: <port> <VID> <Untagged X/-> <Tagged> <Forbidden>
            m = re.match(r"\s*\d+\s+(\d+)\s+(\S+)", line)
            if m and m.group(2).upper() in ("X", "E", "UNTAGGED"):
                vids.append(int(m.group(1)))
        return vids

    def set_access_vlan(self, port_number: int, vlan_id: int) -> None:
        for old in self._current_untagged_vlans(port_number):
            if old != vlan_id:
                self._run(f"config vlan vlanid {old} delete {port_number}")
        self._run(f"config vlan vlanid {vlan_id} add untagged {port_number}")
        # TODO: команда PVID зависит от серии. На DES-3200 — 'config gvrp ports'.
        self._run(f"config gvrp ports {port_number} pvid {vlan_id}")

    def find_port_by_mac(self, mac: str) -> int | None:
        out = self._run(f"show fdb mac_address {macfmt.dash(mac)}")
        # Формат: VID  VLAN_Name  MAC_Address  Port  Type
        m = re.search(r"([0-9a-fA-F-]{17})\s+(\d+)\s+(?:Dynamic|Static|Self)",
                      out, re.IGNORECASE)
        if m:
            return int(m.group(2))
        return None

    def save(self) -> None:
        out = self._run_confirm("save", confirm_re=r"\(y/n\)|\[y/n\]")
        if "fail" in out.lower():
            raise DriverError(f"сохранение не удалось: {out.strip()}")

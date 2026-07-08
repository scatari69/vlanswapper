"""Драйвер D-Link DES-1210 (Smart Managed, серии -10/-28/-52).

Отличия от базового :class:`DlinkDriver` (тот ориентирован на «полноценные»
managed-свитчи вроде DES-3200):

* Это **Smart Managed** свитч. CLI по Telnet присутствует только на прошивках
  ревизий **C1/F** и новее; на ранних ревизиях управление лишь через
  web/SmartConsole/SNMP — там драйвер работать не сможет.
* PVID у DES-1210 задаётся командой ``config vlan_precedence``/``config port_vlan``
  в зависимости от прошивки, а привычной для DES-3200 ``config gvrp ports ...``
  здесь нет. Ниже используется ``config port_vlan`` — при расхождении с вашей
  прошивкой поправьте (отмечено TODO).
* Постраничный вывод отключается тем же ``disable clipaging``; если прошивка
  команду не знает, она вернёт ошибку — это не фатально.

Все шаблоны — **best-effort**, проверены по документации, а не на железе.
Перед боем сверьте вывод через ``--dry-run --vendor dlink_des1210``.
"""

from __future__ import annotations

import re

from .base import parse_int_ranges
from .dlink import DlinkDriver

# Списки портов D-Link ('1-3,5,7-9') разбираются тем же парсером диапазонов.
_parse_port_list = parse_int_ranges


class DlinkDes1210Driver(DlinkDriver):
    name = "dlink_des1210"
    # 'des-1210' длиннее общего 'des-' → автодетект предпочтёт этот драйвер.
    detect_markers = ("des-1210",)

    def disable_paging(self) -> None:
        # На части прошивок DES-1210 команды нет; ошибку игнорируем.
        out = self._run("disable clipaging")
        if "fail" in out.lower() or "error" in out.lower():
            self.s.log("[des-1210] disable clipaging не поддерживается — игнорирую")

    def _current_untagged_vlans(self, port_number: int) -> list[int]:
        """Найти VID'ы, где порт сейчас untagged-член.

        DES-1210-28 (Smart Managed) **не поддерживает** ``show vlan ports <n>``
        — работает только полный ``show vlan``, который печатает по блоку на
        VLAN со строкой вида ``Current Untagged ports : 1-28``. Разбираем эти
        блоки и ищем VLAN'ы, где номер порта попадает в untagged-список.
        """
        out = self._run("show vlan")
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
        # DES-1210 не знает 'show vlan ports <n>' — берём и untagged, и tagged
        # порты из блочного 'show vlan' («защита от дурака» учитывает транк).
        out = self._run("show vlan")
        if not out.strip():
            return None
        vlans: set[int] = set()
        cur_vid: int | None = None
        for line in out.splitlines():
            m = re.search(r"\bVID\b\s*:\s*(\d+)", line)
            if m:
                cur_vid = int(m.group(1))
            if cur_vid is None:
                continue
            if re.search(r"(un)?tagged\s+ports\b", line, re.IGNORECASE) and ":" in line:
                if port_number in _parse_port_list(line.split(":", 1)[1]):
                    vlans.add(cur_vid)
        return vlans

    def set_access_vlan(self, port_number: int, vlan_id: int) -> None:
        # Снять порт со старых untagged-VLAN и добавить в целевой (как в базовом).
        for old in self._current_untagged_vlans(port_number):
            if old != vlan_id:
                self._run(f"config vlan vlanid {old} delete {port_number}")
        self._run(f"config vlan vlanid {vlan_id} add untagged {port_number}")
        # TODO: PVID на DES-1210. По докам — 'config port_vlan <port> pvid <vid>'.
        # На некоторых прошивках F: 'config vlan_precedence'. Проверьте на модели.
        self._run(f"config port_vlan {port_number} pvid {vlan_id}")

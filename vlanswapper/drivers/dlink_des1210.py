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

from .dlink import DlinkDriver


class DlinkDes1210Driver(DlinkDriver):
    name = "dlink_des1210"
    # 'des-1210' длиннее общего 'des-' → автодетект предпочтёт этот драйвер.
    detect_markers = ("des-1210",)

    def disable_paging(self) -> None:
        # На части прошивок DES-1210 команды нет; ошибку игнорируем.
        out = self._run("disable clipaging")
        if "fail" in out.lower() or "error" in out.lower():
            self.s.log("[des-1210] disable clipaging не поддерживается — игнорирую")

    def set_access_vlan(self, port_number: int, vlan_id: int) -> None:
        # Снять порт со старых untagged-VLAN и добавить в целевой (как в базовом).
        for old in self._current_untagged_vlans(port_number):
            if old != vlan_id:
                self._run(f"config vlan vlanid {old} delete {port_number}")
        self._run(f"config vlan vlanid {vlan_id} add untagged {port_number}")
        # TODO: PVID на DES-1210. По докам — 'config port_vlan <port> pvid <vid>'.
        # На некоторых прошивках F: 'config vlan_precedence'. Проверьте на модели.
        self._run(f"config port_vlan {port_number} pvid {vlan_id}")

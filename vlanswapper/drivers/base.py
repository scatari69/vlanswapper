"""Базовый драйвер: общий сценарий и контракт для вендорских реализаций.

Главное действие одинаково для всех вендоров:

    vlan_id = VLAN_BASE + port
    → снять старый access-VLAN с порта
    → создать нужный VLAN (если ещё нет)
    → назначить его access'ом на порт
    → (опц.) сохранить конфиг

Различается только синтаксис CLI — он инкапсулирован в методах, которые
переопределяют наследники. Метод :meth:`swap` содержит общий каркас.
"""

from __future__ import annotations

import re

from ..session import Session

VLAN_BASE = 100

#: типовой транковый VLAN на аплинке — «защита от дурака» перед настройкой порта.
UPLINK_VLAN = 253


def parse_int_ranges(spec: str) -> set[int]:
    """Разобрать список чисел вида ``1-3,5,7-9`` (порты или VID) в множество."""
    out: set[int] = set()
    for token in spec.replace(";", ",").split(","):
        for chunk in token.split():
            chunk = chunk.strip()
            if not chunk:
                continue
            m = re.match(r"(\d+)\s*-\s*(\d+)$", chunk)
            if m:
                out.update(range(int(m.group(1)), int(m.group(2)) + 1))
            elif chunk.isdigit():
                out.add(int(chunk))
    return out


#: нормализация «сырых» слов статуса линка из вывода разных вендоров.
LINK_STATUS_MAP = {
    "connected": "up",
    "up": "up",
    "notconnect": "down",
    "notconnected": "down",
    "not-connect": "down",
    "down": "down",
    "disabled": "disabled",
    "admin-down": "disabled",
    "err-disabled": "disabled",
    "errdisabled": "disabled",
}

#: приоритет статусов при слиянии дублей (напр. combo-порт: медь down, оптика up).
_STATUS_RANK = {"up": 0, "down": 1, "disabled": 2, "unknown": 3}


class DriverError(Exception):
    """Ошибка выполнения на устройстве (порт не найден, отказ команды и т.п.)."""


class BaseDriver:
    #: человекочитаемое имя вендора
    name: str = "base"
    #: подстроки, по которым detect определяет вендора в выводе version-команд
    detect_markers: tuple[str, ...] = ()
    #: команда полного дампа таблицы MAC/FDB (переопределяется вендором)
    mac_table_cmd: str = ""
    #: команда вывода статуса портов (переопределяется вендором)
    port_status_cmd: str = ""

    def __init__(self, session: Session):
        self.s = session

    # -- переопределяемые примитивы ----------------------------------------
    def disable_paging(self) -> None:
        """Отключить постраничный вывод (--More--), чтобы не зависнуть на чтении."""
        raise NotImplementedError

    def enter_config(self) -> None:
        """Войти в режим конфигурации."""
        raise NotImplementedError

    def exit_config(self) -> None:
        """Выйти из режима конфигурации в exec-режим."""
        raise NotImplementedError

    def iface(self, port_number: int) -> str:
        """Собрать имя интерфейса из номера порта (напр. 5 -> GigabitEthernet0/0/5)."""
        raise NotImplementedError

    def create_vlan(self, vlan_id: int) -> None:
        """Создать VLAN, если его ещё нет (идемпотентно)."""
        raise NotImplementedError

    def set_access_vlan(self, port_number: int, vlan_id: int) -> None:
        """Снять старый access-VLAN с порта и назначить новый."""
        raise NotImplementedError

    def find_port_by_mac(self, mac: str) -> int | None:
        """Вернуть номер порта, за которым виден MAC, либо ``None``."""
        raise NotImplementedError

    def save(self) -> None:
        """Сохранить running- в startup-конфиг."""
        raise NotImplementedError

    def show_mac_table(self) -> str:
        """Вернуть текстовый дамп таблицы MAC-адресов (FDB) как есть.

        Команда задаётся вендором через атрибут :attr:`mac_table_cmd`.
        """
        if not self.mac_table_cmd:
            raise DriverError("просмотр MAC-таблицы не поддержан для этого вендора")
        return self._run(self.mac_table_cmd)

    def parse_port_status(self, output: str) -> list[tuple[int, str]]:
        """Разобрать вывод :attr:`port_status_cmd` в список ``(порт, статус)``.

        Базовая реализация рассчитана на Cisco-подобный ``show interfaces
        status`` (первый столбец — интерфейс, где-то дальше — слово статуса).
        Вендоры с иным форматом переопределяют метод.
        """
        rows: list[tuple[int, str]] = []
        for line in output.splitlines():
            toks = line.split()
            if len(toks) < 2:
                continue
            port = self._last_port_number(toks[0])
            if port is None:
                continue
            status = next((LINK_STATUS_MAP[t.lower()] for t in toks[1:]
                           if t.lower() in LINK_STATUS_MAP), None)
            if status is not None:
                rows.append((port, status))
        return rows

    def list_port_status(self) -> list[tuple[int, str]]:
        """Вернуть отсортированный список ``(порт, статус)`` без дублей.

        Дубли по одному порту (напр. combo copper/fiber) сливаются в «лучший»
        статус: up > down > disabled.
        """
        if not self.port_status_cmd:
            raise DriverError("просмотр списка портов не поддержан для этого вендора")
        best: dict[int, str] = {}
        for port, status in self.parse_port_status(self._run(self.port_status_cmd)):
            rank = _STATUS_RANK.get(status, _STATUS_RANK["unknown"])
            if port not in best or rank < _STATUS_RANK.get(best[port], 3):
                best[port] = status
        return sorted(best.items())

    def port_vlans(self, port_number: int) -> set[int] | None:
        """Вернуть VLAN'ы, в которых порт сейчас состоит (access + trunk).

        Служит «защитой от дурака»: если среди них окажется аплинковый VLAN,
        вызывающий код откажется перенастраивать порт. Возврат ``None`` — не
        удалось определить (пустой вывод, dry-run, незнакомый формат) — тогда
        проверка не блокирует, но предупреждает.

        База разбирает Cisco-подобный ``show interfaces switchport <iface>``
        (строки ``Access Mode VLAN``, ``Trunking Native Mode VLAN``,
        ``Trunking VLANs Enabled``). Вендоры с иным CLI переопределяют метод.
        """
        out = self._run(f"show interfaces switchport {self.iface(port_number)}")
        if not out.strip():
            return None
        vlans: set[int] = set()
        found = False
        for line in out.splitlines():
            if ":" not in line or "vlan" not in line.lower():
                continue
            low, val = line.lower(), line.split(":", 1)[1].strip()
            if "access mode vlan" in low or "native mode vlan" in low:
                m = re.match(r"(\d+)", val)
                if m:
                    vlans.add(int(m.group(1)))
                    found = True
            elif "trunk" in low and ("enable" in low or "allow" in low):
                got = parse_int_ranges(val)
                vlans |= got
                found = True
        return vlans if found else None

    def is_uplink_port(self, port_number: int, uplink_vlan: int) -> bool | None:
        """``True`` — порт несёт ``uplink_vlan`` (похоже на аплинк), ``False`` —
        нет, ``None`` — определить не удалось."""
        vlans = self.port_vlans(port_number)
        if vlans is None:
            return None
        return uplink_vlan in vlans

    # -- общий сценарий ----------------------------------------------------
    def vlan_for_port(self, port_number: int) -> int:
        return VLAN_BASE + port_number

    def swap(self, port_number: int, vlan_id: int, save: bool = True) -> None:
        """Полный сценарий назначения ``vlan_id`` access'ом на порт ``port_number``."""
        self.enter_config()
        try:
            self.create_vlan(vlan_id)
            self.set_access_vlan(port_number, vlan_id)
        finally:
            self.exit_config()
        if save:
            self.save()

    # -- утилиты для наследников -------------------------------------------
    def _run(self, command: str, **kw) -> str:
        return self.s.run(command, **kw)

    def _run_confirm(self, command: str, yes: str = "Y", confirm_re: str = r"\[?(y/n|yes/no)\]?",
                     timeout: float | None = None) -> str:
        """Выполнить команду, которая может запросить подтверждение (Y/N).

        Если устройство спрашивает подтверждение — отсылаем ``yes``. Если сразу
        вернулся промпт — ничего лишнего не шлём.
        """
        text, idx = self.s.run_expect(command, expect=[confirm_re, self.s.prompt_re], timeout=timeout)
        if idx == 0:
            return self._run(yes, timeout=timeout)
        return text

    @staticmethod
    def _last_port_number(token: str) -> int | None:
        """Вытащить номер порта из строки вида ``gi1/0/5`` / ``Eth0/0/5`` / ``5``.

        Берётся последняя группа цифр — это индекс порта в стеке/слоте.
        """
        nums = re.findall(r"\d+", token)
        return int(nums[-1]) if nums else None

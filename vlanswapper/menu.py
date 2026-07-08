"""Интерактивное текстовое меню.

Показывается, когда скрипт запущен интерактивно без явного --port/--mac.
Соединение и вендор уже определены в вызывающем коде; меню крутится в цикле,
позволяя настроить несколько портов за одну сессию.
"""

from __future__ import annotations

import sys

from . import mac as macfmt
from .drivers import DriverError


def _ask(prompt: str) -> str:
    print(prompt, end="", file=sys.stderr, flush=True)
    return input().strip()


def _choose_port_by_number(driver) -> int | None:
    raw = _ask("Номер порта: ")
    if not raw.isdigit():
        print("Нужно целое число.", file=sys.stderr)
        return None
    return int(raw)


def _choose_port_by_mac(driver) -> int | None:
    raw = _ask("MAC-адрес клиента: ")
    try:
        mac = macfmt.normalize(raw)
    except ValueError as exc:
        print(f"{exc}", file=sys.stderr)
        return None
    print(f"Ищу порт по MAC {macfmt.colon(mac)}...", file=sys.stderr)
    port = driver.find_port_by_mac(mac)
    if port is None:
        print("MAC не найден в таблице коммутации.", file=sys.stderr)
        return None
    print(f"MAC найден на порту {port}.", file=sys.stderr)
    return port


def run_menu(driver, host: str, vendor: str, apply_cb) -> int:
    """Крутить меню до выхода. ``apply_cb(port) -> bool`` применяет VLAN к порту."""
    while True:
        print(
            f"\n=== Свитч {host} ({vendor}) ===\n"
            "  1. Указать номер порта\n"
            "  2. Найти порт по MAC-адресу\n"
            "  0. Выход",
            file=sys.stderr,
        )
        choice = _ask("Выбор: ")
        if choice in ("0", "q", "exit"):
            print("Выход.", file=sys.stderr)
            return 0
        if choice == "1":
            port = _choose_port_by_number(driver)
        elif choice == "2":
            port = _choose_port_by_mac(driver)
        else:
            print("Неизвестный пункт меню.", file=sys.stderr)
            continue
        if port is None:
            continue
        try:
            apply_cb(port)
        except DriverError as exc:
            print(f"Ошибка: {exc}", file=sys.stderr)

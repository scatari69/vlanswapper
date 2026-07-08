"""Точка входа CLI: разбор аргументов, интерактивный диалог, оркестрация.

Сценарий работы:
  1. Разобрать настройки подключения (CLI/env/config, недостающее — спросить).
  2. Подключиться по Telnet, залогиниться, войти в enable (если нужно).
  3. Определить вендора (или взять из --vendor) и создать драйвер.
  4. Определить номер порта: из --port, или найти по --mac, или спросить.
  5. Посчитать vlan = 100 + порт и выполнить swap (с подтверждением).
"""

from __future__ import annotations

import argparse
import sys

from . import mac as macfmt
from .detect import build_driver, detect_vendor
from .drivers import REGISTRY, VLAN_BASE, DriverError
from .menu import run_menu
from .session import LoginError, Session, TelnetError
from .settings import resolve
from .telnet import TelnetClient


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="vlanswapper",
        description="Настройка access-VLAN (vlan = 100 + номер порта) на портах свитча по Telnet.",
    )
    p.add_argument("--host", help="IP/имя свитча (иначе из env/config или спросим)")
    p.add_argument("--port-tcp", dest="port", type=int, help="TCP-порт Telnet (по умолчанию 23)")
    p.add_argument("--username", help="логин (иначе из env/config или спросим)")
    p.add_argument("--password", help="пароль (лучше через env/config, а не в командной строке)")
    p.add_argument("--vendor", choices=sorted(REGISTRY),
                   help="принудительно задать вендора вместо автоопределения")
    p.add_argument("--timeout", type=float, help="таймаут чтения, сек (по умолчанию 10)")

    target = p.add_mutually_exclusive_group()
    target.add_argument("-p", "--port", dest="switch_port", type=int,
                        help="номер порта свитча")
    target.add_argument("-m", "--mac", help="MAC клиента — найти порт по нему")

    p.add_argument("--no-save", action="store_true", help="не сохранять конфиг после настройки")
    p.add_argument("--dry-run", action="store_true",
                   help="показать команды, но не отправлять их на устройство")
    p.add_argument("--yes", action="store_true", help="не спрашивать подтверждение")
    p.add_argument("-v", "--verbose", action="store_true", help="печатать отправляемые команды")
    return p


def _logger(verbose: bool):
    def log(msg: str) -> None:
        if verbose or msg.startswith("$"):
            print(msg, file=sys.stderr)
    return log


def _resolve_port_from_args(driver, args) -> int:
    """Определить порт по явным --port/--mac (неинтерактивный путь)."""
    if args.switch_port is not None:
        return args.switch_port
    mac = macfmt.normalize(args.mac)
    print(f"Ищу порт по MAC {macfmt.colon(mac)}...", file=sys.stderr)
    port = driver.find_port_by_mac(mac)
    if port is None:
        raise DriverError(f"MAC {macfmt.colon(mac)} не найден в таблице коммутации")
    print(f"MAC найден на порту {port}", file=sys.stderr)
    return port


def _apply(driver, port: int, args, interactive: bool) -> bool:
    """Посчитать VLAN, подтвердить (если нужно) и выполнить swap. Вернуть успех."""
    vlan_id = driver.vlan_for_port(port)
    print(f"Порт {port} → access VLAN {vlan_id} (= {VLAN_BASE} + {port})", file=sys.stderr)
    if not args.yes and interactive and not args.dry_run:
        if input("Применить? [y/N]: ").strip().lower() not in ("y", "yes", "д", "да"):
            print("Пропущено.", file=sys.stderr)
            return False
    driver.swap(port, vlan_id, save=not args.no_save)
    print(f"Готово: порт {port} = VLAN {vlan_id}"
          + (" (dry-run, ничего не отправлено)" if args.dry_run else ""),
          file=sys.stderr)
    return True


def run(argv=None) -> int:
    args = build_parser().parse_args(argv)
    interactive = sys.stdin.isatty()
    log = _logger(args.verbose)

    try:
        cfg = resolve(args, interactive=interactive)
    except ValueError as exc:
        print(f"Ошибка настроек: {exc}", file=sys.stderr)
        return 2

    client = TelnetClient(cfg.host, cfg.port, timeout=cfg.timeout)
    try:
        client.open()
        session = Session(client, dry_run=args.dry_run, log=log)
        prompt = session.login(cfg.username, cfg.password, cfg.enable_password)

        vendor = args.vendor or detect_vendor(session, prompt)
        driver = build_driver(session, vendor)
        print(f"Вендор: {vendor}", file=sys.stderr)
        driver.disable_paging()

        # Явно заданный порт/MAC — одноразовый прогон. Иначе интерактивное меню.
        if args.switch_port is not None or args.mac:
            port = _resolve_port_from_args(driver, args)
            return 0 if _apply(driver, port, args, interactive) else 1

        if not interactive:
            raise ValueError("не задан порт: укажите --port или --mac")

        return run_menu(driver, cfg.host, vendor,
                        lambda port: _apply(driver, port, args, interactive))

    except (TelnetError, LoginError, DriverError, ValueError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nПрервано.", file=sys.stderr)
        return 130
    finally:
        client.close()

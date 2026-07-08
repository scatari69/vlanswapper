"""Разрешение параметров подключения из конфиг-файла, окружения и CLI.

Приоритет (по убыванию): аргумент командной строки → переменная окружения →
config.ini → интерактивный запрос. Пароль в интерактиве читается через getpass.
"""

from __future__ import annotations

import configparser
import getpass
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG_PATHS = (
    Path("config.ini"),
    Path.home() / ".config" / "vlanswapper" / "config.ini",
)

ENV = {
    "host": "VLANSWAPPER_HOST",
    "port": "VLANSWAPPER_PORT",
    "username": "VLANSWAPPER_USER",
    "password": "VLANSWAPPER_PASSWORD",
    "enable_password": "VLANSWAPPER_ENABLE",
}


@dataclass
class Settings:
    host: str
    username: str
    password: str
    port: int = 23
    enable_password: str | None = None
    timeout: float = 10.0


def _load_ini(path: Path | None) -> dict[str, str]:
    parser = configparser.ConfigParser()
    if path is not None:
        parser.read(path)
    else:
        for candidate in DEFAULT_CONFIG_PATHS:
            if candidate.exists():
                parser.read(candidate)
                break
    return dict(parser["switch"]) if parser.has_section("switch") else {}


def resolve(args, config_path: Path | None = None, interactive: bool = True) -> Settings:
    """Собрать :class:`Settings`, дозапрашивая недостающее у пользователя."""
    ini = _load_ini(config_path)

    def pick(key: str, default=None):
        cli_val = getattr(args, key, None)
        if cli_val:
            return cli_val
        if ENV[key] in os.environ:
            return os.environ[ENV[key]]
        return ini.get(key, default)

    host = pick("host")
    if not host and interactive:
        host = input("IP/имя свитча: ").strip()
    if not host:
        raise ValueError("не задан адрес свитча (--host / VLANSWAPPER_HOST / config.ini)")

    username = pick("username")
    if not username and interactive:
        username = input("Логин: ").strip()

    password = pick("password")
    if not password and interactive:
        password = getpass.getpass("Пароль: ")

    enable_password = pick("enable_password")
    port = int(pick("port", 23) or 23)
    timeout = float(getattr(args, "timeout", None) or ini.get("timeout", 10.0))

    if not username or password is None:
        raise ValueError("не заданы логин/пароль")

    return Settings(
        host=host,
        username=username,
        password=password,
        port=port,
        enable_password=enable_password or None,
        timeout=timeout,
    )

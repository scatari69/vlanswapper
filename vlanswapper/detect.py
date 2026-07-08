"""Автоопределение вендора по выводу version-команд и приглашению.

Логика: перебираем нейтральные команды показа версии (у каждого вендора своя),
собираем ответы и ищем в них вендор-специфичные маркеры. Дополнительно
учитываем текст приглашения — иногда в нём уже есть имя устройства.
"""

from __future__ import annotations

from .drivers import REGISTRY, BaseDriver, DriverError
from .session import Session

# Команды, безопасные для показа версии/платформы на разных CLI.
PROBE_COMMANDS = (
    "show version",      # Eltex, BDCOM, Zyxel
    "display version",   # Huawei
    "show switch",       # D-Link
)


def detect_vendor(session: Session, prompt: str = "") -> str:
    """Вернуть имя вендора из :data:`REGISTRY` или бросить :class:`DriverError`."""
    haystack = prompt.lower()
    for cmd in PROBE_COMMANDS:
        try:
            out = session.run(cmd, timeout=session.c.timeout)
        except Exception as exc:  # таймаут/ошибка на неподдерживаемой команде — пропускаем
            session.log(f"[detect] '{cmd}' не отработала: {exc}")
            continue
        haystack += "\n" + out.lower()
        vendor = _match(haystack)
        if vendor:
            session.log(f"[detect] вендор определён как {vendor} по '{cmd}'")
            return vendor
    vendor = _match(haystack)
    if vendor:
        return vendor
    raise DriverError(
        "не удалось определить вендора автоматически; укажите его через --vendor"
    )


def _match(text: str) -> str | None:
    best_name: str | None = None
    best_len = 0
    for name, cls in REGISTRY.items():
        for marker in cls.detect_markers:
            # Самый длинный совпавший маркер = самый специфичный: 'des-1210'
            # должен побеждать общий 'des-' у базового D-Link.
            if marker in text and len(marker) > best_len:
                best_name, best_len = name, len(marker)
    return best_name


def build_driver(session: Session, vendor: str) -> BaseDriver:
    from .drivers import get_driver
    return get_driver(vendor)(session)

"""Нормализация MAC-адресов под разные вендорские форматы."""

from __future__ import annotations

import re

_HEX = re.compile(r"[0-9a-fA-F]")


def normalize(mac: str) -> str:
    """Привести MAC к 12 hex-символам в нижнем регистре без разделителей.

    Принимает любые распространённые формы: ``aa:bb:cc:dd:ee:ff``,
    ``aabb.ccdd.eeff``, ``AA-BB-CC-DD-EE-FF``, ``aabbccddeeff``.
    Бросает :class:`ValueError`, если это не похоже на MAC.
    """
    digits = "".join(_HEX.findall(mac))
    if len(digits) != 12:
        raise ValueError(f"некорректный MAC-адрес: {mac!r}")
    return digits.lower()


def _grouped(mac: str, size: int, sep: str) -> str:
    d = normalize(mac)
    return sep.join(d[i:i + size] for i in range(0, 12, size))


def colon(mac: str) -> str:
    """aa:bb:cc:dd:ee:ff — Eltex, BDCOM, Zyxel."""
    return _grouped(mac, 2, ":")


def dot(mac: str) -> str:
    """aabb.ccdd.eeff — Cisco-подобный формат."""
    return _grouped(mac, 4, ".")


def dash(mac: str) -> str:
    """aa-bb-cc-dd-ee-ff — D-Link."""
    return _grouped(mac, 2, "-").upper()


def huawei(mac: str) -> str:
    """aabb-ccdd-eeff — Huawei VRP."""
    return _grouped(mac, 4, "-")

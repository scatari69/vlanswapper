"""Vendor driver registry."""

from __future__ import annotations

from .base import UPLINK_VLAN, VLAN_BASE, BaseDriver, DriverError
from .bdcom import BdcomDriver
from .dlink import DlinkDriver
from .dlink_1100 import Dlink1100Driver
from .dlink_1100_me import Dlink1100MeDriver
from .dlink_des1210 import DlinkDes1210Driver
from .eltex import EltexDriver
from .huawei import HuaweiDriver
from .zyxel import ZyxelDriver

#: vendor name -> driver class
REGISTRY: dict[str, type[BaseDriver]] = {
    d.name: d
    for d in (DlinkDriver, DlinkDes1210Driver, Dlink1100Driver, Dlink1100MeDriver,
              EltexDriver, HuaweiDriver, BdcomDriver, ZyxelDriver)
}


def get_driver(name: str) -> type[BaseDriver]:
    try:
        return REGISTRY[name.lower()]
    except KeyError:
        raise DriverError(
            f"unknown vendor {name!r}; available: {', '.join(sorted(REGISTRY))}"
        ) from None


__all__ = ["REGISTRY", "get_driver", "BaseDriver", "DriverError", "VLAN_BASE",
           "UPLINK_VLAN"]

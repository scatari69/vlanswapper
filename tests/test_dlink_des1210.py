"""Тесты драйвера D-Link DES-1210: детект-приоритет и команды VLAN.

Используется recording-двойник сессии (без сети): он отдаёт заранее заданные
ответы по подстроке команды и запоминает всё отправленное.
"""

import types
import unittest

from vlanswapper.detect import detect_vendor
from vlanswapper.drivers import get_driver
from vlanswapper.drivers.dlink_des1210 import DlinkDes1210Driver


class FakeSession:
    def __init__(self, responses: dict[str, str] | None = None):
        self.responses = responses or {}
        self.sent: list[str] = []
        self.prompt_re = r"[>#]\s*$"
        self.c = types.SimpleNamespace(timeout=3.0)

    def _lookup(self, command: str) -> str:
        for key, val in self.responses.items():
            if key in command:
                return val
        return ""

    def run(self, command, **kw):
        self.sent.append(command)
        return self._lookup(command)

    def run_expect(self, command, expect, **kw):
        self.sent.append(command)
        return self._lookup(command), 1  # 1 = сразу промпт, без подтверждения

    def log(self, msg):
        pass


class DetectTests(unittest.TestCase):
    def test_des1210_beats_generic_dlink(self):
        # В баннере есть и общий 'D-Link', и конкретная модель — должен победить DES-1210.
        sess = FakeSession({"show switch": "Device Type : DES-1210-28 D-Link Smart Switch"})
        self.assertEqual(detect_vendor(sess), "dlink_des1210")

    def test_generic_dlink_for_other_models(self):
        sess = FakeSession({"show switch": "Device Type : DES-3200-28 D-Link"})
        self.assertEqual(detect_vendor(sess), "dlink")

    def test_registry_has_model(self):
        self.assertIs(get_driver("dlink_des1210"), DlinkDes1210Driver)


class VlanCommandTests(unittest.TestCase):
    def test_set_access_vlan_moves_port(self):
        # Порт 5 сейчас untagged в VLAN 1 -> должен уйти в VLAN 105.
        sess = FakeSession({"show vlan ports 5": "  5     1     X     -     -"})
        DlinkDes1210Driver(sess).set_access_vlan(5, 105)
        self.assertIn("config vlan vlanid 1 delete 5", sess.sent)
        self.assertIn("config vlan vlanid 105 add untagged 5", sess.sent)
        self.assertIn("config port_vlan 5 pvid 105", sess.sent)
        # PVID-команда именно модельная, а не gvrp от DES-3200.
        self.assertFalse(any("gvrp" in c for c in sess.sent))

    def test_find_port_by_mac(self):
        fdb = "100  vlan100  AA-BB-CC-DD-EE-FF  7  Dynamic"
        sess = FakeSession({"show fdb mac_address": fdb})
        port = DlinkDes1210Driver(sess).find_port_by_mac("aa:bb:cc:dd:ee:ff")
        self.assertEqual(port, 7)


if __name__ == "__main__":
    unittest.main()

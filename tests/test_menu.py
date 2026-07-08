"""Тест интерактивного меню против mock-свитча (input/isatty подменяются)."""

import unittest
from unittest import mock

from tests.mock_switch import MockSwitch
from vlanswapper.cli import run


def _run_with_input(sw: MockSwitch, answers):
    it = iter(answers)
    with mock.patch("sys.stdin.isatty", return_value=True), \
         mock.patch("builtins.input", lambda *a: next(it)):
        return run(["--host", "127.0.0.1", "--port-tcp", str(sw.port),
                    "--username", "admin", "--password", "secret"])


class MenuTests(unittest.TestCase):
    def test_menu_specify_port(self):
        sw = MockSwitch().start()
        rc = _run_with_input(sw, ["1", "5", "y", "0"])
        self.assertEqual(rc, 0)
        self.assertIn("switchport access vlan 105", "\n".join(sw.received))

    def test_menu_find_by_mac(self):
        sw = MockSwitch(mac_port={"aabb.ccdd.eeff": 7}).start()
        rc = _run_with_input(sw, ["2", "aa:bb:cc:dd:ee:ff", "y", "0"])
        self.assertEqual(rc, 0)
        joined = "\n".join(sw.received)
        self.assertIn("show mac address-table address", joined)
        self.assertIn("switchport access vlan 107", joined)  # порт 7 -> VLAN 107

    def test_menu_show_mac_table(self):
        sw = MockSwitch(mac_port={"aabb.ccdd.eeff": 7}).start()
        rc = _run_with_input(sw, ["3", "0"])
        self.assertEqual(rc, 0)
        joined = "\n".join(sw.received)
        # Полный дамп таблицы, без адреса — новый пункт меню.
        self.assertIn("show mac address-table", joined)
        self.assertFalse(any("show mac address-table address" in c for c in sw.received))
        # Просмотр таблицы не должен ничего конфигурировать.
        self.assertNotIn("switchport access vlan", joined)

    def test_menu_immediate_exit(self):
        sw = MockSwitch().start()
        rc = _run_with_input(sw, ["0"])
        self.assertEqual(rc, 0)
        # После логина/детекта конфигурационных команд быть не должно.
        self.assertNotIn("configure terminal", sw.received)


if __name__ == "__main__":
    unittest.main()

"""Интеграционный тест против mock-свитча: логин, детект, swap, поиск по MAC."""

import unittest

from tests.mock_switch import MockSwitch
from vlanswapper.detect import build_driver, detect_vendor
from vlanswapper.session import Session
from vlanswapper.telnet import TelnetClient


class IntegrationTests(unittest.TestCase):
    def _connect(self, sw: MockSwitch) -> tuple[TelnetClient, Session, str]:
        client = TelnetClient("127.0.0.1", sw.port, timeout=3.0)
        client.open()
        session = Session(client)
        prompt = session.login("admin", "secret", enable_password="secret")
        return client, session, prompt

    def test_login_detect_and_swap(self):
        sw = MockSwitch().start()
        client, session, prompt = self._connect(sw)
        try:
            self.assertTrue(prompt.endswith("#"), f"ожидали enable-режим, получили {prompt!r}")

            vendor = detect_vendor(session, prompt)
            self.assertEqual(vendor, "eltex")

            driver = build_driver(session, vendor)
            driver.disable_paging()
            driver.swap(5, driver.vlan_for_port(5), save=True)
        finally:
            client.close()

        # Порт 5 -> VLAN 105, назначенный access'ом, и конфиг сохранён.
        joined = "\n".join(sw.received)
        self.assertIn("vlan 105", joined)
        self.assertIn("interface gigabitethernet 1/0/5", joined)
        self.assertIn("switchport access vlan 105", joined)
        self.assertIn("write", joined)

    def test_find_port_by_mac(self):
        sw = MockSwitch(mac_port={"aabb.ccdd.eeff": 7}).start()
        client, session, prompt = self._connect(sw)
        try:
            driver = build_driver(session, detect_vendor(session, prompt))
            port = driver.find_port_by_mac("aabb.ccdd.eeff")
        finally:
            client.close()
        self.assertEqual(port, 7)


if __name__ == "__main__":
    unittest.main()

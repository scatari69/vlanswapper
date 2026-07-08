"""Юнит-тесты без сети: форматы MAC и разбор IAC в TelnetClient."""

import unittest

from vlanswapper import mac
from vlanswapper.telnet import DO, ECHO, IAC, WILL, TelnetClient


class MacTests(unittest.TestCase):
    def test_normalize_accepts_all_forms(self):
        for form in ("aa:bb:cc:dd:ee:ff", "aabb.ccdd.eeff", "AA-BB-CC-DD-EE-FF", "aabbccddeeff"):
            self.assertEqual(mac.normalize(form), "aabbccddeeff")

    def test_vendor_formats(self):
        m = "aabbccddeeff"
        self.assertEqual(mac.colon(m), "aa:bb:cc:dd:ee:ff")
        self.assertEqual(mac.dot(m), "aabb.ccdd.eeff")
        self.assertEqual(mac.dash(m), "AA-BB-CC-DD-EE-FF")
        self.assertEqual(mac.huawei(m), "aabb-ccdd-eeff")

    def test_invalid(self):
        with self.assertRaises(ValueError):
            mac.normalize("not-a-mac")


class TelnetParsingTests(unittest.TestCase):
    def test_iac_negotiation_stripped(self):
        c = TelnetClient("127.0.0.1")           # сокет не открываем
        c._feed(bytes([ord("A"), ord("B"), IAC, WILL, ECHO, ord("C"), IAC, DO, ECHO, ord("D")]))
        self.assertEqual(c.take_buffer(), "ABCD")

    def test_escaped_iac_becomes_single_byte(self):
        c = TelnetClient("127.0.0.1")
        c._feed(bytes([ord("X"), IAC, IAC, ord("Y")]))
        self.assertEqual(c.take_buffer(), "X\xffY")

    def test_split_sequence_across_feeds(self):
        c = TelnetClient("127.0.0.1")
        c._feed(bytes([ord("A"), IAC]))          # последовательность оборвана
        c._feed(bytes([WILL, ECHO, ord("B")]))   # продолжение
        self.assertEqual(c.take_buffer(), "AB")


if __name__ == "__main__":
    unittest.main()

import random
import unittest
from types import SimpleNamespace

from sip.server_location import ServerLocator


class ServerLocationTests(unittest.TestCase):
    def test_naptr_srv_priority_weight_and_address_family_expansion(self):
        records = {
            ("voice.example", "NAPTR"): [
                SimpleNamespace(order=10, preference=10, service='"SIP+D2T"', replacement="_sip._tcp.voice.example."),
            ],
            ("_sip._tcp.voice.example", "SRV"): [
                SimpleNamespace(priority=20, weight=0, port=5070, target="backup.example."),
                SimpleNamespace(priority=10, weight=10, port=5060, target="primary.example."),
            ],
        }
        addresses = {
            ("primary.example", 5060): [("2001:db8::10", 5060), ("192.0.2.10", 5060)],
            ("backup.example", 5070): [("192.0.2.20", 5070)],
        }
        locator = ServerLocator(
            query=lambda name, kind: records.get((name, kind), []),
            address_lookup=lambda host, port: addresses[(host, port)],
            rng=random.Random(1),
        )

        targets = locator.resolve("voice.example", 5060, "tcp")

        self.assertEqual([item.host for item in targets], ["2001:db8::10", "192.0.2.10", "192.0.2.20"])
        self.assertEqual([item.port for item in targets], [5060, 5060, 5070])

    def test_cache_ttl_avoids_repeat_queries_and_then_refreshes(self):
        now = [10.0]
        calls = []

        def query(name, kind):
            calls.append((name, kind))
            return []

        locator = ServerLocator(
            {"cache_ttl": 20, "negative_ttl": 2},
            query=query,
            address_lookup=lambda host, port: [("192.0.2.30", port)],
            clock=lambda: now[0],
        )
        self.assertEqual(locator.resolve("cache.example", 5060, "udp")[0].host, "192.0.2.30")
        first_call_count = len(calls)
        locator.resolve("cache.example", 5060, "udp")
        self.assertEqual(len(calls), first_call_count)
        now[0] += 21
        locator.resolve("cache.example", 5060, "udp")
        self.assertGreater(len(calls), first_call_count)

    def test_ip_literal_bypasses_dns(self):
        locator = ServerLocator(query=lambda *_args: self.fail("DNS query was not expected"))
        target = locator.resolve("2001:db8::5", 5061, "tls")[0]
        self.assertEqual((target.host, target.port, target.transport), ("2001:db8::5", 5061, "tls"))

    def test_target_list_is_bounded(self):
        locator = ServerLocator(
            {"naptr": False, "srv": False, "max_targets": 2},
            address_lookup=lambda _host, port: [
                ("192.0.2.1", port), ("192.0.2.2", port), ("192.0.2.3", port)
            ],
        )
        self.assertEqual(len(locator.resolve("bounded.example", 5060, "tcp")), 2)


if __name__ == "__main__":
    unittest.main()

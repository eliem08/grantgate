import unittest
from datetime import datetime, timedelta

from agent_plaid import AccessError, Hub, MockBank, MockStore

NOW = datetime(2026, 10, 24, 12)


class Tests(unittest.TestCase):
    def setUp(self):
        self.hub = Hub()
        self.bank = MockBank()
        self.bank.open("a1", 1000)
        self.hub.add_institution("Bank", self.bank)
        self.hub.add_institution("Store", MockStore())
        self.link = self.hub.link("u", "Bank", "a1")
        self.tok = self.hub.grant(
            "u",
            "agent",
            self.link,
            ["bank:read", "bank:transfer"],
            per_txn_cap=100,
            total_cap=150,
            payees={"ConEd"},
            ttl=timedelta(days=1),
            now=NOW,
        )

    def test_read_and_transfer(self):
        self.assertEqual(self.hub.call(self.tok, "balances", now=NOW), {"available": 1000})
        self.hub.call(self.tok, "transfer", now=NOW, amount=80, payee="ConEd")
        self.assertEqual(self.bank.balances_["a1"], 920)

    def test_caps_and_payees(self):
        with self.assertRaises(AccessError):
            self.hub.call(self.tok, "transfer", now=NOW, amount=120, payee="ConEd")
        with self.assertRaises(AccessError):
            self.hub.call(self.tok, "transfer", now=NOW, amount=10, payee="Mallory")
        self.hub.call(self.tok, "transfer", now=NOW, amount=100, payee="ConEd")
        with self.assertRaises(AccessError):
            self.hub.call(self.tok, "transfer", now=NOW, amount=60, payee="ConEd")

    def test_expiry_revoke_invalid(self):
        with self.assertRaises(AccessError):
            self.hub.call(self.tok, "balances", now=NOW + timedelta(days=2))
        with self.assertRaises(AccessError):
            self.hub.call("ap_fake", "balances", now=NOW)
        gid = next(iter(self.hub.grants))
        self.hub.revoke(gid)
        with self.assertRaises(AccessError):
            self.hub.call(self.tok, "balances", now=NOW)
        row = self.hub.dashboard("u")[0]
        self.assertEqual(row["denied"], 2)

    def test_scope_validation(self):
        with self.assertRaises(AccessError):
            self.hub.grant("u", "agent", self.link, ["store:buy"])
        with self.assertRaises(AccessError):
            self.hub.grant("someone-else", "agent", self.link, ["bank:read"])
        ro = self.hub.grant("u", "agent", self.link, ["bank:read"], now=NOW)
        with self.assertRaises(AccessError):
            self.hub.call(ro, "transfer", now=NOW, amount=1, payee="x")

    def test_tokens_not_stored_in_clear(self):
        self.assertNotIn(self.tok, self.hub.tokens)

    def test_nan_amount_cannot_slip_past_or_poison_the_caps(self):
        for amount in (float("nan"), "nan", float("inf"), "lots"):
            with self.subTest(amount=amount), self.assertRaises(AccessError):
                self.hub.call(self.tok, "transfer", now=NOW, amount=amount, payee="ConEd")
        self.hub.call(self.tok, "transfer", now=NOW, amount=100, payee="ConEd")
        with self.assertRaises(AccessError):  # total cap 150 still enforced after the junk attempts
            self.hub.call(self.tok, "transfer", now=NOW, amount=60, payee="ConEd")

    def test_negative_amount_cannot_refill_the_budget(self):
        self.hub.call(self.tok, "transfer", now=NOW, amount=100, payee="ConEd")
        with self.assertRaises(AccessError):
            self.hub.call(self.tok, "transfer", now=NOW, amount=-1000, payee="ConEd")
        self.assertEqual(self.bank.balances_["a1"], 900)
        with self.assertRaises(AccessError):
            self.hub.call(self.tok, "transfer", now=NOW, amount=60, payee="ConEd")


if __name__ == "__main__":
    unittest.main()

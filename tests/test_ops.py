import argparse
import contextlib
import hashlib
import hmac
import io
import os
import socket
import tempfile
import unittest

from grantgate.__main__ import build_parser
from grantgate.ops import check_url, load, run_ops


def public(host, port, proto=0):
    return [(socket.AF_INET, socket.SOCK_STREAM, proto, "", ("34.1.2.3", port))]


class GatewayOpsTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop("GRANTGATE_SIGNING_KEY", None)
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "gg.json")
        self.sent = []

    def tearDown(self):
        self.tmp.cleanup()

    def post(self, url, body, signature):
        self.sent.append((url, body, signature))
        return {"status": "sent"}

    def run_cmd(self, *argv):
        a: argparse.Namespace = build_parser().parse_args([*argv, "--store", self.path])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = run_ops(a, post=self.post, resolve=public)
        return code, out.getvalue()

    def grant(self, *extra, url=None):
        inst = ["institution-add", "bank", "--kind", "bank", *(["--url", url] if url else [])]
        self.assertEqual(self.run_cmd(*inst)[0], 0)
        self.run_cmd("link", "ana", "bank", "acct-1")
        code, out = self.run_cmd("grant", "ana", "shopper", "link-1", "--scopes", "bank:transfer", "bank:read", *extra)
        self.assertEqual(code, 0, out)
        return out.strip().splitlines()[-1]

    def test_caps_payees_and_scopes_are_enforced_and_audited(self):
        tok = self.grant("--per-txn-cap", "100", "--total-cap", "150", "--payee", "landlord")
        pay = ["--payee", "landlord"]
        self.assertEqual(self.run_cmd("call", tok, "transfer", "--amount", "80", *pay)[0], 0)
        for argv in (
            ["transfer", "--amount", "80", *pay],  # would pass the total cap
            ["transfer", "--amount", "120", *pay],  # over the per-transaction cap
            ["transfer", "--amount", "10", "--payee", "mallory"],
            ["transfer", "--amount", "-10", *pay],
            ["checkout", "--amount", "5"],  # a store scope on a bank grant
        ):
            with self.subTest(argv=argv):
                code, out = self.run_cmd("call", tok, *argv)
                self.assertEqual((code, out[:4]), (1, "DENY"))
        hub = load(self.path)
        g = hub.grants["grant-2"]
        self.assertEqual((g.spent, sum(not e["allowed"] for e in g.log)), (80.0, 5))  # denials are logged too
        self.assertEqual(self.run_cmd("call", "ap_not_a_real_token", "balances")[0], 1)

    def test_approved_calls_are_forwarded_signed_and_denied_ones_never_leave(self):
        tok = self.grant("--total-cap", "50", url="https://bank.example.com/agent")
        self.assertEqual(self.run_cmd("call", tok, "transfer", "--amount", "20", "--payee", "x")[0], 0)
        self.assertEqual(self.run_cmd("call", tok, "transfer", "--amount", "40", "--payee", "x")[0], 1)
        self.assertEqual(len(self.sent), 1)
        url, body, sig = self.sent[0]
        self.assertEqual(url, "https://bank.example.com/agent")
        with open(os.path.join(self.tmp.name, "gg.key"), encoding="utf-8") as fh:
            key = fh.read().encode()
        self.assertEqual(sig, hmac.new(key, body, hashlib.sha256).hexdigest())
        self.assertIn(b'"account": "acct-1"', body)

    def test_token_is_stored_hashed_and_revocation_is_immediate(self):
        tok = self.grant()
        with open(self.path, encoding="utf-8") as fh:
            self.assertNotIn(tok, fh.read())
        self.assertEqual(self.run_cmd("call", tok, "balances")[0], 0)
        self.run_cmd("revoke", "grant-2")
        self.assertEqual(self.run_cmd("call", tok, "balances")[0], 1)
        self.assertIn("REVOKED", self.run_cmd("dashboard", "ana")[1])

    def test_only_public_https_backends(self):
        self.assertEqual(check_url("https://bank.example.com/x", public), "https://bank.example.com/x")
        self.assertEqual(
            self.run_cmd("institution-add", "a", "--kind", "bank", "--url", "http://bank.example.com")[0], 1
        )
        a = build_parser().parse_args(["institution-add", "b", "--kind", "bank", "--url", "https://10.0.0.5/x"])
        a.store = self.path
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_ops(a, post=self.post), 1)  # real resolver: a private address is refused


if __name__ == "__main__":
    unittest.main()

"""Stripe Issuing real-time authorizations decided by a grant, over real HTTP (no network beyond localhost)."""

import contextlib
import hashlib
import hmac
import io
import json
import os
import pathlib
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

from grantgate.__main__ import main
from grantgate.issuing import verify

SECRET = "whsec_test"


def sign(body: bytes, secret=SECRET, t=None) -> str:
    t = int(time.time()) if t is None else t
    return f"t={t},v1={hmac.new(secret.encode(), f'{t}.'.encode() + body, hashlib.sha256).hexdigest()}"


def auth_event(
    cents, merchant="Landlord LLC", card="ic_1TestCard", currency="usd", kind="issuing_authorization.request"
):
    return {
        "type": kind,
        "data": {
            "object": {
                "id": "iauth_1",
                "object": "issuing_authorization",
                "card": {"id": card},
                "pending_request": {"amount": cents, "currency": currency},
                "merchant_data": {"name": merchant, "network_id": "1234567", "category": "real_estate_agents"},
            }
        },
    }


class IssuingTests(unittest.TestCase):
    def setUp(self):
        from grantgate.web import make_server

        self.tmp = tempfile.TemporaryDirectory()
        self.data = pathlib.Path(self.tmp.name)
        self.store = str(self.data / "grantgate-grantgate.json")
        self.env = mock.patch.dict(os.environ, {"ISSUING_WEBHOOK_SECRET": SECRET, "GRANTGATE_SIGNING_KEY": "k" * 32})
        self.env.start()
        for argv in (
            ("institution-add", "mybank", "--kind", "bank"),
            ("link", "ana", "mybank", "acct-001"),
            (
                "grant",
                "ana",
                "shopper-bot",
                "link-1",
                "--scopes",
                "bank:transfer",
                "--per-txn-cap",
                "100",
                "--total-cap",
                "150",
                "--payee",
                "Landlord LLC",
            ),
            ("card-attach", "grant-2", "--card", "ic_1TestCard"),
        ):
            self.assertEqual(self.cli(*argv)[0], 0, argv)
        self.srv = make_server("127.0.0.1", 0, self.data, "sk_x")
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/stripe/issuing"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.env.stop()
        self.tmp.cleanup()

    def cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = main([*argv, "--store", self.store])
        return code, out.getvalue()

    def post(self, event, signature=None):
        body = json.dumps(event).encode()
        req = urllib.request.Request(self.url, data=body, headers={"Stripe-Signature": signature or sign(body)})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, dict(r.headers), json.loads(r.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, dict(e.headers), e.read().decode()

    def test_the_grant_decides_each_payment(self):
        status, headers, res = self.post(auth_event(8000))
        self.assertEqual(status, 200)
        self.assertTrue(res["approved"])
        self.assertIn("Stripe-Version", headers)
        self.assertEqual(res["metadata"]["grant_id"], "grant-2")
        self.assertFalse(self.post(auth_event(12000))[2]["approved"])  # over the 100 per-payment cap
        _, _, res = self.post(auth_event(8000))  # 80 + 80 > 150 total
        self.assertFalse(res["approved"])
        self.assertIn("total cap", res["metadata"]["grantgate_reason"])
        self.assertFalse(self.post(auth_event(1000, merchant="Casino Royale"))[2]["approved"])
        self.assertFalse(self.post(auth_event(1000, currency="eur"))[2]["approved"])
        self.assertFalse(self.post(auth_event(1000, card="ic_1Unknown"))[2]["approved"])
        _, out = self.cli("audit", "grant-2")
        self.assertIn("card_payment", out)
        _, out = self.cli("dashboard", "ana")
        self.assertIn("$80.00", out)  # the approved payment counts towards the cap

    def test_revoke_and_detach_take_effect_immediately(self):
        self.cli("revoke", "grant-2")
        self.assertFalse(self.post(auth_event(1000))[2]["approved"])
        self.cli("card-detach", "--card", "ic_1TestCard")
        _, _, res = self.post(auth_event(1000))
        self.assertIn("not attached", res["metadata"]["grantgate_reason"])

    def test_signatures_and_other_events(self):
        event = auth_event(1000)
        self.assertEqual(self.post(event, signature=sign(b"other body"))[0], 400)
        self.assertEqual(self.post(event, signature=sign(json.dumps(event).encode(), secret="whsec_wrong"))[0], 400)
        old = sign(json.dumps(event).encode(), t=int(time.time()) - 3600)
        self.assertEqual(self.post(event, signature=old)[0], 400)
        status, _, res = self.post(auth_event(1000, kind="issuing_authorization.created"))
        self.assertEqual((status, res), (200, {"received": True}))
        with mock.patch.dict(os.environ, {"ISSUING_WEBHOOK_SECRET": ""}):
            self.assertEqual(self.post(event)[0], 503)
        with self.assertRaises(ValueError):
            verify(b"{}", "v1=abc", SECRET)

    def test_card_attach_validation(self):
        self.assertEqual(self.cli("card-attach", "grant-2", "--card", "card-123")[0], 1)
        self.assertEqual(self.cli("card-attach", "grant-9", "--card", "ic_1TestCard")[0], 1)
        self.assertEqual(self.cli("card-detach", "--card", "ic_1Nope99")[0], 1)


if __name__ == "__main__":
    unittest.main()

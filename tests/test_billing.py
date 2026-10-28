"""Billing shape: monthly checkout carries the 14-day trial; /pricing notes it."""

import http.client
import pathlib
import tempfile
import threading
import unittest
import urllib.request

from grantgate import web

web.Handler.log_message = lambda *args: None  # keep test output readable


class BillingTests(unittest.TestCase):
    def test_monthly_checkout_carries_14_day_trial_and_pricing_notes_it(self):
        env = {
            "STRIPE_SECRET_KEY": "sk_test_x",
            "STRIPE_WEBHOOK_SECRET": "whsec_test",
            "STRIPE_PRICES": "developer=price_1,growth=price_2,enterprise=price_3",
            "PUBLIC_URL": "https://example.test",
        }
        with tempfile.TemporaryDirectory() as t:
            srv = web.make_server("127.0.0.1", 0, pathlib.Path(t), env=env)
            billing = srv.RequestHandlerClass.billing
            sent = {}
            billing._stripe = lambda path, form: sent.update(form) or {"url": "https://checkout.test/s"}
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{srv.server_address[1]}"
            try:
                page = urllib.request.urlopen(base + "/pricing", timeout=30).read().decode()
                self.assertIn("14-day free trial", page)
                conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=30)
                conn.request("POST", "/billing/checkout", "plan=developer")
                res = conn.getresponse()
                self.assertEqual((res.status, res.getheader("Location")), (303, "https://checkout.test/s"))
                conn.close()
                self.assertEqual(sent["mode"], "subscription")
                self.assertEqual(sent["line_items[0][price]"], "price_1")
                self.assertEqual(sent["subscription_data[trial_period_days]"], "14")
            finally:
                srv.shutdown()
                srv.server_close()


if __name__ == "__main__":
    unittest.main()

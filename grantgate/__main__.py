import argparse
import csv
import os
import sys

from .core import AccessError, Hub, MockAirline, MockBank, MockStore


def demo():
    hub = Hub()
    bank = MockBank()
    bank.open("chk-001", 2500)
    hub.add_institution("First Bank", bank)
    hub.add_institution("TAP Air", MockAirline())
    hub.add_institution("ShopCo", MockStore())
    bank_link = hub.link("dana", "First Bank", "chk-001")
    air_link = hub.link("dana", "TAP Air", "FF123")
    bills = hub.grant(
        "dana",
        "bill-pay-agent",
        bank_link,
        ["bank:read", "bank:transfer"],
        per_txn_cap=200,
        total_cap=500,
        payees={"ConEd", "Verizon"},
    )
    travel = hub.grant("dana", "travel-agent", air_link, ["airline:read", "airline:book"], per_txn_cap=600)
    print("balance:", hub.call(bills, "balances"))
    print("pay ConEd:", hub.call(bills, "transfer", amount=120, payee="ConEd"))
    for desc, kwargs in [
        ("pay stranger", dict(amount=50, payee="Mallory")),
        ("big transfer", dict(amount=450, payee="Verizon")),
    ]:
        try:
            hub.call(bills, "transfer", **kwargs)
        except AccessError as e:
            print(f"{desc}: DENIED ({e})")
    flights = hub.call(travel, "search_flights", origin="JFK", dest="LIS")
    cheapest = min(flights, key=lambda f: f["price"])
    print("book:", hub.call(travel, "book", flight=cheapest["flight"], amount=cheapest["price"]))
    try:
        hub.call(travel, "balances")
    except AccessError as e:
        print(f"travel agent reads bank: DENIED ({e})")
    for row in hub.dashboard("dana"):
        print(row)


def _run(argv=None):
    ap = argparse.ArgumentParser(prog="grantgate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("demo")
    ap.parse_args(argv)
    demo()
    return 0


def main(argv=None):
    """Run the CLI; a bad input file or argument exits 2 with a one-line error instead of a traceback.

    Set AGENT_ECONOMY_DEBUG=1 to see the full traceback.
    """
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    try:
        return _run(argv)
    except (OSError, ValueError, KeyError, csv.Error) as e:
        if os.environ.get("AGENT_ECONOMY_DEBUG"):
            raise
        msg = e.args[0] if isinstance(e, KeyError) and e.args else e
        print(f"error: {msg}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

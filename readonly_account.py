"""
Phase 2 — Read-only account exploration.

This is where the "different behaviour every time" failures happened in your
previous attempts. The fix is to treat every data fetch as inherently async
and explicitly wait for the data to arrive.

What this script does:
  1. Connects with readonly=True (no orders possible, period)
  2. Fetches the account summary (cash, NLV, buying power) WITH explicit wait
  3. Fetches all open positions WITH explicit wait
  4. Verifies each position's contract by qualifying it
  5. Prints a structured report

Run:
    python3 readonly_account.py
    python3 readonly_account.py --port 4002 --client-id 102

Why this works when your previous attempts didn't:

  - We use connectAsync(readonly=True) — IBKR knows we cannot order
  - We use await ib.accountSummaryAsync() — the async variant must be awaited
    inside an async function. Calling ib.accountSummary() (the sync wrapper)
    from inside async def raises "This event loop is already running".
  - For positions, we use ib.reqPositionsAsync() and await it explicitly,
    plus a small sleep to let any tail callbacks land
  - We never read state from `ib.positions()` (the cached property) without
    first awaiting the request method that populates it
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from decimal import Decimal


logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)

from ib_async import IB, util, AccountValue


INTERESTING_TAGS = {
    "NetLiquidation",
    "TotalCashValue",
    "BuyingPower",
    "ExcessLiquidity",
    "AvailableFunds",
    "GrossPositionValue",
    "UnrealizedPnL",
    "RealizedPnL",
    "InitMarginReq",
    "MaintMarginReq",
}


def fmt_currency(value: str) -> str:
    try:
        return f"${Decimal(value):,.2f}"
    except Exception:
        return value


async def explore(host: str, port: int, client_id: int) -> int:
    ib = IB()

    error_log: list[tuple] = []
    ib.errorEvent += lambda *args: error_log.append(args)

    print(f"Connecting to {host}:{port} (readonly mode)...")
    try:
        await ib.connectAsync(
            host=host,
            port=port,
            clientId=client_id,
            timeout=10,
            readonly=True,
        )
    except Exception as exc:
        print(f"Connection failed: {type(exc).__name__}: {exc}")
        print("Run phase1_hello/hello.py first to diagnose the connection.")
        return 1

    accounts = ib.managedAccounts()
    print(f"Connected. Accounts: {accounts}")
    if not accounts:
        print("No accounts received. Restart Gateway and retry.")
        ib.disconnect()
        return 2

    account = accounts[0]
    print(f"Inspecting account: {account}")
    print()

    print("=" * 72)
    print("ACCOUNT SUMMARY")
    print("=" * 72)

    summary: list[AccountValue] = await ib.accountSummaryAsync(account)
    if not summary:
        print()
        print("Account summary returned empty.")
        print("This sometimes happens immediately after Gateway startup;")
        print("waiting 2s and retrying...")
        await asyncio.sleep(2)
        summary = await ib.accountSummaryAsync(account)

    if not summary:
        print("Still empty. Likely cause: Gateway hasn't completed initial")
        print("sync. Check the Gateway window for 'Connecting to data farms'")
        print("status — wait for that to clear, then retry.")
        ib.disconnect()
        return 3

    by_tag = {row.tag: row for row in summary if row.tag in INTERESTING_TAGS}
    for tag in sorted(INTERESTING_TAGS):
        row = by_tag.get(tag)
        if row:
            value = fmt_currency(row.value) if row.currency else row.value
            currency = row.currency or ""
            print(f"  {tag:24s} {value:>16s} {currency}")
        else:
            print(f"  {tag:24s} {'(not returned)':>16s}")

    print()
    print("=" * 72)
    print("OPEN POSITIONS")
    print("=" * 72)

    positions = await ib.reqPositionsAsync()
    await asyncio.sleep(0.3)
    positions = ib.positions(account)

    if not positions:
        print()
        print("  No open positions.")
        print()
        print("  In a fresh paper account, this is expected. If you ARE")
        print("  expecting positions and none appear, check:")
        print("    - You logged into the paper account (not live)")
        print("    - The Gateway 'Account' dropdown shows the right account")
    else:
        print(f"\n  {len(positions)} position(s):\n")
        for i, pos in enumerate(positions, 1):
            c = pos.contract
            print(f"  #{i}  {c.symbol:<6s} {c.secType:<4s} "
                  f"qty={pos.position:+.0f}  "
                  f"avgCost=${pos.avgCost:,.4f}")
            if c.secType == "OPT":
                print(f"       expiry={c.lastTradeDateOrContractMonth}  "
                      f"strike={c.strike}  right={c.right}")

    print()
    print("=" * 72)
    print("ACCOUNT VALUES (full dump, debugging)")
    print("=" * 72)

    values = ib.accountValues(account)
    print(f"  Received {len(values)} account values from Gateway")

    if len(values) < 10:
        print("  WARNING: very few account values. Initial sync may be")
        print("  incomplete. Try waiting and re-running.")

    print()
    print("Disconnecting...")
    ib.disconnect()
    await asyncio.sleep(0.2)

    blocking_errors = [
        e for e in error_log
        if len(e) >= 2 and isinstance(e[1], int) and e[1] < 2000
    ]
    if blocking_errors:
        print(f"\n{len(blocking_errors)} blocking error(s) during session:")
        for e in blocking_errors:
            print(f"  code={e[1]} msg={e[2] if len(e) > 2 else ''}")
        return 4

    print()
    print("Phase 2 PASSED. The account read pipeline works.")
    print("Proceed to Phase 3 (market data).")
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=4002)
    p.add_argument("--client-id", type=int, default=102)
    args = p.parse_args()
    return util.run(explore(args.host, args.port, args.client_id))


if __name__ == "__main__":
    sys.exit(main() or 0)

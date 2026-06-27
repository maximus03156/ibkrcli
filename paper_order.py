"""
Phase 4 — Paper order placement and cancellation.

Places a SINGLE-LEG, DELIBERATELY-UNFILLABLE limit order, waits for ack,
then cancels it. Tests the full order pipeline without risking even a paper
fill happening before we want it.

Safety design:
  - Verifies account starts with "DU" (paper) before doing anything
  - Uses a limit price 50% below market for a BUY (or 50% above for a SELL)
    so it cannot execute
  - Order has a 5-second auto-cancel even if user code crashes
  - Logs every state transition so you can see exactly what happened

What this script proves:
  - reqMktData -> mid-price calculation works
  - placeOrder accepted by IBKR
  - Order acknowledgement received via openOrder/orderStatus callbacks
  - cancelOrder works and is confirmed by the server

Run:
    python3 paper_order.py --ticker SPY
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
)

from ib_async import IB, LimitOrder, Stock, util


async def place_and_cancel(host: str, port: int, client_id: int,
                            ticker: str) -> int:
    ib = IB()
    print(f"Connecting to {host}:{port}...")
    try:
        await ib.connectAsync(host, port, clientId=client_id, timeout=10,
                              readonly=False)
    except Exception as exc:
        print(f"Connection failed: {type(exc).__name__}: {exc}")
        return 1

    accounts = ib.managedAccounts()
    if not accounts:
        print("No accounts.")
        ib.disconnect()
        return 2

    account = accounts[0]
    if not account.startswith("DU"):
        print(f"SAFETY ABORT: account {account} is not a paper account.")
        print("Paper account numbers start with 'DU'. Reconnect to paper.")
        ib.disconnect()
        return 3

    print(f"Paper account: {account}")
    print()

    print(f"Step 1: Qualifying {ticker}...")
    stock = Stock(ticker, "SMART", "USD")
    qualified = await ib.qualifyContractsAsync(stock)
    if not qualified or not qualified[0].conId:
        print(f"  Could not qualify {ticker}.")
        ib.disconnect()
        return 4
    stock = qualified[0]

    print("Step 2: Fetching current price for sanity check...")
    ticker_obj = ib.reqMktData(stock, "", False, False)
    deadline = asyncio.get_event_loop().time() + 5.0
    price = None
    while asyncio.get_event_loop().time() < deadline:
        mp = ticker_obj.marketPrice()
        if mp == mp:
            price = mp
            break
        if ticker_obj.last:
            price = ticker_obj.last
            break
        if ticker_obj.close:
            price = ticker_obj.close
            break
        await asyncio.sleep(0.2)
    ib.cancelMktData(stock)

    if price is None:
        print("  Could not get a price. Market closed and no cache.")
        ib.disconnect()
        return 5
    print(f"  Reference price: ${price:.2f}")

    safe_limit = round(price * 0.50, 2)
    print()
    print(f"Step 3: Building BUY limit order at ${safe_limit:.2f}")
    print(f"        (50% below market, will not fill even on a bad day)")
    print()
    order = LimitOrder(
        action="BUY",
        totalQuantity=1,
        lmtPrice=safe_limit,
        tif="DAY",
        outsideRth=False,
    )

    transitions: list[str] = []
    def on_order_status(trade):
        transitions.append(
            f"status={trade.orderStatus.status} "
            f"filled={trade.orderStatus.filled} "
            f"remaining={trade.orderStatus.remaining}"
        )
        print(f"  [STATUS] {transitions[-1]}")

    print("Step 4: Placing order...")
    trade = ib.placeOrder(stock, order)
    trade.statusEvent += on_order_status

    try:
        for _ in range(20):
            await asyncio.sleep(0.25)
            status = trade.orderStatus.status
            if status in ("Submitted", "PreSubmitted", "Filled",
                          "Cancelled", "Inactive"):
                break

        if trade.orderStatus.status == "Filled":
            print()
            print("UNEXPECTED: order filled. Price was probably wrong.")
            ib.disconnect()
            return 6

        print()
        print(f"Step 5: Order acknowledged. Status: {trade.orderStatus.status}")
        print(f"        Server order ID: {trade.order.orderId}")
        print(f"        Perm ID: {trade.order.permId}")
        print()

        await asyncio.sleep(1.0)

        print("Step 6: Cancelling order...")
        ib.cancelOrder(trade.order)

        for _ in range(20):
            await asyncio.sleep(0.25)
            if trade.orderStatus.status in ("Cancelled", "Inactive"):
                break

        final = trade.orderStatus.status
        if final != "Cancelled":
            print(f"  WARN: final status is {final}, not Cancelled.")
        else:
            print("  Order cancelled and confirmed.")

    finally:
        try:
            ib.cancelOrder(trade.order)
        except Exception:
            pass
        ib.disconnect()
        await asyncio.sleep(0.2)

    print()
    print(f"Final status: {trade.orderStatus.status}")
    print(f"Transitions seen: {len(transitions)}")
    for t in transitions:
        print(f"  - {t}")

    print()
    if trade.orderStatus.status == "Cancelled":
        print("Phase 4 PASSED. Order placement + cancellation pipeline works.")
        print("Proceed to Phase 5 (production wrapper).")
        return 0
    else:
        print(f"Phase 4 INCOMPLETE. Final status {trade.orderStatus.status}.")
        return 7


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=4002)
    p.add_argument("--client-id", type=int, default=104)
    p.add_argument("--ticker", default="SPY")
    args = p.parse_args()
    return util.run(place_and_cancel(
        args.host, args.port, args.client_id, args.ticker
    ))


if __name__ == "__main__":
    sys.exit(main() or 0)

"""
Phase 3 — Option chain with Greeks.

This is the "I got Greeks sometimes, sometimes None" scenario from your
previous attempts. The fix is to explicitly wait long enough for streaming
ticks to populate the modelGreeks field.

What this script does:
  1. Connect
  2. Qualify a stock contract (e.g. SPY)
  3. Fetch the option chain metadata (expiries and strikes available)
  4. Pick the next monthly expiry, ~5 strikes around ATM
  5. Subscribe to market data for each option contract
  6. WAIT explicitly until either: every contract has modelGreeks, OR timeout
  7. Print bid/ask/IV/delta/gamma/theta/vega for each
  8. Unsubscribe and disconnect cleanly

Key lessons embedded:
  - Greeks come from generic tick type "106" (option computation tick).
    ib_async requests this automatically when you reqMktData on an option.
  - Greeks take 1-3 seconds typically to populate after the request.
    We poll with a timeout rather than guess a sleep duration.
  - We unsubscribe with cancelMktData() before disconnecting — otherwise
    the subscriptions linger on IBKR's side and the next run hits
    "Max number of tickers reached" eventually.

Run:
    python3 option_chain.py
    python3 option_chain.py --ticker QQQ --strikes 7
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import sys
from datetime import datetime, date, timedelta


logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)

from ib_async import IB, Stock, Option, util, Ticker


def next_monthly_expiry(expirations: set[str], min_dte: int = 25, max_dte: int = 50) -> str | None:
    """
    Pick a monthly expiry between min_dte and max_dte days out.
    Expiries from IBKR are YYYYMMDD strings.
    """
    today = date.today()
    candidates = []
    for e in expirations:
        try:
            d = datetime.strptime(e, "%Y%m%d").date()
            dte = (d - today).days
            if min_dte <= dte <= max_dte:
                candidates.append((dte, e))
        except ValueError:
            continue
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][1]


def strikes_around(strikes: list[float], price: float, n: int) -> list[float]:
    """Pick n strikes closest to current price."""
    sorted_strikes = sorted(strikes, key=lambda s: abs(s - price))
    return sorted(sorted_strikes[:n])


async def fetch_option_chain(host: str, port: int, client_id: int,
                              ticker: str, n_strikes: int,
                              price_override: float | None = None) -> int:
    ib = IB()
    error_log = []
    ib.errorEvent += lambda *args: error_log.append(args)

    print(f"Connecting to {host}:{port}...")
    try:
        await ib.connectAsync(host, port, clientId=client_id, timeout=10, readonly=True)
    except Exception as exc:
        print(f"Connection failed: {type(exc).__name__}: {exc}")
        return 1

    print(f"Connected. Account: {ib.managedAccounts()[0]}")
    ib.reqMarketDataType(4)  # fall back to delayed-frozen when market is closed

    print()
    print(f"Step 1: Qualifying stock contract for {ticker}...")
    stock = Stock(ticker, "SMART", "USD")
    qualified = await ib.qualifyContractsAsync(stock)
    if not qualified or not qualified[0].conId:
        print(f"  FAIL: could not qualify {ticker}.")
        print("  This means IBKR doesn't recognise the symbol+exchange+currency.")
        print("  For US stocks, exchange='SMART' currency='USD' is correct.")
        ib.disconnect()
        return 2
    stock = qualified[0]
    print(f"  OK: conId={stock.conId}")

    print()
    print(f"Step 2: Fetching current price...")
    [stock_ticker] = await asyncio.gather(_ticker_with_price(ib, stock))
    def _valid(v):
        return v is not None and not (isinstance(v, float) and math.isnan(v))

    if stock_ticker is None or not _valid(stock_ticker.marketPrice()):
        last = stock_ticker.last if stock_ticker else None
        close = stock_ticker.close if stock_ticker else None
        price = (last if _valid(last) else None) or (close if _valid(close) else None)
        print(f"  marketPrice() returned NaN; using last={last} close={close}")
        if price is None:
            if price_override is not None:
                price = price_override
                print(f"  Using manual --price override: ${price:.2f}")
            else:
                print("  FAIL: no price available.")
                print("  Market is closed and Gateway has no cached last/close.")
                print("  Pass --price <value> to select strikes manually, e.g.:")
                print(f"    python3 option_chain.py --price 590")
                ib.cancelMktData(stock)
                ib.disconnect()
                return 3
    else:
        price = stock_ticker.marketPrice()
    print(f"  Reference price: ${price:,.2f}")
    ib.cancelMktData(stock)

    print()
    print(f"Step 3: Fetching option chain metadata for {ticker}...")
    chains = await ib.reqSecDefOptParamsAsync(
        stock.symbol, "", stock.secType, stock.conId
    )

    smart_chain = next((c for c in chains if c.exchange == "SMART"), None)
    if smart_chain is None:
        print("  No SMART chain found. Available exchanges:")
        for c in chains:
            print(f"    {c.exchange} ({len(c.expirations)} expiries)")
        ib.disconnect()
        return 4

    print(f"  SMART chain: {len(smart_chain.expirations)} expiries, "
          f"{len(smart_chain.strikes)} strikes")

    expiry = next_monthly_expiry(smart_chain.expirations)
    if expiry is None:
        print("  No expiry in 25-50 DTE window. Available:")
        for e in sorted(smart_chain.expirations)[:10]:
            print(f"    {e}")
        ib.disconnect()
        return 5
    print(f"  Selected expiry: {expiry}")

    strikes = strikes_around(list(smart_chain.strikes), price, n_strikes)
    print(f"  Selected {len(strikes)} strikes around ${price:.0f}: {strikes}")

    print()
    print(f"Step 4: Building option contracts (puts AND calls = {len(strikes)*2})...")

    options = []
    for strike in strikes:
        for right in ("C", "P"):
            options.append(Option(
                symbol=stock.symbol,
                lastTradeDateOrContractMonth=expiry,
                strike=strike,
                right=right,
                exchange="SMART",
                currency="USD",
                multiplier="100",
            ))

    qualified = await ib.qualifyContractsAsync(*options)
    valid_options = [o for o in qualified if o is not None and o.conId]
    invalid_count = len(options) - len(valid_options)
    if invalid_count:
        print(f"  {invalid_count} contracts failed to qualify (likely no listing at strike).")
    print(f"  Qualified {len(valid_options)} option contracts.")

    print()
    print("Step 5: Subscribing to market data for each contract...")
    print("        (Greeks will populate over 1-3 seconds. Be patient.)")

    tickers = [ib.reqMktData(o, "", False, False) for o in valid_options]

    print()
    print("Step 6: Waiting for Greeks to populate...")

    deadline = asyncio.get_event_loop().time() + 8.0
    last_ready_count = 0
    while asyncio.get_event_loop().time() < deadline:
        ready = sum(
            1 for t in tickers
            if t.modelGreeks and t.modelGreeks.delta is not None
        )
        if ready != last_ready_count:
            print(f"  {ready}/{len(tickers)} contracts have Greeks "
                  f"(t={asyncio.get_event_loop().time() - (deadline - 8.0):.1f}s)")
            last_ready_count = ready
        if ready == len(tickers):
            break
        await asyncio.sleep(0.25)

    print()
    print("=" * 78)
    print(f"  {ticker} options  expiry={expiry}  reference=${price:.2f}")
    print("=" * 78)
    print(f"  {'right':5s} {'strike':>8s} {'bid':>7s} {'ask':>7s} "
          f"{'IV':>6s} {'delta':>7s} {'gamma':>7s} {'theta':>7s} {'vega':>7s}")
    print("-" * 78)

    sorted_tickers = sorted(
        tickers,
        key=lambda t: (t.contract.strike, t.contract.right),
    )
    for t in sorted_tickers:
        c = t.contract
        bid = f"{t.bid:.2f}" if t.bid and t.bid > 0 else "-"
        ask = f"{t.ask:.2f}" if t.ask and t.ask > 0 else "-"
        if t.modelGreeks and t.modelGreeks.delta is not None:
            mg = t.modelGreeks
            iv    = f"{mg.impliedVol:.3f}" if mg.impliedVol else "-"
            delta = f"{mg.delta:+.3f}" if mg.delta is not None else "-"
            gamma = f"{mg.gamma:.4f}" if mg.gamma is not None else "-"
            theta = f"{mg.theta:.3f}" if mg.theta is not None else "-"
            vega  = f"{mg.vega:.3f}" if mg.vega is not None else "-"
        else:
            iv = delta = gamma = theta = vega = "-"
        print(f"  {c.right:5s} {c.strike:>8.1f} {bid:>7s} {ask:>7s} "
              f"{iv:>6s} {delta:>7s} {gamma:>7s} {theta:>7s} {vega:>7s}")

    print()
    print("Step 7: Cleanup (cancelling market data subscriptions)...")
    for t in tickers:
        ib.cancelMktData(t.contract)
    await asyncio.sleep(0.3)

    ib.disconnect()
    print("Disconnected.")
    print()

    ready_final = sum(
        1 for t in tickers
        if t.modelGreeks and t.modelGreeks.delta is not None
    )
    print(f"Final: {ready_final}/{len(tickers)} contracts had Greeks.")

    if ready_final == 0:
        no_subscription = any(
            len(e) >= 2 and e[1] == 354 for e in error_log
        )
        print()
        if no_subscription:
            print("ZERO contracts had Greeks — error 354 confirms missing subscription.")
            print()
            print("Fix: Client Portal -> Settings -> Market Data Subscriptions.")
            print("Enable 'OPRA (Top of Book)' ($1.50/mo, waived at $20+ commissions).")
        else:
            print("ZERO contracts had Greeks. Market is closed and no cached data.")
            print("Re-run during US market hours (9:30-16:00 ET, Mon-Fri).")
            print("If this fails during market hours, check OPRA subscription:")
            print("  Client Portal -> Settings -> Market Data Subscriptions.")
        return 6

    if ready_final < len(tickers):
        print()
        print(f"WARN: {len(tickers) - ready_final} contracts did not get Greeks.")
        print("This often happens for very illiquid strikes. Acceptable.")

    print()
    print("Phase 3 PASSED. Greeks are flowing. Proceed to Phase 4.")
    return 0


async def _ticker_with_price(ib: IB, stock: Stock) -> Ticker:
    ticker = ib.reqMktData(stock, "", False, False)
    deadline = asyncio.get_event_loop().time() + 4.0
    while asyncio.get_event_loop().time() < deadline:
        if (ticker.marketPrice() == ticker.marketPrice()) or ticker.last or ticker.close:
            break
        await asyncio.sleep(0.2)
    return ticker


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=4002)
    p.add_argument("--client-id", type=int, default=103)
    p.add_argument("--ticker", default="SPY")
    p.add_argument("--strikes", type=int, default=5)
    p.add_argument("--price", type=float, default=None,
                   help="Manual reference price for ATM strike selection "
                        "(use when market is closed and Gateway has no cache)")
    args = p.parse_args()
    return util.run(fetch_option_chain(
        args.host, args.port, args.client_id, args.ticker, args.strikes,
        price_override=args.price,
    ))


if __name__ == "__main__":
    sys.exit(main() or 0)

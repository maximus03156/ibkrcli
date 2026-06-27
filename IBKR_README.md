# IBKR client — phased build

This is the IBKR integration for the data. Built as five independent
phases that prove progressively more capability against your paper account.

The phases exist because IBKR integration fails for many small reasons, and
"my bot doesn't work" is impossible to debug. Each phase isolates one part
of the system. If phase 2 works and phase 3 doesn't, you know exactly where
to look.

## What you need before starting

- Mint Linux box on your home network
- IBKR account with paper trading credentials (separate username from live)
- Python 3.10 or newer
- ib_async installed: `pip install ib_async`

## The five phases

| Phase | File | What it proves | Time |
|---|---|---|---|
| 0 | `phase0_preflight/SETUP_GATEWAY.md` | Gateway is correctly configured | 20 min |
| 0 | `phase0_preflight/preflight.py` | Network + Python environment OK | 1 min |
| 1 | `phase1_hello/hello.py` | Gateway accepts API connection | 1 min |
| 2 | `phase2_readonly/readonly_account.py` | Account data flows correctly | 1 min |
| 3 | `phase3_marketdata/option_chain.py` | Option chains + Greeks work | 1 min |
| 4 | `phase4_orders/paper_order.py` | Order placement + cancel work | 2 min |
| 5 | `phase5_wrapper/ibkr_client.py` | Production wrapper, ready for integration | 1 min |

Do them in order. Each one builds on the last.

## Recommended execution sequence

### Sequence 1: prerequisites (do once)

Read `phase0_preflight/SETUP_GATEWAY.md` end to end. Every checkbox in that
document corresponds to a real failure mode. Don't skip steps.

After ticking all the checkboxes:

```bash
cd phase0_preflight
python3 preflight.py
```

Expected: "All checks passed. Proceed to Phase 1."

If it fails, the script will tell you exactly which prerequisite is wrong.
Fix and re-run before continuing.

### Sequence 2: hello world

```bash
cd phase1_hello
python3 hello.py
```

Expected ending: "Phase 1 PASSED. Proceed to Phase 2."

If it fails: the error message will distinguish the three common cases:
- Connection refused: Gateway not running on that port
- Timeout: Gateway running but API setting unchecked
- Unexpected error: paste the error here

### Sequence 3: read your account

```bash
cd phase2_readonly
python3 readonly_account.py
```

Expected: a structured report of your paper account's cash, NLV, buying
power, positions, and a count of account values received from Gateway.

If positions don't appear and you have positions in the paper account:
- Check Gateway is showing the paper account (not live)
- Restart Gateway and wait for "Connecting to data farms" to clear
- Re-run

This is the script that proves the "intermittent data" problem is fixed.
Run it 3-4 times in a row. You should get consistent results every time.

### Sequence 4: option chains with Greeks

```bash
cd phase3_marketdata
python3 option_chain.py --ticker SPY
```

Expected: a 10-row table of option contracts with bid, ask, IV, delta,
gamma, theta, vega. Run during US market hours for live quotes; outside
hours you'll see cached close prices.

If Greeks all show "-":
- Check Client Portal -> Market Data Subscriptions has OPRA enabled
- Verify your subscriber status is "non-professional"
- See the IBKR data subscription guide in our earlier conversation

### Sequence 5: paper order

```bash
cd phase4_orders
python3 paper_order.py --ticker SPY
```

Expected: places a BUY 1 share LIMIT order at 50% below market, waits for
acknowledgement, cancels it, confirms cancellation. The script SAFETY
ABORTS if the account is not paper.

If this works, the order pipeline is proven.

### Sequence 6: wrapper smoke test

```bash
cd phase5_wrapper
python3 ibkr_client.py --self-test
```

Expected: a brief summary of account NLV, cash, BP, and position count
using the production wrapper. This is the wrapper that strategy code and
FastAPI endpoints will import.

## Client IDs by phase

Each phase uses a distinct client ID so you can run multiple at once and
they won't collide:

| Phase | Default client ID |
|---|---|
| 1 hello | 101 |
| 2 readonly | 102 |
| 3 marketdata | 103 |
| 4 orders | 104 |
| 5 wrapper | 200 |

When the real bot runs, it will use client IDs 300-399. Stay clear of those
for your manual exploration.

## When things go wrong

The three symptoms you've hit before, and what to look at first:

### "Sometimes I get positions, sometimes I don't"

The script uses explicit awaits — but if it still happens, the issue is
Gateway hasn't completed its initial position sync when you connected. Add
a `time.sleep(3)` between connect and the first data request.

### "Sometimes Greeks are None"

Greeks take 1-3 seconds to populate from `reqMktData`. Phase 3 explicitly
polls for them. If you copy/paste the pattern into your own code, ALWAYS
wait. Never read `ticker.modelGreeks.delta` synchronously after the request.

### "The connection drops randomly"

Two possible causes:
1. Daily auto-restart kicked in (configurable in Gateway settings to a
   non-trading hour). Reconnect logic handles this.
2. Client ID collision with another script. Make sure every script that
   connects uses a unique client ID.

## What happens after phase 5

Once all 5 phases pass:

- The wrapper plugs into FastAPI as the broker layer
- The risk gate's `OrderProposal` gets converted to ib_async orders by
  this wrapper
- The account snapshot endpoint (which the risk gate consumes) reads from
  `client.account_snapshot()`
- Strategy bots use `client.qualify_option()` and
  `client.market_data_with_greeks()` to build candidates

Getting the five
phases green against your paper account is the goal.

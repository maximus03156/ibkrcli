# IB Gateway settings — the only checklist that matters

**Do not skip steps.** Every "intermittent" behaviour we discussed has a specific
cause in this list. Tick each one before proceeding to Phase 1.

We're using IB Gateway, not TWS. Gateway is lighter, headless-capable, and the
production target. TWS only matters if you want a UI to watch what your bot is doing,
and at that point we have better tools (the database, the post-close report).

---

## Step 1 — Install IB Gateway on Mint Linux

IBKR offers two builds: stable (recommended) and latest. Use **stable**.

```bash
# In your home directory
cd ~
wget https://download2.interactivebrokers.com/installers/ibgateway/stable-standalone/ibgateway-stable-standalone-linux-x64.sh
chmod +x ibgateway-stable-standalone-linux-x64.sh
./ibgateway-stable-standalone-linux-x64.sh -c
```

The `-c` flag runs the installer in console mode (works without X11 if you ever
need it). When prompted for install path, accept the default `~/Jts/ibgateway/stable`.

Gateway will create a launcher script at `~/Jts/ibgateway/stable/ibgateway`.

---

## Step 2 — First launch (paper account)

```bash
~/Jts/ibgateway/stable/ibgateway &
```

On the login screen:

- **Trading mode**: select **Paper Trading** (this is critical — live and paper
  use different ports and different account data)
- **User name**: your paper trading username (NOT your live username — paper
  has a separate username, usually with a suffix like `youraccount`)
- **Password**: your paper trading password

If you don't have paper account credentials, log into Client Portal at
`https://www.interactivebrokers.com.au/portal`, navigate to **Settings →
Account Settings → Paper Trading Account** and request access. The paper
account is free but provisioning takes a few hours.

After successful login, Gateway will show a small window with connection status.
Leave it running.

---

## Step 3 — The settings page (THE critical part)

In the Gateway window:

**Configure → Settings** (top menu)

### 3a. API → Settings

| Setting | Value | Why |
|---|---|---|
| Enable ActiveX and Socket Clients | ✅ checked | Without this, the API port is closed. This is reason #1 connections fail. |
| Read-Only API | ⬜ unchecked | We need to place orders eventually. Leave unchecked. |
| Socket port | `4002` | Paper Gateway default. **Verify this number.** Live uses 4001, TWS paper uses 7497. |
| Master API client ID | `0` | Leaving this at 0 means no client takes "master" privileges. Important for letting multiple clients connect cleanly. |
| Bypass Order Precautions for API Orders | ⬜ unchecked | Keeps IBKR's own safety checks. Don't disable. |
| Allow connections from localhost only | ⬜ unchecked (see 3b) | We may connect from another host; this is governed by Trusted IPs. |
| Create API message log file | ✅ checked | Critical for debugging. Logs go to `~/Jts/ibgateway/stable/logs/`. |
| Logging level | **Detail** | More verbose. We want everything when diagnosing failures. |

**Click Apply, then OK.**

### 3b. API → Precautions

| Setting | Value |
|---|---|
| Bypass Bond warning for API orders | ⬜ unchecked |
| Bypass negative yield to worst confirmation | ⬜ unchecked |
| Bypass Called Bond warning | ⬜ unchecked |
| Bypass "same action pair trade" warning | ⬜ unchecked |
| Bypass price-based volatility risk warning | ⬜ unchecked |
| Bypass US Stocks market data in shares warning | ✅ checked |
| Bypass Redirect Order warning | ⬜ unchecked |
| Bypass No Overfill protection precaution | ⬜ unchecked |

The only checked one in this list lets API quote requests use share-based
data without throwing a precaution dialog every time. Everything else stays
unchecked so dangerous orders get manual confirmation.

### 3c. Configuration → Lock and Exit

This is the section that nukes 90% of overnight bot operations:

| Setting | Value | Why |
|---|---|---|
| Auto restart | ✅ checked | Daily restart is *forced by IBKR* anyway. Better controlled. |
| Auto restart time | **23:55** | Just before midnight Sydney. We pick a time outside trading hours. **NOT during US market hours.** |
| Auto-logoff | ⬜ disable if possible | If your version forces it, leave it but configure logoff time outside US market hours. |

The forced daily restart is documented at
`https://www.ibkrguides.com/tws/usersguidebook/loginandsecurity/tws-auto-restart.htm`
and is non-negotiable from IBKR. The only choice is *when* during your day.

### 3d. Configuration → Memory Allocation

| Setting | Value |
|---|---|
| Memory cap | **4096 MB minimum** (8192 if your box has the RAM) |

The default is much lower and Gateway will crash silently under bulk data load
(e.g. requesting a full option chain). This is the gotcha that PyPI documentation
specifically warns about.

### 3e. Configuration → Display → Ticker Row

Only relevant if you ever use the TWS UI for monitoring. For Gateway, ignore.

---

## Step 4 — Verify the socket is actually open

After clicking OK on settings, exit Gateway entirely and restart it. Settings
sometimes don't take effect without a restart.

Then from your Mint Linux terminal:

```bash
# Should show LISTEN on port 4002
ss -tlnp | grep -E '4002|7497|4001|7496'
```

You should see something like:

```
LISTEN  0  50  127.0.0.1:4002  0.0.0.0:*  users:(("java",pid=12345,fd=128))
```

If you see nothing — Gateway has API disabled. Re-check 3a.

If you see `:::4002` instead of `127.0.0.1:4002` — Gateway is listening on all
interfaces (slightly less secure but works). Either is fine for local-only use.

---

## Step 5 — Outcome check

Before moving to Phase 1, confirm all of the following:

- [ ] Gateway shows "**Logged In: Paper Trading**" status indicator
- [ ] No red error indicators in the Gateway window
- [ ] `ss -tlnp | grep 4002` shows Java listening
- [ ] API log file exists at `~/Jts/ibgateway/stable/logs/`
- [ ] You know the **paper account number** — should look like `DU1234567` (D for paper, U for user)

The paper account number will be needed in Phase 2 when we verify we're talking
to the right account.

---

## Things that look like bugs but aren't

These are the trip-wires that produce the "different behaviour each time"
symptom. Knowing them in advance prevents 80% of phantom issues.

**Trip-wire 1: Multiple clients with the same client ID.**
Every Python script that connects must use a unique `clientId` parameter. If
two scripts try to connect with `clientId=1`, the second one *replaces* the
first, the first sees a disconnect, and any in-flight data is lost. We'll
assign different IDs per script.

**Trip-wire 2: Data takes time to arrive.**
`ib.positions()` returns the cached state. If you called it 50ms after
connecting, the cache hasn't been populated yet by the initial sync from
Gateway. `ib_async` provides `ib.connect()` which already waits for the sync,
but only if you `await` it correctly. We'll use explicit waits.

**Trip-wire 3: Greeks need a market data subscription.**
For options, `modelGreeks` is populated by `reqMktData` with generic ticks.
The data accumulates over time — typically 1-3 seconds. Reading immediately
gives `None`. We'll wait explicitly.

**Trip-wire 4: Market data requires data subscriptions.**
Some option chains require OPRA subscription (you have this — $1.50/month
waived at $20 commissions). Some indices require additional subs. If you see
"requested market data is not subscribed" errors, the answer is in Client
Portal under Market Data Subscriptions, not in your code.

**Trip-wire 5: Paper account doesn't have all real account data.**
Position values, P&L, and some Greeks behave slightly differently in paper.
This is by design. Verify behaviour in paper first, but don't assume real
will behave identically.

**Trip-wire 6: SMART routing vs exchange-specific.**
Most stocks: use `exchange='SMART'`, IBKR's smart router picks the venue.
Indices and futures: must specify a specific exchange (`CBOE`, `CME`, etc).
Get this wrong and contract qualification fails silently.

**Trip-wire 7: Async event loop already running (Jupyter / IDEs).**
If you run from a notebook or IDE that has its own event loop, `ib_async`
will deadlock. Use `util.startLoop()` in notebooks. Run from a plain Python
script in your terminal — we will.

---

## What's next

Once every checkbox above is ticked, move to **Phase 1**.

Phase 1 is the smallest possible Python script that confirms the connection
works. Roughly 40 lines. If Phase 1 fails, the diagnostics it prints will
tell you exactly which trip-wire above hit. If Phase 1 succeeds, the hardest
part is genuinely behind us.

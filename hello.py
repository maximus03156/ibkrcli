"""
Phase 1 — Hello, Gateway.

The smallest possible IBKR script. Connects, reads server time, disconnects.

What this proves:
  - Gateway accepts API connections at the configured port
  - The API handshake completes (version match, account list received)
  - The async event loop runs without deadlocking

What this does NOT prove:
  - Account data access (that's Phase 2)
  - Market data (Phase 3)
  - Order placement (Phase 4)

Run:
    python3 hello.py
or:
    python3 hello.py --port 4002

If this fails, the error output below pinpoints the failure mode.

Trip-wires deliberately surfaced:
  - Wrong port: clear connection refused message
  - Client ID collision: explicit error 326
  - API disabled in Gateway: connection accepted but no handshake response
  - Read-only API mismatch: harmless here, becomes relevant in Phase 4
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone


# Always import after logging is configured so ib_async's own logger respects it.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)

from ib_async import IB, util


async def hello(host: str, port: int, client_id: int, timeout: float) -> int:
    ib = IB()

    print()
    print(f"Connecting to {host}:{port} with clientId={client_id} "
          f"(timeout={timeout}s)...")
    print()

    error_log: list[tuple[int, int, str, object]] = []

    def on_error(reqId, errorCode, errorString, contract):
        error_log.append((reqId, errorCode, errorString, contract))
        if errorCode < 2000:
            print(f"  [IBKR ERROR] code={errorCode} reqId={reqId} "
                  f"msg={errorString}")
        elif errorCode < 3000:
            print(f"  [IBKR WARN ] code={errorCode} {errorString}")
        else:
            print(f"  [IBKR INFO ] code={errorCode} {errorString}")

    ib.errorEvent += on_error

    try:
        await ib.connectAsync(
            host=host,
            port=port,
            clientId=client_id,
            timeout=timeout,
            readonly=False,
        )
    except asyncio.TimeoutError:
        print()
        print("CONNECTION TIMED OUT.")
        print()
        print("Most likely causes:")
        print("  1. Gateway is running but 'Enable ActiveX and Socket Clients'")
        print("     is unchecked in Configure->Settings->API.")
        print("  2. Wrong port. Paper Gateway is 4002, paper TWS is 7497.")
        print("  3. Gateway is showing a popup dialog that blocks API.")
        print("     Click any pending dialog in the Gateway window.")
        return 2
    except ConnectionRefusedError:
        print()
        print("CONNECTION REFUSED.")
        print("Nothing is listening on that port. Either Gateway is not")
        print("running, or it is running but the socket port differs.")
        print()
        print("Run the preflight script to detect which port is active:")
        print("  python3 ../phase0_preflight/preflight.py")
        return 3
    except Exception as exc:
        print()
        print(f"UNEXPECTED ERROR: {type(exc).__name__}: {exc}")
        print()
        if hasattr(exc, "__cause__") and exc.__cause__:
            print(f"Caused by: {type(exc.__cause__).__name__}: {exc.__cause__}")
        return 4

    print("Connection: ESTABLISHED")
    print()

    print(f"  Server version  : {ib.client.serverVersion()}")
    print(f"  Connection stats: {ib.client.connectionStats()}")
    print(f"  Account list    : {ib.managedAccounts()}")
    print()

    accounts = ib.managedAccounts()
    if not accounts:
        print("WARNING: No accounts returned. This usually means Gateway")
        print("is not fully logged in. Check the Gateway window for prompts.")
        ib.disconnect()
        return 5

    first_account = accounts[0]
    if first_account.startswith("DU"):
        print(f"  Mode            : PAPER (account {first_account})")
    elif first_account.startswith("U"):
        print(f"  Mode            : LIVE (account {first_account}) <-- caution")
    else:
        print(f"  Mode            : unknown prefix on {first_account}")
    print()

    try:
        server_time = await asyncio.wait_for(ib.reqCurrentTimeAsync(), timeout=5)
        local_now = datetime.now(timezone.utc)
        drift = (local_now - server_time).total_seconds()
        print(f"  Server time     : {server_time.isoformat()}")
        print(f"  Local time UTC  : {local_now.isoformat()}")
        print(f"  Clock drift     : {drift:+.2f}s")
        if abs(drift) > 60:
            print("  WARNING: clock drift > 60s. Sync your system clock.")
    except asyncio.TimeoutError:
        print("  WARNING: reqCurrentTime timed out after 5s.")
        print("  This is unusual after a successful handshake. The server is")
        print("  not responding to follow-up requests; restart Gateway.")
    except Exception as exc:
        print(f"  reqCurrentTime failed: {type(exc).__name__}: {exc}")

    print()
    print("Disconnecting cleanly...")
    ib.disconnect()
    await asyncio.sleep(0.1)
    print("Disconnected.")
    print()

    if error_log:
        non_info = [e for e in error_log if e[1] < 3000 and e[1] not in (2104, 2106, 2158)]
        if non_info:
            print(f"Note: {len(non_info)} non-info messages during session.")
        else:
            print(f"All {len(error_log)} server messages were informational.")
    else:
        print("No server messages — clean session.")

    print()
    print("Phase 1 PASSED. Proceed to Phase 2.")
    return 0


def parse_args():
    p = argparse.ArgumentParser(description="IBKR Phase 1 hello script")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument(
        "--port",
        type=int,
        default=4002,
        help="4002=paper Gateway (default), 7497=paper TWS, "
             "4001=live Gateway, 7496=live TWS",
    )
    p.add_argument("--client-id", type=int, default=101)
    p.add_argument("--timeout", type=float, default=10.0)
    return p.parse_args()


def main():
    args = parse_args()
    return util.run(hello(args.host, args.port, args.client_id, args.timeout))


if __name__ == "__main__":
    sys.exit(main() or 0)

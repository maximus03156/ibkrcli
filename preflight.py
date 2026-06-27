"""
Preflight check — Phase 0.

Runs zero IBKR API calls. Verifies only the network and Python environment.

If this script fails, the problem is NOT in your application code — it's in
the Gateway setup or your network. Each check has an explicit "what to fix"
instruction.

Run:
    python3 preflight.py
"""

from __future__ import annotations

import socket
import struct
import subprocess
import sys
from dataclasses import dataclass


# IBKR-defined port conventions
PORTS = {
    7497: ("TWS",      "paper"),
    7496: ("TWS",      "live"),
    4002: ("Gateway",  "paper"),
    4001: ("Gateway",  "live"),
}


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    fix: str = ""


def check_python_version() -> CheckResult:
    major, minor = sys.version_info[:2]
    ok = (major, minor) >= (3, 10)
    return CheckResult(
        name="Python version",
        ok=ok,
        detail=f"Python {major}.{minor}",
        fix="ib_async needs Python 3.10+. Install via apt or pyenv.",
    )


def check_ib_async_installed() -> CheckResult:
    try:
        import ib_async  # noqa: F401
        return CheckResult(
            name="ib_async installed",
            ok=True,
            detail=f"version {ib_async.__version__}",
        )
    except ImportError:
        return CheckResult(
            name="ib_async installed",
            ok=False,
            detail="not installed",
            fix="pip install ib_async",
        )


def check_port_listening(port: int) -> CheckResult:
    app, mode = PORTS[port]
    label = f"{app} {mode} (port {port})"
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1.0)
    try:
        sock.connect(("127.0.0.1", port))
        sock.close()
        return CheckResult(
            name=f"Port {port} ({app} {mode})",
            ok=True,
            detail=f"{label} is listening on 127.0.0.1",
        )
    except (ConnectionRefusedError, socket.timeout):
        return CheckResult(
            name=f"Port {port} ({app} {mode})",
            ok=False,
            detail=f"nothing listening on 127.0.0.1:{port}",
            fix=(
                f"If you intend to use {label}: start it, log in, and "
                f"ensure Configure->Settings->API has 'Enable ActiveX and "
                f"Socket Clients' checked and Socket port = {port}."
            ),
        )
    finally:
        sock.close()


def check_can_handshake(port: int) -> CheckResult:
    """
    The IBKR API has a tiny handshake: client sends 'API\\0' followed by a
    framed version string. If the server is reachable but not in API mode,
    the socket connects but we never get a response.

    We don't actually do the full handshake here (that's ib_async's job).
    We only check the TCP connection is acceptable.
    """
    app, mode = PORTS[port]
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2.0) as sock:
            sock.settimeout(0.5)
            try:
                data = sock.recv(64)
                detail = f"server pre-sent {len(data)} bytes (unexpected)"
            except socket.timeout:
                detail = "TCP connect succeeded, server quiet (expected)"
            return CheckResult(
                name=f"TCP handshake on {port}",
                ok=True,
                detail=detail,
            )
    except OSError as exc:
        return CheckResult(
            name=f"TCP handshake on {port}",
            ok=False,
            detail=str(exc),
            fix="Gateway is not listening. See port check above.",
        )


def detect_active_ports() -> list[int]:
    """Look at `ss` output for listening sockets on IBKR ports."""
    active = []
    try:
        out = subprocess.check_output(
            ["ss", "-tln"], stderr=subprocess.DEVNULL, timeout=2
        ).decode()
        for port in PORTS:
            if f":{port}" in out:
                active.append(port)
    except (subprocess.SubprocessError, FileNotFoundError):
        pass
    return active


def main() -> int:
    print("=" * 64)
    print("IBKR preflight check")
    print("=" * 64)

    results: list[CheckResult] = [
        check_python_version(),
        check_ib_async_installed(),
    ]

    active_ports = detect_active_ports()
    if not active_ports:
        print()
        print("No IBKR-style ports are listening on this machine.")
        print("Checking each conventional port anyway...")
        print()
        for port in PORTS:
            results.append(check_port_listening(port))
    else:
        print(f"\nDetected listening on: {active_ports}\n")
        for port in active_ports:
            results.append(check_port_listening(port))
            results.append(check_can_handshake(port))

    fail_count = 0
    for r in results:
        symbol = "OK  " if r.ok else "FAIL"
        print(f"  [{symbol}] {r.name:40s} {r.detail}")
        if not r.ok:
            fail_count += 1
            if r.fix:
                print(f"         -> {r.fix}")

    print()
    print("=" * 64)
    if fail_count == 0:
        print("All checks passed. Proceed to Phase 1.")
        print("If the paper Gateway port was detected, use that in Phase 1.")
        return 0
    elif fail_count == len(results):
        print("Everything failed. Most likely: Gateway is not running")
        print("at all. Start it and re-run this script.")
        return 1
    else:
        print(f"{fail_count} check(s) failed. Fix them and re-run.")
        return 1


if __name__ == "__main__":
    sys.exit(main())

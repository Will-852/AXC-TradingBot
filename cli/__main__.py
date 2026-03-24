"""
AXC CLI — Unified entry point for backtests and tools.

Usage:
    python3 -m cli backtest squeeze --coin BTC --days 360
    python3 -m cli backtest burst --coin XRP --days 180
    python3 -m cli backtest main --coin BTC --days 360
"""

import argparse
import os
import sys

# Ensure project root and scripts/ are in path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_SCRIPTS = os.path.join(_ROOT, "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)


def main():
    parser = argparse.ArgumentParser(prog="axc", description="AXC Trading CLI")
    sub = parser.add_subparsers(dest="command")

    # ─── backtest ───
    bt = sub.add_parser("backtest", help="Run backtests")
    bt_sub = bt.add_subparsers(dest="strategy")

    # backtest squeeze
    sq = bt_sub.add_parser("squeeze", help="Squeeze-explosion strategy backtest")
    sq.add_argument("--coin", required=True, help="BTC, ETH, SOL, XRP, POL")
    sq.add_argument("--days", type=int, default=360)
    sq.add_argument("--margin", type=float, default=None)
    sq.add_argument("--save", action="store_true")

    # backtest main (existing engine)
    mn = bt_sub.add_parser("main", help="Main backtest engine (range/trend/crash)")
    mn.add_argument("--coin", default="BTC")
    mn.add_argument("--days", type=int, default=360)
    mn.add_argument("args", nargs=argparse.REMAINDER)

    args = parser.parse_args()

    if args.command == "backtest":
        if args.strategy == "squeeze":
            # Delegate to bt_squeeze.py
            sys.argv = [
                "bt_squeeze.py",
                "--coin", args.coin,
                "--days", str(args.days),
            ]
            if args.margin:
                sys.argv.extend(["--margin", str(args.margin)])
            if args.save:
                sys.argv.append("--save")
            from backtest.bt_squeeze import main as squeeze_main
            squeeze_main()
        elif args.strategy == "main":
            # Delegate to existing run_backtest.py
            sys.argv = ["run_backtest.py"] + args.args
            from backtest.run_backtest import main as bt_main
            bt_main()
        else:
            bt.print_help()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

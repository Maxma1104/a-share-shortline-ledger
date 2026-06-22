#!/usr/bin/env python3
"""Refresh market data and print a pre-analysis freshness report."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
import sqlite3


SOURCE = "RSSCAST"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the required data refresh before stock analysis."
    )
    parser.add_argument("--db", default="stock_tracking.db", help="Path to SQLite database.")
    parser.add_argument(
        "--skip-sync",
        action="store_true",
        help="Only check current database freshness; do not fetch quotes or refresh watchlist.",
    )
    parser.add_argument(
        "--skip-watchlist-refresh",
        action="store_true",
        help="Skip watchlist activity refresh.",
    )
    parser.add_argument(
        "--prune-removal-days",
        type=int,
        default=0,
        help=(
            "Pass through to refresh_watchlist.py. A value above 0 hard-removes "
            "non-holding removal candidates after the grace period."
        ),
    )
    return parser.parse_args()


def run_sync(db_path: Path) -> None:
    script = Path(__file__).with_name("sync_rsscast_prices.py")
    result = subprocess.run(
        [sys.executable, str(script), "--db", str(db_path)],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.stdout:
        print(result.stdout.strip())
    if result.returncode != 0:
        if result.stderr:
            print(result.stderr.strip(), file=sys.stderr)
        raise SystemExit(result.returncode)


def run_watchlist_refresh(db_path: Path, prune_removal_days: int) -> None:
    script = Path(__file__).with_name("refresh_watchlist.py")
    command = [sys.executable, str(script), "--db", str(db_path)]
    if prune_removal_days > 0:
        command.extend(["--prune-removal-days", str(prune_removal_days)])
    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.stdout:
        print(result.stdout.strip())
    if result.returncode != 0:
        if result.stderr:
            print(result.stderr.strip(), file=sys.stderr)
        raise SystemExit(result.returncode)


def fetch_rows(db_path: Path) -> tuple[list[sqlite3.Row], list[sqlite3.Row], list[sqlite3.Row]]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        target_rows = list(
            conn.execute(
                """
                SELECT id, code, name, is_holding, is_watchlisted
                FROM stocks
                WHERE is_holding = 1 OR is_watchlisted = 1
                ORDER BY is_holding DESC, is_watchlisted DESC, code
                """
            )
        )
        latest_rows = list(
            conn.execute(
                """
                WITH latest AS (
                  SELECT stock_id, MAX(id) AS latest_id
                  FROM price_snapshots
                  WHERE source = ?
                  GROUP BY stock_id
                )
                SELECT
                  st.code,
                  st.name,
                  ps.snapshot_at,
                  ps.price,
                  ps.change_pct,
                  ps.volume_amount,
                  ps.source
                FROM latest l
                JOIN price_snapshots ps ON ps.id = l.latest_id
                JOIN stocks st ON st.id = ps.stock_id
                WHERE st.is_holding = 1 OR st.is_watchlisted = 1
                ORDER BY st.code
                """,
                (SOURCE,),
            )
        )
        holding_rows = list(
            conn.execute(
                """
                SELECT
                  st.code,
                  st.name,
                  h.quantity,
                  h.available_quantity,
                  h.cost_price,
                  h.last_price,
                  h.unrealized_return_pct,
                  h.stop_loss,
                  h.updated_on
                FROM holdings h
                JOIN stocks st ON st.id = h.stock_id
                ORDER BY h.position_pct DESC
                """
            )
        )
    return target_rows, latest_rows, holding_rows


def print_report(target_rows: list[sqlite3.Row], latest_rows: list[sqlite3.Row], holding_rows: list[sqlite3.Row]) -> int:
    target_codes = {row["code"] for row in target_rows}
    latest_by_code = {row["code"]: row for row in latest_rows}
    missing = sorted(target_codes - set(latest_by_code))

    print("\nPre-analysis data check")
    print(f"- Target stocks: {len(target_rows)}")
    print(f"- Latest {SOURCE} snapshots: {len(latest_rows)}")
    if latest_rows:
        latest_time = max(row["snapshot_at"] for row in latest_rows)
        print(f"- Latest market timestamp: {latest_time}")
    print(f"- Missing snapshots: {', '.join(missing) if missing else 'none'}")

    print("\nLatest quotes")
    for row in latest_rows:
        change = "" if row["change_pct"] is None else f" {row['change_pct']:.2f}%"
        print(f"{row['code']} {row['name']}: {row['price']}{change} @ {row['snapshot_at']}")

    print("\nHoldings after refresh")
    for row in holding_rows:
        pnl = "" if row["unrealized_return_pct"] is None else f"{row['unrealized_return_pct']:.2f}%"
        stop = "" if row["stop_loss"] is None else f" stop={row['stop_loss']}"
        print(
            f"{row['code']} {row['name']}: last={row['last_price']} cost={row['cost_price']} pnl={pnl}{stop}"
        )

    if missing:
        print("\nStatus: BLOCKED - some target stocks have no refreshed snapshot.")
        return 1

    print("\nStatus: OK - use this refreshed database for analysis.")
    print("Data source: RSSCAST, https://app-cn.rsscast.io")
    return 0


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 1

    if not args.skip_sync:
        run_sync(db_path)

    if not args.skip_sync and not args.skip_watchlist_refresh:
        run_watchlist_refresh(db_path, args.prune_removal_days)

    target_rows, latest_rows, holding_rows = fetch_rows(db_path)
    return print_report(target_rows, latest_rows, holding_rows)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Check whether public watchlist data is fresh enough to publish."""

from __future__ import annotations

import argparse
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


SOURCE = "RSSCAST"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify latest market snapshot date equals today's China date."
    )
    parser.add_argument("--db", default="stock_tracking.db", help="Path to SQLite database.")
    parser.add_argument("--source", default=SOURCE, help="Market data source.")
    parser.add_argument(
        "--github-output",
        help="Optional GitHub Actions output file. Writes fresh/latest_market_date/today_cn.",
    )
    return parser.parse_args()


def snapshot_date(value: str | None) -> str:
    if not value:
        return ""
    match = re.search(r"\d{4}-\d{2}-\d{2}", value)
    return match.group(0) if match else ""


def write_github_output(path: str | None, values: dict[str, str]) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT MAX(ps.snapshot_at)
            FROM price_snapshots ps
            JOIN stocks st ON st.id = ps.stock_id
            WHERE ps.source = ?
              AND (st.is_watchlisted = 1 OR st.is_holding = 1)
            """,
            (args.source,),
        ).fetchone()

    latest_timestamp = row[0] if row else ""
    latest_market_date = snapshot_date(latest_timestamp)
    today_cn = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    fresh = latest_market_date == today_cn

    print("Publication freshness check")
    print(f"- Today China date: {today_cn}")
    print(f"- Latest market timestamp: {latest_timestamp or '-'}")
    print(f"- Fresh: {'true' if fresh else 'false'}")

    write_github_output(
        args.github_output,
        {
            "fresh": "true" if fresh else "false",
            "latest_market_date": latest_market_date,
            "today_cn": today_cn,
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

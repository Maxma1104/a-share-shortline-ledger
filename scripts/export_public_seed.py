#!/usr/bin/env python3
"""Create an anonymized public seed SQL file from the local private database."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from export_public_watchlist import SOURCE, sanitize_text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export a public SQLite seed with watchlist data only."
    )
    parser.add_argument("--db", default="stock_tracking.db", help="Private source database.")
    parser.add_argument("--output", default="db/public_seed.sql", help="Output SQL path.")
    return parser.parse_args()


def sql(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).replace("'", "''")
    return f"'{text}'"


def fetch_rows(db_path: Path) -> dict[str, list[sqlite3.Row]]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        markets = list(
            conn.execute(
                """
                SELECT DISTINCT m.name, m.country, m.trade_status, m.note
                FROM watchlist w
                JOIN stocks st ON st.id = w.stock_id
                JOIN markets m ON m.id = st.market_id
                WHERE st.is_watchlisted = 1
                  AND st.is_holding = 0
                ORDER BY m.name
                """
            )
        )
        sectors = list(
            conn.execute(
                """
                SELECT DISTINCT se.name, se.focus_reason, se.catalysts, se.risks, se.status, se.updated_on
                FROM watchlist w
                JOIN stocks st ON st.id = w.stock_id
                LEFT JOIN sectors se ON se.id = st.sector_id
                WHERE se.id IS NOT NULL
                  AND st.is_watchlisted = 1
                  AND st.is_holding = 0
                ORDER BY se.name
                """
            )
        )
        stocks = list(
            conn.execute(
                """
                SELECT st.code, m.name AS market_name, st.name, st.board, se.name AS sector_name, st.note
                FROM watchlist w
                JOIN stocks st ON st.id = w.stock_id
                JOIN markets m ON m.id = st.market_id
                LEFT JOIN sectors se ON se.id = st.sector_id
                WHERE st.is_watchlisted = 1
                  AND st.is_holding = 0
                ORDER BY st.code
                """
            )
        )
        watchlist = list(
            conn.execute(
                """
                SELECT
                  st.code,
                  m.name AS market_name,
                  w.focus_reason,
                  w.key_levels_text,
                  w.invalid_condition,
                  w.status,
                  w.added_on,
                  w.updated_on
                FROM watchlist w
                JOIN stocks st ON st.id = w.stock_id
                JOIN markets m ON m.id = st.market_id
                WHERE st.is_watchlisted = 1
                  AND st.is_holding = 0
                ORDER BY st.code
                """
            )
        )
        snapshots = list(
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
                  m.name AS market_name,
                  ps.snapshot_at,
                  ps.price,
                  ps.change_pct,
                  ps.volume_amount,
                  ps.source,
                  ps.note
                FROM latest l
                JOIN price_snapshots ps ON ps.id = l.latest_id
                JOIN stocks st ON st.id = ps.stock_id
                JOIN markets m ON m.id = st.market_id
                JOIN watchlist w ON w.stock_id = st.id
                WHERE st.is_watchlisted = 1
                  AND st.is_holding = 0
                ORDER BY st.code
                """,
                (SOURCE,),
            )
        )
    return {
        "markets": markets,
        "sectors": sectors,
        "stocks": stocks,
        "watchlist": watchlist,
        "snapshots": snapshots,
    }


def write_seed(rows: dict[str, list[sqlite3.Row]], output: Path) -> None:
    lines = [
        "PRAGMA foreign_keys = ON;",
        "",
        "-- Public seed generated from a private ledger.",
        "-- It contains watchlist data only. Holdings, cost basis, position size,",
        "-- available shares, allocation, and private review logs are excluded.",
        "",
    ]

    for row in rows["markets"]:
        lines.append(
            "INSERT INTO markets (name, country, trade_status, note) VALUES "
            f"({sql(row['name'])}, {sql(row['country'])}, {sql('公开观察')}, {sql(sanitize_text(row['note']))}) "
            "ON CONFLICT(name) DO UPDATE SET country=excluded.country, trade_status=excluded.trade_status, note=excluded.note;"
        )

    lines.append("")
    for row in rows["sectors"]:
        lines.append(
            "INSERT INTO sectors (name, focus_reason, catalysts, risks, status, updated_on) VALUES "
            f"({sql(row['name'])}, {sql(sanitize_text(row['focus_reason']))}, {sql(sanitize_text(row['catalysts']))}, "
            f"{sql(sanitize_text(row['risks']))}, {sql(sanitize_text(row['status']) or '观察')}, {sql(row['updated_on'])}) "
            "ON CONFLICT(name) DO UPDATE SET focus_reason=excluded.focus_reason, catalysts=excluded.catalysts, "
            "risks=excluded.risks, status=excluded.status, updated_on=excluded.updated_on;"
        )

    lines.append("")
    for row in rows["stocks"]:
        lines.append(
            "INSERT INTO stocks (code, market_id, name, board, sector_id, is_watchlisted, is_holding, note) "
            f"SELECT {sql(row['code'])}, m.id, {sql(row['name'])}, {sql(row['board'])}, se.id, 1, 0, {sql('公开关注池')} "
            f"FROM markets m LEFT JOIN sectors se ON se.name = {sql(row['sector_name'])} "
            f"WHERE m.name = {sql(row['market_name'])} "
            "ON CONFLICT(code, market_id) DO UPDATE SET sector_id=excluded.sector_id, "
            "is_watchlisted=1, is_holding=0, name=excluded.name, board=excluded.board, note=excluded.note;"
        )

    lines.append("")
    for row in rows["watchlist"]:
        lines.append(
            "INSERT INTO watchlist (stock_id, focus_reason, key_levels_text, invalid_condition, status, added_on, updated_on) "
            f"SELECT st.id, {sql(sanitize_text(row['focus_reason']))}, {sql(sanitize_text(row['key_levels_text']))}, "
            f"{sql(sanitize_text(row['invalid_condition']))}, {sql(sanitize_text(row['status']))}, {sql(row['added_on'])}, {sql(row['updated_on'])} "
            "FROM stocks st JOIN markets m ON m.id = st.market_id "
            f"WHERE st.code = {sql(row['code'])} AND m.name = {sql(row['market_name'])} "
            "ON CONFLICT(stock_id) DO UPDATE SET focus_reason=excluded.focus_reason, "
            "key_levels_text=excluded.key_levels_text, invalid_condition=excluded.invalid_condition, "
            "status=excluded.status, updated_on=excluded.updated_on;"
        )

    lines.append("")
    for row in rows["snapshots"]:
        note = row["note"]
        if note:
            try:
                parsed = json.loads(note)
                note = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
            except json.JSONDecodeError:
                note = None
        lines.append(
            "INSERT INTO price_snapshots (stock_id, snapshot_at, price, change_pct, volume_amount, source, note) "
            f"SELECT st.id, {sql(row['snapshot_at'])}, {sql(row['price'])}, {sql(row['change_pct'])}, "
            f"{sql(row['volume_amount'])}, {sql(row['source'])}, {sql(note)} "
            "FROM stocks st JOIN markets m ON m.id = st.market_id "
            f"WHERE st.code = {sql(row['code'])} AND m.name = {sql(row['market_name'])} "
            "ON CONFLICT(stock_id, snapshot_at, source) DO UPDATE SET price=excluded.price, "
            "change_pct=excluded.change_pct, volume_amount=excluded.volume_amount, note=excluded.note;"
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    rows = fetch_rows(Path(args.db))
    write_seed(rows, Path(args.output))
    print(f"Exported public seed: {args.output}")
    print(f"Watchlist rows: {len(rows['watchlist'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Sync A-share quotes from the RssCast MCP server into stock_tracking.db."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from env_loader import load_project_env


MCP_URL = "https://app-cn.rsscast.io/api/mcp/v1/mcp"
SOURCE = "RSSCAST"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch RssCast MCP real-time A-share quotes and update the local SQLite ledger."
    )
    parser.add_argument(
        "--db",
        default="stock_tracking.db",
        help="Path to SQLite database. Default: stock_tracking.db",
    )
    parser.add_argument(
        "--codes",
        nargs="+",
        help="Optional stock codes to sync. Default: all watchlisted or holding stocks.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print fetched quotes without writing to the database.",
    )
    return parser.parse_args()


def load_token() -> str:
    load_project_env()
    token = os.environ.get("RSSCAST_MCP_TOKEN", "").strip()
    if not token:
        raise SystemExit(
            "Missing RSSCAST_MCP_TOKEN. Set it in your shell or macOS launch environment first."
        )
    return token


def call_mcp_tool(token: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }
    request = urllib.request.Request(
        MCP_URL,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-03-26",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"RssCast MCP HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Cannot reach RssCast MCP: {exc}") from exc

    data_line = None
    for line in body.splitlines():
        if line.startswith("data:"):
            data_line = line.removeprefix("data:").strip()

    if not data_line:
        raise RuntimeError(f"Unexpected MCP response: {body[:500]}")

    message = json.loads(data_line)
    if "error" in message:
        raise RuntimeError(f"MCP error: {message['error']}")
    return message["result"]


def extract_quote_rows(tool_result: dict[str, Any]) -> list[dict[str, Any]]:
    content = tool_result.get("content") or []
    if not content:
        return []

    text = content[0].get("text", "")
    match = re.search(r"(\[\s*\{.*\}\s*\])", text, flags=re.S)
    if not match:
        raise RuntimeError(f"Cannot find quote JSON array in MCP tool result: {text[:500]}")
    return json.loads(match.group(1))


def get_target_stocks(db_path: Path, codes: list[str] | None) -> list[sqlite3.Row]:
    query = """
        SELECT id, code, name, is_holding
        FROM stocks
        WHERE is_watchlisted = 1 OR is_holding = 1
        ORDER BY is_holding DESC, is_watchlisted DESC, code
    """
    params: tuple[Any, ...] = ()
    if codes:
        placeholders = ",".join("?" for _ in codes)
        query = f"""
            SELECT id, code, name, is_holding
            FROM stocks
            WHERE code IN ({placeholders})
            ORDER BY is_holding DESC, is_watchlisted DESC, code
        """
        params = tuple(codes)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return list(conn.execute(query, params))


def pct_from_rsscast(value: Any) -> float | None:
    if value is None:
        return None
    return float(value) * 100


def sync_quotes(db_path: Path, stocks: list[sqlite3.Row], quotes: list[dict[str, Any]], dry_run: bool) -> None:
    stock_by_code = {row["code"]: row for row in stocks}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows_to_write = []

    for quote in quotes:
        code = str(quote.get("code", "")).zfill(6)
        stock = stock_by_code.get(code)
        if not stock:
            continue

        price = quote.get("last_price")
        if price is None:
            continue

        rows_to_write.append(
            {
                "stock_id": stock["id"],
                "code": code,
                "name": stock["name"],
                "snapshot_at": quote.get("timeString") or now,
                "price": float(price),
                "change_pct": pct_from_rsscast(quote.get("change_pct")),
                "amount": quote.get("amount"),
                "note": json.dumps(
                    {
                        "pre_close": quote.get("prev_close") or quote.get("pre_close"),
                        "open": quote.get("open"),
                        "high": quote.get("high"),
                        "low": quote.get("low"),
                        "volume": quote.get("volume"),
                        "amplitude_pct": pct_from_rsscast(quote.get("amplitude")),
                        "turnover_rate": quote.get("turnover_rate"),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            }
        )

    if dry_run:
        for row in rows_to_write:
            change = "" if row["change_pct"] is None else f" {row['change_pct']:.2f}%"
            print(f"{row['code']} {row['name']}: {row['price']}{change} @ {row['snapshot_at']}")
        print(f"Dry run: {len(rows_to_write)} quote(s) fetched; database not modified.")
        return

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        for row in rows_to_write:
            conn.execute(
                """
                INSERT INTO price_snapshots
                  (stock_id, snapshot_at, price, change_pct, volume_amount, source, note)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(stock_id, snapshot_at, source) DO UPDATE SET
                  price = excluded.price,
                  change_pct = excluded.change_pct,
                  volume_amount = excluded.volume_amount,
                  note = excluded.note
                """,
                (
                    row["stock_id"],
                    row["snapshot_at"],
                    row["price"],
                    row["change_pct"],
                    row["amount"],
                    SOURCE,
                    row["note"],
                ),
            )
            conn.execute(
                """
                UPDATE holdings
                SET
                  last_price = ?,
                  unrealized_return_pct = ROUND(((? - cost_price) / cost_price) * 100, 2),
                  updated_on = date('now')
                WHERE stock_id = ?
                """,
                (row["price"], row["price"], row["stock_id"]),
            )

        conn.execute(
            """
            INSERT INTO update_logs (log_date, content, source)
            VALUES (date('now'), ?, ?)
            """,
            (f"Synced {len(rows_to_write)} A-share quote snapshots from RssCast MCP.", SOURCE),
        )
        conn.commit()

    print(f"Synced {len(rows_to_write)} quote(s) into {db_path}.")
    print("Data source: RSSCAST, https://app-cn.rsscast.io")


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 1

    stocks = get_target_stocks(db_path, args.codes)
    if not stocks:
        print("No matching watchlisted or holding stocks found.", file=sys.stderr)
        return 1

    token = load_token()
    codes = [row["code"] for row in stocks]
    result = call_mcp_tool(token, "StockPriceQuery", {"codes": codes})
    quotes = extract_quote_rows(result)
    sync_quotes(db_path, stocks, quotes, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

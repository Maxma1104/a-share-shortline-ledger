#!/usr/bin/env python3
"""Refresh watchlist status so inactive stocks do not dilute attention."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from env_loader import load_project_env


MCP_URL = "https://app-cn.rsscast.io/api/mcp/v1/mcp"
SOURCE = "RSSCAST"
REMOVE_STATUS = "剔除候选"
LOW_FREQ_STATUS = "低频观察"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate watchlisted stocks and mark inactive names for removal."
    )
    parser.add_argument("--db", default="stock_tracking.db", help="Path to SQLite database.")
    parser.add_argument("--lookback-days", type=int, default=14, help="Calendar days for K-line lookup.")
    parser.add_argument("--dry-run", action="store_true", help="Print decisions without updating watchlist.")
    parser.add_argument(
        "--prune-removal-days",
        type=int,
        default=0,
        help=(
            "Hard-remove non-holding stocks that remain removal candidates for this many "
            "calendar days. Default 0 keeps the current status-only behavior."
        ),
    )
    return parser.parse_args()


def load_token() -> str:
    load_project_env()
    token = os.environ.get("RSSCAST_MCP_TOKEN", "").strip()
    if not token:
        raise SystemExit("Missing RSSCAST_MCP_TOKEN. Refresh RssCast credentials before analysis.")
    return token


def call_mcp_tool(token: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
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
        with urllib.request.urlopen(request, timeout=25) as response:
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


def extract_array(tool_result: dict[str, Any]) -> list[dict[str, Any]]:
    content = tool_result.get("content") or []
    if not content:
        return []

    text = content[0].get("text", "")
    match = re.search(r"(\[\s*\{.*\}\s*\])", text, flags=re.S)
    if not match:
        return []
    return json.loads(match.group(1))


def parse_snapshot_date(value: str | None) -> date:
    if not value:
        return date.today()
    match = re.search(r"\d{4}-\d{2}-\d{2}", value)
    if not match:
        return date.today()
    return datetime.strptime(match.group(0), "%Y-%m-%d").date()


def parse_bar_date(value: str | None) -> date:
    if not value:
        return date.min
    match = re.search(r"\d{4}-\d{2}-\d{2}", value)
    if not match:
        return date.min
    return datetime.strptime(match.group(0), "%Y-%m-%d").date()


def amount_yi(value: Any) -> float:
    if value is None:
        return 0.0
    return float(value) / 100_000_000


def pct(value: float) -> float:
    return value * 100


def key_break_price(text: str | None) -> float | None:
    if not text:
        return None
    match = re.search(r"跌破\s*([0-9]+(?:\.[0-9]+)?)", text)
    if match:
        return float(match.group(1))
    numbers = [float(item) for item in re.findall(r"[0-9]+(?:\.[0-9]+)?", text)]
    if not numbers:
        return None
    return min(numbers)


def load_watchlist(db_path: Path) -> list[sqlite3.Row]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return list(
            conn.execute(
                """
                WITH latest AS (
                  SELECT stock_id, MAX(id) AS latest_id
                  FROM price_snapshots
                  WHERE source = ?
                  GROUP BY stock_id
                )
                SELECT
                  st.id AS stock_id,
                  st.code,
                  st.name,
                  st.is_holding,
                  w.status,
                  w.focus_reason,
                  w.key_levels_text,
                  w.invalid_condition,
                  w.updated_on,
                  ps.snapshot_at,
                  ps.price,
                  ps.change_pct,
                  ps.volume_amount,
                  ps.note
                FROM watchlist w
                JOIN stocks st ON st.id = w.stock_id
                LEFT JOIN latest l ON l.stock_id = st.id
                LEFT JOIN price_snapshots ps ON ps.id = l.latest_id
                WHERE st.is_watchlisted = 1
                ORDER BY st.code
                """,
                (SOURCE,),
            )
        )


def group_bars(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        code = str(row.get("code", "")).zfill(6)
        grouped.setdefault(code, []).append(row)

    for code, bars in grouped.items():
        grouped[code] = sorted(bars, key=lambda item: parse_bar_date(item.get("timeString")))
    return grouped


def close_value(row: dict[str, Any]) -> float:
    if row.get("close") is not None:
        return float(row["close"])
    if row.get("last_price") is not None:
        return float(row["last_price"])
    return 0.0


def row_amplitude(row: dict[str, Any]) -> float:
    close = close_value(row)
    high = float(row.get("high") or 0)
    low = float(row.get("low") or 0)
    if close <= 0 or high <= 0 or low <= 0:
        return 0.0
    return ((high - low) / close) * 100


def row_change_pct(rows: list[dict[str, Any]], idx: int) -> float:
    current_close = close_value(rows[idx])
    if current_close <= 0 or idx == 0:
        return 0.0
    prev_close = close_value(rows[idx - 1])
    if prev_close <= 0:
        return 0.0
    return ((current_close - prev_close) / prev_close) * 100


def evaluate_stock(stock: sqlite3.Row, bars: list[dict[str, Any]], latest_date: date) -> dict[str, Any]:
    latest_price = float(stock["price"] or 0)
    latest_amount_yi = amount_yi(stock["volume_amount"])
    latest_change_pct = float(stock["change_pct"] or 0)
    latest_amplitude_pct = 0.0

    if stock["note"]:
        try:
            note = json.loads(stock["note"])
            latest_amplitude_pct = float(note.get("amplitude_pct") or 0)
        except json.JSONDecodeError:
            latest_amplitude_pct = 0.0

    recent = bars[-5:]
    last3 = bars[-3:]
    amounts = [amount_yi(row.get("amount")) for row in recent if row.get("amount") is not None]
    avg_amount_5 = sum(amounts) / len(amounts) if amounts else latest_amount_yi

    flat_days = 0
    for idx, row in enumerate(bars[-3:], start=max(len(bars) - 3, 0)):
        day_amount = amount_yi(row.get("amount"))
        day_amplitude = row_amplitude(row)
        day_change = abs(row_change_pct(bars, idx))
        if day_amount < 20 and day_amplitude < 2.5 and day_change < 1.5:
            flat_days += 1

    break_price = key_break_price(stock["invalid_condition"]) or key_break_price(stock["key_levels_text"])
    broke_invalid = break_price is not None and latest_price > 0 and latest_price < break_price
    amount_shrinking = bool(avg_amount_5 and latest_amount_yi < avg_amount_5 * 0.65 and latest_amount_yi < 30)
    low_liquidity = latest_amount_yi < 15
    no_money_sensitivity = flat_days >= 3
    heavy_fade = latest_change_pct <= -4 and latest_amplitude_pct >= 4 and latest_amount_yi >= max(avg_amount_5, 1) * 0.9
    updated_date = parse_bar_date(stock["updated_on"])
    stale_status = updated_date != date.min and (latest_date - updated_date).days >= 5

    score = 0
    if latest_amount_yi >= 50:
        score += 30
    elif latest_amount_yi >= 20:
        score += 20
    elif latest_amount_yi >= 10:
        score += 10

    if avg_amount_5 and latest_amount_yi >= avg_amount_5 * 1.2:
        score += 20
    elif avg_amount_5 and latest_amount_yi >= avg_amount_5 * 0.8:
        score += 10

    if latest_amplitude_pct >= 4:
        score += 20
    elif latest_amplitude_pct >= 2.5:
        score += 10

    if abs(latest_change_pct) >= 3:
        score += 20
    elif abs(latest_change_pct) >= 1.5:
        score += 10

    reasons = []
    if broke_invalid:
        reasons.append(f"跌破失效位{break_price:g}")
    if no_money_sensitivity:
        reasons.append("连续3日缩量窄幅，资金敏感度下降")
    if low_liquidity:
        reasons.append(f"最新成交额仅{latest_amount_yi:.1f}亿")
    if amount_shrinking:
        reasons.append(f"成交额低于5日均额65%且不足30亿")
    if heavy_fade:
        reasons.append("放量下跌/冲高回落，短线强度失效")
    if stale_status:
        reasons.append("关注状态过旧，需重新确认")

    if broke_invalid or heavy_fade or no_money_sensitivity or (low_liquidity and amount_shrinking):
        decision = REMOVE_STATUS
    elif score >= 65:
        decision = "重点关注"
    elif score >= 45:
        decision = "观察"
    elif score < 35 or low_liquidity or amount_shrinking or stale_status:
        decision = LOW_FREQ_STATUS
    else:
        decision = "观察"

    if stock["is_holding"]:
        decision = "持仓风控"
        reasons.append("持仓股不从风险管理池剔除")

    if not reasons:
        reasons.append("成交额/波动仍满足盘中观察")

    return {
        "code": stock["code"],
        "name": stock["name"],
        "old_status": stock["status"],
        "updated_on": stock["updated_on"],
        "decision": decision,
        "score": score,
        "price": latest_price,
        "change_pct": latest_change_pct,
        "amount_yi": latest_amount_yi,
        "avg_amount_5": avg_amount_5,
        "amplitude_pct": latest_amplitude_pct,
        "snapshot_at": stock["snapshot_at"],
        "reason": "；".join(reasons),
    }


def apply_decisions(
    db_path: Path,
    decisions: list[dict[str, Any]],
    status_date: date,
    prune_removal_days: int,
) -> None:
    today = status_date.isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        for item in decisions:
            status = f"{today}{item['decision']}"
            old_status = item.get("old_status") or ""
            old_date = parse_bar_date(item.get("updated_on"))
            should_prune = (
                prune_removal_days > 0
                and item["decision"] == REMOVE_STATUS
                and REMOVE_STATUS in old_status
                and old_date != date.min
                and (status_date - old_date).days >= prune_removal_days
            )

            if should_prune:
                conn.execute(
                    """
                    UPDATE stocks
                    SET is_watchlisted = 0, updated_at = datetime('now')
                    WHERE code = ? AND is_holding = 0
                    """,
                    (item["code"],),
                )
                item["pruned"] = True

            conn.execute(
                """
                UPDATE watchlist
                SET status = ?, updated_on = ?
                WHERE stock_id = (SELECT id FROM stocks WHERE code = ?)
                """,
                (status, today, item["code"]),
            )
        remove_count = sum(1 for item in decisions if item["decision"] == REMOVE_STATUS)
        low_freq_count = sum(1 for item in decisions if item["decision"] == LOW_FREQ_STATUS)
        conn.execute(
            """
            INSERT INTO update_logs (log_date, content, source)
            VALUES (?, ?, ?)
            """,
            (
                today,
                (
                    "Refreshed watchlist activity: "
                    f"{remove_count} removal candidate(s), "
                    f"{low_freq_count} low-frequency candidate(s), "
                    f"{sum(1 for item in decisions if item.get('pruned'))} pruned."
                ),
                SOURCE,
            ),
        )
        conn.commit()


def print_report(decisions: list[dict[str, Any]], dry_run: bool) -> int:
    print("\nWatchlist refresh")
    print("- Rule: remove stale names that lose money sensitivity; keep holdings in risk-control scope.")
    print(f"- Mode: {'dry-run' if dry_run else 'applied'}")

    for item in decisions:
        print(
            f"{item['code']} {item['name']}: {item['decision']} "
            f"score={item['score']} price={item['price']} "
            f"chg={item['change_pct']:.2f}% amount={item['amount_yi']:.1f}亿 "
            f"avg5={item['avg_amount_5']:.1f}亿 @ {item['snapshot_at']} | "
            f"{item['reason']}{'；已从关注池硬剔除' if item.get('pruned') else ''}"
        )

    blocked = [item for item in decisions if item["snapshot_at"] is None]
    if blocked:
        print("\nStatus: BLOCKED - watchlist has stocks without fresh snapshots.")
        return 1

    print("\nStatus: OK - watchlist activity refreshed.")
    print("Data source: RSSCAST, https://app-cn.rsscast.io")
    return 0


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 1

    watchlist = load_watchlist(db_path)
    if not watchlist:
        print("No watchlisted stocks found.")
        return 0

    missing = [row["code"] for row in watchlist if row["snapshot_at"] is None]
    if missing:
        print(f"Missing latest RSSCAST snapshots for watchlist: {', '.join(missing)}", file=sys.stderr)
        return 1

    latest_date = max(parse_snapshot_date(row["snapshot_at"]) for row in watchlist)
    start_date = latest_date - timedelta(days=args.lookback_days)
    token = load_token()
    codes = [row["code"] for row in watchlist]
    result = call_mcp_tool(
        token,
        "StockKLineQuery",
        {
            "codes": codes,
            "startDate": start_date.isoformat(),
            "endDate": latest_date.isoformat(),
        },
    )
    bars_by_code = group_bars(extract_array(result))

    decisions = [evaluate_stock(row, bars_by_code.get(row["code"], []), latest_date) for row in watchlist]
    if not args.dry_run:
        apply_decisions(db_path, decisions, latest_date, args.prune_removal_days)
    return print_report(decisions, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())

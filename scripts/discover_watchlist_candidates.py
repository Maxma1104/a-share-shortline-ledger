#!/usr/bin/env python3
"""Promote new public watchlist candidates from a configured stock universe."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from env_loader import load_project_env


MCP_URL = "https://app-cn.rsscast.io/api/mcp/v1/mcp"
SOURCE = "RSSCAST"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find and promote new shortline watchlist candidates."
    )
    parser.add_argument("--db", default="stock_tracking.db", help="Path to SQLite database.")
    parser.add_argument(
        "--config",
        default="config/watchlist_candidates.json",
        help="Candidate universe and threshold config.",
    )
    parser.add_argument("--lookback-days", type=int, default=14, help="K-line lookback window.")
    parser.add_argument("--max-add", type=int, help="Override max additions per run.")
    parser.add_argument("--max-active", type=int, help="Override max active watchlist size.")
    parser.add_argument("--dry-run", action="store_true", help="Print candidates without writing.")
    return parser.parse_args()


def load_token() -> str:
    load_project_env()
    token = os.environ.get("RSSCAST_MCP_TOKEN", "").strip()
    if not token:
        raise SystemExit("Missing RSSCAST_MCP_TOKEN. Cannot discover new candidates.")
    return token


def load_config(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rules = data.get("rules") or {}
    universe = data.get("universe") or []
    if not isinstance(universe, list) or not universe:
        raise SystemExit(f"Candidate universe is empty: {path}")

    seen: set[str] = set()
    candidates = []
    required = {"code", "name", "market", "board", "sector", "theme"}
    for item in universe:
        missing = sorted(required - set(item))
        if missing:
            raise SystemExit(f"Candidate config missing {missing}: {item}")
        code = str(item["code"]).zfill(6)
        if code in seen:
            continue
        seen.add(code)
        normalized = dict(item)
        normalized["code"] = code
        candidates.append(normalized)
    return rules, candidates


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
        with urllib.request.urlopen(request, timeout=30) as response:
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


def parse_date(value: str | None) -> datetime.date:
    if not value:
        return datetime.now(ZoneInfo("Asia/Shanghai")).date()
    match = re.search(r"\d{4}-\d{2}-\d{2}", value)
    if not match:
        return datetime.now(ZoneInfo("Asia/Shanghai")).date()
    return datetime.strptime(match.group(0), "%Y-%m-%d").date()


def amount_yi(value: Any) -> float:
    if value is None:
        return 0.0
    return float(value) / 100_000_000


def pct_from_rsscast(value: Any) -> float:
    if value is None:
        return 0.0
    return float(value) * 100


def close_value(row: dict[str, Any]) -> float:
    if row.get("close") is not None:
        return float(row["close"])
    if row.get("last_price") is not None:
        return float(row["last_price"])
    return 0.0


def bar_date(row: dict[str, Any]) -> datetime.date:
    return parse_date(row.get("timeString"))


def group_bars(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        code = str(row.get("code", "")).zfill(6)
        grouped.setdefault(code, []).append(row)
    for code, bars in grouped.items():
        grouped[code] = sorted(bars, key=bar_date)
    return grouped


def fmt_price(value: float) -> str:
    text = f"{value:.2f}"
    return text.rstrip("0").rstrip(".")


def latest_quote_snapshot_at(quote: dict[str, Any]) -> str:
    value = quote.get("timeString")
    if value:
        return str(value)
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def ensure_reference_data(conn: sqlite3.Connection, candidates: list[dict[str, Any]]) -> None:
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    for item in candidates:
        conn.execute(
            """
            INSERT INTO markets (name, country, trade_status, note)
            VALUES (?, '中国', '公开观察', '自动候选宇宙')
            ON CONFLICT(name) DO NOTHING
            """,
            (item["market"],),
        )
        conn.execute(
            """
            INSERT INTO sectors (name, focus_reason, catalysts, risks, status, updated_on)
            VALUES (?, ?, ?, ?, '观察中', ?)
            ON CONFLICT(name) DO NOTHING
            """,
            (
                item["sector"],
                f"自动候选宇宙：{item['theme']}",
                item["theme"],
                "自动筛选仅看资金强度，仍需盘中确认承接；追高和同向暴露是主要风险",
                today,
            ),
        )
        conn.execute(
            """
            INSERT INTO stocks (code, market_id, name, board, sector_id, is_watchlisted, is_holding, note)
            SELECT ?, m.id, ?, ?, se.id, 0, 0, '自动候选宇宙'
            FROM markets m
            LEFT JOIN sectors se ON se.name = ?
            WHERE m.name = ?
            ON CONFLICT(code, market_id) DO UPDATE SET
              name = excluded.name,
              board = excluded.board,
              sector_id = excluded.sector_id,
              note = CASE
                WHEN stocks.is_watchlisted = 1 OR stocks.is_holding = 1 THEN stocks.note
                ELSE excluded.note
              END
            """,
            (item["code"], item["name"], item["board"], item["sector"], item["market"]),
        )


def load_stock_state(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT
          st.id,
          st.code,
          st.name,
          st.is_watchlisted,
          st.is_holding,
          w.status
        FROM stocks st
        LEFT JOIN watchlist w ON w.stock_id = st.id
        """
    )
    return {row["code"]: row for row in rows}


def sync_candidate_quotes(
    conn: sqlite3.Connection,
    state_by_code: dict[str, sqlite3.Row],
    quotes: list[dict[str, Any]],
) -> None:
    for quote in quotes:
        code = str(quote.get("code", "")).zfill(6)
        state = state_by_code.get(code)
        if not state or quote.get("last_price") is None:
            continue
        note = json.dumps(
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
        )
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
                state["id"],
                latest_quote_snapshot_at(quote),
                float(quote["last_price"]),
                pct_from_rsscast(quote.get("change_pct")),
                quote.get("amount"),
                SOURCE,
                note,
            ),
        )


def score_candidate(
    item: dict[str, Any],
    quote: dict[str, Any] | None,
    bars: list[dict[str, Any]],
    state: sqlite3.Row | None,
    rules: dict[str, Any],
) -> dict[str, Any]:
    result = {
        "code": item["code"],
        "name": item["name"],
        "theme": item["theme"],
        "score": 0,
        "eligible": False,
        "reason": "",
        "status": "观察",
        "price": 0.0,
        "change_pct": 0.0,
        "amount_yi": 0.0,
        "avg_amount_5": 0.0,
        "amplitude_pct": 0.0,
        "key_levels": "",
        "invalid_condition": "",
        "focus_reason": "",
    }

    if state and state["is_holding"]:
        result["reason"] = "已有持仓，不作为新开候选"
        return result
    if state and state["is_watchlisted"]:
        result["reason"] = "已在关注池，不重复加入"
        return result
    if not quote or quote.get("last_price") is None:
        result["reason"] = "缺少最新行情"
        return result
    if "ST" in item["name"].upper():
        result["reason"] = "ST/风险警示标的排除"
        return result

    price = float(quote["last_price"])
    change_pct = pct_from_rsscast(quote.get("change_pct"))
    amount = amount_yi(quote.get("amount"))
    amplitude = pct_from_rsscast(quote.get("amplitude"))
    high = float(quote.get("high") or price)
    low = float(quote.get("low") or price)
    recent = bars[-5:]
    amounts = [amount_yi(row.get("amount")) for row in recent if row.get("amount") is not None]
    avg_amount_5 = sum(amounts) / len(amounts) if amounts else amount

    day_range = max(high - low, 0.0)
    upper_shadow_ratio = ((high - price) / day_range) if day_range > 0 else 0.0
    close_position_ratio = ((price - low) / day_range) if day_range > 0 else 1.0
    previous_highs = [float(row.get("high") or 0) for row in bars[:-1][-10:]]
    previous_high = max(previous_highs) if previous_highs else 0.0

    rejects = []
    if amount < float(rules.get("min_amount_yi", 20)):
        rejects.append(f"成交额不足{float(rules.get('min_amount_yi', 20)):g}亿")
    if avg_amount_5 < float(rules.get("min_avg_amount_yi", 15)):
        rejects.append(f"5日均额不足{float(rules.get('min_avg_amount_yi', 15)):g}亿")
    if amplitude < float(rules.get("min_amplitude_pct", 3)) and abs(change_pct) < float(
        rules.get("min_abs_change_pct", 2)
    ):
        rejects.append("波动/涨跌不足，资金敏感度不够")
    if change_pct <= -2:
        rejects.append("当日收跌过深，不新增弱势票")
    if (
        upper_shadow_ratio > float(rules.get("max_upper_shadow_ratio", 0.65))
        and close_position_ratio < float(rules.get("min_close_position_ratio", 0.45))
        and amount >= max(avg_amount_5, 1)
    ):
        rejects.append("放量上影，冲高回落风险高")

    score = 0
    if amount >= 100:
        score += 30
    elif amount >= 50:
        score += 25
    elif amount >= 20:
        score += 15

    if avg_amount_5 and amount >= avg_amount_5 * 1.5:
        score += 25
    elif avg_amount_5 and amount >= avg_amount_5 * 1.2:
        score += 15
    elif avg_amount_5 and amount >= avg_amount_5 * 0.9:
        score += 5

    if amplitude >= 6:
        score += 15
    elif amplitude >= 4:
        score += 10
    elif amplitude >= 3:
        score += 5

    if change_pct >= 9.5:
        score += 20
    elif change_pct >= 5:
        score += 15
    elif change_pct >= 2:
        score += 10
    elif change_pct >= 0:
        score += 3

    if previous_high and price >= previous_high * 0.995:
        score += 15
    if close_position_ratio >= 0.7:
        score += 10
    elif close_position_ratio < float(rules.get("min_close_position_ratio", 0.45)):
        score -= 10

    score = max(0, min(score, 100))
    result.update(
        {
            "score": score,
            "price": price,
            "change_pct": change_pct,
            "amount_yi": amount,
            "avg_amount_5": avg_amount_5,
            "amplitude_pct": amplitude,
        }
    )

    if rejects:
        result["reason"] = "；".join(rejects)
        return result

    min_score = int(rules.get("min_score", 65))
    if score < min_score:
        result["reason"] = f"评分{score}低于入池线{min_score}"
        return result

    support = low if low > 0 else price * 0.97
    resistance = high if high > 0 else price
    result["eligible"] = True
    result["status"] = "重点关注" if score >= int(rules.get("high_focus_score", 80)) else "观察"
    result["key_levels"] = "、".join(
        dict.fromkeys([fmt_price(support), fmt_price(price), fmt_price(resistance)])
    )
    result["invalid_condition"] = (
        f"跌破{fmt_price(support)}且不能收回，或放量冲高回落跌破"
        f"{fmt_price(price)}，视为短线强度失效"
    )
    result["focus_reason"] = (
        f"自动候选：{item['theme']}；成交额{amount:.1f}亿，5日均额{avg_amount_5:.1f}亿，"
        f"涨跌幅{change_pct:.2f}%，振幅{amplitude:.2f}%，评分{score}"
    )
    result["reason"] = "满足自动入池规则"
    return result


def active_watchlist_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM watchlist w
        JOIN stocks st ON st.id = w.stock_id
        WHERE st.is_watchlisted = 1
          AND st.is_holding = 0
          AND w.status NOT LIKE '%低频观察%'
          AND w.status NOT LIKE '%剔除候选%'
        """
    ).fetchone()
    return int(row[0] if row else 0)


def promote(
    conn: sqlite3.Connection,
    selected: list[dict[str, Any]],
    state_by_code: dict[str, sqlite3.Row],
    dry_run: bool,
) -> None:
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    if dry_run:
        return

    for item in selected:
        state = state_by_code[item["code"]]
        status = f"{today}{item['status']}"
        conn.execute(
            "UPDATE stocks SET is_watchlisted = 1, updated_at = datetime('now') WHERE id = ?",
            (state["id"],),
        )
        conn.execute(
            """
            INSERT INTO watchlist
              (stock_id, focus_reason, key_levels_text, invalid_condition, status, added_on, updated_on)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(stock_id) DO UPDATE SET
              focus_reason = excluded.focus_reason,
              key_levels_text = excluded.key_levels_text,
              invalid_condition = excluded.invalid_condition,
              status = excluded.status,
              added_on = excluded.added_on,
              updated_on = excluded.updated_on
            """,
            (
                state["id"],
                item["focus_reason"],
                item["key_levels"],
                item["invalid_condition"],
                status,
                today,
                today,
            ),
        )
    conn.execute(
        """
        INSERT INTO update_logs (log_date, content, source)
        VALUES (?, ?, ?)
        """,
        (today, f"Discovered and promoted {len(selected)} new watchlist candidate(s).", SOURCE),
    )


def print_report(results: list[dict[str, Any]], selected: list[dict[str, Any]], dry_run: bool) -> None:
    print("\nWatchlist candidate discovery")
    print(f"- Mode: {'dry-run' if dry_run else 'applied'}")
    print(f"- Eligible candidates: {sum(1 for item in results if item['eligible'])}")
    print(f"- Promoted candidates: {len(selected)}")
    for item in selected:
        print(
            f"+ {item['code']} {item['name']}: {item['status']} score={item['score']} "
            f"price={item['price']} chg={item['change_pct']:.2f}% "
            f"amount={item['amount_yi']:.1f}亿 avg5={item['avg_amount_5']:.1f}亿 | "
            f"{item['focus_reason']}"
        )

    rejected = [item for item in results if not item["eligible"]]
    for item in sorted(rejected, key=lambda row: row["score"], reverse=True)[:8]:
        print(f"- {item['code']} {item['name']}: score={item['score']} | {item['reason']}")

    print("Status: OK - candidate discovery complete.")
    print("Data source: RSSCAST, https://app-cn.rsscast.io")


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 1

    rules, candidates = load_config(Path(args.config))
    max_add = args.max_add if args.max_add is not None else int(rules.get("max_add_per_run", 5))
    max_active = (
        args.max_active if args.max_active is not None else int(rules.get("max_active_watchlist", 18))
    )

    token = load_token()
    codes = [item["code"] for item in candidates]
    quote_rows = extract_array(call_mcp_tool(token, "StockPriceQuery", {"codes": codes}))
    quote_by_code = {str(row.get("code", "")).zfill(6): row for row in quote_rows}
    latest_date = max((parse_date(row.get("timeString")) for row in quote_rows), default=parse_date(None))
    start_date = latest_date - timedelta(days=args.lookback_days)
    bars = extract_array(
        call_mcp_tool(
            token,
            "StockKLineQuery",
            {
                "codes": codes,
                "startDate": start_date.isoformat(),
                "endDate": latest_date.isoformat(),
            },
        )
    )
    bars_by_code = group_bars(bars)

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        if not args.dry_run:
            ensure_reference_data(conn, candidates)
            state_by_code = load_stock_state(conn)
            sync_candidate_quotes(conn, state_by_code, quote_rows)
        else:
            state_by_code = load_stock_state(conn)

        results = [
            score_candidate(
                item,
                quote_by_code.get(item["code"]),
                bars_by_code.get(item["code"], []),
                state_by_code.get(item["code"]),
                rules,
            )
            for item in candidates
        ]
        available_slots = max(0, max_active - active_watchlist_count(conn))
        selected = sorted(
            [item for item in results if item["eligible"]],
            key=lambda row: (row["score"], row["amount_yi"]),
            reverse=True,
        )[: min(max_add, available_slots)]
        promote(conn, selected, state_by_code, args.dry_run)
        if not args.dry_run:
            conn.commit()

    print_report(results, selected, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

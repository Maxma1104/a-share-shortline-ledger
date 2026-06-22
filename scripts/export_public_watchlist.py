#!/usr/bin/env python3
"""Export an anonymized public watchlist from the local SQLite ledger."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


SOURCE = "RSSCAST"
SOURCE_URL = "https://app-cn.rsscast.io"
STATUS_EN = {
    "重点关注": "High Focus",
    "观察": "Watch",
    "低频观察": "Low Frequency",
    "剔除候选": "Removal Candidate",
}
STATUS_ORDER = {
    "重点关注": 1,
    "观察": 2,
    "低频观察": 3,
    "剔除候选": 4,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export public watchlist JSON and Markdown without private holding data."
    )
    parser.add_argument("--db", default="stock_tracking.db", help="Path to SQLite database.")
    parser.add_argument(
        "--json",
        default="docs/data/watchlist_latest.json",
        help="Output JSON path.",
    )
    parser.add_argument(
        "--markdown",
        default="docs/watchlist.md",
        help="Output Markdown path.",
    )
    return parser.parse_args()


def normalize_status(value: str | None) -> str:
    text = value or ""
    for status in ("重点关注", "观察", "低频观察", "剔除候选"):
        if status in text:
            return status
    return text or "观察"


def sanitize_text(value: str | None) -> str:
    if not value:
        return ""
    text = value.strip()
    replacements = [
        (r"用户指定加入；?", "历史关注；"),
        (r"作为持仓[^；，。]*风向标", "作为相关方向风向标"),
        (r"持仓", "相关标的"),
        (r"用户", "维护者"),
        (r"默认实盘推荐股票池：仅", "公开关注池范围："),
        (r"实盘", "公开观察"),
        (r"不能重复堆仓", "避免同向暴露过高"),
        (r"堆仓", "提高同向暴露"),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text)
    text = re.sub(r"；{2,}", "；", text)
    text = re.sub(r"^[；，。]\s*", "", text)
    return text


def amount_yi(value: Any) -> float | None:
    if value is None:
        return None
    return round(float(value) / 100_000_000, 2)


def load_rows(db_path: Path) -> list[dict[str, Any]]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = list(
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
                  w.status,
                  w.focus_reason,
                  w.key_levels_text,
                  w.invalid_condition,
                  w.updated_on,
                  ps.snapshot_at,
                  ps.price,
                  ps.change_pct,
                  ps.volume_amount,
                  ps.source
                FROM watchlist w
                JOIN stocks st ON st.id = w.stock_id
                LEFT JOIN latest l ON l.stock_id = st.id
                LEFT JOIN price_snapshots ps ON ps.id = l.latest_id
                WHERE st.is_watchlisted = 1
                  AND st.is_holding = 0
                ORDER BY st.code
                """,
                (SOURCE,),
            )
        )

    public_rows: list[dict[str, Any]] = []
    for row in rows:
        status = normalize_status(row["status"])
        public_rows.append(
            {
                "code": row["code"],
                "name": row["name"],
                "status": status,
                "status_en": STATUS_EN.get(status, status),
                "active_for_new_entry": status not in {"低频观察", "剔除候选"},
                "focus_reason": sanitize_text(row["focus_reason"]),
                "key_levels": sanitize_text(row["key_levels_text"]),
                "invalid_condition": sanitize_text(row["invalid_condition"]),
                "watchlist_updated_on": row["updated_on"],
                "snapshot_at": row["snapshot_at"],
                "price": row["price"],
                "change_pct": None if row["change_pct"] is None else round(float(row["change_pct"]), 4),
                "volume_amount_yi": amount_yi(row["volume_amount"]),
                "source": row["source"] or SOURCE,
            }
        )

    public_rows.sort(key=lambda item: (STATUS_ORDER.get(item["status"], 99), item["code"]))
    for idx, item in enumerate(public_rows, start=1):
        item["rank"] = idx
    return public_rows


def build_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    now = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    latest_market_timestamp = max((row["snapshot_at"] or "" for row in rows), default="")
    summary = {status: 0 for status in STATUS_ORDER}
    for row in rows:
        summary[row["status"]] = summary.get(row["status"], 0) + 1

    return {
        "schema_version": "1.0",
        "generated_at": now,
        "latest_market_timestamp": latest_market_timestamp,
        "refresh_schedule": {
            "timezone": "Asia/Shanghai",
            "local_time": "18:00",
            "cron_utc": "10:00",
            "days": "Monday-Friday",
        },
        "data_source": {"name": SOURCE, "url": SOURCE_URL},
        "privacy": (
            "This public export excludes holdings, cost basis, position size, "
            "available shares, allocation, and private review logs."
        ),
        "summary": {"total": len(rows), **summary},
        "watchlist": rows,
    }


def fmt(value: Any, suffix: str = "") -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, float):
        return f"{value:g}{suffix}"
    return f"{value}{suffix}"


def write_markdown(payload: dict[str, Any], path: Path) -> None:
    rows = payload["watchlist"]
    lines = [
        "# Public Watchlist / 公开关注池",
        "",
        f"- Generated at: {payload['generated_at']} (Asia/Shanghai)",
        f"- Latest market timestamp: {payload['latest_market_timestamp'] or '-'}",
        "- Scheduled refresh: every Monday-Friday at 18:00 China Standard Time",
        f"- Data source: {SOURCE}, {SOURCE_URL}",
        "- Privacy: no holdings, cost basis, position size, or private review logs are exported.",
        "",
        "| Rank | Status | Code | Name | Price | Change | Amount (亿) | Key levels | Invalidation | Public note |",
        "| ---: | --- | --- | --- | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for row in rows:
        change = "-" if row["change_pct"] is None else f"{row['change_pct']:.2f}%"
        lines.append(
            "| {rank} | {status} / {status_en} | {code} | {name} | {price} | {change} | {amount} | {levels} | {invalid} | {reason} |".format(
                rank=row["rank"],
                status=row["status"],
                status_en=row["status_en"],
                code=row["code"],
                name=row["name"],
                price=fmt(row["price"]),
                change=change,
                amount=fmt(row["volume_amount_yi"]),
                levels=row["key_levels"] or "-",
                invalid=row["invalid_condition"] or "-",
                reason=row["focus_reason"] or "-",
            )
        )
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    rows = load_rows(db_path)
    payload = build_payload(rows)

    json_path = Path(args.json)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_markdown(payload, Path(args.markdown))
    print(f"Exported {len(rows)} public watchlist row(s).")
    print(f"JSON: {json_path}")
    print(f"Markdown: {args.markdown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

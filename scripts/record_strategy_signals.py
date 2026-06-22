#!/usr/bin/env python3
"""记录策略推荐信号到数据库，用于后续回测。

用法：
  python3 scripts/record_strategy_signals.py --db stock_tracking.db --signals signals.json

signals.json 格式：
[
  {
    "code": "000725",
    "score": 74,
    "score_tracker": 20, "score_sector": 25, "score_quality": 12,
    "score_technical": 10, "score_sentiment": 7,
    "direction": "long",
    "trigger_price": 6.40,
    "trigger_condition": "回踩 6.30-6.54 不破 + 放量重返",
    "invalid_condition": "跌破 6.15 放弃",
    "signal_type": "conditional"
  }
]

也可以直接传 JSON 字符串：
  python3 scripts/record_strategy_signals.py --db stock_tracking.db --json '[{...}]'
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="记录策略推荐信号")
    parser.add_argument("--db", default="stock_tracking.db")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--signals", help="JSON 文件路径")
    group.add_argument("--json", help="JSON 字符串")
    return parser.parse_args()


def load_signals(args: argparse.Namespace) -> list[dict]:
    if args.json:
        return json.loads(args.json)
    path = Path(args.signals)
    if not path.exists():
        raise SystemExit(f"Signals file not found: {path}")
    return json.loads(path.read_text())


def record(db_path: Path, signals: list[dict]) -> int:
    today = date.today().isoformat()
    inserted = 0

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")

        for sig in signals:
            code = sig["code"]
            row = conn.execute(
                "SELECT id FROM stocks WHERE code = ?", (code,)
            ).fetchone()
            if not row:
                print(f"SKIP {code}: not in stocks table", file=sys.stderr)
                continue

            stock_id = row[0]

            # 检查今天是否已有同股票信号
            existing = conn.execute(
                """SELECT id FROM strategy_signals
                   WHERE signal_date = ? AND stock_id = ?""",
                (today, stock_id),
            ).fetchone()
            if existing:
                print(f"SKIP {code}: already has signal for {today}", file=sys.stderr)
                continue

            conn.execute(
                """INSERT INTO strategy_signals
                   (signal_date, stock_id, score,
                    score_tracker, score_sector, score_quality,
                    score_technical, score_sentiment,
                    direction, trigger_price, trigger_condition,
                    invalid_condition, signal_type, signal_status,
                    data_source)
                   VALUES (?, ?, ?,
                           ?, ?, ?,
                           ?, ?,
                           ?, ?, ?,
                           ?, ?, ?,
                           ?)""",
                (
                    today,
                    stock_id,
                    sig.get("score", 0),
                    sig.get("score_tracker"),
                    sig.get("score_sector"),
                    sig.get("score_quality"),
                    sig.get("score_technical"),
                    sig.get("score_sentiment"),
                    sig.get("direction", "long"),
                    sig.get("trigger_price"),
                    sig.get("trigger_condition", ""),
                    sig.get("invalid_condition", ""),
                    sig.get("signal_type", "conditional"),
                    sig.get("signal_status", "pending"),
                    sig.get("data_source", "RSSCAST"),
                ),
            )
            inserted += 1
            print(f"OK {code} score={sig.get('score')} → strategy_signals")

    return inserted


def main() -> int:
    args = parse_args()
    signals = load_signals(args)
    if not signals:
        print("No signals to record.", file=sys.stderr)
        return 0

    count = record(Path(args.db), signals)
    print(f"\nRecorded {count} signal(s) into {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

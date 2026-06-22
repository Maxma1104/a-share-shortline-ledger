#!/usr/bin/env python3
"""策略回溯测试：计算每条历史信号的 N 日实际收益。

用法：
  python3 scripts/backtest_strategy.py --db stock_tracking.db
  python3 scripts/backtest_strategy.py --db stock_tracking.db --signal-id 3
  python3 scripts/backtest_strategy.py --db stock_tracking.db --force  # 允许同日重复运行

每次运行创建独立的 backtest_runs 记录，backtest_results 始终 INSERT（不覆写历史）。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


SOURCE = "RSSCAST"
CHINA_TZ = ZoneInfo("Asia/Shanghai")
RET_WINDOWS = [1, 3, 5, 10]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="回测策略信号（append-only）")
    parser.add_argument("--db", default="stock_tracking.db")
    parser.add_argument("--signal-id", type=int, help="只回测指定信号 ID")
    parser.add_argument("--force", action="store_true", help="允许同日重复运行（跳过 UNIQUE 约束）")
    return parser.parse_args()


def latest_price(conn: sqlite3.Connection, stock_id: int, after_date: str) -> dict | None:
    """获取 after_date 之后的最新行情快照。"""
    row = conn.execute(
        """SELECT snapshot_at, price, change_pct, volume_amount
           FROM price_snapshots
           WHERE stock_id = ? AND source = ? AND snapshot_at > ?
           ORDER BY snapshot_at DESC
           LIMIT 1""",
        (stock_id, SOURCE, after_date),
    ).fetchone()
    if not row:
        return None
    return {"snapshot_at": row[0], "price": row[1], "change_pct": row[2], "volume_amount": row[3]}


def price_at_offset(conn: sqlite3.Connection, stock_id: int, signal_date: str, offset_days: int) -> dict | None:
    """获取信号日 + offset_days 交易日附近的收盘价。"""
    target_date = (datetime.strptime(signal_date, "%Y-%m-%d") + timedelta(days=offset_days)).strftime("%Y-%m-%d")
    start = (datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=2)).strftime("%Y-%m-%d")
    end = (datetime.strptime(target_date, "%Y-%m-%d") + timedelta(days=2)).strftime("%Y-%m-%d")

    row = conn.execute(
        """SELECT snapshot_at, price
           FROM price_snapshots
           WHERE stock_id = ? AND source = ?
             AND snapshot_at >= ? AND snapshot_at <= ?
           ORDER BY snapshot_at DESC
           LIMIT 1""",
        (stock_id, SOURCE, start, end + " 23:59:59"),
    ).fetchone()
    if not row:
        return None
    return {"snapshot_at": row[0], "price": row[1]}


def max_price_in_range(conn: sqlite3.Connection, stock_id: int, start: str, end: str) -> tuple[float, float] | None:
    """获取区间内最高价和最低价。"""
    row = conn.execute(
        """SELECT MAX(price), MIN(price)
           FROM price_snapshots
           WHERE stock_id = ? AND source = ?
             AND snapshot_at >= ? AND snapshot_at <= ?""",
        (stock_id, SOURCE, start, end),
    ).fetchone()
    if not row or row[0] is None:
        return None
    return (row[0], row[1])


def backtest_signal(conn: sqlite3.Connection, signal: sqlite3.Row, run_id: int) -> dict | None:
    """对单条信号执行回测，返回结果 dict（error 字段表示失败）。"""
    sid = signal["id"]
    stock_id = signal["stock_id"]
    signal_date = signal["signal_date"]

    # 获取信号日当天的收盘价作为 entry_price
    entry_snap = latest_price(conn, stock_id, signal_date)
    if not entry_snap:
        return {"signal_id": sid, "error": "no entry snapshot"}

    entry_price = entry_snap["price"]

    result = {
        "signal_id": sid,
        "run_id": run_id,
        "entry_date": entry_snap["snapshot_at"][:10],
        "entry_price": entry_price,
        "exit_date": None,
        "exit_price": None,
        "exit_reason": "pending",
        "return_pct": None,
        "holding_days": None,
        "max_favorable": None,
        "max_adverse": None,
    }

    # N 日收益
    for w in RET_WINDOWS:
        p = price_at_offset(conn, stock_id, signal_date, w)
        result[f"ret_{w}d"] = round((p["price"] - entry_price) / entry_price * 100, 2) if p and p["price"] else None

    # 最新可用价格作为当前退出价
    latest = latest_price(conn, stock_id, signal_date)
    if latest and latest["price"]:
        result["exit_price"] = latest["price"]
        result["exit_date"] = latest["snapshot_at"][:10]
        result["return_pct"] = round((latest["price"] - entry_price) / entry_price * 100, 2)
        result["holding_days"] = (
            datetime.strptime(latest["snapshot_at"][:10], "%Y-%m-%d")
            - datetime.strptime(signal_date, "%Y-%m-%d")
        ).days

    # 最大浮盈/浮亏
    end_date = (latest["snapshot_at"][:10] if latest else date.today().isoformat())
    mm = max_price_in_range(conn, stock_id, signal_date, end_date)
    if mm and entry_price:
        result["max_favorable"] = round((mm[0] - entry_price) / entry_price * 100, 2)
        result["max_adverse"] = round((mm[1] - entry_price) / entry_price * 100, 2)

    return result


def insert_result(conn: sqlite3.Connection, r: dict) -> None:
    """INSERT 一条回测结果（绝不用 UPSERT）。"""
    fields = [
        "signal_id", "run_id", "entry_date", "entry_price", "exit_date", "exit_price",
        "exit_reason", "return_pct", "holding_days",
        "max_favorable", "max_adverse",
    ] + [f"ret_{w}d" for w in RET_WINDOWS]

    values = [r.get(f) for f in fields]

    placeholders = ", ".join("?" for _ in fields)
    conn.execute(
        f"""INSERT INTO backtest_results ({", ".join(fields)}, calculated_at)
            VALUES ({placeholders}, datetime('now'))""",
        values,
    )


def create_run(conn: sqlite3.Connection, run_date: str, force: bool) -> int | None:
    """创建一条 backtest_runs 记录，返回 run_id。"""
    now = datetime.now(CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S")

    if force:
        # 追加毫秒级唯一标识避免 UNIQUE 冲突
        now = datetime.now(CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S.%f")

    try:
        cur = conn.execute(
            "INSERT INTO backtest_runs (run_at, run_date, status) VALUES (?, ?, 'running')",
            (now, run_date),
        )
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError as e:
        if "UNIQUE" in str(e):
            print(f"  ⚠️  今日已有回测记录，跳过（使用 --force 强制重新运行）")
            return None
        raise


def update_run(conn: sqlite3.Connection, run_id: int, signal_count: int, results_count: int, status: str = "completed") -> None:
    """更新 run 的元数据。"""
    conn.execute(
        "UPDATE backtest_runs SET signal_count=?, results_count=?, status=? WHERE id=?",
        (signal_count, results_count, status, run_id),
    )
    conn.commit()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 1

    run_date = date.today().isoformat()

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")

        # ---- 1. 创建运行记录 ----
        run_id = create_run(conn, run_date, args.force)
        if run_id is None:
            return 1

        print(f"📋 Backtest Run #{run_id} | {run_date} | {datetime.now(CHINA_TZ).strftime('%H:%M:%S')} CST")

        # ---- 2. 获取待回测信号 ----
        if args.signal_id:
            signals = conn.execute(
                "SELECT * FROM strategy_signals WHERE id = ?", (args.signal_id,)
            ).fetchall()
        else:
            signals = conn.execute(
                """SELECT * FROM strategy_signals
                   WHERE signal_status IN ('pending', 'triggered')
                   ORDER BY signal_date"""
            ).fetchall()

        if not signals:
            print("No signals to backtest.")
            update_run(conn, run_id, 0, 0, "empty")
            return 0

        # ---- 3. 逐条回测 ----
        print(f"Backtesting {len(signals)} signal(s)...\n")
        results = []
        error_count = 0

        for sig in signals:
            r = backtest_signal(conn, sig, run_id)
            if r is None:
                continue

            results.append(r)
            code_row = conn.execute(
                "SELECT code, name FROM stocks WHERE id = ?", (sig["stock_id"],)
            ).fetchone()
            code = code_row["code"] + " " + code_row["name"] if code_row else f"id={sig['stock_id']}"

            if "error" in r:
                error_count += 1
                print(f"  ⚠️  {sig['signal_date']} {code} score={sig['score']} → ERROR: {r['error']}")
            else:
                insert_result(conn, r)
                ret_str = f"return={r['return_pct']}%" if r.get("return_pct") is not None else "return=N/A"
                print(f"  ✅ {sig['signal_date']} {code} score={sig['score']} → {ret_str}")

        # ---- 4. 更新运行元数据 ----
        saved = len([r for r in results if "error" not in r])
        status = "partial" if error_count > 0 else "completed"
        update_run(conn, run_id, len(signals), saved, status)

        print(f"\n📊 Run #{run_id} complete: {saved}/{len(signals)} results saved ({error_count} errors, status={status})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Export backtest results and holdings data for the web dashboard.

Usage:
  python3 scripts/export_backtest_data.py --db stock_tracking.db
  python3 scripts/export_backtest_data.py --db stock_tracking.db --run-id 3
  python3 scripts/export_backtest_data.py --db stock_tracking.db --output custom.json

Exports to docs/data/backtest_latest.json by default.
Includes:
  - Holdings (code, name, shares, cost, last price, PnL%, stop loss)
  - Latest backtest run performance KPIs
  - Individual signal results with returns
  - Score-segment breakdown
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

SOURCE = "RSSCAST"
CHINA_TZ = ZoneInfo("Asia/Shanghai")
RET_WINDOWS = [1, 3, 5, 10]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export backtest + holdings JSON for the dashboard")
    parser.add_argument("--db", default="stock_tracking.db")
    parser.add_argument("--run-id", type=int, help="指定回测运行 ID（默认最新有结果的一次）")
    parser.add_argument("--output", default="docs/data/backtest_latest.json")
    return parser.parse_args()


# ── Holdings ──────────────────────────────────────────────────

def load_holdings(conn: sqlite3.Connection) -> list[dict]:
    """Load holdings with stock info and latest price snapshot."""
    rows = conn.execute(
        """SELECT h.stock_id, h.quantity, h.available_quantity,
                  h.cost_price, h.last_price, h.unrealized_return_pct,
                  h.stop_loss, h.position_pct, h.plan_cycle,
                  h.current_strategy, h.updated_on,
                  st.code, st.name
           FROM holdings h
           JOIN stocks st ON st.id = h.stock_id
           ORDER BY h.position_pct DESC"""
    ).fetchall()

    holdings = []
    for r in rows:
        # Get latest price snapshot timestamp
        snap = conn.execute(
            """SELECT snapshot_at, price, change_pct
               FROM price_snapshots
               WHERE stock_id = ? AND source = ?
               ORDER BY snapshot_at DESC LIMIT 1""",
            (r["stock_id"], SOURCE),
        ).fetchone()

        holdings.append({
            "code": r["code"],
            "name": r["name"],
            "quantity": r["quantity"],
            "available_quantity": r["available_quantity"],
            "cost_price": round(r["cost_price"], 2) if r["cost_price"] else None,
            "last_price": r["last_price"],
            "unrealized_return_pct": round(r["unrealized_return_pct"], 2) if r["unrealized_return_pct"] is not None else None,
            "stop_loss": r["stop_loss"],
            "position_pct": round(r["position_pct"], 2) if r["position_pct"] else None,
            "plan_cycle": r["plan_cycle"],
            "current_strategy": r["current_strategy"],
            "updated_on": r["updated_on"],
            "snapshot_at": snap["snapshot_at"] if snap else None,
            "snapshot_price": snap["price"] if snap else None,
            "snapshot_change_pct": round(snap["change_pct"], 4) if snap and snap["change_pct"] is not None else None,
        })
    return holdings


def aggregate_holdings(holdings: list[dict]) -> dict:
    """Aggregate holdings-level KPIs."""
    if not holdings:
        return {
            "count": 0, "total_position_pct": 0,
            "total_unrealized_return_pct": 0,
            "win_count": 0, "loss_count": 0,
        }

    win = sum(1 for h in holdings if h.get("unrealized_return_pct") is not None and h["unrealized_return_pct"] > 0)
    loss = sum(1 for h in holdings if h.get("unrealized_return_pct") is not None and h["unrealized_return_pct"] < 0)
    total_pos = sum(h.get("position_pct", 0) or 0 for h in holdings)
    # Weighted return by position_pct
    weighted_returns = []
    for h in holdings:
        if h.get("unrealized_return_pct") is not None and h.get("position_pct"):
            weighted_returns.append(h["unrealized_return_pct"] * h["position_pct"])
    total_ret = round(sum(weighted_returns) / total_pos, 2) if total_pos > 0 and weighted_returns else 0

    return {
        "count": len(holdings),
        "total_position_pct": round(total_pos, 2),
        "total_unrealized_return_pct": total_ret,
        "win_count": win,
        "loss_count": loss,
    }


# ── Backtest ──────────────────────────────────────────────────

def get_latest_run_id(conn: sqlite3.Connection) -> int | None:
    """Get latest run_id that has backtest_results."""
    row = conn.execute(
        """SELECT r.id FROM backtest_runs r
           JOIN backtest_results b ON b.run_id = r.id
           ORDER BY r.id DESC LIMIT 1"""
    ).fetchone()
    if row:
        return row["id"]
    # fallback: latest run even without results
    row = conn.execute(
        "SELECT id FROM backtest_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return row["id"] if row else None


def get_signal_results(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    """Get all signal backtest results for a run."""
    rows = conn.execute(
        """SELECT s.id as signal_id, s.signal_date, s.score,
                  s.signal_status, s.direction, s.trigger_price,
                  s.trigger_condition, s.invalid_condition,
                  b.id as result_id, b.entry_date, b.entry_price,
                  b.exit_date, b.exit_price, b.exit_reason,
                  b.return_pct, b.holding_days,
                  b.max_favorable, b.max_adverse,
                  b.ret_1d, b.ret_3d, b.ret_5d, b.ret_10d,
                  st.code, st.name
           FROM strategy_signals s
           JOIN backtest_results b ON b.signal_id = s.id AND b.run_id = ?
           JOIN stocks st ON st.id = s.stock_id
           ORDER BY s.signal_date""",
        (run_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_pending_signals(conn: sqlite3.Connection) -> list[dict]:
    """Get pending signals (no backtest results yet)."""
    rows = conn.execute(
        """SELECT s.id as signal_id, s.signal_date, s.score,
                  s.signal_status, s.direction, s.trigger_price,
                  s.trigger_condition, s.invalid_condition,
                  st.code, st.name
           FROM strategy_signals s
           JOIN stocks st ON st.id = s.stock_id
           WHERE s.signal_status IN ('pending', 'triggered')
             AND s.id NOT IN (SELECT signal_id FROM backtest_results)
           ORDER BY s.signal_date"""
    ).fetchall()
    return [dict(r) for r in rows]


def calc_sharpe(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    mean = statistics.mean(returns)
    stdev = statistics.stdev(returns) if len(returns) > 1 else 0.01
    if stdev == 0:
        return 0.0
    return round(mean / stdev * (252 ** 0.5), 2)


def profit_factor(wins: float, losses: float) -> float:
    if losses == 0:
        return 99.0 if wins > 0 else 0.0
    return round(abs(wins / losses), 2)


def aggregate_returns(returns: list[float]) -> dict:
    if not returns:
        return {
            "count": 0, "win_count": 0, "loss_count": 0,
            "win_rate": 0, "avg": None, "median": None,
            "max": None, "min": None, "total": None,
            "sharpe": None, "profit_factor": None,
            "sum_wins": 0, "sum_losses": 0,
        }
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]
    return {
        "count": len(returns),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": round(len(wins) / len(returns) * 100, 1),
        "avg": round(statistics.mean(returns), 2),
        "median": round(statistics.median(returns), 2),
        "max": round(max(returns), 2),
        "min": round(min(returns), 2),
        "total": round(sum(returns), 2),
        "sharpe": calc_sharpe(returns),
        "profit_factor": profit_factor(sum(wins), abs(sum(losses))),
        "sum_wins": round(sum(wins), 2),
        "sum_losses": round(sum(losses), 2),
    }


def build_backtest_payload(conn: sqlite3.Connection, run_id: int) -> dict:
    """Build the backtest section of the JSON payload."""
    signal_results = get_signal_results(conn, run_id)
    pending_signals = get_pending_signals(conn)

    # Run metadata
    run = conn.execute(
        "SELECT * FROM backtest_runs WHERE id = ?", (run_id,)
    ).fetchone()

    returns_all = [r["return_pct"] for r in signal_results if r.get("return_pct") is not None]
    ret_1d_list = [r["ret_1d"] for r in signal_results if r.get("ret_1d") is not None]
    ret_5d_list = [r["ret_5d"] for r in signal_results if r.get("ret_5d") is not None]

    # Score segments
    segments = {}
    for label, lo, hi in [("≥80 重点关注", 80, 100), ("65-79 有条件", 65, 79), ("<65 观察", 0, 64)]:
        seg_ret = [r["return_pct"] for r in signal_results
                   if lo <= r["score"] <= hi and r.get("return_pct") is not None]
        segments[label] = aggregate_returns(seg_ret)

    # Signals detail
    signal_detail = []
    for r in signal_results:
        signal_detail.append({
            "signal_id": r["signal_id"],
            "signal_date": r["signal_date"],
            "code": r["code"],
            "name": r["name"],
            "score": r["score"],
            "signal_status": r["signal_status"],
            "direction": r["direction"],
            "trigger_price": r["trigger_price"],
            "trigger_condition": r["trigger_condition"],
            "invalid_condition": r["invalid_condition"],
            "entry_date": r["entry_date"],
            "entry_price": r["entry_price"],
            "exit_date": r["exit_date"],
            "exit_price": r["exit_price"],
            "exit_reason": r["exit_reason"],
            "return_pct": r["return_pct"],
            "holding_days": r["holding_days"],
            "max_favorable": r["max_favorable"],
            "max_adverse": r["max_adverse"],
            "ret_1d": r["ret_1d"],
            "ret_3d": r["ret_3d"],
            "ret_5d": r["ret_5d"],
            "ret_10d": r["ret_10d"],
        })

    return {
        "run_id": run_id,
        "run_at": run["run_at"] if run else None,
        "run_date": run["run_date"] if run else None,
        "run_status": run["status"] if run else None,
        "run_signal_count": run["signal_count"] if run else 0,
        "run_results_count": run["results_count"] if run else 0,
        "total_results": len(signal_results),
        "total_pending": len(pending_signals),
        "overall": aggregate_returns(returns_all),
        "ret_1d": aggregate_returns(ret_1d_list),
        "ret_5d": aggregate_returns(ret_5d_list),
        "segments": segments,
        "signals": signal_detail,
        "pending_signals": [
            {
                "signal_id": s["signal_id"],
                "signal_date": s["signal_date"],
                "code": s["code"],
                "name": s["name"],
                "score": s["score"],
                "signal_status": s["signal_status"],
                "direction": s["direction"],
                "trigger_price": s["trigger_price"],
                "trigger_condition": s["trigger_condition"],
                "invalid_condition": s["invalid_condition"],
            }
            for s in pending_signals
        ],
    }


def build_optimization_notes(backtest: dict) -> list[str]:
    """Generate optimization notes from backtest data."""
    notes = []
    signals = backtest.get("signals", [])

    if len(signals) < 10:
        notes.append("样本量不足 10，统计结论置信度低，继续积累数据。")
        return notes

    high = [r["return_pct"] for r in signals if r["score"] >= 80 and r.get("return_pct") is not None]
    mid = [r["return_pct"] for r in signals if 65 <= r["score"] < 80 and r.get("return_pct") is not None]
    low = [r["return_pct"] for r in signals if r["score"] < 65 and r.get("return_pct") is not None]

    high_avg = statistics.mean(high) if high else None
    mid_avg = statistics.mean(mid) if mid else None
    low_avg = statistics.mean(low) if low else None

    if high_avg is not None and mid_avg is not None and high_avg > mid_avg:
        notes.append(f"分数 ≥80 组平均收益 {high_avg:.2f}% > 65-79 组 {mid_avg:.2f}%——分数与收益正相关，模型有效。")
    elif high_avg is not None and mid_avg is not None:
        notes.append(f"分数 ≥80 组平均收益 {high_avg:.2f}% ≤ 65-79 组 {mid_avg:.2f}%——高分筛选可能需要调高阈值或审核权重。")

    if low and low_avg is not None and low_avg < 0:
        notes.append(f"分数 <65 组平均收益 {low_avg:.2f}%——低分票实际表现差，入池阈值 65 分合理。")

    ret1d = [r["ret_1d"] for r in signals if r.get("ret_1d") is not None]
    if ret1d:
        avg_1d = statistics.mean(ret1d)
        if avg_1d > 0.5:
            notes.append(f"1 日平均收益 +{avg_1d:.2f}%——策略信号对次日有正向预测力。")
        elif avg_1d < -0.5:
            notes.append(f"1 日平均收益 {avg_1d:.2f}%——信号发出后首日偏跌，触发条件可能需要更严格。")

    all_ret = [r["return_pct"] for r in signals if r.get("return_pct") is not None]
    if all_ret:
        wins = [r for r in all_ret if r > 0]
        wr = len(wins) / len(all_ret) * 100
        if wr < 45:
            notes.append(f"胜率仅 {wr:.1f}%——策略可能需要更高的盈亏比来覆盖。")
        elif wr >= 60:
            notes.append(f"胜率 {wr:.1f}%——策略方向判断准确，可考虑适当提高仓位分配。")

    return notes if notes else ["继续积累信号数据以生成有效的优化建议。"]


# ── Main ──────────────────────────────────────────────────────

def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # ── Holdings ──
    holdings = load_holdings(conn)
    holdings_agg = aggregate_holdings(holdings)

    # ── Backtest ──
    run_id = args.run_id or get_latest_run_id(conn)
    if run_id:
        backtest = build_backtest_payload(conn, run_id)
    else:
        backtest = {
            "run_id": None, "run_at": None, "run_date": None,
            "run_status": "no_data", "total_results": 0, "total_pending": 0,
            "overall": {}, "ret_1d": {}, "ret_5d": {},
            "segments": {}, "signals": [], "pending_signals": [],
        }

    optimization_notes = build_optimization_notes(backtest)

    # ── Build full payload ──
    now = datetime.now(CHINA_TZ).isoformat(timespec="seconds")

    # Get latest market timestamp from holdings
    latest_snapshot = max((h.get("snapshot_at") or "" for h in holdings), default="")

    payload = {
        "schema_version": "1.0",
        "generated_at": now,
        "latest_market_timestamp": latest_snapshot,
        "data_source": {"name": "RSSCAST", "url": "https://app-cn.rsscast.io"},
        "holdings": {
            "summary": holdings_agg,
            "positions": holdings,
        },
        "backtest": backtest,
        "optimization_notes": optimization_notes,
    }

    # ── Write ──
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"✅ Export complete: {out_path}")
    print(f"   Holdings: {holdings_agg['count']} positions")
    print(f"   Backtest signals: {backtest['total_results']} results + {backtest['total_pending']} pending")
    print(f"   Run ID: {backtest.get('run_id') or 'N/A'}")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

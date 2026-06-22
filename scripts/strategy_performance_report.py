#!/usr/bin/env python3
"""策略绩效报告：统计回测结果，生成绩效汇总和优化建议。

用法：
  python3 scripts/strategy_performance_report.py --db stock_tracking.db
  python3 scripts/strategy_performance_report.py --db stock_tracking.db --period 30d
  python3 scripts/strategy_performance_report.py --db stock_tracking.db --run-id 3
  python3 scripts/strategy_performance_report.py --db stock_tracking.db --list-runs

默认使用最新一次回测运行的数据。每次回测运行独立存储，历史不可覆写。
"""

from __future__ import annotations

import argparse
import sqlite3
import statistics
import sys
from pathlib import Path
from datetime import date, datetime, timedelta


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="策略绩效报告（基于回测运行）")
    parser.add_argument("--db", default="stock_tracking.db")
    parser.add_argument("--period", default="all", help="统计周期: all / 7d / 30d / 90d")
    parser.add_argument("--format", default="text", choices=["text", "json"], help="输出格式")
    parser.add_argument("--run-id", type=int, help="指定回测运行 ID（默认使用最新一次）")
    parser.add_argument("--list-runs", action="store_true", help="列出所有回测运行记录")
    return parser.parse_args()


def date_filter(period: str) -> str | None:
    if period == "all":
        return None
    days = {"7d": 7, "30d": 30, "90d": 90}.get(period, 0)
    if days:
        return (date.today() - timedelta(days=days)).isoformat()
    return None


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


def aggregate(returns: list[float]) -> dict:
    if not returns:
        return {
            "count": 0, "win_count": 0, "loss_count": 0,
            "win_rate": 0, "avg": None, "median": None, "max": None, "min": None,
            "total": None, "sharpe": None, "profit_factor": None,
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


def list_runs(conn: sqlite3.Connection) -> None:
    """列出所有回测运行记录。"""
    runs = conn.execute(
        """SELECT r.id, r.run_at, r.run_date, r.signal_count, r.results_count, r.status,
                  COUNT(b.id) as actual_results
           FROM backtest_runs r
           LEFT JOIN backtest_results b ON b.run_id = r.id
           GROUP BY r.id
           ORDER BY r.id DESC
           LIMIT 20"""
    ).fetchall()

    if not runs:
        print("No backtest runs found.")
        return

    print(f"{'ID':<5} {'Date':<12} {'Time':<20} {'Signals':<8} {'Results':<8} {'Status':<10}")
    print("-" * 65)
    for r in runs:
        print(f"{r['id']:<5} {r['run_date']:<12} {r['run_at']:<20} {r['signal_count'] or 0:<8} {r['actual_results']:<8} {r['status']:<10}")


def get_latest_run_id(conn: sqlite3.Connection) -> int | None:
    """获取最新一次有结果的 run_id。"""
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


def generate_optimization_notes(conn: sqlite3.Connection, results: list[sqlite3.Row]) -> list[str]:
    notes = []

    if len(results) < 10:
        notes.append("样本量不足 10，统计结论置信度低，继续积累数据。")
        return notes

    high = [r["return_pct"] for r in results if r["score"] >= 80 and r["return_pct"] is not None]
    mid = [r["return_pct"] for r in results if 65 <= r["score"] < 80 and r["return_pct"] is not None]
    low = [r["return_pct"] for r in results if r["score"] < 65 and r["return_pct"] is not None]

    high_avg = statistics.mean(high) if high else None
    mid_avg = statistics.mean(mid) if mid else None
    low_avg = statistics.mean(low) if low else None

    if high_avg is not None and mid_avg is not None and high_avg > mid_avg:
        notes.append(f"分数 ≥80 组平均收益 {high_avg:.2f}% > 65-79 组 {mid_avg:.2f}%——分数与收益正相关，模型有效。")
    elif high_avg is not None and mid_avg is not None:
        notes.append(f"分数 ≥80 组平均收益 {high_avg:.2f}% ≤ 65-79 组 {mid_avg:.2f}%——高分筛选可能需要调高阈值或审核权重。")

    if low and low_avg is not None and low_avg < 0:
        notes.append(f"分数 <65 组平均收益 {low_avg:.2f}%——低分票实际表现差，入池阈值 65 分合理，不建议降低。")

    ret1d = [r["ret_1d"] for r in results if r["ret_1d"] is not None]
    if ret1d:
        avg_1d = statistics.mean(ret1d)
        if avg_1d > 0.5:
            notes.append(f"1 日平均收益 +{avg_1d:.2f}%——策略信号对次日有正向预测力。")
        elif avg_1d < -0.5:
            notes.append(f"1 日平均收益 {avg_1d:.2f}%——信号发出后首日偏跌，触发条件可能需要更严格。")

    all_ret = [r["return_pct"] for r in results if r["return_pct"] is not None]
    if all_ret:
        wins = [r for r in all_ret if r > 0]
        wr = len(wins) / len(all_ret) * 100
        if wr < 45:
            notes.append(f"胜率仅 {wr:.1f}%——策略可能需要更高的盈亏比来覆盖。")
        elif wr >= 60:
            notes.append(f"胜率 {wr:.1f}%——策略方向判断准确，可考虑适当提高仓位分配。")

    if not notes:
        notes.append("当前数据量不足以生成有意义的优化建议，继续积累信号。")

    return notes


def build_report(conn: sqlite3.Connection, period: str, run_id: int) -> dict:
    """构建完整绩效报告（基于指定 run_id）。"""
    cutoff = date_filter(period)

    if cutoff:
        rows = conn.execute(
            """SELECT s.*, b.return_pct, b.ret_1d, b.ret_3d, b.ret_5d, b.ret_10d,
                      b.entry_price, b.exit_price, b.max_favorable, b.max_adverse,
                      b.holding_days, b.exit_reason, b.id as result_id,
                      st.code, st.name
               FROM strategy_signals s
               JOIN backtest_results b ON b.signal_id = s.id AND b.run_id = ?
               JOIN stocks st ON st.id = s.stock_id
               WHERE s.signal_date >= ?
               ORDER BY s.signal_date""",
            (run_id, cutoff),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT s.*, b.return_pct, b.ret_1d, b.ret_3d, b.ret_5d, b.ret_10d,
                      b.entry_price, b.exit_price, b.max_favorable, b.max_adverse,
                      b.holding_days, b.exit_reason, b.id as result_id,
                      st.code, st.name
               FROM strategy_signals s
               JOIN backtest_results b ON b.signal_id = s.id AND b.run_id = ?
               JOIN stocks st ON st.id = s.stock_id
               ORDER BY s.signal_date""",
            (run_id,),
        ).fetchall()

    returns_all = [r["return_pct"] for r in rows if r["return_pct"] is not None]
    ret_1d = [r["ret_1d"] for r in rows if r["ret_1d"] is not None]
    ret_5d = [r["ret_5d"] for r in rows if r["ret_5d"] is not None]

    # 获取 run 元数据
    run_meta = conn.execute(
        "SELECT * FROM backtest_runs WHERE id = ?", (run_id,)
    ).fetchone()

    report = {
        "generated_at": datetime.now().isoformat(),
        "run_id": run_id,
        "run_at": run_meta["run_at"] if run_meta else None,
        "run_date": run_meta["run_date"] if run_meta else None,
        "period": period,
        "total_signals": len(rows),
        "overall": aggregate(returns_all),
        "ret_1d": aggregate(ret_1d),
        "ret_5d": aggregate(ret_5d),
        "signals": [
            {
                "date": r["signal_date"],
                "code": r["code"],
                "name": r["name"],
                "score": r["score"],
                "entry_price": r["entry_price"],
                "exit_price": r["exit_price"],
                "return_pct": r["return_pct"],
                "ret_1d": r["ret_1d"],
                "ret_5d": r["ret_5d"],
                "max_favorable": r["max_favorable"],
                "max_adverse": r["max_adverse"],
                "holding_days": r["holding_days"],
            }
            for r in rows
        ],
        "optimization_notes": generate_optimization_notes(conn, rows),
    }

    for label, lo, hi in [("≥80 重点关注", 80, 100), ("65-79 有条件", 65, 79), ("<65 观察", 0, 64)]:
        seg_ret = [r["return_pct"] for r in rows if lo <= r["score"] <= hi and r["return_pct"] is not None]
        report[f"segment_{label}"] = aggregate(seg_ret)

    return report


def format_text(report: dict) -> str:
    lines = [
        "═══════════════════════════════════════",
        "  策略回溯测试绩效报告",
        f"  生成时间: {report['generated_at']}",
        f"  回测运行: #{report['run_id']} | {report.get('run_date') or 'N/A'}",
        f"  统计周期: {report['period']}",
        f"  总信号数: {report['total_signals']}",
        "═══════════════════════════════════════",
        "",
        "▸ 整体表现",
    ]

    ov = report["overall"]
    lines.extend([
        f"  胜率:       {ov['win_rate']}%",
        f"  平均收益:   {ov.get('avg') or '-'}%",
        f"  中位数收益: {ov.get('median') or '-'}%",
        f"  累计收益:   {ov.get('total') or '-'}%",
        f"  最大单笔:   {ov.get('max') or '-'}% / {ov.get('min') or '-'}%",
        f"  夏普比率:   {ov.get('sharpe') or '-'}",
        f"  盈亏比:     {ov.get('profit_factor') or '-'}",
        f"  盈利次数:   {ov['win_count']}",
        f"  亏损次数:   {ov['loss_count']}",
        "",
        "▸ 时间窗口",
        f"  1 日平均:   {report['ret_1d'].get('avg') or '-'}%",
        f"  5 日平均:   {report['ret_5d'].get('avg') or '-'}%",
        "",
        "▸ 分数段对比",
    ])

    for seg_key in ["≥80 重点关注", "65-79 有条件", "<65 观察"]:
        seg = report.get(f"segment_{seg_key}", {})
        if seg.get("count", 0) > 0:
            lines.append(f"  {seg_key}: avg={seg.get('avg') or '-'}% 胜率={seg.get('win_rate') or '-'}% n={seg['count']}")
        else:
            lines.append(f"  {seg_key}: 无数据")

    lines.extend(["", "▸ 策略优化建议"])

    for note in report.get("optimization_notes", []):
        lines.append(f"  · {note}")

    lines.extend(["", "▸ 最近信号明细"])

    for s in report.get("signals", [])[-10:]:
        ret_str = f"{s['return_pct']}%" if s["return_pct"] is not None else "N/A"
        lines.append(f"  {s['date']} {s['code']} {s['name']} score={s['score']} → {ret_str}")

    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # --list-runs
    if args.list_runs:
        list_runs(conn)
        conn.close()
        return 0

    # 确定 run_id
    if args.run_id:
        run_id = args.run_id
    else:
        run_id = get_latest_run_id(conn)

    if run_id is None:
        print("❌ 无可用回测运行记录。请先执行 backtest_strategy.py。")
        conn.close()
        return 1

    report = build_report(conn, args.period, run_id)

    if args.format == "json":
        import json
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_text(report))

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

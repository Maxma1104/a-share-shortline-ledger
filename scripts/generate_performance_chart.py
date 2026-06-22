#!/usr/bin/env python3
"""自动生成策略回测绩效图表 HTML 报告。

用法：
  python3 scripts/generate_performance_chart.py --db stock_tracking.db
  python3 scripts/generate_performance_chart.py --db stock_tracking.db --run-id 3
  python3 scripts/generate_performance_chart.py --db stock_tracking.db --output reports/custom.html

从数据库读取最新的回测运行数据 + 行情快照，生成深色主题 Chart.js 看板。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from datetime import date, datetime


SOURCE = "RSSCAST"
RET_WINDOWS = [1, 3, 5, 10]

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>策略回测绩效报告｜{report_date}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
  :root {{ --bg:#0d1117; --card:#161b22; --border:#30363d; --text:#c9d1d9; --accent:#58a6ff; --green:#3fb950; --red:#f85149; --yellow:#d2991d; --muted:#8b949e; }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:var(--bg); color:var(--text); font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; padding:20px 24px; max-width:1100px; margin:0 auto; }}
  h1 {{ font-size:22px; margin-bottom:2px; letter-spacing:-0.3px; }}
  .subtitle {{ color:var(--muted); font-size:12px; margin-bottom:18px; display:flex; gap:16px; align-items:center; }}
  .alert {{ background:#1a1a2e; border:1px solid var(--yellow); border-radius:6px; padding:10px 14px; margin-bottom:16px; font-size:12px; color:var(--yellow); line-height:1.5; }}
  .kpi-row {{ display:grid; grid-template-columns: repeat(5,1fr); gap:10px; margin-bottom:16px; }}
  .kpi {{ background:var(--card); border:1px solid var(--border); border-radius:6px; padding:12px 10px; text-align:center; }}
  .kpi-val {{ font-size:26px; font-weight:700; line-height:1.1; }}
  .kpi-lbl {{ font-size:11px; color:var(--muted); margin-top:3px; }}
  .grid2 {{ display:grid; grid-template-columns: 1fr 1fr; gap:12px; margin-bottom:16px; }}
  .grid3 {{ display:grid; grid-template-columns: 1fr 2fr; gap:12px; margin-bottom:16px; }}
  .card {{ background:var(--card); border:1px solid var(--border); border-radius:6px; padding:14px; }}
  .card h3 {{ font-size:13px; margin-bottom:10px; color:var(--accent); font-weight:600; }}
  .card canvas {{ max-height:260px; }}
  .full {{ grid-column:1/-1; }}
  table {{ width:100%; border-collapse:collapse; font-size:12px; margin-top:6px; }}
  th,td {{ padding:7px 10px; text-align:left; border-bottom:1px solid var(--border); }}
  th {{ color:var(--muted); font-weight:500; font-size:11px; }}
  .hi {{ color:var(--red); font-weight:700; }}
  .up {{ color:var(--green); }}
  .dn {{ color:var(--red); }}
  .tag {{ display:inline-block; padding:1px 6px; border-radius:3px; font-size:10px; font-weight:600; }}
  .tag-pending {{ background:rgba(210,153,29,0.15); color:var(--yellow); }}
  .insight {{ background:rgba(88,166,255,0.06); border:1px solid rgba(88,166,255,0.2); border-radius:6px; padding:12px 14px; margin-top:16px; font-size:12px; line-height:1.7; }}
  .insight strong {{ color:var(--accent); }}
  .footer {{ margin-top:16px; font-size:10px; color:var(--muted); text-align:center; border-top:1px solid var(--border); padding-top:12px; }}
</style>
</head>
<body>

<h1>📊 策略回测绩效报告</h1>
<div class="subtitle">
  <span>{report_date}（{weekday}）</span>
  <span>回测运行 #{run_id} | {run_status}</span>
</div>

<div class="alert">
  <strong>⏱️ 数据口径</strong>：回测运行 #{run_id}，执行于 {run_at}。<br>
  数据来源：RSSCAST + 上证指数（Sina Finance API）。每次运行独立存储，历史不可覆写。
</div>

<!-- KPI Row -->
<div class="kpi-row">
  <div class="kpi"><div class="kpi-val" style="color:var(--accent)">{total_signals}</div><div class="kpi-lbl">策略信号</div></div>
  <div class="kpi"><div class="kpi-val" style="color:var(--red)">{high_score_count}</div><div class="kpi-lbl">≥80 高分</div></div>
  <div class="kpi"><div class="kpi-val" style="color:{portfolio_color}">{portfolio_return}</div><div class="kpi-lbl">组合预估收益</div></div>
  <div class="kpi"><div class="kpi-val" style="color:{sh_color}">{sh_return}</div><div class="kpi-lbl">同期上证指数</div></div>
  <div class="kpi"><div class="kpi-val" style="color:{alpha_color}">{alpha}</div><div class="kpi-lbl">超额收益 α</div></div>
</div>

<!-- Row 1: Score + Strategy vs Benchmark -->
<div class="grid2">
  <div class="card">
    <h3>🎯 信号分数分布</h3>
    <canvas id="scoreChart"></canvas>
  </div>
  <div class="card">
    <h3>📈 策略组合 vs 上证指数 · 累计收益曲线</h3>
    <canvas id="benchmarkChart"></canvas>
  </div>
</div>

<!-- Row 2: Estimated returns + Market context -->
<div class="grid3">
  <div class="card">
    <h3>💰 信号预估收益（触发价 → 最新收盘）</h3>
    <canvas id="estReturnChart"></canvas>
  </div>
  <div class="card">
    <h3>🏛️ 上证指数近 10 日走势 + 信号触发日标注</h3>
    <canvas id="shIndexChart"></canvas>
  </div>
</div>

<!-- Signal Table -->
<div class="card full">
  <h3>📋 策略信号明细</h3>
  <table>
    <thead><tr>
      <th>代码</th><th>名称</th><th>分数</th><th>触发日期</th><th>触发价</th>
      <th>最新收盘</th><th>价差%</th><th>1日收益</th><th>5日收益</th><th>最大浮盈</th><th>最大浮亏</th><th>状态</th>
    </tr></thead>
    <tbody>
      {signal_rows}
    </tbody>
  </table>
</div>

<!-- Insight -->
<div class="insight">
  <strong>💡 策略解读</strong><br>
  {insights}
</div>

<div class="footer">
  数据来源：RSSCAST · https://app-cn.rsscast.io &nbsp;|&nbsp; 上证指数：Sina Finance API &nbsp;|&nbsp; 回测运行 #{run_id} · {run_at}
</div>

<script>
Chart.defaults.color = '#8b949e';
Chart.defaults.borderColor = '#30363d';
Chart.defaults.font.family = "-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif";
Chart.defaults.font.size = 10;
const G = 'rgba(48,54,61,0.4)';
const C = {{ blue:'#58a6ff', green:'#3fb950', red:'#f85149', yellow:'#d2991d', purple:'#bc8cff', orange:'#f0883e', white:'rgba(255,255,255,0.25)' }};

// ===== Chart 1: Score Distribution =====
new Chart(document.getElementById('scoreChart'), {{
  type: 'bar',
  data: {{
    labels: {score_labels},
    datasets: [{{
      data: {score_values},
      backgroundColor: {score_colors},
      borderRadius: 3, borderWidth:0
    }}]
  }},
  options: {{
    responsive:true, plugins:{{legend:{{display:false}}}},
    scales: {{ y:{{min:0,max:100,grid:{{color:G}},ticks:{{callback:v=>v+'分'}}}}, x:{{grid:{{display:false}}}} }}
  }}
}});

// ===== Chart 2: Strategy vs Benchmark Cumulative Return =====
new Chart(document.getElementById('benchmarkChart'), {{
  type: 'line',
  data: {{
    labels: {bench_dates},
    datasets: [
      {{ label:'策略组合 (等权)', data:{strat_returns}, borderColor:C.green, backgroundColor:'rgba(63,185,80,0.1)', fill:true, tension:0.3, pointRadius:5, pointBackgroundColor:C.green, borderWidth:2.5 }},
      {{ label:'上证指数', data:{sh_returns}, borderColor:C.white, borderDash:[5,3], tension:0.3, pointRadius:5, pointBackgroundColor:'rgba(255,255,255,0.6)', borderWidth:2 }},
      {{ label:'超额 α', data:{alpha_returns}, borderColor:C.blue, borderDash:[2,2], tension:0, pointRadius:4, pointBackgroundColor:C.blue, borderWidth:2 }},
    ]
  }},
  options: {{
    responsive:true,
    plugins: {{
      legend:{{position:'bottom',labels:{{boxWidth:10,padding:10,usePointStyle:true,font:{{size:10}}}}}},
      tooltip:{{callbacks:{{label:ctx=>ctx.dataset.label+': '+(ctx.raw>=0?'+':'')+ctx.raw.toFixed(2)+'%'}}}}
    }},
    scales: {{
      y: {{ grid:{{color:G}}, ticks:{{callback:v=>v+'%'}}, title:{{display:true,text:'累计收益 %',font:{{size:10}}}} }},
      x: {{ grid:{{display:false}} }}
    }}
  }}
}});

// ===== Chart 3: Estimated Return (Horizontal Bar) =====
new Chart(document.getElementById('estReturnChart'), {{
  type: 'bar',
  data: {{
    labels: {est_labels},
    datasets: [{{
      data: {est_values},
      backgroundColor: {est_colors},
      borderRadius: 3, borderWidth:0
    }}]
  }},
  options: {{
    indexAxis:'y', responsive:true, plugins:{{legend:{{display:false}}}},
    scales: {{ x:{{grid:{{color:G}},ticks:{{callback:v=>v+'%'}}}}, y:{{grid:{{display:false}}}} }}
  }}
}});

// ===== Chart 4: SH Index 10-day Trend + Signal Trigger Marker =====
new Chart(document.getElementById('shIndexChart'), {{
  type: 'line',
  data: {{
    labels: {sh_dates},
    datasets: [
      {{ label:'上证指数', data:{sh_closes}, borderColor:C.white, tension:0.3, pointRadius:3, borderWidth:2, fill:false }},
      {{ label:'信号触发日 ({trigger_date})', data:{trigger_markers}, borderColor:C.red, pointRadius:6, pointBackgroundColor:C.red, pointStyle:'rectRot', showLine:false, borderWidth:0 }},
    ]
  }},
  options: {{
    responsive:true,
    plugins: {{
      legend:{{position:'bottom',labels:{{boxWidth:10,padding:10,usePointStyle:true,font:{{size:10}}}}}},
      tooltip:{{callbacks:{{label:ctx=>ctx.dataset.label+': '+ctx.raw.toFixed(0)}}}}
    }},
    scales: {{
      y: {{ grid:{{color:G}}, ticks:{{callback:v=>(v/1000).toFixed(1)+'k'}} }},
      x: {{ grid:{{display:false}} }}
    }}
  }}
}});
</script>

</body>
</html>"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成策略回测绩效图表 HTML 报告")
    parser.add_argument("--db", default="stock_tracking.db")
    parser.add_argument("--run-id", type=int, help="指定回测运行 ID（默认最新）")
    parser.add_argument("--output", help="输出文件路径（默认 reports/performance_chart_YYYY-MM-DD.html）")
    return parser.parse_args()


def get_latest_run(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """获取最新一次有回测结果的运行。"""
    row = conn.execute(
        """SELECT r.* FROM backtest_runs r
           JOIN backtest_results b ON b.run_id = r.id
           ORDER BY r.id DESC LIMIT 1"""
    ).fetchone()
    if row:
        return row
    # fallback: latest run even without results
    return conn.execute("SELECT * FROM backtest_runs ORDER BY id DESC LIMIT 1").fetchone()


def get_signal_results(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    """获取指定运行中所有信号回测结果。"""
    rows = conn.execute(
        """SELECT s.*, b.return_pct, b.ret_1d, b.ret_3d, b.ret_5d, b.ret_10d,
                  b.entry_price, b.exit_price, b.max_favorable, b.max_adverse,
                  b.holding_days, b.exit_reason, b.entry_date, b.exit_date,
                  st.code, st.name
           FROM strategy_signals s
           JOIN backtest_results b ON b.signal_id = s.id AND b.run_id = ?
           JOIN stocks st ON st.id = s.stock_id
           ORDER BY s.signal_date""",
        (run_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_all_signals(conn: sqlite3.Connection) -> list[dict]:
    """获取所有 pending/triggered 信号（用于无回测结果时的展示）。"""
    rows = conn.execute(
        """SELECT s.*, st.code, st.name
           FROM strategy_signals s
           JOIN stocks st ON st.id = s.stock_id
           WHERE s.signal_status IN ('pending', 'triggered')
           ORDER BY s.signal_date"""
    ).fetchall()
    return [dict(r) for r in rows]


def get_price_history(conn: sqlite3.Connection, stock_id: int, days: int = 10) -> list[dict]:
    """获取个股近 N 个交易日收盘价。"""
    rows = conn.execute(
        """SELECT DISTINCT substr(snapshot_at,1,10) as d, price
           FROM price_snapshots
           WHERE stock_id = ? AND source = ? AND snapshot_at LIKE '%15:00%'
           ORDER BY d DESC LIMIT ?""",
        (stock_id, SOURCE, days),
    ).fetchall()
    return [{"date": r["d"], "price": r["price"]} for r in rows][::-1]  # reverse to ascending


def get_sh_index_data() -> list[dict]:
    """获取上证指数近 15 个交易日数据（从 Sina API）。"""
    import ssl
    import urllib.request

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        url = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol=sh000001&scale=240&ma=no&datalen=15"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"})
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            raw = resp.read().decode("gbk", errors="replace")
        data = json.loads(raw)
        return [{"date": d["day"], "close": float(d["close"])} for d in data]
    except Exception:
        return []


def fmt_pct(v: float | None) -> str:
    if v is None:
        return "N/A"
    return f"{v:+.1f}%"


def color_for(v: float | None) -> str:
    if v is None:
        return "var(--muted)"
    return "var(--green)" if v >= 0 else "var(--red)"


def generate_insights(signals: list[dict], sh_data: list[dict]) -> str:
    """生成策略解读文本。"""
    parts = []

    if not signals:
        return "暂无回测数据。待今日收盘后运行 backtest_strategy.py 生成首批收益数据。"

    high_score = [s for s in signals if s.get("score", 0) >= 80]
    positive = [s for s in signals if s.get("return_pct") is not None and s["return_pct"] > 0]
    negative = [s for s in signals if s.get("return_pct") is not None and s["return_pct"] < 0]

    if high_score:
        names = ", ".join(f"{s['name']}({s['score']}分)" for s in high_score)
        parts.append(f"· <strong>高分信号</strong>：{names}，共 {len(high_score)} 只。")

    if positive:
        parts.append(f"· 盈利信号 {len(positive)}/{len(signals)}，胜率 {len(positive)/max(len(signals),1)*100:.0f}%。")

    if negative:
        names = ", ".join(s['name'] for s in negative)
        parts.append(f"· <strong>亏损信号</strong>：{names}，需关注是否触发止损条件。")

    if len(signals) < 5:
        parts.append("· 样本量 <5，统计结论置信度低，继续积累信号。")

    if not parts:
        parts.append("· 当前数据量不足以生成有意义的策略解读，继续积累信号。")

    return "<br>\n  ".join(parts)


def build_html(conn: sqlite3.Connection, run: sqlite3.Row | None, run_id: int) -> str:
    """构建完整 HTML 报告。"""
    run_at = run["run_at"] if run else datetime.now().isoformat()
    run_date_str = run["run_date"] if run else date.today().isoformat()
    run_status = run["status"] if run else "N/A"
    today = date.today()
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    weekday = weekdays[today.weekday()]

    # 获取信号数据
    signal_results = get_signal_results(conn, run_id)
    if not signal_results:
        # 无回测结果，用 pending 信号做展示
        all_signals = get_all_signals(conn)
        signals_for_display = [
            {**s, "return_pct": None, "ret_1d": None, "ret_5d": None,
             "max_favorable": None, "max_adverse": None,
             "entry_price": s.get("trigger_price"), "exit_price": None}
            for s in all_signals
        ]
    else:
        signals_for_display = signal_results

    total_signals = len(signals_for_display)
    high_score_count = len([s for s in signals_for_display if s.get("score", 0) >= 80])

    # 信号表格行
    signal_rows = ""
    for s in signals_for_display:
        code = s.get("code", "?")
        name = s.get("name", "?")
        score = s.get("score", 0)
        score_cls = "hi" if score >= 80 else ""
        signal_date = s.get("signal_date", "?")
        trigger = s.get("trigger_price", 0)
        exit_p = s.get("exit_price")
        ret = s.get("return_pct")
        ret_1d = s.get("ret_1d")
        ret_5d = s.get("ret_5d")
        max_fav = s.get("max_favorable")
        max_adv = s.get("max_adverse")
        status = s.get("signal_status", "pending")
        diff = round((exit_p - trigger) / trigger * 100, 1) if exit_p and trigger else None

        signal_rows += (
            f'<tr><td>{code}</td><td>{name}</td><td class="{score_cls}">{score}</td>'
            f'<td>{signal_date}</td><td>{trigger}</td>'
            f'<td>{exit_p or "-"}</td>'
            f'<td class="{"up" if diff and diff>0 else "dn" if diff and diff<0 else ""}">{fmt_pct(diff) if diff is not None else "-"}</td>'
            f'<td class="{"up" if ret_1d and ret_1d>0 else "dn" if ret_1d and ret_1d<0 else ""}">{fmt_pct(ret_1d) if ret_1d is not None else "-"}</td>'
            f'<td class="{"up" if ret_5d and ret_5d>0 else "dn" if ret_5d and ret_5d<0 else ""}">{fmt_pct(ret_5d) if ret_5d is not None else "-"}</td>'
            f'<td class="up">{fmt_pct(max_fav) if max_fav is not None else "-"}</td>'
            f'<td class="dn">{fmt_pct(max_adv) if max_adv is not None else "-"}</td>'
            f'<td><span class="tag tag-pending">{status}</span></td></tr>\n'
        )

    # 计算组合预估收益（等权）
    diffs = []
    for s in signals_for_display:
        ep = s.get("exit_price") or s.get("trigger_price")
        tp = s.get("trigger_price")
        if ep and tp:
            diffs.append((ep - tp) / tp * 100)
    portfolio_return = round(sum(diffs) / len(diffs), 2) if diffs else 0

    # 上证指数数据
    sh_data = get_sh_index_data()
    trigger_date = signals_for_display[0]["signal_date"] if signals_for_display else today.isoformat()

    # 计算上证指数同期收益
    sh_return = 0.0
    if sh_data:
        # 找到触发日对应的上证收盘
        trigger_sh = None
        latest_sh = sh_data[-1]["close"] if sh_data else 0
        for d in sh_data:
            if d["date"] == trigger_date:
                trigger_sh = d["close"]
                break
        if trigger_sh is None and len(sh_data) >= 2:
            # 使用触发日前一个交易日
            for i, d in enumerate(sh_data):
                if d["date"] > trigger_date:
                    trigger_sh = sh_data[i - 1]["close"] if i > 0 else d["close"]
                    break
        if trigger_sh and latest_sh:
            sh_return = round((latest_sh - trigger_sh) / trigger_sh * 100, 2)

    alpha = round(portfolio_return - sh_return, 2)
    portfolio_color = "var(--green)" if portfolio_return >= 0 else "var(--red)"
    sh_color = "var(--green)" if sh_return >= 0 else "var(--red)"
    alpha_color = "var(--green)" if alpha >= 0 else "var(--red)"

    # 图表数据
    score_labels = json.dumps([s.get("name", "?") for s in signals_for_display])
    score_values = json.dumps([s.get("score", 0) for s in signals_for_display])
    score_colors = json.dumps(["#f85149" if s.get("score", 0) >= 80 else "#d2991d" for s in signals_for_display])

    # 收益曲线数据
    bench_dates = json.dumps([trigger_date, signals_for_display[0].get("exit_date", today.isoformat()) if signals_for_display else today.isoformat()])
    strat_returns = json.dumps([0, portfolio_return])
    sh_returns = json.dumps([0, sh_return])
    alpha_returns = json.dumps([0, alpha])

    # 预估收益（水平柱状图）
    est_items = sorted(
        [(s.get("name", "?"), diff) for s, diff in zip(signals_for_display, diffs)],
        key=lambda x: x[1] if x[1] is not None else -999, reverse=True
    )
    est_labels = json.dumps([item[0] for item in est_items])
    est_values = json.dumps([item[1] if item[1] is not None else 0 for item in est_items])
    est_colors = json.dumps(["#3fb950" if (item[1] or 0) >= 0 else "#f85149" for item in est_items])

    # 上证指数走势
    if sh_data:
        sh_dates = json.dumps([d["date"][5:] for d in sh_data[-10:]])  # MM-DD format
        sh_closes = json.dumps([d["close"] for d in sh_data[-10:]])
        trigger_markers = json.dumps([d["close"] if d["date"] == trigger_date else None for d in sh_data[-10:]])
    else:
        sh_dates = json.dumps([])
        sh_closes = json.dumps([])
        trigger_markers = json.dumps([])

    # 策略解读
    insights = generate_insights(signals_for_display, sh_data)

    return HTML_TEMPLATE.format(
        report_date=today.isoformat(),
        weekday=weekday,
        run_id=run_id,
        run_status=run_status,
        run_at=run_at,
        total_signals=total_signals,
        high_score_count=high_score_count,
        portfolio_return=f"{portfolio_return:+.2f}%",
        sh_return=f"{sh_return:+.2f}%",
        alpha=f"{alpha:+.2f}%",
        portfolio_color=portfolio_color,
        sh_color=sh_color,
        alpha_color=alpha_color,
        signal_rows=signal_rows,
        score_labels=score_labels,
        score_values=score_values,
        score_colors=score_colors,
        bench_dates=bench_dates,
        strat_returns=strat_returns,
        sh_returns=sh_returns,
        alpha_returns=alpha_returns,
        est_labels=est_labels,
        est_values=est_values,
        est_colors=est_colors,
        sh_dates=sh_dates,
        sh_closes=sh_closes,
        trigger_markers=trigger_markers,
        trigger_date=trigger_date[5:] if len(trigger_date) >= 10 else trigger_date,
        insights=insights,
    )


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # 确定 run_id
    if args.run_id:
        run_id = args.run_id
        run = conn.execute("SELECT * FROM backtest_runs WHERE id = ?", (run_id,)).fetchone()
    else:
        run = get_latest_run(conn)
        run_id = run["id"] if run else None

    if run_id is None:
        print("❌ 无可用回测运行记录。请先执行 backtest_strategy.py。")
        conn.close()
        return 1

    # 生成 HTML
    html = build_html(conn, run, run_id)

    # 输出路径
    if args.output:
        out_path = Path(args.output)
    else:
        out_dir = Path("reports")
        out_dir.mkdir(exist_ok=True)
        out_path = out_dir / f"performance_chart_{date.today().isoformat()}.html"

    out_path.write_text(html, encoding="utf-8")
    print(f"✅ HTML 报告已生成：{out_path}")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

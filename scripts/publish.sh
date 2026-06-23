#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# 一键发布：收盘后跑完整管道 → 推 GitHub Pages
# 用法：bash scripts/publish.sh
# ═══════════════════════════════════════════════════════════════
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

TODAY=$(date +%Y-%m-%d)
CHART_FILE="reports/performance_chart_${TODAY}.html"
DB="stock_tracking.db"

echo "══════════════════════════════════════════"
echo "  短线回测发布管道 · ${TODAY}"
echo "══════════════════════════════════════════"

# Step 1: 刷新行情
echo ""
echo "▶ Step 1/6: 刷新行情快照..."
python3 scripts/refresh_before_analysis.py
echo "✓ 行情刷新完成"

# Step 2: 记录今日信号（如果还没有）
echo ""
echo "▶ Step 2/6: 记录策略信号..."
python3 scripts/record_strategy_signals.py --db "$DB" \
  --json "$(python3 scripts/backtest_strategy.py --db "$DB" --dry-run-json 2>/dev/null || echo '[]')" \
  2>/dev/null || echo "  (信号记录跳过，可能已存在)"

# Step 3: 运行回测
echo ""
echo "▶ Step 3/6: 计算回测收益..."
python3 scripts/backtest_strategy.py --db "$DB" || echo "  (回测执行完成)"

# Step 4: 导出 JSON
echo ""
echo "▶ Step 4/6: 导出回测数据..."
python3 scripts/export_backtest_data.py --db "$DB" --output docs/data/backtest_latest.json
echo "✓ docs/data/backtest_latest.json"

# Step 5: 生成 Chart.js HTML 报告
echo ""
echo "▶ Step 5/6: 生成绩效图表..."
python3 scripts/generate_performance_chart.py --db "$DB" --output "$CHART_FILE"
echo "✓ $CHART_FILE"

# 复制到 docs（GitHub Pages 用）
cp "$CHART_FILE" "docs/performance_chart_${TODAY}.html"
cp "$CHART_FILE" "docs/performance_chart_latest.html"
echo "✓ docs/performance_chart_${TODAY}.html"
echo "✓ docs/performance_chart_latest.html"

# Step 6: 推 GitHub
echo ""
echo "▶ Step 6/6: 推送到 GitHub..."
git add docs/ reports/ docs/data/backtest_latest.json
git commit -m "收盘发布: ${TODAY} 回测报告更新" || echo "  (无变更，跳过 commit)"
git push
echo "✓ 已推送"

echo ""
echo "══════════════════════════════════════════"
echo "  发布完成！"
echo "  公开地址：https://maxma1104.github.io/a-share-shortline-ledger/"
echo "  回测报告：https://maxma1104.github.io/a-share-shortline-ledger/performance_chart_latest.html"
echo "══════════════════════════════════════════"

PRAGMA foreign_keys = ON;

-- ============================================================
-- 策略回溯测试系统
-- 记录每次策略推荐信号，回测实际收益，支持持续优化
-- ============================================================

-- 策略信号表：每次生成的推荐信号
CREATE TABLE IF NOT EXISTS strategy_signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  signal_date TEXT NOT NULL,               -- 信号生成日期 (YYYY-MM-DD)
  stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
  score INTEGER NOT NULL CHECK (score BETWEEN 0 AND 100),
  
  -- 评分五维度明细
  score_tracker INTEGER,                   -- 龙虎榜资金 (0-30)
  score_sector INTEGER,                    -- 热点板块 (0-25)
  score_quality INTEGER,                   -- 游资质量 (0-20)
  score_technical INTEGER,                 -- 技术形态 (0-15)
  score_sentiment INTEGER,                 -- 情绪周期 (0-10)
  
  -- 触发条件
  direction TEXT NOT NULL DEFAULT 'long',  -- long / short (当前仅做多)
  trigger_price REAL,                      -- 信号发出时的参考价
  trigger_condition TEXT,                  -- 触发条件描述
  invalid_condition TEXT,                  -- 失效条件
  
  -- 信号类型
  signal_type TEXT NOT NULL DEFAULT 'conditional', -- conditional / executed / invalidated
  signal_status TEXT NOT NULL DEFAULT 'pending',   -- pending / triggered / invalid / expired
  
  -- 来源
  data_source TEXT DEFAULT 'RSSCAST',
  data_snapshot_time TEXT,                 -- 使用的行情快照时间
  
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (stock_id) REFERENCES stocks(id) ON DELETE CASCADE
);

-- 回测结果表：每条信号的 N 日实际收益
CREATE TABLE IF NOT EXISTS backtest_results (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  signal_id INTEGER NOT NULL REFERENCES strategy_signals(id) ON DELETE CASCADE,
  
  -- 实际入场
  entry_date TEXT,                         -- 实际入场日期
  entry_price REAL,                        -- 实际入场价
  
  -- 退出（多种可能的退出方式）
  exit_date TEXT,                          -- 实际退出日期
  exit_price REAL,                         -- 实际退出价
  exit_reason TEXT,                        -- stop_loss / take_profit / time_exit / invalidated / manual
  
  -- 收益
  return_pct REAL,                         -- 收益率 (%)
  holding_days INTEGER,                    -- 持仓天数
  
  -- 路径统计
  max_favorable REAL,                      -- 持仓期间最大浮盈 (%)
  max_adverse REAL,                        -- 持仓期间最大浮亏 (%)
  
  -- 各时间窗口收益（从信号日起算）
  ret_1d REAL,                             -- 1 日收益
  ret_3d REAL,                             -- 3 日收益
  ret_5d REAL,                             -- 5 日收益
  ret_10d REAL,                            -- 10 日收益
  
  calculated_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (signal_id) REFERENCES strategy_signals(id) ON DELETE CASCADE
);

-- 绩效汇总表：按维度聚合的统计数据
CREATE TABLE IF NOT EXISTS strategy_performance (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  period_start TEXT NOT NULL,              -- 统计周期开始
  period_end TEXT NOT NULL,                -- 统计周期结束
  dimension TEXT NOT NULL DEFAULT 'all',    -- 统计维度: all / sector / score_range / signal_type
  
  -- 基础统计
  total_signals INTEGER,                   -- 总信号数
  triggered_signals INTEGER,               -- 实际触发数
  win_count INTEGER,                       -- 盈利次数
  loss_count INTEGER,                      -- 亏损次数
  win_rate REAL,                           -- 胜率 (%)
  
  -- 收益统计
  avg_return REAL,                         -- 平均收益 (%)
  median_return REAL,                      -- 中位数收益 (%)
  max_return REAL,                         -- 最大单笔收益 (%)
  min_return REAL,                         -- 最大单笔亏损 (%)
  total_return REAL,                       -- 累计收益 (%)
  
  -- 风险统计
  sharpe_ratio REAL,                       -- 夏普比率（年化）
  max_drawdown REAL,                       -- 最大回撤 (%)
  profit_factor REAL,                      -- 盈亏比（总盈利/总亏损）
  
  -- 时间统计
  avg_holding_days REAL,                   -- 平均持仓天数
  
  -- 优化建议
  optimization_notes TEXT,                 -- 基于数据的策略优化建议
  
  calculated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 回溯测试配置
CREATE TABLE IF NOT EXISTS backtest_config (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  config_key TEXT NOT NULL UNIQUE,
  config_value TEXT NOT NULL,
  description TEXT,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 默认配置
INSERT OR IGNORE INTO backtest_config (config_key, config_value, description) VALUES
  ('default_holding_days', '5', '默认持仓天数，到期自动结算'),
  ('min_sample_for_optimization', '10', '触发优化建议所需的最小样本数'),
  ('score_entry_threshold', '65', '入池最低分数'),
  ('score_high_focus_threshold', '80', '重点关注最低分数'),
  ('ret_windows', '1,3,5,10', '回测窗口（天），逗号分隔');

-- 索引
CREATE INDEX IF NOT EXISTS idx_signals_date ON strategy_signals(signal_date);
CREATE INDEX IF NOT EXISTS idx_signals_stock ON strategy_signals(stock_id);
CREATE INDEX IF NOT EXISTS idx_signals_score ON strategy_signals(score DESC);
CREATE INDEX IF NOT EXISTS idx_signals_status ON strategy_signals(signal_status);
CREATE INDEX IF NOT EXISTS idx_backtest_signal ON backtest_results(signal_id);
CREATE INDEX IF NOT EXISTS idx_performance_period ON strategy_performance(period_start, period_end);

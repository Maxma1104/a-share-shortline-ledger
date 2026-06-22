PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS markets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  country TEXT NOT NULL,
  trade_status TEXT NOT NULL,
  note TEXT
);

CREATE TABLE IF NOT EXISTS sectors (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  focus_reason TEXT,
  catalysts TEXT,
  risks TEXT,
  status TEXT NOT NULL DEFAULT '观察中',
  updated_on TEXT NOT NULL DEFAULT (date('now'))
);

CREATE TABLE IF NOT EXISTS stocks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL,
  market_id INTEGER NOT NULL REFERENCES markets(id),
  name TEXT NOT NULL,
  board TEXT,
  sector_id INTEGER REFERENCES sectors(id),
  is_watchlisted INTEGER NOT NULL DEFAULT 0 CHECK (is_watchlisted IN (0, 1)),
  is_holding INTEGER NOT NULL DEFAULT 0 CHECK (is_holding IN (0, 1)),
  note TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (code, market_id)
);

CREATE TABLE IF NOT EXISTS watchlist (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  stock_id INTEGER NOT NULL UNIQUE REFERENCES stocks(id) ON DELETE CASCADE,
  focus_reason TEXT NOT NULL,
  key_levels_text TEXT,
  invalid_condition TEXT,
  status TEXT NOT NULL,
  added_on TEXT NOT NULL,
  updated_on TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS holdings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  stock_id INTEGER NOT NULL UNIQUE REFERENCES stocks(id) ON DELETE CASCADE,
  cost_price REAL NOT NULL,
  quantity INTEGER NOT NULL,
  available_quantity INTEGER,
  position_pct REAL,
  plan_cycle TEXT,
  stop_loss REAL,
  take_profit_condition TEXT,
  current_strategy TEXT,
  last_price REAL,
  unrealized_return_pct REAL,
  updated_on TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS key_levels (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
  level_price REAL NOT NULL,
  level_type TEXT NOT NULL,
  source TEXT,
  active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
  created_on TEXT NOT NULL DEFAULT (date('now'))
);

CREATE TABLE IF NOT EXISTS trade_preferences (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item TEXT NOT NULL UNIQUE,
  current_record TEXT NOT NULL,
  updated_on TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_strategies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  strategy_date TEXT NOT NULL,
  stock_id INTEGER REFERENCES stocks(id) ON DELETE CASCADE,
  scope TEXT NOT NULL DEFAULT '个股',
  strategy TEXT NOT NULL,
  trigger_condition TEXT,
  risk_control TEXT,
  review_result TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS shortline_scores (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  score_date TEXT NOT NULL,
  stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
  stock_type TEXT NOT NULL,
  score INTEGER NOT NULL CHECK (score BETWEEN 0 AND 100),
  core_reason TEXT,
  action TEXT,
  data_scope TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (score_date, stock_id)
);

CREATE TABLE IF NOT EXISTS price_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  stock_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
  snapshot_at TEXT NOT NULL,
  price REAL,
  change_pct REAL,
  volume_amount REAL,
  source TEXT,
  note TEXT
);

CREATE TABLE IF NOT EXISTS automation_tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_type TEXT NOT NULL,
  beijing_time TEXT NOT NULL,
  paris_time TEXT NOT NULL,
  frequency TEXT NOT NULL,
  task TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1))
);

CREATE TABLE IF NOT EXISTS update_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  log_date TEXT NOT NULL,
  content TEXT NOT NULL,
  source TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_stocks_code ON stocks(code);
CREATE INDEX IF NOT EXISTS idx_watchlist_status ON watchlist(status);
CREATE INDEX IF NOT EXISTS idx_holdings_stock ON holdings(stock_id);
CREATE INDEX IF NOT EXISTS idx_daily_strategies_date ON daily_strategies(strategy_date);
CREATE INDEX IF NOT EXISTS idx_scores_date_score ON shortline_scores(score_date, score DESC);
CREATE INDEX IF NOT EXISTS idx_price_snapshots_stock_time ON price_snapshots(stock_id, snapshot_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_price_snapshots_unique_source_time
  ON price_snapshots(stock_id, snapshot_at, source);

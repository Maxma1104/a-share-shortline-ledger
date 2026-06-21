# A-Share Shortline Ledger

[中文](#中文) | [English](#english)

## 中文

一个面向 A 股短线交易的本地台账和公开关注池项目。

核心原则很简单：先刷新数据，再看关注池；先排除低质量机会，再谈策略。项目不会公开个人持仓、成本价、仓位数量或私人复盘，只公开脱敏后的关注池和行情快照。

### 公开入口

- 在线首页：<https://maxma1104.github.io/a-share-shortline-ledger/>
- 关注池 Markdown：[docs/watchlist.md](docs/watchlist.md)
- 关注池 JSON：[docs/data/watchlist_latest.json](docs/data/watchlist_latest.json)

公开关注池每天北京时间 **18:00** 自动刷新。普通用户不需要配置 Token，不需要运行脚本，直接打开入口即可查看。

### 项目做什么

1. **关注池每日更新**
   每天中国时间 18:00 刷新公开关注池，输出 `重点关注`、`观察`、`低频观察`、`剔除候选` 等状态，方便快速检查市场短线候选。

2. **本地交易台账**
   使用 SQLite 记录关注池、持仓、关键价位、短线评分、行情快照和复盘日志。真实数据库默认只保存在本地。

3. **数据新鲜度门禁**
   分析前必须运行 `scripts/refresh_before_analysis.py`。只有输出 `Status: OK`，才允许基于数据库做进一步判断。

4. **双语介绍**
   README 和网页首页同时提供中文与英文说明，方便中文用户使用，也方便海外开发者理解项目结构。

### 快速开始

只看公开关注池：

```bash
open docs/watchlist.md
```

本地创建公开演示库：

```bash
sqlite3 stock_tracking.public.db < db/schema.sql
sqlite3 stock_tracking.public.db < db/public_seed.sql
python3 scripts/refresh_before_analysis.py --db stock_tracking.public.db --skip-sync
```

维护者刷新实时行情：

```bash
export RSSCAST_MCP_TOKEN="你的 RssCast MCP Token"
python3 scripts/refresh_before_analysis.py --db stock_tracking.db
python3 scripts/export_public_seed.py --db stock_tracking.db --output db/public_seed.sql
python3 scripts/export_public_watchlist.py --db stock_tracking.db
```

### 隐私边界

公开仓库不会提交：

- `stock_tracking.db`
- `db/seed.sql`
- `股票跟踪台账.md`
- `.env`
- 成本价、持仓数量、可用数量、仓位比例、私人复盘

公开数据只包含关注池股票、状态、关键价位、失效条件、公开行情快照和脱敏说明。

### 数据来源

行情来源：RSSCAST，<https://app-cn.rsscast.io>

免责声明：本项目是交易记录和风险控制工具，不构成投资建议。关注池不是买入建议，任何交易决策都需要自行承担风险。

---

## English

A local-first A-share shortline trading ledger with a public, anonymized watchlist.

The core workflow is simple: refresh data first, inspect the watchlist second, and remove low-quality opportunities before discussing tactics. The public repository does not expose personal positions, cost basis, position size, or private trading logs.

### Public Entry

- Live homepage: <https://maxma1104.github.io/a-share-shortline-ledger/>
- Watchlist Markdown: [docs/watchlist.md](docs/watchlist.md)
- Watchlist JSON: [docs/data/watchlist_latest.json](docs/data/watchlist_latest.json)

The public watchlist refreshes every day at **18:00 China Standard Time**. Visitors do not need tokens, setup, or local scripts.

### What It Does

1. **Daily public watchlist**
   Publishes an anonymized A-share watchlist with statuses such as `High Focus`, `Watch`, `Low Frequency`, and `Removal Candidate`.

2. **Local trading ledger**
   Uses SQLite to track watchlists, positions, key levels, shortline scores, market snapshots, and review logs. Private data stays local by default.

3. **Freshness gate**
   Analysis must run through `scripts/refresh_before_analysis.py`. Only `Status: OK` should be treated as a valid database state.

4. **Bilingual documentation**
   The repository homepage and README support Chinese and English.

### Quick Start

Read the public watchlist:

```bash
open docs/watchlist.md
```

Build a public demo database:

```bash
sqlite3 stock_tracking.public.db < db/schema.sql
sqlite3 stock_tracking.public.db < db/public_seed.sql
python3 scripts/refresh_before_analysis.py --db stock_tracking.public.db --skip-sync
```

Maintainer live refresh:

```bash
export RSSCAST_MCP_TOKEN="your RssCast MCP token"
python3 scripts/refresh_before_analysis.py --db stock_tracking.db
python3 scripts/export_public_seed.py --db stock_tracking.db --output db/public_seed.sql
python3 scripts/export_public_watchlist.py --db stock_tracking.db
```

### Privacy Boundary

The public repository excludes:

- `stock_tracking.db`
- `db/seed.sql`
- `股票跟踪台账.md`
- `.env`
- cost basis, position size, available shares, allocation, and private reviews

Public exports contain only watchlist symbols, status, key levels, invalidation conditions, public market snapshots, and sanitized notes.

### Data Source

Market data source: RSSCAST, <https://app-cn.rsscast.io>

Disclaimer: This project is a trading journal and risk-control tool, not investment advice. The public watchlist is not a buy recommendation.

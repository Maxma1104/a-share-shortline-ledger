# 项目规则

## 股票分析前置检查

在回答任何持仓、关注池、短线策略、止损止盈、盘前/盘中/盘后复盘相关问题前，必须先运行：

```bash
python3 scripts/refresh_before_analysis.py
```

只有当脚本输出 `Status: OK` 后，才可以基于数据库进行分析。

如果刷新失败、RssCast MCP 不可用、token 缺失，或脚本提示缺失行情快照，必须明确告诉用户数据不新鲜，不能给出确定性交易结论。

## 关注池刷新

盘中复盘、收盘复盘和任何涉及新机会筛选的报告，必须包含关注池刷新结论。

刷新入口仍是：

```bash
python3 scripts/refresh_before_analysis.py
```

该入口会调用 `scripts/refresh_watchlist.py`，按成交额、振幅、涨跌幅、5日成交额变化和既定失效条件，把关注池股票标记为：

- `重点关注`
- `观察`
- `低频观察`
- `剔除候选`

状态包含 `低频观察` 或 `剔除候选` 的股票，不得作为主动新开仓推荐；除非用户明确要求复核，否则只保留为复盘证据或低频跟踪。

数据来源需标明：RSSCAST，https://app-cn.rsscast.io

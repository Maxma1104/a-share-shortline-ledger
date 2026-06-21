# Contributing

The best contributions make the public watchlist easier to verify and the private ledger harder to misuse.

Good first areas:

- Improve bilingual documentation.
- Improve `scripts/export_public_watchlist.py` output.
- Add tests for public seed generation.
- Add a small dashboard feature without exposing private holdings.

Run local checks before opening a pull request:

```bash
python3 -m py_compile scripts/*.py
sqlite3 stock_tracking.public.db < db/schema.sql
sqlite3 stock_tracking.public.db < db/public_seed.sql
python3 scripts/refresh_before_analysis.py --db stock_tracking.public.db --skip-sync
python3 scripts/export_public_watchlist.py --db stock_tracking.public.db
```

Do not submit private trading data, brokerage screenshots, tokens, cost basis, position size, or private review logs.

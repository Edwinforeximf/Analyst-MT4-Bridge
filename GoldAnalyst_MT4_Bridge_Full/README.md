# Gold Analyst Pro — Multi Asset + MT4 Live Feed

**Six instruments:** XAUUSD, XAGUSD, EURUSD, GBPUSD, USDJPY, USOIL.

- `app.py`: FastAPI login, dashboard, report, review, export DOCX
- `mt4_feed.py`: secure HTTPS endpoint MT4 ingestion and read-only feed cache
- `mt4/GoldAnalyst_MT4_Feed.mq4`: MetaTrader 4 Expert Advisor, **read only**
- `market_feed.py`: optional Twelve Data fallback, selected by `LIVE_PROVIDER=TWELVE`
- `live_engine.py`: indicator-based directional scenarios, not market order execution
- `static/index.html`: responsive dashboard with MT4 quote / symbol, spread, PNG / DOCX
- `README_MT4.md`: installation instructions for Windows MT4 and Railway

No broker credentials or trading permissions are transmitted. Use a **demo terminal** initially, subject to broker data distribution terms. Retain your existing Railway persistent volume `/data` and APP_SECRET when upgrading. Source is not a compiled EA or installed APK; it must be compiled in MetaEditor on Windows and deployed to Railway before use.

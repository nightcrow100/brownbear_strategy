# Interactive Brokers Trend‐Capture Bot (macOS GUI)

A simple, rule‑based trading bot for blue‑chip stocks and ETFs, wrapped in a lightweight Tkinter GUI for macOS. Scan your watchlist on a fixed schedule, spot “golden‑cross” trend entries, filter by RSI/MACD/volume/price patterns, and manage dry‑run vs. live orders via Interactive Brokers (IBKR) using the `ib_async` library.

---

## 🚀 Features

- **Technical filters**:  
  - 50‑day vs. 200‑day Moving Average crossover (“Golden Cross”)  
  - RSI band entry (default 40–60) and exit (> 70)  
  - MACD confirmation  
  - Volume spike (20‑day average)  
  - Bullish/bearish engulfing candlesticks  
  - Profit‐target & stop‑loss orders  

- **Dry‑run mode** to see “would BUY/SELL” logs without placing real orders  
- **Async scheduler** (via APScheduler) to scan every N minutes  
- **Interactive GUI**:  
  - Edit host/port, tickers, indicators, profit/stop parameters  
  - Start/Stop buttons to control the bot  
  - Live log window for INFO & DEBUG output  

---

## 📦 Dependencies

- Python 3.8+  
- [ib_async](https://pypi.org/project/ib-async/)  
- pandas  
- TA‑Lib wrapper: [ta](https://pypi.org/project/ta/)  
- APScheduler  
- Tkinter (included with macOS Python)  

Install with:

```bash
pip install ib_async pandas ta apscheduler


import asyncio
import threading
import logging
from datetime import datetime
import tkinter as tk
from tkinter import scrolledtext, messagebox

import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import MACD
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ib_async.ib import IB
from ib_async.contract import Stock
from ib_async.order import MarketOrder

# ─── DEFAULT PARAMETERS FOR GUI FIELDS ─────────────────────────────────────────
DEFAULT_PARAMS = {
    "IB_HOST":                   "127.0.0.1",
    "IB_PORT":                   7496,
    "CLIENT_ID":                 1,
    "TICKERS":                   "SPY,TSLA,AAPL,VOO,NVDA,QQQ,SOUN,RKLB,SMCI,BABA,RDDT,VZ,RGTI,AMD,HOOD,HIMS,TEM",
    "SCHEDULE_INTERVAL_MINUTES": 1,
    "FAST_MA":                   50,
    "SLOW_MA":                   200,
    "RSI_LOW":                   40,
    "RSI_HIGH":                  60,
    "RSI_EXIT":                  70,
    "PROFIT_TARGET":             0.05,
    "STOP_LOSS_PCT":             0.02,
    "HIST_DAYS":                 300,
    "DRY_RUN":                   True,
}
# ────────────────────────────────────────────────────────────────────────────────

# ─── TRADING BOT IMPLEMENTATION ────────────────────────────────────────────────
class TradingBot:
    def __init__(self, params):
        self.params = params
        self.ib = None
        self.scheduler = None

    async def compute_indicators(self, df):
        p = self.params
        df["MA_FAST"]   = df["close"].rolling(p["FAST_MA"]).mean()
        df["MA_SLOW"]   = df["close"].rolling(p["SLOW_MA"]).mean()
        df["VOL20"]     = df["volume"].rolling(20).mean()
        df["RSI"]       = RSIIndicator(df["close"], window=14).rsi()
        macd            = MACD(df["close"],
                               window_slow=26,
                               window_fast=12,
                               window_sign=9)
        df["MACD"]      = macd.macd()
        df["MACD_SIG"]  = macd.macd_signal()
        return df.dropna()

    async def fetch_data(self, symbol):
        p = self.params
        contract = Stock(symbol=symbol,
                         exchange=p["EXCHANGE"],
                         currency=p["CURRENCY"])
        bars = await self.ib.reqHistoricalDataAsync(
            contract,
            endDateTime='',
            durationStr=f"{p['HIST_DAYS']} D",
            barSizeSetting="1 day",
            whatToShow="TRADES",
            useRTH=True
        )
        df = pd.DataFrame([
            {"date": b.date, "open": b.open, "high": b.high,
             "low": b.low,  "close": b.close, "volume": b.volume}
            for b in bars
        ]).set_index("date")
        return await self.compute_indicators(df)

    def get_position_info(self, positions, symbol):
        p = self.params
        for pos in positions:
            if pos.contract.symbol == symbol and pos.contract.currency == p["CURRENCY"]:
                return int(pos.position), pos.avgCost
        return 0, 0.0

    async def check_signals(self):
        p = self.params
        positions = await self.ib.reqPositionsAsync()

        for sym in p["TICKERS"]:
            df = await self.fetch_data(sym)
            if len(df) < 2:
                continue
            last, prev = df.iloc[-1], df.iloc[-2]
            qty, avg_cost = self.get_position_info(positions, sym)
            logger.info(f"[{sym}] Position: {qty} @ {avg_cost:.2f}")

            # BUY logic
            golden   = prev["MA_FAST"] <= prev["MA_SLOW"] and last["MA_FAST"] > last["MA_SLOW"]
            rsi_ok   = p["RSI_LOW"] <= last["RSI"] <= p["RSI_HIGH"]
            breakout = last["close"] > prev["high"]
            macd_ok  = last["MACD"] > last["MACD_SIG"]
            vol_ok   = last["volume"] > last["VOL20"]
            engulf   = (last["close"] > last["open"]
                        and prev["close"] < prev["open"]
                        and last["close"] > prev["open"]
                        and last["open"] < prev["close"])
            buy_conds = [golden, rsi_ok, breakout, macd_ok, vol_ok, engulf]

            logger.debug(f"[{sym}] BUY conds → {buy_conds}")
            if all(buy_conds) and qty == 0:
                logger.info(f"[{sym}] >>> BUY @ {last['close']:.2f}")
                if not p["DRY_RUN"]:
                    contract = Stock(sym, p["EXCHANGE"], p["CURRENCY"])
                    order = MarketOrder("BUY", p["SHARES_PER_TRADE"])
                    self.ib.placeOrder(contract, order)
                else:
                    logger.info(f"[{sym}] DRY-RUN BUY {p['SHARES_PER_TRADE']}")

            # SELL logic
            if qty > 0:
                tp_hit = last["close"] >= avg_cost * (1 + p["PROFIT_TARGET"])
                sl_hit = last["close"] <= avg_cost * (1 - p["STOP_LOSS_PCT"])
                bearish_eng = (last["volume"] > last["VOL20"]
                               and not engulf)
                other_exits = [
                    last["RSI"] > p["RSI_EXIT"],
                    last["close"] < last["MA_FAST"],
                    last["MACD"] < last["MACD_SIG"],
                    bearish_eng
                ]
                if tp_hit or sl_hit or any(other_exits):
                    reasons = []
                    if tp_hit:    reasons.append("TP")
                    if sl_hit:    reasons.append("SL")
                    for cond, name in zip(other_exits,
                                          ["RSI","MA","MACD","VolBear"]):
                        if cond: reasons.append(name)
                    logger.info(f"[{sym}] >>> SELL @ {last['close']:.2f}; {reasons}")
                    if not p["DRY_RUN"]:
                        contract = Stock(sym, p["EXCHANGE"], p["CURRENCY"])
                        order = MarketOrder("SELL", qty)
                        self.ib.placeOrder(contract, order)
                    else:
                        logger.info(f"[{sym}] DRY-RUN SELL {qty}")

    async def _run(self):
        p = self.params
        self.ib = IB()
        await self.ib.connectAsync(p["IB_HOST"], p["IB_PORT"], p["CLIENT_ID"])
        logger.info("Connected to IB")
        loop = asyncio.get_running_loop()
        self.scheduler = AsyncIOScheduler(event_loop=loop)
        self.scheduler.add_job(
            self.check_signals,
            "interval",
            minutes=p["SCHEDULE_INTERVAL_MINUTES"],
            next_run_time=datetime.now()
        )
        self.scheduler.start()
        logger.info(f"Scheduler every {p['SCHEDULE_INTERVAL_MINUTES']} min")
        await asyncio.Event().wait()

    def start(self):
        if hasattr(self, "_thread") and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=lambda: asyncio.run(self._run()),
            daemon=True
        )
        self._thread.start()

    def stop(self):
        if self.scheduler:
            self.scheduler.shutdown(wait=False)
        if self.ib:
            try: asyncio.run(self.ib.disconnectAsync())
            except: pass
        logger.info("Bot stopped")
# ────────────────────────────────────────────────────────────────────────────────

# ─── GUI APPLICATION ────────────────────────────────────────────────────────────
class TextHandler(logging.Handler):
    def __init__(self, text_widget):
        super().__init__()
        self.text = text_widget

    def emit(self, record):
        msg = self.format(record)
        self.text.after(0, lambda: (
            self.text.insert(tk.END, msg + "\n"),
            self.text.see(tk.END)
        ))

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Trading Bot GUI")
        self.bot = None

        # Parameter frame
        frm = tk.Frame(self)
        frm.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)
        self.entries = {}
        fields = [
            ("IB Host", "IB_HOST"), ("IB Port", "IB_PORT"),
            ("Client ID", "CLIENT_ID"),
            ("Tickers (comma)", "TICKERS"),
            ("Interval (min)", "SCHEDULE_INTERVAL_MINUTES"),
            ("Fast MA", "FAST_MA"), ("Slow MA", "SLOW_MA"),
            ("RSI Low", "RSI_LOW"), ("RSI High", "RSI_HIGH"),
            ("RSI Exit", "RSI_EXIT"),
            ("Profit %", "PROFIT_TARGET"), ("Stop Loss %", "STOP_LOSS_PCT"),
            ("History Days", "HIST_DAYS"), ("Dry Run", "DRY_RUN")
        ]
        for i, (label, key) in enumerate(fields):
            tk.Label(frm, text=label).grid(row=i, column=0, sticky=tk.W, pady=2)
            ent = tk.Entry(frm, width=40)
            ent.grid(row=i, column=1, pady=2)
            default = DEFAULT_PARAMS.get(key, "")
            ent.insert(0, str(default))
            self.entries[key] = ent

        # Buttons
        btn_frm = tk.Frame(self)
        btn_frm.pack(fill=tk.X, padx=5, pady=5)
        tk.Button(btn_frm, text="Start", command=self.start_bot).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frm, text="Stop",  command=self.stop_bot).pack(side=tk.LEFT, padx=5)

        # Log window
        self.log = scrolledtext.ScrolledText(self, height=20)
        self.log.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Hook up logging
        handler = TextHandler(self.log)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(handler)

    def start_bot(self):
        try:
            p = {}
            for key, ent in self.entries.items():
                val = ent.get().strip()
                if key == "TICKERS":
                    p[key] = [t.strip().upper() for t in val.split(",") if t.strip()]
                elif key in {"IB_PORT","CLIENT_ID","FAST_MA","SLOW_MA",
                             "RSI_LOW","RSI_HIGH","RSI_EXIT",
                             "HIST_DAYS","SCHEDULE_INTERVAL_MINUTES"}:
                    p[key] = int(val)
                elif key in {"PROFIT_TARGET","STOP_LOSS_PCT"}:
                    p[key] = float(val)
                elif key == "DRY_RUN":
                    p[key] = val.lower() in ("1","true","yes","y")
                else:
                    p[key] = val
            # fill in constants
            p.update({
                "EXCHANGE": "SMART",
                "CURRENCY": "USD",
                "SHARES_PER_TRADE": 1
            })
            self.bot = TradingBot(p)
            self.bot.start()
            logger.info("Bot starting...")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def stop_bot(self):
        if self.bot:
            self.bot.stop()
            logger.info("Stopping bot...")

# ─── MAIN ENTRYPOINT ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # configure root logger
    logger = logging.getLogger("trade_bot")
    logger.setLevel(logging.INFO)
    fh = logging.FileHandler("trade_bot.log")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(fh)

    app = App()
    app.geometry("900x600")
    app.mainloop()
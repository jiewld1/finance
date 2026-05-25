"""
StockLens Backend — FastAPI 数据服务
每日自动缓存，复盘分析，买卖点+情绪
"""
import os
import sys
import json
import time
import threading
import traceback
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

# ── 清理代理 ──
for k in list(os.environ.keys()):
    if k.upper() in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy"):
        os.environ.pop(k, None)
os.environ.setdefault("NO_PROXY", "*")

# ── requests 不走代理 ──
try:
    import requests as _req
    _o = _req.Session.__init__
    def _p(self, *a, **kw):
        _o(self, *a, **kw)
        self.trust_env = False
    _req.Session.__init__ = _p
except Exception:
    pass

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# ── 数据库 ──
import sqlite3

DB = os.path.join(os.path.dirname(__file__), "stock_cache.db")

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS idx_cache (
            code TEXT, name TEXT, market TEXT, price REAL, change_pct REAL,
            change_amt REAL, high REAL, low REAL, open REAL, prev_close REAL,
            volume REAL, updated TEXT, PRIMARY KEY (code, market)
        );
        CREATE TABLE IF NOT EXISTS sector_cache (
            code TEXT PRIMARY KEY, name TEXT, change_pct REAL,
            net_inflow REAL, turnover REAL, updated TEXT
        );
        CREATE TABLE IF NOT EXISTS flow_cache (
            type TEXT PRIMARY KEY, net_inflow REAL, updated TEXT
        );
        CREATE TABLE IF NOT EXISTS news_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, url TEXT,
            source TEXT, time TEXT, summary TEXT, sentiment TEXT, fetched TEXT
        );
        CREATE TABLE IF NOT EXISTS watchlist (
            code TEXT PRIMARY KEY, name TEXT, market TEXT DEFAULT 'A',
            added_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, value TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_news_fetched ON news_cache(fetched);
    """)
    conn.commit()
    conn.close()

# ── 数据源 ──

def _get_baostock():
    import baostock as bs
    bs.login()
    return bs

_bs = None
_bs_lock = threading.Lock()

def _bs_query(code, fields, start, end, freq="d", adjust="2"):
    global _bs
    with _bs_lock:
        if _bs is None:
            _bs = _get_baostock()
    rs = _bs.query_history_k_data_plus(code, fields, start_date=start, end_date=end,
                                        frequency=freq, adjustflag=adjust)
    if rs.error_code != "0":
        raise Exception(rs.error_msg)
    return rs.get_data()

_A_INDEX_MAP = {
    "sh.000001": "上证指数", "sz.399001": "深证成指", "sz.399006": "创业板指",
    "sh.000688": "科创50", "sh.000300": "沪深300", "sh.000905": "中证500",
}

def _ensure_ak():
    try:
        import akshare
        return akshare
    except Exception:
        return None

# ── 数据获取 ──

def fetch_a_indices() -> list[dict]:
    fields = "date,code,open,high,low,close,preclose,volume,amount,pctChg"
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    results = []
    for bs_code, name in _A_INDEX_MAP.items():
        try:
            df = _bs_query(bs_code, fields, start, end)
            if df.empty:
                continue
            df = df[df["tradestatus"] == "1"] if "tradestatus" in df.columns else df
            if df.empty:
                continue
            r = df.iloc[-1]
            close = float(r["close"])
            pre = float(r["preclose"])
            results.append({
                "code": bs_code.replace("sh.", "").replace("sz.", ""),
                "name": name, "market": "A",
                "price": close, "change_pct": float(r["pctChg"]),
                "change_amt": close - pre,
                "high": float(r["high"]), "low": float(r["low"]),
                "open": float(r["open"]), "prev_close": pre,
                "volume": float(r.get("amount", 0)) / 1e8,
            })
        except Exception:
            continue
    return results

def fetch_hk_indices() -> list[dict]:
    ak = _ensure_ak()
    if not ak:
        return []
    # Try multiple sources
    sources = [
        ("stock_hk_index_spot_em", None),   # EastMoney (faster)
        ("stock_hk_index_spot_sina", ["代码", "名称", "最新价", "涨跌额", "涨跌幅", "昨收", "今开", "最高", "最低"]),
    ]
    for fn_name, col_names in sources:
        try:
            fn = getattr(ak, fn_name, None)
            if not fn:
                continue
            if col_names:
                df = fn()
            else:
                df = fn()
            if df.empty:
                continue
            targets = {"HSI", "HSCEI", "HSTECH", "HSCCI"}
            results = []
            for _, r in df.iterrows():
                code = str(r.iloc[0])
                name = str(r.iloc[1])
                if code in targets or any(t in name for t in ["恒生指数", "恒生科技", "国企指数", "恒生中国企业", "恒生香港中资"]):
                    try:
                        results.append({
                            "code": code, "name": name, "market": "HK",
                            "price": float(r.iloc[2]),
                            "change_pct": float(r.iloc[4]) if col_names else float(r.iloc[3]),
                            "change_amt": float(r.iloc[3]) if col_names else float(r.iloc[4]),
                            "high": float(r.iloc[7]) if col_names and len(df.columns) > 7 else float(r.iloc[5]),
                            "low": float(r.iloc[8]) if col_names and len(df.columns) > 8 else float(r.iloc[6]),
                            "open": float(r.iloc[6]) if col_names and len(df.columns) > 6 else float(r.iloc[5]),
                            "prev_close": float(r.iloc[5]) if col_names and len(df.columns) > 5 else 0,
                            "volume": 0,
                        })
                    except (ValueError, KeyError, TypeError, IndexError):
                        continue
            if results:
                return results
        except Exception:
            continue
    return []

def fetch_capital_flow() -> list[dict]:
    ak = _ensure_ak()
    if not ak:
        return []
    flows = []
    for symbol, ftype in [("北向资金", "north"), ("南向资金", "south")]:
        try:
            df = ak.stock_hsgt_hist_em(symbol=symbol)
            if df.empty:
                continue
            last = df.iloc[-1]
            net_val = 0
            for col in df.columns:
                if '净买额' in str(col) or '净流入' in str(col):
                    v = last.get(col)
                    if v and not (isinstance(v, float) and pd.isna(v)):
                        net_val = float(v)
                    break
            flows.append({"type": ftype, "net_inflow": net_val})
        except Exception:
            continue
    return flows

def fetch_sectors() -> list[dict]:
    ak = _ensure_ak()
    if not ak:
        return []
    # Try multiple sources: EM board_change (push2ex), then EM concept spot (push2)
    for fn_name in ["stock_board_change_em", "stock_board_concept_name_em"]:
        try:
            fn = getattr(ak, fn_name, None)
            if not fn:
                continue
            df = fn()
            if df.empty:
                continue
            results = []
            for _, r in df.iterrows():
                try:
                    name_col = next((c for c in df.columns if '板块' in str(c) and '名称' in str(c)), df.columns[0])
                    chg_col = next((c for c in df.columns if '涨跌幅' in str(c) or 'change' in str(c).lower()), None)
                    flow_col = next((c for c in df.columns if '净流入' in str(c) or 'net' in str(c).lower()), None)
                    name = str(r[name_col])
                    results.append({
                        "code": str(r.get(df.columns[0], '')),
                        "name": name,
                        "change_pct": float(r[chg_col]) if chg_col and r[chg_col] is not None else 0,
                        "net_inflow": float(r[flow_col]) / 1e8 if flow_col and r[flow_col] is not None else 0,
                        "turnover": 0,
                    })
                except (ValueError, KeyError, TypeError, IndexError):
                    continue
            if results:
                return sorted(results, key=lambda x: x["net_inflow"], reverse=True)[:80]
        except Exception:
            continue
    return []

def fetch_news(limit: int = 30) -> list[dict]:
    ak = _ensure_ak()
    if not ak:
        return []
    items = []
    for fn_name in ["stock_info_global_em", "stock_news_em"]:
        try:
            fn = getattr(ak, fn_name, None)
            if not fn:
                continue
            df = fn()
            if df.empty:
                continue
            for _, r in df.head(limit).iterrows():
                title = url = src = tm = summary = ""
                for col in df.columns:
                    s = str(col)
                    v = str(r.get(col, ''))
                    if '标题' in s or s.lower() == 'title': title = v
                    elif '链接' in s or s.lower() == 'url': url = v
                    elif '时间' in s or '发布时间' in s: tm = v
                    elif '摘要' in s: summary = v
                    elif '来源' in s or s.lower() == 'source': src = v
                if title:
                    items.append({"title": title, "url": url, "source": src or "东方财富",
                                  "time": tm, "summary": summary})
            if items:
                break
        except Exception:
            continue
    return items[:limit]

def fetch_kline(code: str, market: str = "A", days: int = 250) -> list[dict]:
    if market == "HK":
        return _fetch_hk_kline(code, days)
    prefix = "sh." if code.startswith("6") else "sz."
    fields = "date,open,high,low,close,volume,amount,tradestatus"
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=days * 3)).strftime("%Y-%m-%d")
    try:
        df = _bs_query(f"{prefix}{code}", fields, start, end)
        if df.empty:
            return []
        if "tradestatus" in df.columns:
            df = df[df["tradestatus"] == "1"]
        df = df.tail(days)
        bars = []
        for _, r in df.iterrows():
            bars.append({
                "date": r["date"], "open": float(r["open"]), "high": float(r["high"]),
                "low": float(r["low"]), "close": float(r["close"]),
                "volume": float(r["volume"]), "amount": float(r.get("amount", 0)),
            })
        return bars
    except Exception:
        return []

def _fetch_hk_kline(code: str, days: int) -> list[dict]:
    ak = _ensure_ak()
    if not ak:
        return []
    try:
        df = ak.stock_hk_hist(symbol=code, period="daily", adjust="qfq")
        df = df.tail(days)
        bars = []
        for _, r in df.iterrows():
            d = r.get('日期', r.get('date', ''))
            ds = d.strftime('%Y-%m-%d') if hasattr(d, 'strftime') else str(d)[:10]
            bars.append({
                "date": ds,
                "open": float(r.get('开盘', r.get('open', 0))),
                "high": float(r.get('最高', r.get('high', 0))),
                "low": float(r.get('最低', r.get('low', 0))),
                "close": float(r.get('收盘', r.get('close', 0))),
                "volume": float(r.get('成交量', r.get('volume', 0))),
                "amount": float(r.get('成交额', r.get('amount', 0))),
            })
        return bars
    except Exception:
        return []

def fetch_stock_quote(code: str, market: str = "A") -> Optional[dict]:
    if market == "HK":
        return _fetch_hk_quote(code)
    prefix = "sh." if code.startswith("6") else "sz."
    fields = "date,open,high,low,close,preclose,volume,amount,turn,pctChg,peTTM,tradestatus"
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    try:
        df = _bs_query(f"{prefix}{code}", fields, start, end)
        if df.empty:
            return None
        df = df[df["tradestatus"] == "1"] if "tradestatus" in df.columns else df
        if df.empty:
            return None
        r = df.iloc[-1]
        pe = r.get("peTTM", "")
        return {
            "code": code, "name": code, "market": "A",
            "price": float(r["close"]), "change_pct": float(r["pctChg"]),
            "change_amt": float(r["close"]) - float(r["preclose"]),
            "volume": float(r["volume"]),
            "amount": float(r.get("amount", 0)) / 1e8,
            "high": float(r["high"]), "low": float(r["low"]),
            "open": float(r["open"]), "prev_close": float(r["preclose"]),
            "turnover_rate": float(r.get("turn", 0)) if r.get("turn") and r["turn"] != "" else 0,
            "pe": float(pe) if pe and pe != "" else None,
            "market_cap": None,
        }
    except Exception:
        return None

def _fetch_hk_quote(code: str) -> Optional[dict]:
    ak = _ensure_ak()
    if not ak:
        return None
    try:
        for fn_name in ["stock_hk_spot_em", "stock_hk_spot"]:
            try:
                fn = getattr(ak, fn_name, None)
                if not fn: continue
                df = fn()
                if df.empty: continue
                cc = next((c for c in df.columns if '代码' in str(c)), df.columns[0])
                nc = next((c for c in df.columns if '名称' in str(c)), df.columns[1])
                df[cc] = df[cc].astype(str)
                row = df[df[cc] == code]
                if row.empty: continue
                r = row.iloc[0]
                def _f(*ks):
                    for k in ks:
                        for c in df.columns:
                            if k in str(c):
                                v = r.get(c)
                                if v is not None and not (isinstance(v, float) and pd.isna(v)):
                                    return float(v)
                    return 0.0
                return {
                    "code": code, "name": str(r[nc]), "market": "HK",
                    "price": _f('最新价'), "change_pct": _f('涨跌幅'),
                    "change_amt": _f('涨跌额'), "volume": _f('成交量'),
                    "amount": _f('成交额') / 1e8,
                    "high": _f('最高'), "low": _f('最低'),
                    "open": _f('今开'), "prev_close": _f('昨收'),
                    "turnover_rate": _f('换手率'),
                    "pe": None, "market_cap": None,
                }
            except Exception:
                continue
    except Exception:
        pass
    return None

def search_stocks(keyword: str) -> list[dict]:
    results = []
    # A股
    global _bs
    with _bs_lock:
        if _bs is None:
            _bs = _get_baostock()
    try:
        rs = _bs.query_stock_basic()
        if rs.error_code == "0":
            df = rs.get_data()
            mask = df["code_name"].str.contains(keyword, na=False)
            for _, r in df[mask].head(15).iterrows():
                code = r["code"].replace("sh.", "").replace("sz.", "")
                results.append({"code": code, "name": r["code_name"], "market": "A"})
    except Exception:
        pass
    # 港股
    ak = _ensure_ak()
    if ak:
        try:
            for fn_name in ["stock_hk_spot_em", "stock_hk_spot"]:
                try:
                    fn = getattr(ak, fn_name, None)
                    if not fn: continue
                    df = fn()
                    if df.empty: continue
                    cc = next((c for c in df.columns if '代码' in str(c)), df.columns[0])
                    nc = next((c for c in df.columns if '名称' in str(c)), df.columns[1])
                    df[cc] = df[cc].astype(str); df[nc] = df[nc].astype(str)
                    mask = df[nc].str.contains(keyword, na=False) | df[cc].str.contains(keyword, na=False)
                    for _, r in df[mask].head(15).iterrows():
                        results.append({"code": str(r[cc]), "name": str(r[nc]), "market": "HK"})
                    break
                except Exception:
                    continue
        except Exception:
            pass
    return results[:40]

# ── 分析引擎 ──

def _ema(data: np.ndarray, period: int) -> np.ndarray:
    r = np.zeros_like(data); r[0] = data[0]
    a = 2 / (period + 1)
    for i in range(1, len(data)):
        r[i] = a * data[i] + (1 - a) * r[i - 1]
    return r

def _rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    d = np.diff(closes)
    g = np.where(d > 0, d, 0); l = np.where(d < 0, -d, 0)
    rs = np.zeros(len(closes))
    ag = np.mean(g[:period]); al = np.mean(l[:period])
    rs[period] = 100 - 100 / (1 + ag / al) if al else 100
    for i in range(period + 1, len(closes)):
        ag = (ag * (period - 1) + g[i - 1]) / period
        al = (al * (period - 1) + l[i - 1]) / period
        rs[i] = 100 - 100 / (1 + ag / al) if al else 100
    return rs

def _kdj(closes, highs, lows, n=9):
    k = np.zeros(len(closes)); d = np.zeros(len(closes)); j = np.zeros(len(closes))
    for i in range(n - 1, len(closes)):
        hh = np.max(highs[i - n + 1:i + 1]); ll = np.min(lows[i - n + 1:i + 1])
        rsv = (closes[i] - ll) / (hh - ll) * 100 if hh != ll else 50
        if i == n - 1: k[i] = d[i] = rsv
        else:
            k[i] = 2 / 3 * k[i - 1] + 1 / 3 * rsv
            d[i] = 2 / 3 * d[i - 1] + 1 / 3 * k[i]
        j[i] = 3 * k[i] - 2 * d[i]
    return k, d, j

def analyze_sentiment(news: list[dict]) -> dict:
    """简单情绪分析"""
    pos_words = ["涨", "升", "利好", "增长", "突破", "盈利", "回购", "增持", "反弹", "新高",
                 "买入", "看好", "超预期", "涨停", "牛市", "复苏", "扩张", "分红"]
    neg_words = ["跌", "降", "利空", "下滑", "亏损", "减持", "风险", "崩盘", "新低",
                 "卖出", "看空", "低于预期", "跌停", "熊市", "衰退", "裁员", "暴雷"]
    pos = sum(1 for n in news for w in pos_words if w in n.get("title", ""))
    neg = sum(1 for n in news for w in neg_words if w in n.get("title", ""))
    total = len(news) or 1
    if pos > neg:
        label, score = "偏乐观", min(100, 50 + (pos - neg) * 5)
    elif neg > pos:
        label, score = "偏悲观", max(0, 50 - (neg - pos) * 5)
    else:
        label, score = "中性", 50
    return {"label": label, "score": score, "positive": pos, "negative": neg, "total": total}

def run_analysis(code: str, market: str = "A") -> dict:
    quote = fetch_stock_quote(code, market)
    if not quote:
        return {"error": f"无法获取 {code} 行情数据"}
    bars = fetch_kline(code, market, 250)
    news = fetch_news(20)
    sentiment = analyze_sentiment(news)

    result = {
        "quote": quote,
        "sentiment": sentiment,
        "news": news[:10],
        "signals": [],
        "indicators": {},
        "verdict": {"label": "数据不足", "score": 0, "color": "#888"},
    }

    if len(bars) < 20:
        return result

    closes = np.array([b["close"] for b in bars])
    highs = np.array([b["high"] for b in bars])
    lows = np.array([b["low"] for b in bars])
    vols = np.array([b["volume"] for b in bars])

    ma5 = np.mean(closes[-5:]); ma10 = np.mean(closes[-10:]); ma20 = np.mean(closes[-20:])
    ma60 = np.mean(closes[-60:]) if len(closes) >= 60 else ma20
    ema12 = _ema(closes, 12); ema26 = _ema(closes, 26)
    dif = ema12 - ema26; dea = _ema(dif, 9); macd_bar = 2 * (dif - dea)
    rsi = _rsi(closes); k, d, j = _kdj(closes, highs, lows)
    bb_mid = np.mean(closes[-20:]); bb_std = np.std(closes[-20:])
    bb_upper = bb_mid + 2 * bb_std; bb_lower = bb_mid - 2 * bb_std
    vol_ma5 = np.mean(vols[-5:]); vol_ma20 = np.mean(vols[-20:])
    vol_ratio = vol_ma5 / vol_ma20 if vol_ma20 else 1

    # 买卖信号
    signals = []
    if closes[-1] > ma5 and closes[-2] <= ma5:
        signals.append({"type": "buy", "msg": "股价上穿 MA5，短期转强"})
    if closes[-1] < ma5 and closes[-2] >= ma5:
        signals.append({"type": "sell", "msg": "股价下穿 MA5，短期转弱"})
    if len(dif) >= 2 and dif[-1] > dea[-1] and dif[-2] <= dea[-2]:
        signals.append({"type": "buy", "msg": "MACD 金叉，动能转多"})
    if len(dif) >= 2 and dif[-1] < dea[-1] and dif[-2] >= dea[-2]:
        signals.append({"type": "sell", "msg": "MACD 死叉，动能转空"})
    if rsi[-1] < 30:
        signals.append({"type": "buy", "msg": f"RSI={rsi[-1]:.0f} 超卖，反弹概率大"})
    if rsi[-1] > 70:
        signals.append({"type": "sell", "msg": f"RSI={rsi[-1]:.0f} 超买，警惕回调"})
    if closes[-1] > bb_upper:
        signals.append({"type": "warn", "msg": f"突破布林上轨 {bb_upper:.2f}"})
    if closes[-1] < bb_lower:
        signals.append({"type": "buy", "msg": f"跌破布林下轨 {bb_lower:.2f}，超跌"})
    if j[-1] > 100 and k[-1] > 80:
        signals.append({"type": "sell", "msg": "KDJ 高位钝化，超买"})
    if j[-1] < 0 and k[-1] < 20:
        signals.append({"type": "buy", "msg": "KDJ 低位钝化，超卖"})

    # 综合评分
    score = 0
    if ma5 > ma10 > ma20: score += 2
    elif ma5 < ma10 < ma20: score -= 2
    if dif[-1] > dea[-1]: score += 1
    else: score -= 1
    if macd_bar[-1] > 0: score += 1
    else: score -= 1
    if rsi[-1] < 30: score += 1
    elif rsi[-1] > 70: score -= 1
    if vol_ratio > 1.5 and closes[-1] > closes[-5]: score += 1
    elif vol_ratio > 1.5 and closes[-1] <= closes[-5]: score -= 1
    if closes[-1] > ma60: score += 1
    else: score -= 1
    # 情绪因子
    sent_bias = (sentiment["score"] - 50) / 10
    score += sent_bias

    if score >= 3: verdict = {"label": "买入", "score": round(score, 1), "color": "#ff4757"}
    elif score >= 1: verdict = {"label": "偏多", "score": round(score, 1), "color": "#ff6b81"}
    elif score <= -3: verdict = {"label": "卖出", "score": round(score, 1), "color": "#2ed573"}
    elif score <= -1: verdict = {"label": "偏空", "score": round(score, 1), "color": "#7bed9f"}
    else: verdict = {"label": "观望", "score": round(score, 1), "color": "#ffa502"}

    trend = "多头排列 ↑" if ma5 > ma10 > ma20 else "空头排列 ↓" if ma5 < ma10 < ma20 else "震荡整理 ↔"
    price_vs_ma60 = (closes[-1] - ma60) / ma60 * 100

    result["signals"] = signals
    result["indicators"] = {
        "ma5": round(ma5, 2), "ma10": round(ma10, 2), "ma20": round(ma20, 2),
        "ma60": round(ma60, 2), "trend": trend, "price_vs_ma60": round(price_vs_ma60, 1),
        "macd": round(float(macd_bar[-1]), 3), "dif": round(float(dif[-1]), 3),
        "dea": round(float(dea[-1]), 3),
        "rsi14": round(float(rsi[-1]), 1),
        "kdj_k": round(float(k[-1]), 1), "kdj_d": round(float(d[-1]), 1), "kdj_j": round(float(j[-1]), 1),
        "bb_upper": round(bb_upper, 2), "bb_mid": round(bb_mid, 2), "bb_lower": round(bb_lower, 2),
        "vol_ratio": round(float(vol_ratio), 2),
    }
    result["verdict"] = verdict
    result["bars"] = bars[-120:]  # 最近120根K线用于图表
    return result

# ── 缓存管理 ──

def _is_cache_stale(table: str) -> bool:
    conn = get_db()
    row = conn.execute(f"SELECT MAX(updated) as t FROM {table}").fetchone()
    conn.close()
    if not row or not row["t"]:
        return True
    try:
        last = datetime.strptime(row["t"][:10], "%Y-%m-%d")
        return last.date() < datetime.now().date()
    except Exception:
        return True

def _save_indices(data: list[dict], market: str):
    conn = get_db()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for d in data:
        conn.execute("""INSERT OR REPLACE INTO idx_cache (code,name,market,price,change_pct,
            change_amt,high,low,open,prev_close,volume,updated)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (d["code"], d["name"], market, d["price"], d["change_pct"], d["change_amt"],
             d["high"], d["low"], d["open"], d["prev_close"], d.get("volume", 0), now))
    conn.commit(); conn.close()

def _load_indices() -> list[dict]:
    conn = get_db()
    rows = conn.execute("SELECT * FROM idx_cache ORDER BY market, code").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def refresh_all_cache():
    """刷新所有缓存（每日调用）"""
    print(f"[{datetime.now():%H:%M:%S}] 开始刷新数据缓存...")
    try:
        a_idx = fetch_a_indices()
        _save_indices(a_idx, "A")
        print(f"  A股指数: {len(a_idx)} 条")
    except Exception as e:
        print(f"  A股指数失败: {e}")
    try:
        hk_idx = fetch_hk_indices()
        _save_indices(hk_idx, "HK")
        print(f"  港股指数: {len(hk_idx)} 条")
    except Exception as e:
        print(f"  港股指数失败: {e}")
    try:
        flows = fetch_capital_flow()
        conn = get_db(); now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for f in flows:
            conn.execute("INSERT OR REPLACE INTO flow_cache (type,net_inflow,updated) VALUES (?,?,?)",
                         (f["type"], f["net_inflow"], now))
        conn.commit(); conn.close()
        print(f"  资金流向: {len(flows)} 条")
    except Exception as e:
        print(f"  资金流向失败: {e}")
    try:
        sectors = fetch_sectors()
        conn = get_db(); now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for s in sectors:
            conn.execute("INSERT OR REPLACE INTO sector_cache (code,name,change_pct,net_inflow,turnover,updated) VALUES (?,?,?,?,?,?)",
                         (s["code"], s["name"], s["change_pct"], s["net_inflow"], s["turnover"], now))
        conn.commit(); conn.close()
        print(f"  板块: {len(sectors)} 条")
    except Exception as e:
        print(f"  板块失败: {e}")
    try:
        news = fetch_news(50)
        conn = get_db(); now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for n in news:
            sentiment = analyze_sentiment([n])
            conn.execute("""INSERT INTO news_cache (title,url,source,time,summary,sentiment,fetched)
                VALUES (?,?,?,?,?,?,?)""",
                (n["title"], n["url"], n["source"], n["time"], n["summary"],
                 sentiment["label"], now))
        # 清理旧新闻
        conn.execute("DELETE FROM news_cache WHERE fetched < ?", (now[:10],))
        conn.commit(); conn.close()
        print(f"  资讯: {len(news)} 条")
    except Exception as e:
        print(f"  资讯失败: {e}")
    print(f"[{datetime.now():%H:%M:%S}] 缓存刷新完成")

from contextlib import asynccontextmanager

# ── FastAPI 应用 ──

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if _is_cache_stale("idx_cache"):
        threading.Thread(target=refresh_all_cache, daemon=True).start()
    yield

app = FastAPI(title="StockLens API", version="2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.get("/api/indices")
def api_indices():
    data = _load_indices()
    if not data:
        # 缓存为空，实时获取
        a = fetch_a_indices(); _save_indices(a, "A")
        hk = fetch_hk_indices(); _save_indices(hk, "HK")
        data = _load_indices()
    return {"data": data, "count": len(data)}

@app.get("/api/sectors")
def api_sectors(sort: str = "net_inflow"):
    conn = get_db()
    rows = conn.execute("SELECT * FROM sector_cache").fetchall()
    conn.close()
    data = [dict(r) for r in rows]
    if sort in ("net_inflow", "change_pct", "turnover"):
        data.sort(key=lambda x: x.get(sort, 0) or 0, reverse=True)
    return {"data": data, "count": len(data)}

@app.get("/api/flow")
def api_flow():
    conn = get_db()
    rows = conn.execute("SELECT * FROM flow_cache").fetchall()
    conn.close()
    return {"data": [dict(r) for r in rows]}

@app.get("/api/news")
def api_news(limit: int = 20):
    conn = get_db()
    rows = conn.execute("SELECT * FROM news_cache ORDER BY fetched DESC, id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    data = []
    for r in rows:
        d = dict(r)
        d["id"] = d.pop("id", None)
        data.append(d)
    return {"data": data, "count": len(data)}

@app.get("/api/search")
def api_search(keyword: str = Query(..., min_length=1)):
    results = search_stocks(keyword)
    return {"data": results, "count": len(results)}

@app.get("/api/stock/{code}")
def api_stock_quote(code: str, market: str = "A"):
    q = fetch_stock_quote(code, market)
    if not q:
        raise HTTPException(404, f"未找到股票: {code}")
    return {"data": q}

@app.get("/api/kline/{code}")
def api_kline(code: str, market: str = "A", days: int = 250):
    bars = fetch_kline(code, market, days)
    return {"data": bars, "count": len(bars)}

@app.get("/api/analysis/{code}")
def api_analysis(code: str, market: str = "A"):
    result = run_analysis(code, market)
    return result

@app.get("/api/watchlist")
def api_watchlist_get():
    conn = get_db()
    rows = conn.execute("SELECT * FROM watchlist ORDER BY added_at DESC").fetchall()
    conn.close()
    return {"data": [dict(r) for r in rows]}

@app.post("/api/watchlist/{code}")
def api_watchlist_add(code: str, name: str = "", market: str = "A"):
    conn = get_db()
    cnt = conn.execute("SELECT COUNT(*) as c FROM watchlist").fetchone()["c"]
    if cnt >= 50:
        conn.close()
        raise HTTPException(400, "自选股已达上限(50只)")
    conn.execute("INSERT OR IGNORE INTO watchlist (code,name,market) VALUES (?,?,?)",
                 (code, name or code, market))
    conn.commit(); conn.close()
    return {"ok": True}

@app.delete("/api/watchlist/{code}")
def api_watchlist_remove(code: str):
    conn = get_db()
    conn.execute("DELETE FROM watchlist WHERE code=?", (code,))
    conn.commit(); conn.close()
    return {"ok": True}

@app.post("/api/refresh")
def api_refresh():
    threading.Thread(target=refresh_all_cache, daemon=True).start()
    return {"ok": True, "msg": "后台刷新中..."}

@app.get("/api/health")
def api_health():
    return {"status": "ok", "time": datetime.now().isoformat()}

def run_server(port: int = 8765):
    print(f"[StockLens] 后端启动: http://127.0.0.1:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")

if __name__ == "__main__":
    run_server()

"""
数据源抽象层：baostock（默认）/ akshare（可选）
baostock: 日线历史数据，TCP 直连，稳定可靠 → 复盘+预测
akshare: 实时行情+资讯，HTTP 接口 → 可选增强
"""
import time as _time

# 延迟导入 akshare 和代理修复标志
_akshare_available = None
_proxy_fixed = False


def _ensure_akshare():
    """惰性加载 akshare，同时修复代理问题"""
    global _akshare_available, _proxy_fixed
    if _akshare_available is not None:
        return _akshare_available
    try:
        import os as _os
        for _key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                      "http_proxy", "https_proxy", "all_proxy"):
            _os.environ.pop(_key, None)
        _os.environ.setdefault("NO_PROXY", "*")
        _os.environ.setdefault("no_proxy", "*")
        if not _proxy_fixed:
            try:
                import requests as _req
                _orig_init = _req.Session.__init__
                def _patched_init(self, *args, **kwargs):
                    _orig_init(self, *args, **kwargs)
                    self.trust_env = False
                _req.Session.__init__ = _patched_init
                _proxy_fixed = True
            except Exception:
                pass
        import akshare as _ak
        _akshare_available = _ak
        return _ak
    except Exception:
        _akshare_available = False
        return None
import threading as _threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd
from datetime import datetime, timedelta


@dataclass
class IndexData:
    """大盘指数"""
    code: str
    name: str
    price: float
    change_pct: float
    change_amt: float
    volume: float          # 成交额（亿）
    high: float
    low: float
    open: float
    prev_close: float


@dataclass
class SectorData:
    """板块数据"""
    code: str
    name: str
    change_pct: float
    net_inflow: float      # 主力净流入（亿），正=买入
    turnover: float        # 成交额（亿）
    top_stocks: list = field(default_factory=list)


@dataclass
class StockQuote:
    """个股行情"""
    code: str
    name: str
    price: float
    change_pct: float
    change_amt: float
    volume: float
    amount: float          # 成交额（亿）
    high: float
    low: float
    open: float
    prev_close: float
    turnover_rate: float   # 换手率
    pe: Optional[float] = None        # 市盈率
    market_cap: Optional[float] = None  # 总市值（亿）


@dataclass
class KlineBar:
    """K线数据"""
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float


@dataclass
class CapitalFlow:
    """资金流向"""
    type: str              # north / south
    net_inflow: float      # 净流入（亿）
    balance: float         # 累计余额（亿）


@dataclass
class NewsItem:
    """资讯条目"""
    title: str
    url: str
    source: str
    time: str
    summary: str = ""


# ============ 抽象基类 ============

class DataSource(ABC):
    """数据源抽象基类"""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def get_major_indices(self) -> list[IndexData]: ...

    @abstractmethod
    def get_hk_indices(self) -> list[IndexData]: ...

    @abstractmethod
    def get_sectors(self) -> list[SectorData]: ...

    @abstractmethod
    def get_capital_flow(self) -> list[CapitalFlow]: ...

    @abstractmethod
    def search_stock(self, keyword: str) -> list[dict]: ...

    @abstractmethod
    def get_stock_quote(self, code: str, market: str = "A") -> Optional[StockQuote]: ...

    @abstractmethod
    def get_kline(self, code: str, market: str = "A", period: str = "daily", count: int = 250) -> list[KlineBar]: ...

    @abstractmethod
    def get_news(self, code: str = "", limit: int = 20) -> list[NewsItem]: ...

    @abstractmethod
    def get_sector_stocks(self, sector_code: str) -> list[dict]: ...


# ============ Baostock 实现（默认） ============

# A股指数 baostock 代码映射
_A_INDEX_MAP = {
    "sh.000001": "上证指数",
    "sz.399001": "深证成指",
    "sz.399006": "创业板指",
    "sh.000688": "科创50",
    "sh.000300": "沪深300",
    "sh.000905": "中证500",
}


class BaostockSource(DataSource):
    """基于 baostock 的历史数据源，TCP 直连无 HTTP 指纹问题，稳定可靠"""

    def __init__(self):
        import baostock as bs
        self._bs = bs
        self._logged_in = False
        self._lock = _threading.Lock()
        self._stock_basic_df: Optional[pd.DataFrame] = None
        self._industry_df: Optional[pd.DataFrame] = None

    @property
    def name(self):
        return "baostock"

    def _login(self):
        with self._lock:
            if not self._logged_in:
                self._bs.login()
                self._logged_in = True

    def _logout(self):
        with self._lock:
            if self._logged_in:
                try:
                    self._bs.logout()
                except Exception:
                    pass
                self._logged_in = False

    def _query_kline(self, code: str, fields: str, start_date: str, end_date: str,
                     frequency: str = "d", adjustflag: str = "2") -> pd.DataFrame:
        self._login()
        rs = self._bs.query_history_k_data_plus(
            code, fields,
            start_date=start_date, end_date=end_date,
            frequency=frequency, adjustflag=adjustflag
        )
        if rs.error_code != "0":
            raise Exception(rs.error_msg)
        return rs.get_data()

    def _get_latest_bar(self, code: str, fields: str, days_back: int = 10) -> Optional[pd.Series]:
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        try:
            df = self._query_kline(code, fields, start, end, "d", "2")
            if df.empty:
                return None
            df = df[df["tradestatus"] == "1"] if "tradestatus" in df.columns else df
            if df.empty:
                return None
            return df.iloc[-1]
        except Exception:
            return None

    def _load_stock_basic(self):
        if self._stock_basic_df is not None:
            return
        self._login()
        try:
            rs = self._bs.query_stock_basic()
            if rs.error_code == "0":
                self._stock_basic_df = rs.get_data()
            else:
                self._stock_basic_df = pd.DataFrame()
        except Exception:
            self._stock_basic_df = pd.DataFrame()

    def _load_industry(self):
        if self._industry_df is not None:
            return
        self._login()
        try:
            rs = self._bs.query_stock_industry()
            if rs.error_code == "0":
                df = rs.get_data()
                # 只保留个股，过滤指数
                self._industry_df = df[df["code"].str.startswith(("sh.", "sz."))]
            else:
                self._industry_df = pd.DataFrame()
        except Exception:
            self._industry_df = pd.DataFrame()

    # ── 大盘指数 ──

    def get_major_indices(self) -> list[IndexData]:
        fields = "date,code,open,high,low,close,preclose,volume,amount,pctChg"
        result = []
        for bs_code, name in _A_INDEX_MAP.items():
            try:
                row = self._get_latest_bar(bs_code, fields, days_back=10)
                if row is None:
                    continue
                close = float(row["close"])
                preclose = float(row["preclose"])
                change_amt = close - preclose
                result.append(IndexData(
                    code=bs_code.replace("sh.", "").replace("sz.", ""),
                    name=name,
                    price=close,
                    change_pct=float(row["pctChg"]),
                    change_amt=change_amt,
                    volume=float(row.get("amount", 0)) / 1e8,
                    high=float(row["high"]),
                    low=float(row["low"]),
                    open=float(row["open"]),
                    prev_close=preclose,
                ))
            except Exception:
                continue
        return result

    def get_hk_indices(self) -> list[IndexData]:
        try:
            ak = _ensure_akshare()
            if not ak:
                return []
            df = None
            for func_name in ["stock_hk_index_spot_em", "stock_hk_index_spot"]:
                try:
                    fn = getattr(ak, func_name, None)
                    if fn:
                        df = fn()
                        if not df.empty:
                            break
                except Exception:
                    continue
            if df is None or df.empty:
                return []

            col_map = {}
            for col in df.columns:
                col_str = str(col)
                col_lower = col_str.lower()
                if '代码' in col_str or col_lower == 'code':
                    col_map['code'] = col
                elif '名称' in col_str or col_lower == 'name':
                    col_map['name'] = col
                elif '最新价' in col_str or col_lower == 'price':
                    col_map['price'] = col
                elif '涨跌幅' in col_str or 'change_pct' in col_lower:
                    col_map['change_pct'] = col
                elif '涨跌额' in col_str or 'change_amt' in col_lower:
                    col_map['change_amt'] = col
                elif '成交额' in col_str or 'volume' in col_lower:
                    col_map['volume'] = col
                elif '最高' in col_str or col_lower == 'high':
                    col_map['high'] = col
                elif '最低' in col_str or col_lower == 'low':
                    col_map['low'] = col
                elif '今开' in col_str or col_lower == 'open':
                    col_map['open'] = col
                elif '昨收' in col_str or 'prev_close' in col_lower:
                    col_map['prev_close'] = col

            targets = ["恒生指数", "恒生科技指数", "国企指数", "恒生中国企业指数"]
            result = []
            for _, row in df.iterrows():
                name = str(row.get(col_map.get('name', ''), ''))
                if any(t in name for t in targets) or '恒生' in name:
                    try:
                        result.append(IndexData(
                            code=str(row.get(col_map.get('code', ''), '')),
                            name=name,
                            price=float(row[col_map['price']]),
                            change_pct=float(row[col_map['change_pct']]),
                            change_amt=float(row[col_map['change_amt']]),
                            volume=float(row.get(col_map.get('volume', ''), 0)) / 1e8 if col_map.get('volume') and row.get(col_map['volume'], 0) else 0,
                            high=float(row[col_map['high']]),
                            low=float(row[col_map['low']]),
                            open=float(row[col_map['open']]),
                            prev_close=float(row[col_map['prev_close']]),
                        ))
                    except (ValueError, KeyError, TypeError):
                        continue
            return result
        except Exception as e:
            print(f"[Baostock→AKShare] 获取港股指数失败: {e}")
            return []

    # ── 板块 ──

    def get_sectors(self) -> list[SectorData]:
        """基于申万行业分类，返回行业列表（含股票数，涨跌幅需实时源）"""
        self._load_industry()
        df = self._industry_df
        if df is None or df.empty:
            return []

        grouped = df.groupby("industry")
        result = []
        for ind_name, group in grouped:
            if not ind_name or ind_name == "":
                continue
            result.append(SectorData(
                code=ind_name,
                name=ind_name,
                change_pct=0,
                net_inflow=0,
                turnover=0,
                top_stocks=[],
            ))

        result.sort(key=lambda x: x.name)
        return result

    def get_capital_flow(self) -> list[CapitalFlow]:
        try:
            ak = _ensure_akshare()
            if not ak:
                return []
            flows = []
            for symbol, flow_type in [("北向资金", "north"), ("南向资金", "south")]:
                try:
                    df = ak.stock_hsgt_hist_em(symbol=symbol)
                    if df.empty:
                        continue
                    last = df.iloc[-1]
                    net_val = None
                    for col in df.columns:
                        if '净买额' in col or '净流入' in col:
                            net_val = last.get(col)
                            break
                    if net_val is None or (isinstance(net_val, float) and pd.isna(net_val)):
                        net_val = 0
                    flows.append(CapitalFlow(
                        type=flow_type,
                        net_inflow=float(net_val),
                        balance=0
                    ))
                except Exception as e:
                    print(f"[Baostock→AKShare] {symbol}失败: {e}")
            return flows
        except Exception as e:
            print(f"[Baostock→AKShare] 资金流向失败: {e}")
            return []

    # ── 搜索 ──

    def search_stock(self, keyword: str) -> list[dict]:
        results = []
        # A股搜索
        self._load_stock_basic()
        df = self._stock_basic_df
        if df is not None and not df.empty:
            mask = df["code_name"].str.contains(keyword, na=False)
            a_results = df[mask].head(20)
            for _, r in a_results.iterrows():
                code = r["code"].replace("sh.", "").replace("sz.", "")
                results.append({"code": code, "name": r["code_name"], "market": "A"})

        # 港股搜索（通过 akshare）
        try:
            ak = _ensure_akshare()
            if not ak:
                return results[:40]
            hk_df = None
            for func_name in ["stock_hk_spot_em", "stock_hk_spot"]:
                try:
                    fn = getattr(ak, func_name, None)
                    if fn:
                        hk_df = fn()
                        if not hk_df.empty:
                            break
                except Exception:
                    continue
            if hk_df is not None and not hk_df.empty:
                code_col = next((c for c in hk_df.columns if '代码' in str(c) or str(c).lower() == 'code'), hk_df.columns[0])
                name_col = next((c for c in hk_df.columns if '名称' in str(c) or str(c).lower() == 'name'), hk_df.columns[1])
                hk_df[code_col] = hk_df[code_col].astype(str)
                hk_df[name_col] = hk_df[name_col].astype(str)
                mask = hk_df[name_col].str.contains(keyword, na=False) | hk_df[code_col].str.contains(keyword, na=False)
                hk_results = hk_df[mask].head(20)
                for _, r in hk_results.iterrows():
                    results.append({"code": str(r[code_col]), "name": str(r[name_col]), "market": "HK"})
        except Exception as e:
            print(f"[Baostock→AKShare] 港股搜索失败: {e}")

        return results[:40]

    # ── 个股行情 ──

    def get_stock_quote(self, code: str, market: str = "A") -> Optional[StockQuote]:
        # 港股走 akshare
        if market == "HK":
            return self._get_hk_stock_quote(code)

        prefix = "sh." if code.startswith("6") else "sz."
        full_code = f"{prefix}{code}"
        fields = "date,code,open,high,low,close,preclose,volume,amount,turn,pctChg,peTTM,tradestatus"
        try:
            row = self._get_latest_bar(full_code, fields, days_back=10)
            if row is None:
                return None

            self._load_stock_basic()
            name = code
            if self._stock_basic_df is not None and not self._stock_basic_df.empty:
                match = self._stock_basic_df[self._stock_basic_df["code"] == full_code]
                if not match.empty:
                    name = match.iloc[0]["code_name"]

            pe = row.get("peTTM", "0")
            pe_val = float(pe) if pe and pe != "" else None

            return StockQuote(
                code=code, name=name,
                price=float(row["close"]),
                change_pct=float(row["pctChg"]),
                change_amt=float(row["close"]) - float(row["preclose"]),
                volume=float(row["volume"]),
                amount=float(row.get("amount", 0)) / 1e8,
                high=float(row["high"]), low=float(row["low"]),
                open=float(row["open"]), prev_close=float(row["preclose"]),
                turnover_rate=float(row.get("turn", 0)) if row.get("turn") and row["turn"] != "" else 0,
                pe=pe_val,
                market_cap=None,
            )
        except Exception as e:
            print(f"[Baostock] 获取行情失败 {code}: {e}")
            return None

    def _get_hk_stock_quote(self, code: str) -> Optional[StockQuote]:
        try:
            ak = _ensure_akshare()
            if not ak:
                return None
            df = None
            for func_name in ["stock_hk_spot_em", "stock_hk_spot"]:
                try:
                    fn = getattr(ak, func_name, None)
                    if fn:
                        df = fn()
                        if not df.empty:
                            break
                except Exception:
                    continue
            if df is None or df.empty:
                return None

            code_col = next((c for c in df.columns if '代码' in str(c) or str(c).lower() == 'code'), df.columns[0])
            name_col = next((c for c in df.columns if '名称' in str(c) or str(c).lower() == 'name'), df.columns[1])
            df[code_col] = df[code_col].astype(str)
            row = df[df[code_col] == code]
            if row.empty:
                return None
            r = row.iloc[0]

            def _f(*keys):
                for k in keys:
                    for col in df.columns:
                        col_str = str(col)
                        if k in col_str or k.lower() == col_str.lower():
                            v = r.get(col)
                            if v is not None and not (isinstance(v, float) and pd.isna(v)):
                                return float(v)
                return 0.0

            def _f_opt(*keys):
                for k in keys:
                    for col in df.columns:
                        col_str = str(col)
                        if k in col_str or k.lower() == col_str.lower():
                            v = r.get(col)
                            if v is not None and not (isinstance(v, float) and pd.isna(v)):
                                return float(v)
                return None

            return StockQuote(
                code=code, name=str(r[name_col]),
                price=_f('最新价', 'price'),
                change_pct=_f('涨跌幅', 'change_pct'),
                change_amt=_f('涨跌额', 'change_amt'),
                volume=_f('成交量', 'volume'),
                amount=_f('成交额', 'amount') / 1e8,
                high=_f('最高', 'high'), low=_f('最低', 'low'),
                open=_f('今开', 'open'), prev_close=_f('昨收', 'prev_close'),
                turnover_rate=_f('换手率', 'turnover_rate'),
                pe=_f_opt('市盈率', 'pe'),
                market_cap=_f_opt('总市值', 'market_cap'),
            )
        except Exception as e:
            print(f"[Baostock→AKShare] 港股行情失败 {code}: {e}")
            return None

    # ── K线 ──

    def get_kline(self, code: str, market: str = "A", period: str = "daily", count: int = 250) -> list[KlineBar]:
        # 港股走 akshare
        if market == "HK":
            return self._get_hk_kline(code, period, count)

        freq_map = {"daily": "d", "weekly": "w", "monthly": "m"}
        freq = freq_map.get(period, "d")

        prefix = "sh." if code.startswith("6") else "sz."
        full_code = f"{prefix}{code}"
        fields = "date,open,high,low,close,volume,amount,tradestatus"

        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=count * 3)).strftime("%Y-%m-%d")

        try:
            df = self._query_kline(full_code, fields, start, end, freq, "2")
            if df.empty:
                return []

            if "tradestatus" in df.columns:
                df = df[df["tradestatus"] == "1"]

            df = df.tail(count)
            bars = []
            for _, r in df.iterrows():
                bars.append(KlineBar(
                    date=r["date"],
                    open=float(r["open"]), high=float(r["high"]),
                    low=float(r["low"]), close=float(r["close"]),
                    volume=float(r["volume"]),
                    amount=float(r.get("amount", 0)),
                ))
            return bars
        except Exception as e:
            print(f"[Baostock] 获取K线失败 {code}: {e}")
            return []

    def _get_hk_kline(self, code: str, period: str, count: int) -> list[KlineBar]:
        try:
            ak = _ensure_akshare()
            if not ak:
                return []
            period_map = {"daily": "daily", "weekly": "weekly", "monthly": "monthly"}
            ak_period = period_map.get(period, "daily")
            try:
                df = ak.stock_hk_hist(symbol=code, period=ak_period, adjust="qfq")
            except TypeError:
                df = ak.stock_hk_hist(symbol=code, period=ak_period)
            df = df.tail(count)
            bars = []
            for _, r in df.iterrows():
                # 日期列
                date_val = r.get('日期', r.get('date', ''))
                if hasattr(date_val, 'strftime'):
                    date_str = date_val.strftime('%Y-%m-%d')
                else:
                    date_str = str(date_val)[:10]

                def _get_val(*keys):
                    for k in keys:
                        for col in df.columns:
                            if k in str(col):
                                v = r.get(col)
                                if v is not None and v != '':
                                    return float(v)
                    return 0.0

                bars.append(KlineBar(
                    date=date_str,
                    open=_get_val('开盘', 'open'),
                    high=_get_val('最高', 'high'),
                    low=_get_val('最低', 'low'),
                    close=_get_val('收盘', 'close'),
                    volume=_get_val('成交量', 'volume'),
                    amount=_get_val('成交额', 'amount'),
                ))
            return bars
        except Exception as e:
            print(f"[Baostock→AKShare] 港股K线失败 {code}: {e}")
            return []

    # ── 资讯 ──

    def get_news(self, code: str = "", limit: int = 20) -> list[NewsItem]:
        try:
            ak = _ensure_akshare()
            if not ak:
                return []
            items = []

            # 尝试多个资讯源
            for func_name in ["stock_info_global_em", "stock_news_em"]:
                try:
                    fn = getattr(ak, func_name, None)
                    if fn is None:
                        continue
                    df = fn()
                    if df.empty:
                        continue
                    for _, r in df.head(limit).iterrows():
                        title = ''
                        url = ''
                        time_str = ''
                        summary = ''
                        for col in df.columns:
                            col_str = str(col)
                            val = str(r.get(col, ''))
                            if '标题' in col_str or 'title' in col_str.lower():
                                title = val
                            elif '链接' in col_str or 'url' in col_str.lower():
                                url = val
                            elif '时间' in col_str or 'time' in col_str.lower() or '发布时间' in col_str:
                                time_str = val
                            elif '摘要' in col_str or 'summary' in col_str.lower():
                                summary = val
                        if title:
                            items.append(NewsItem(
                                title=title, url=url, source="东方财富",
                                time=time_str, summary=summary,
                            ))
                    if items:
                        break
                except Exception:
                    continue

            return items[:limit]
        except Exception as e:
            print(f"[Baostock→AKShare] 获取资讯失败: {e}")
            return []

    # ── 板块成分股 ──

    def get_sector_stocks(self, sector_code: str) -> list[dict]:
        self._load_industry()
        df = self._industry_df
        if df is None or df.empty:
            return []

        matched = df[df["industry"] == sector_code]
        out = []
        for _, r in matched.iterrows():
            code = r["code"].replace("sh.", "").replace("sz.", "")
            out.append({"code": code, "name": r["code_name"]})
        return out


# ============ AKShare 实现（可选） ============

class AKShareSource(DataSource):
    """基于 akshare 的数据源（需要网络环境支持）"""

    def __init__(self):
        self._spot_cache: Optional[pd.DataFrame] = None
        self._spot_cache_time: Optional[datetime] = None

    @property
    def name(self):
        return "akshare"

    @staticmethod
    def _col(df: pd.DataFrame, *candidates: str):
        for c in candidates:
            if c in df.columns:
                return c
        return candidates[0]

    def _get_spot_df(self) -> pd.DataFrame:
        now = datetime.now()
        if self._spot_cache is not None and self._spot_cache_time is not None:
            if (now - self._spot_cache_time).total_seconds() < 30:
                return self._spot_cache
        import akshare as ak
        for attempt in range(3):
            try:
                self._spot_cache = ak.stock_zh_a_spot_em()
                self._spot_cache_time = now
                return self._spot_cache
            except Exception:
                if attempt < 2:
                    _time.sleep(1)
        try:
            self._spot_cache = ak.stock_zh_a_spot()
            self._spot_cache_time = now
            return self._spot_cache
        except Exception:
            pass
        self._spot_cache = pd.DataFrame()
        self._spot_cache_time = now
        return self._spot_cache

    def get_major_indices(self) -> list[IndexData]:
        import akshare as ak
        try:
            df = ak.stock_zh_index_spot_em()
            c_code = self._col(df, "代码", "code")
            c_name = self._col(df, "名称", "name")
            c_price = self._col(df, "最新价", "price")
            c_chgpct = self._col(df, "涨跌幅", "change_pct")
            c_chgamt = self._col(df, "涨跌额", "change_amt")
            c_vol = self._col(df, "成交额", "volume")
            c_high = self._col(df, "最高", "high")
            c_low = self._col(df, "最低", "low")
            c_open = self._col(df, "今开", "open")
            c_prev = self._col(df, "昨收", "prev_close")
            targets = {
                "上证指数": "000001", "深证成指": "399001",
                "创业板指": "399006", "科创50": "000688",
                "沪深300": "000300", "中证500": "000905"
            }
            result = []
            for _, row in df.iterrows():
                code = str(row[c_code])
                name = str(row[c_name])
                if name in targets or code in targets.values():
                    try:
                        result.append(IndexData(
                            code=code, name=name,
                            price=float(row[c_price]),
                            change_pct=float(row[c_chgpct]),
                            change_amt=float(row[c_chgamt]),
                            volume=float(row.get(c_vol, 0)) / 1e8,
                            high=float(row[c_high]),
                            low=float(row[c_low]),
                            open=float(row[c_open]),
                            prev_close=float(row[c_prev]),
                        ))
                    except (ValueError, KeyError):
                        continue
            return result
        except Exception as e:
            print(f"[AKShare] 获取A股大盘失败: {e}")
            return []

    def get_hk_indices(self) -> list[IndexData]:
        import akshare as ak
        try:
            df = ak.stock_hk_index_spot_em()
            c_code = self._col(df, "代码", "code")
            c_name = self._col(df, "名称", "name")
            c_price = self._col(df, "最新价", "price")
            c_chgpct = self._col(df, "涨跌幅", "change_pct")
            c_chgamt = self._col(df, "涨跌额", "change_amt")
            c_vol = self._col(df, "成交额", "volume")
            c_high = self._col(df, "最高", "high")
            c_low = self._col(df, "最低", "low")
            c_open = self._col(df, "今开", "open")
            c_prev = self._col(df, "昨收", "prev_close")
            targets = ["恒生指数", "恒生科技指数", "国企指数"]
            result = []
            for _, row in df.iterrows():
                name = str(row[c_name])
                if name in targets:
                    try:
                        result.append(IndexData(
                            code=str(row[c_code]), name=name,
                            price=float(row[c_price]),
                            change_pct=float(row[c_chgpct]),
                            change_amt=float(row[c_chgamt]),
                            volume=float(row.get(c_vol, 0)) / 1e8,
                            high=float(row[c_high]),
                            low=float(row[c_low]),
                            open=float(row[c_open]),
                            prev_close=float(row[c_prev]),
                        ))
                    except (ValueError, KeyError):
                        continue
            return result
        except Exception as e:
            print(f"[AKShare] 获取港股大盘失败: {e}")
            return []

    def get_sectors(self) -> list[SectorData]:
        import akshare as ak
        try:
            df = ak.stock_board_concept_name_em()
            c_code = self._col(df, "代码", "code")
            c_name = self._col(df, "板块名称", "name")
            c_chgpct = self._col(df, "涨跌幅", "change_pct")
            c_inflow = self._col(df, "主力净流入", "net_inflow")
            c_turnover = self._col(df, "成交额", "turnover")
            result = []
            for _, row in df.head(80).iterrows():
                try:
                    result.append(SectorData(
                        code=str(row[c_code]), name=str(row[c_name]),
                        change_pct=float(row[c_chgpct]),
                        net_inflow=float(row.get(c_inflow, 0)) / 1e8,
                        turnover=float(row.get(c_turnover, 0)) / 1e8,
                    ))
                except (ValueError, KeyError):
                    continue
            return sorted(result, key=lambda x: x.net_inflow, reverse=True)
        except Exception as e:
            print(f"[AKShare] 获取板块失败: {e}")
            return []

    def get_capital_flow(self) -> list[CapitalFlow]:
        import akshare as ak
        flows = []
        for symbol, flow_type in [("北向资金", "north"), ("南向资金", "south")]:
            try:
                df = ak.stock_hsgt_hist_em(symbol=symbol)
                if not df.empty:
                    last = df.iloc[-1]
                    net_val = last.get("当日成交净买额", last.get("当日成交额", 0))
                    if pd.isna(net_val):
                        col = "当日成交净买额" if "当日成交净买额" in df.columns else df.columns[1]
                        valid = df[col].dropna()
                        net_val = valid.iloc[-1] if len(valid) > 0 else 0
                    flows.append(CapitalFlow(
                        type=flow_type,
                        net_inflow=float(net_val),
                        balance=0
                    ))
            except Exception as e:
                print(f"[AKShare] {symbol}失败: {e}")
        return flows

    def search_stock(self, keyword: str) -> list[dict]:
        import akshare as ak
        try:
            df = ak.stock_info_a_code_name()
            code_col = "code" if "code" in df.columns else "代码"
            name_col = "name" if "name" in df.columns else "名称"
            code_str = df[code_col].astype(str)
            name_str = df[name_col].astype(str)
            mask = name_str.str.contains(keyword, na=False) | code_str.str.contains(keyword, na=False)
            results = df[mask].head(20)
            return [
                {"code": str(r[code_col]), "name": str(r[name_col]), "market": "A"}
                for _, r in results.iterrows()
            ]
        except Exception:
            return []

    def get_stock_quote(self, code: str, market: str = "A") -> Optional[StockQuote]:
        try:
            df = self._get_spot_df()
            if df.empty:
                return None
            c_code = self._col(df, "代码", "code")
            row = df[df[c_code] == code]
            if row.empty:
                return None
            r = row.iloc[0]

            def _f(*keys):
                for k in keys:
                    v = r.get(k)
                    if v is not None and not (isinstance(v, float) and pd.isna(v)):
                        return float(v)
                return 0.0

            def _f_opt(*keys):
                for k in keys:
                    v = r.get(k)
                    if v is not None and not (isinstance(v, float) and pd.isna(v)):
                        return float(v)
                return None

            c_name = self._col(df, "名称", "name")
            market_cap_raw = _f_opt("总市值", "market_cap")
            return StockQuote(
                code=code, name=str(r[c_name]),
                price=_f("最新价", "price"),
                change_pct=_f("涨跌幅", "change_pct"),
                change_amt=_f("涨跌额", "change_amt"),
                volume=_f("成交量", "volume"),
                amount=_f("成交额", "amount") / 1e8,
                high=_f("最高", "high"), low=_f("最低", "low"),
                open=_f("今开", "open"), prev_close=_f("昨收", "prev_close"),
                turnover_rate=_f("换手率", "turnover_rate"),
                pe=_f_opt("市盈率-动态", "市盈率", "pe"),
                market_cap=market_cap_raw / 1e8 if market_cap_raw else None,
            )
        except Exception as e:
            print(f"[AKShare] 获取行情失败 {code}: {e}")
            return None

    def get_kline(self, code: str, market: str = "A", period: str = "daily", count: int = 250) -> list[KlineBar]:
        import akshare as ak
        try:
            if market == "HK":
                df = ak.stock_hk_hist(symbol=code, period=period, adjust="qfq")
            else:
                df = ak.stock_zh_a_hist(symbol=code, period=period, adjust="qfq")
            df = df.tail(count)
            c_date = self._col(df, "日期", "date")
            c_open = self._col(df, "开盘", "open")
            c_high = self._col(df, "最高", "high")
            c_low = self._col(df, "最低", "low")
            c_close = self._col(df, "收盘", "close")
            c_vol = self._col(df, "成交量", "volume")
            c_amt = self._col(df, "成交额", "amount")
            bars = []
            for _, r in df.iterrows():
                bars.append(KlineBar(
                    date=str(r[c_date])[:10],
                    open=float(r[c_open]), high=float(r[c_high]),
                    low=float(r[c_low]), close=float(r[c_close]),
                    volume=float(r[c_vol]),
                    amount=float(r.get(c_amt, 0)),
                ))
            return bars
        except Exception as e:
            print(f"[AKShare] 获取K线失败 {code}: {e}")
            return []

    def get_news(self, code: str = "", limit: int = 20) -> list[NewsItem]:
        import akshare as ak
        try:
            df = ak.stock_info_global_em()
            c_title = self._col(df, "标题", "title")
            c_url = self._col(df, "链接", "url")
            c_time = self._col(df, "发布时间", "time")
            c_summary = self._col(df, "摘要", "summary")
            items = []
            for _, r in df.head(limit).iterrows():
                items.append(NewsItem(
                    title=str(r[c_title]),
                    url=str(r.get(c_url, "")),
                    source="",
                    time=str(r.get(c_time, "")),
                    summary=str(r.get(c_summary, "")),
                ))
            return items
        except Exception as e:
            print(f"[AKShare] 获取资讯失败: {e}")
            return []

    def get_sector_stocks(self, sector_code: str) -> list[dict]:
        import akshare as ak
        try:
            df = ak.stock_board_concept_cons_em(symbol=sector_code)
            c_code = self._col(df, "代码", "code")
            c_name = self._col(df, "名称", "name")
            return [
                {"code": str(r[c_code]), "name": str(r[c_name])}
                for _, r in df.iterrows()
            ]
        except Exception:
            return []


# ============ 数据源工厂 ============

_data_source: Optional[DataSource] = None
_source_lock = _threading.Lock()


def get_source() -> DataSource:
    global _data_source
    if _data_source is None:
        with _source_lock:
            if _data_source is None:
                _data_source = BaostockSource()
    return _data_source


def set_source(name: str, **kwargs):
    global _data_source
    with _source_lock:
        if name == "baostock":
            _data_source = BaostockSource()
        elif name == "akshare":
            _data_source = AKShareSource()
        elif name == "tushare":
            _data_source = BaostockSource()
        else:
            raise ValueError(f"不支持的数据源: {name}")

"""
StockLens 2.0 — PySide6 前端
前后端分离，通过 HTTP API 与后端通信
"""
import sys
import os
import json
import threading
import subprocess
import time as _time
from datetime import datetime
from typing import Optional

import numpy as np
import requests

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QLabel, QLineEdit, QPushButton, QComboBox, QSplitter,
    QTextEdit, QMessageBox, QStatusBar, QGroupBox, QListWidget,
    QListWidgetItem, QAbstractItemView, QProgressBar, QDialog,
)
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QUrl
from PySide6.QtGui import QFont, QColor, QIcon, QDesktopServices, QPalette

import pyqtgraph as pg
import webbrowser

# ═══════════════ 常量 ═══════════════

API_BASE = "http://127.0.0.1:8765"
SERVER_SCRIPT = os.path.join(os.path.dirname(__file__), "server.py")

# ═══════════════ 样式 ═══════════════

STYLE = """
QMainWindow, QWidget { background: #0f0f1a; color: #d0d0d0; font-family: "Microsoft YaHei"; font-size: 12px; }
QTabWidget::pane { border: 1px solid #1e1e30; background: #0f0f1a; }
QTabBar::tab { background: #16162a; color: #777; padding: 6px 16px; margin-right: 2px; border-top-left-radius: 4px; border-top-right-radius: 4px; }
QTabBar::tab:selected { background: #1e1e38; color: #ff4757; font-weight: bold; }
QTableWidget { background: #13132a; alternate-background-color: #181835; gridline-color: #1e1e30; border: 1px solid #1e1e30; selection-background-color: #2a1a3e; }
QTableWidget::item { padding: 2px 6px; border: none; }
QHeaderView::section { background: #1a1a38; color: #ccc; padding: 4px; border: 1px solid #1e1e30; font-weight: bold; font-size: 11px; }
QLineEdit { background: #13132a; border: 1px solid #1e1e30; border-radius: 3px; padding: 5px 8px; color: #d0d0d0; font-size: 12px; }
QLineEdit:focus { border-color: #ff4757; }
QPushButton { background: #2a1a3e; color: #d0d0d0; border: none; border-radius: 3px; padding: 5px 12px; font-weight: bold; font-size: 11px; }
QPushButton:hover { background: #ff4757; }
QPushButton#addBtn { background: #1a3a2a; padding: 3px 8px; }
QPushButton#addBtn:hover { background: #2ed573; color: #000; }
QPushButton#delBtn { background: #3a1a1a; padding: 3px 8px; }
QPushButton#delBtn:hover { background: #ff4757; }
QPushButton#linkBtn { background: #1a2a3a; padding: 2px 8px; font-size: 10px; }
QPushButton#linkBtn:hover { background: #3742fa; }
QComboBox { background: #13132a; border: 1px solid #1e1e30; border-radius: 3px; padding: 3px 6px; color: #d0d0d0; font-size: 11px; }
QComboBox::drop-down { border: none; }
QComboBox QAbstractItemView { background: #13132a; selection-background-color: #2a1a3e; color: #d0d0d0; }
QTextEdit { background: #13132a; border: 1px solid #1e1e30; border-radius: 3px; color: #d0d0d0; padding: 6px; font-size: 11px; }
QScrollBar:vertical { background: #0f0f1a; width: 6px; border-radius: 3px; }
QScrollBar::handle:vertical { background: #2a2a4a; border-radius: 3px; min-height: 20px; }
QScrollBar::handle:vertical:hover { background: #ff4757; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QStatusBar { background: #1a1a38; color: #888; font-size: 10px; }
QGroupBox { border: 1px solid #1e1e30; border-radius: 4px; margin-top: 0.8em; padding-top: 0.8em; color: #ff4757; font-weight: bold; font-size: 11px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QProgressBar { border: 1px solid #1e1e30; border-radius: 2px; text-align: center; background: #13132a; height: 4px; }
QProgressBar::chunk { background: #ff4757; border-radius: 2px; }
QSplitter::handle { background: #1e1e30; width: 1px; }
"""

# ═══════════════ API 客户端 ═══════════════

def api_get(path: str, params: dict = None, timeout: int = 15) -> dict:
    try:
        r = requests.get(f"{API_BASE}{path}", params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def api_post(path: str, params: dict = None, timeout: int = 10) -> dict:
    try:
        r = requests.post(f"{API_BASE}{path}", params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def api_delete(path: str, timeout: int = 10) -> dict:
    try:
        r = requests.delete(f"{API_BASE}{path}", timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"error": str(e)}

# ═══════════════ API 线程 ═══════════════

class ApiThread(QThread):
    done = Signal(str, object)
    _running = []

    def __init__(self, key: str, path: str, params: dict = None, method: str = "GET", parent=None):
        super().__init__(parent)
        self.key = key
        self.path = path
        self.params = params
        self.method = method
        self.done.connect(self._cleanup)

    def run(self):
        try:
            if self.method == "POST":
                result = api_post(self.path, self.params)
            elif self.method == "DELETE":
                result = api_delete(self.path)
            else:
                result = api_get(self.path, self.params)
            self.done.emit(self.key, result)
        except Exception as e:
            self.done.emit(f"{self.key}_error", str(e))

    def _cleanup(self, key, result):
        try:
            ApiThread._running.remove(self)
        except ValueError:
            pass

    @classmethod
    def go(cls, key: str, path: str, params: dict = None, method: str = "GET", parent=None, callback=None):
        t = cls(key, path, params, method, parent)
        cls._running.append(t)
        if callback:
            t.done.connect(callback)
        t.start()
        return t

# ═══════════════ 颜色工具 ═══════════════

def color_for(val: float) -> str:
    if val > 0: return "#ff4757"
    elif val < 0: return "#2ed573"
    return "#888"

# ═══════════════ K线图 ═══════════════

class KLineWidget(pg.GraphicsLayoutWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setBackground("#13132a")
        self.price_plot = self.addPlot(row=0, col=0, rowspan=3)
        self.price_plot.showGrid(x=True, y=True, alpha=0.2)
        self.price_plot.setLabel("left", "")
        self.vol_plot = self.addPlot(row=3, col=0)
        self.vol_plot.showGrid(x=True, y=True, alpha=0.2)
        self.vol_plot.setXLink(self.price_plot)

    def plot(self, bars: list):
        self.price_plot.clear(); self.vol_plot.clear()
        if not bars or len(bars) < 2:
            return
        closes = np.array([b["close"] for b in bars])
        opens = np.array([b["open"] for b in bars])
        highs = np.array([b["high"] for b in bars])
        lows = np.array([b["low"] for b in bars])
        vols = np.array([b["volume"] for b in bars])
        dates = [b["date"] for b in bars]
        n = len(bars)
        w = 0.6
        up, dn = "#ff4757", "#2ed573"
        for i in range(n):
            c = up if closes[i] >= opens[i] else dn
            body_t = max(opens[i], closes[i]); body_b = min(opens[i], closes[i])
            bh = max(body_t - body_b, 0.001)
            body = pg.QtWidgets.QGraphicsRectItem(i - w / 2, body_b, w, bh)
            body.setPen(pg.mkPen(c, width=1)); body.setBrush(pg.mkBrush(c))
            self.price_plot.addItem(body)
            if highs[i] > body_t:
                l = pg.QtWidgets.QGraphicsLineItem(i, body_t, i, highs[i])
                l.setPen(pg.mkPen(c, width=1)); self.price_plot.addItem(l)
            if lows[i] < body_b:
                l = pg.QtWidgets.QGraphicsLineItem(i, lows[i], i, body_b)
                l.setPen(pg.mkPen(c, width=1)); self.price_plot.addItem(l)
        for per, clr in [(5, "#f9ca24"), (10, "#70a1ff"), (20, "#a29bfe"), (60, "#ff6b81")]:
            if n >= per:
                ma = np.convolve(closes, np.ones(per) / per, mode="valid")
                self.price_plot.plot(np.arange(per - 1, n), ma, pen=pg.mkPen(clr, width=1))
        for i in range(n):
            c = up if closes[i] >= opens[i] else dn
            bar = pg.QtWidgets.QGraphicsRectItem(i - w / 2, 0, w, vols[i])
            bar.setPen(pg.mkPen(c, width=1)); bar.setBrush(pg.mkBrush(c + "60"))
            self.vol_plot.addItem(bar)
        self.price_plot.autoRange(); self.vol_plot.autoRange()
        tick = max(1, n // 6)
        self.price_plot.getAxis("bottom").setTicks([[(i, dates[i]) for i in range(0, n, tick)]])

# ═══════════════ 大盘面板 ═══════════════

class DashboardPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        l = QVBoxLayout(self); l.setSpacing(8)

        h = QHBoxLayout()
        h.addWidget(QLabel("大盘行情")); h.addStretch()
        self.refresh_btn = QPushButton("刷新"); self.refresh_btn.clicked.connect(self.load)
        h.addWidget(self.refresh_btn); l.addLayout(h)

        self.a_group = QGroupBox("A股指数")
        al = QVBoxLayout(self.a_group)
        self.a_table = self._make_table(7, ["代码", "名称", "最新价", "涨跌幅%", "涨跌额", "最高", "最低"])
        al.addWidget(self.a_table); l.addWidget(self.a_group)

        self.hk_group = QGroupBox("港股指数")
        hl = QVBoxLayout(self.hk_group)
        self.hk_table = self._make_table(7, ["代码", "名称", "最新价", "涨跌幅%", "涨跌额", "最高", "最低"])
        hl.addWidget(self.hk_table); l.addWidget(self.hk_group)

        self.flow_group = QGroupBox("资金流向")
        fl = QHBoxLayout(self.flow_group)
        self.flow_labels = {}; fl.addStretch()
        l.addWidget(self.flow_group)

        QTimer.singleShot(100, self.load)

    def _make_table(self, cols: int, headers: list) -> QTableWidget:
        t = QTableWidget(); t.setColumnCount(cols)
        t.setHorizontalHeaderLabels(headers)
        t.horizontalHeader().setStretchLastSection(True)
        t.setAlternatingRowColors(True); t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.verticalHeader().setVisible(False); t.setMinimumHeight(100)
        return t

    def load(self):
        self.refresh_btn.setEnabled(False); self.refresh_btn.setText("加载中...")
        ApiThread.go("indices", "/api/indices", callback=self._on)
        ApiThread.go("flow", "/api/flow", callback=self._on_flow)

    def _on(self, key, data):
        if key == "indices" and "data" in data:
            a_data = [d for d in data["data"] if d.get("market") == "A"]
            hk_data = [d for d in data["data"] if d.get("market") == "HK"]
            self._populate(self.a_table, a_data)
            self._populate(self.hk_table, hk_data)
        self.refresh_btn.setEnabled(True); self.refresh_btn.setText("刷新")

    def _on_flow(self, key, data):
        if key == "flow" and "data" in data:
            # Clear old labels (skip spacers/stretches)
            layout = self.flow_group.layout()
            while layout.count():
                item = layout.takeAt(0)
                w = item.widget()
                if w:
                    w.deleteLater()
            nm = {"north": "北向(外资→A股)", "south": "南向(内资→港股)"}
            for f in data["data"]:
                name = nm.get(f["type"], f["type"])
                lbl = QLabel(f"{name}: <b style='color:{color_for(f['net_inflow'])}'>{f['net_inflow']:+.2f}亿</b>")
                lbl.setStyleSheet("font-size:13px; padding:4px 16px;")
                self.flow_group.layout().addWidget(lbl)

    def _populate(self, table, data):
        table.setRowCount(len(data))
        for i, d in enumerate(data):
            vals = [d.get("code", ""), d.get("name", ""),
                    f"{d.get('price', 0):.2f}", f"{d.get('change_pct', 0):+.2f}%",
                    f"{d.get('change_amt', 0):+.2f}", f"{d.get('high', 0):.2f}", f"{d.get('low', 0):.2f}"]
            for j, v in enumerate(vals):
                item = QTableWidgetItem(v); item.setTextAlignment(Qt.AlignCenter)
                if j == 3: item.setForeground(QColor(color_for(d.get("change_pct", 0))))
                table.setItem(i, j, item)
        table.resizeColumnsToContents()

# ═══════════════ 板块面板 ═══════════════

class SectorPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        l = QVBoxLayout(self); l.setSpacing(8)
        h = QHBoxLayout(); h.addWidget(QLabel("板块资金"))
        self.sort_cb = QComboBox(); self.sort_cb.addItems(["净流入↓", "涨跌幅↓", "成交额↓"])
        self.sort_cb.currentIndexChanged.connect(self._sort)
        h.addWidget(self.sort_cb); h.addStretch()
        self.refresh_btn = QPushButton("刷新"); self.refresh_btn.clicked.connect(self.load)
        h.addWidget(self.refresh_btn); l.addLayout(h)

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["板块", "涨跌幅%", "主力净流入(亿)", "成交额(亿)"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True); self.table.verticalHeader().setVisible(False)
        l.addWidget(self.table)
        self._data = []
        QTimer.singleShot(200, self.load)

    def load(self):
        self.refresh_btn.setEnabled(False)
        ApiThread.go("sectors", "/api/sectors", callback=self._on)

    def _on(self, key, data):
        if key == "sectors" and "data" in data:
            self._data = data["data"]; self._sort()
        self.refresh_btn.setEnabled(True)

    def _sort(self):
        idx = self.sort_cb.currentIndex()
        keys = ["net_inflow", "change_pct", "turnover"]
        data = sorted(self._data, key=lambda x: x.get(keys[idx], 0) or 0, reverse=True)
        self.table.setRowCount(len(data))
        for i, d in enumerate(data):
            self.table.setItem(i, 0, self._item(d.get("name", "")))
            i1 = self._item(f"{d.get('change_pct', 0):+.2f}%"); i1.setForeground(QColor(color_for(d.get("change_pct", 0))))
            self.table.setItem(i, 1, i1)
            i2 = self._item(f"{d.get('net_inflow', 0):+.2f}"); i2.setForeground(QColor(color_for(d.get("net_inflow", 0))))
            self.table.setItem(i, 2, i2)
            self.table.setItem(i, 3, self._item(f"{d.get('turnover', 0):.2f}"))
        self.table.resizeColumnsToContents()

    def _item(self, text: str) -> QTableWidgetItem:
        it = QTableWidgetItem(text); it.setTextAlignment(Qt.AlignCenter); return it

# ═══════════════ 自选股面板 ═══════════════

class WatchlistPanel(QWidget):
    analyze_requested = Signal(str, str, str)  # code, name, market

    def __init__(self, parent=None):
        super().__init__(parent)
        l = QVBoxLayout(self); l.setSpacing(8)
        h = QHBoxLayout(); h.addWidget(QLabel("自选股"))

        self.search_input = QLineEdit(); self.search_input.setPlaceholderText("搜索代码/名称...")
        self.search_input.setMaximumWidth(200); self.search_input.returnPressed.connect(self._search)
        h.addWidget(self.search_input)
        self.search_btn = QPushButton("搜索"); self.search_btn.clicked.connect(self._search)
        h.addWidget(self.search_btn)
        h.addStretch()
        self.refresh_btn = QPushButton("刷新"); self.refresh_btn.clicked.connect(self.load_watchlist)
        h.addWidget(self.refresh_btn)
        l.addLayout(h)

        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels(["代码", "名称", "最新价", "涨跌幅%", "换手%", "市盈率", "操作", ""])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True); self.table.verticalHeader().setVisible(False)
        self.table.doubleClicked.connect(self._dbl_click)
        l.addWidget(self.table)
        self._watchlist = []
        QTimer.singleShot(300, self.load_watchlist)

    def load_watchlist(self):
        ApiThread.go("watchlist", "/api/watchlist", callback=self._on_wl)

    def _on_wl(self, key, data):
        if key == "watchlist" and "data" in data:
            self._watchlist = data["data"]
            self.table.setRowCount(len(self._watchlist))
            for i, s in enumerate(self._watchlist):
                self.table.setItem(i, 0, self._item(s["code"]))
                self.table.setItem(i, 1, self._item(s["name"]))
                self.table.setItem(i, 2, self._item("..."))
                self.table.setItem(i, 3, self._item("..."))
                self.table.setItem(i, 4, self._item(""))
                self.table.setItem(i, 5, self._item(""))
                # 删除按钮
                btn = QPushButton("✕"); btn.setObjectName("delBtn")
                btn.clicked.connect(lambda checked, c=s["code"]: self._remove(c))
                self.table.setCellWidget(i, 6, btn)
                # 分析按钮
                abtn = QPushButton("分析"); abtn.setObjectName("addBtn")
                abtn.clicked.connect(lambda checked, c=s["code"], n=s["name"], m=s.get("market","A"): self.analyze_requested.emit(c, n, m))
                self.table.setCellWidget(i, 7, abtn)
            self.table.resizeColumnsToContents()
            # 异步加载行情
            for i, s in enumerate(self._watchlist):
                ApiThread.go(f"quote_{i}", f"/api/stock/{s['code']}", {"market": s.get("market", "A")}, callback=self._on_quote)

    def _on_quote(self, key, data):
        if key.startswith("quote_") and "data" in data:
            idx = int(key.split("_")[1])
            if idx < self.table.rowCount():
                d = data["data"]
                self.table.setItem(idx, 2, self._item(f"{d['price']:.2f}"))
                it = self._item(f"{d['change_pct']:+.2f}%"); it.setForeground(QColor(color_for(d['change_pct'])))
                self.table.setItem(idx, 3, it)
                self.table.setItem(idx, 4, self._item(f"{d.get('turnover_rate', 0):.2f}"))
                self.table.setItem(idx, 5, self._item(f"{d['pe']:.0f}" if d.get('pe') else "--"))

    def _remove(self, code):
        ApiThread.go("del", f"/api/watchlist/{code}", method="DELETE", callback=lambda k, d: self.load_watchlist())

    def _search(self):
        kw = self.search_input.text().strip()
        if not kw: return
        ApiThread.go("search", "/api/search", {"keyword": kw}, callback=self._on_search)

    def _on_search(self, key, data):
        if key != "search" or "data" not in data: return
        results = data["data"]
        if not results: QMessageBox.information(self, "搜索", "无结果"); return
        dlg = QDialog(self); dlg.setWindowTitle("搜索结果"); dlg.resize(380, 320)
        dl = QVBoxLayout(dlg)
        lst = QListWidget()
        for r in results:
            lst.addItem(f"[{r['market']}] {r['code']}  {r['name']}")
        dl.addWidget(lst)
        btn = QPushButton("添加自选"); btn.clicked.connect(lambda: self._add_selected(lst, results, dlg))
        dl.addWidget(btn); dlg.exec()

    def _add_selected(self, lst, results, dlg):
        idx = lst.currentRow()
        if idx < 0: return
        r = results[idx]
        ApiThread.go("add", f"/api/watchlist/{r['code']}", {"name": r["name"], "market": r["market"]}, "POST",
            callback=lambda k, d: (QMessageBox.information(dlg, "成功", f"已添加 {r['name']}"), dlg.accept(), self.load_watchlist()))

    def _dbl_click(self, index):
        row = index.row()
        if row < len(self._watchlist):
            s = self._watchlist[row]
            self.analyze_requested.emit(s["code"], s["name"], s.get("market", "A"))

    def _item(self, text: str) -> QTableWidgetItem:
        it = QTableWidgetItem(text); it.setTextAlignment(Qt.AlignCenter); return it

# ═══════════════ 一键分析面板 ═══════════════

class AnalysisPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        l = QVBoxLayout(self); l.setSpacing(6)
        h = QHBoxLayout()
        h.addWidget(QLabel("一键分析"))
        self.code_input = QLineEdit(); self.code_input.setPlaceholderText("输入代码如 600519 或 00700")
        self.code_input.setMaximumWidth(160); h.addWidget(self.code_input)
        self.market_cb = QComboBox(); self.market_cb.addItems(["A", "HK"]); h.addWidget(self.market_cb)
        self.analyze_btn = QPushButton("分析"); self.analyze_btn.clicked.connect(self._run)
        h.addWidget(self.analyze_btn); h.addStretch(); l.addLayout(h)

        split = QSplitter(Qt.Vertical)
        self.kline = KLineWidget(); self.kline.setMinimumHeight(300); split.addWidget(self.kline)
        self.report = QTextEdit(); self.report.setReadOnly(True); split.addWidget(self.report)
        split.setSizes([380, 320]); l.addWidget(split)

    def analyze(self, code: str, name: str = "", market: str = "A"):
        self.code_input.setText(code); self.market_cb.setCurrentText(market)
        self.code_input.setPlaceholderText(f"{name} ({code})" if name else code)
        self._run()

    def _run(self):
        code = self.code_input.text().strip()
        if not code: return
        market = self.market_cb.currentText()
        self.analyze_btn.setEnabled(False); self.analyze_btn.setText("分析中...")
        self.report.clear(); self.report.append(f"正在分析 {code}...")
        ApiThread.go("analysis", f"/api/analysis/{code}", {"market": market}, callback=self._on)

    def _on(self, key, data):
        self.analyze_btn.setEnabled(True); self.analyze_btn.setText("分析")
        if key != "analysis": return
        if "error" in data:
            self.report.setHtml(f"<p style='color:#ff4757'>错误: {data['error']}</p>"); return

        q = data.get("quote", {})
        ind = data.get("indicators", {})
        sig = data.get("signals", [])
        sent = data.get("sentiment", {})
        verdict = data.get("verdict", {})
        bars = data.get("bars", [])
        news = data.get("news", [])

        self.kline.plot(bars)

        # 综合研判
        vc = verdict.get("color", "#888")
        parts = [f"""
        <div style='background:#1a1a38;border-radius:6px;padding:10px;margin-bottom:8px;'>
        <h2 style='color:#ff4757;margin:0 0 6px 0;'>综合研判</h2>
        <p style='font-size:20px;margin:4px 0;'>结论: <b style='color:{vc};font-size:24px;'>{verdict.get('label','--')}</b>
        &nbsp;评分: <b style='color:{vc}'>{verdict.get('score',0):+.1f}</b></p>
        <p style='color:#aaa;margin:2px 0;'>情绪: {sent.get('label','--')} | 正面{sent.get('positive',0)} 负面{sent.get('negative',0)} / {sent.get('total',0)}条</p>
        </div>"""]

        # 基本信息
        parts.append(f"""
        <h3 style='color:#ff4757;'>基本行情</h3>
        <table style='width:100%;color:#ccc;'>
        <tr><td>{q.get('name','')} ({q.get('code','')})</td><td>最新价 <b>{q.get('price',0):.2f}</b></td>
        <td>涨跌 <b style='color:{color_for(q.get('change_pct',0))}'>{q.get('change_pct',0):+.2f}%</b></td>
        <td>换手 {q.get('turnover_rate',0):.2f}%</td></tr>
        </table>""")

        # 买卖信号
        sig_html = ""
        for s in sig:
            t = s["type"]
            c = "#ff4757" if t == "buy" else "#2ed573" if t == "sell" else "#f9ca24"
            sig_html += f"<tr><td style='color:{c};padding:2px;'>● {s['msg']}</td></tr>"
        if not sig_html:
            sig_html = "<tr><td style='color:#888;'>暂无明确信号</td></tr>"
        parts.append(f"<h3 style='color:#ff4757;'>买卖信号</h3><table style='width:100%;color:#ccc;'>{sig_html}</table>")

        # 技术指标
        if ind:
            parts.append(f"""
            <h3 style='color:#ff4757;'>技术指标</h3>
            <table style='width:100%;color:#ccc;'>
            <tr><td>趋势</td><td><b>{ind.get('trend','')}</b></td><td>MA5</td><td>{ind.get('ma5','')}</td></tr>
            <tr><td>MA10</td><td>{ind.get('ma10','')}</td><td>MA20</td><td>{ind.get('ma20','')}</td></tr>
            <tr><td>MA60</td><td>{ind.get('ma60','')}</td><td>距MA60</td><td style='color:{color_for(ind.get('price_vs_ma60',0))}'>{ind.get('price_vs_ma60',0):+.1f}%</td></tr>
            <tr><td>MACD</td><td style='color:{color_for(ind.get('macd',0))}'>{ind.get('macd','')}</td><td>RSI(14)</td><td>{ind.get('rsi14','')}</td></tr>
            <tr><td>KDJ-K</td><td>{ind.get('kdj_k','')}</td><td>KDJ-D</td><td>{ind.get('kdj_d','')}</td></tr>
            <tr><td>布林上</td><td>{ind.get('bb_upper','')}</td><td>布林下</td><td>{ind.get('bb_lower','')}</td></tr>
            <tr><td>量比(5/20)</td><td>{ind.get('vol_ratio','')}</td><td></td><td></td></tr>
            </table>""")

        # 资讯
        if news:
            nh = ""
            for n in news[:6]:
                title = n.get("title", "")[:60]
                url = n.get("url", "")
                if url: title = f"<a href='{url}' style='color:#70a1ff;'>{title}</a>"
                nh += f"<li>{title} <span style='color:#666;'>{n.get('source','')}</span></li>"
            parts.append(f"<h3 style='color:#ff4757;'>相关资讯</h3><ul style='color:#ccc;'>{nh}</ul>")

        self.report.setHtml("".join(parts))

# ═══════════════ 资讯面板 ═══════════════

class NewsPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        l = QVBoxLayout(self); l.setSpacing(8)
        h = QHBoxLayout(); h.addWidget(QLabel("财经资讯")); h.addStretch()
        self.refresh_btn = QPushButton("刷新"); self.refresh_btn.clicked.connect(self.load)
        h.addWidget(self.refresh_btn); l.addLayout(h)

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["标题", "来源", "时间", "链接"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True); self.table.verticalHeader().setVisible(False)
        self.table.doubleClicked.connect(self._open)
        l.addWidget(self.table)
        self._news = []
        QTimer.singleShot(400, self.load)

    def load(self):
        self.refresh_btn.setEnabled(False)
        ApiThread.go("news", "/api/news", {"limit": 30}, callback=self._on)

    def _on(self, key, data):
        if key == "news" and "data" in data:
            self._news = data["data"]
            self.table.setRowCount(len(self._news))
            for i, n in enumerate(self._news):
                self.table.setItem(i, 0, QTableWidgetItem(n.get("title", "")[:80]))
                self.table.setItem(i, 1, self._item(n.get("source", "")))
                self.table.setItem(i, 2, self._item((n.get("time", "") or "")[:10]))
                if n.get("url"):
                    btn = QPushButton("原文"); btn.setObjectName("linkBtn")
                    btn.clicked.connect(lambda checked, u=n["url"]: webbrowser.open(u))
                    self.table.setCellWidget(i, 3, btn)
            self.table.resizeColumnsToContents(); self.table.setColumnWidth(0, 420)
        self.refresh_btn.setEnabled(True)

    def _open(self, idx):
        row = idx.row()
        if row < len(self._news) and self._news[row].get("url"):
            webbrowser.open(self._news[row]["url"])

    def _item(self, text: str) -> QTableWidgetItem:
        it = QTableWidgetItem(text); it.setTextAlignment(Qt.AlignCenter); return it

# ═══════════════ 主窗口 ═══════════════

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("StockLens 2.0 — 股票复盘分析")
        self.resize(1300, 820)
        self.setStyleSheet(STYLE)

        central = QWidget(); self.setCentralWidget(central)
        ml = QVBoxLayout(central); ml.setContentsMargins(6, 6, 6, 6); ml.setSpacing(2)

        # 工具栏
        tb = QHBoxLayout()
        title = QLabel("StockLens 2.0"); title.setStyleSheet("font-size:18px;font-weight:bold;color:#ff4757;")
        tb.addWidget(title); tb.addStretch()
        self.status_lbl = QLabel("后端连接中..."); self.status_lbl.setStyleSheet("color:#888;font-size:10px;")
        tb.addWidget(self.status_lbl)
        self.refresh_all_btn = QPushButton("刷新全部缓存"); self.refresh_all_btn.clicked.connect(self._refresh_all)
        tb.addWidget(self.refresh_all_btn)
        ml.addLayout(tb)

        # Tab页
        self.tabs = QTabWidget()
        self.dashboard = DashboardPanel()
        self.sector = SectorPanel()
        self.watchlist = WatchlistPanel()
        self.analysis = AnalysisPanel()
        self.news = NewsPanel()

        self.tabs.addTab(self.dashboard, "大盘")
        self.tabs.addTab(self.sector, "板块")
        self.tabs.addTab(self.watchlist, "自选")
        self.tabs.addTab(self.analysis, "分析")
        self.tabs.addTab(self.news, "资讯")
        ml.addWidget(self.tabs)

        self.watchlist.analyze_requested.connect(self._goto_analysis)

        # 状态栏
        self.sb = QStatusBar(); self.setStatusBar(self.sb)
        self.sb.showMessage("就绪 | 前后端分离架构 | 支持A股+港股")

        # 等待后端就绪
        QTimer.singleShot(500, self._health_check)

    def _health_check(self):
        try:
            r = requests.get(f"{API_BASE}/api/health", timeout=3)
            if r.status_code == 200:
                self.status_lbl.setText("后端 ✓"); self.status_lbl.setStyleSheet("color:#2ed573;font-size:10px;")
                return
        except Exception:
            pass
        # 重试
        self.status_lbl.setText("后端启动中..."); self.status_lbl.setStyleSheet("color:#f9ca24;font-size:10px;")
        QTimer.singleShot(1000, self._health_check)

    def _goto_analysis(self, code, name, market):
        self.analysis.analyze(code, name, market)
        self.tabs.setCurrentIndex(3)

    def _refresh_all(self):
        self.refresh_all_btn.setEnabled(False); self.refresh_all_btn.setText("刷新中...")
        ApiThread.go("refresh", "/api/refresh", method="POST", callback=self._on_refresh)

    def _on_refresh(self, key, data):
        self.refresh_all_btn.setEnabled(True); self.refresh_all_btn.setText("刷新全部缓存")
        self.dashboard.load(); self.sector.load(); self.news.load()
        self.sb.showMessage("缓存刷新完成")

# ═══════════════ 入口 ═══════════════

def _kill_existing_server():
    """杀掉占用端口的旧进程"""
    try:
        for line in os.popen('netstat -ano 2>nul').read().splitlines():
            if ':8765' in line and 'LISTENING' in line:
                pid = line.strip().split()[-1]
                try:
                    subprocess.run(['taskkill', '/F', '/PID', pid],
                                   capture_output=True, timeout=5)
                    _time.sleep(0.5)
                    print(f"[StockLens] 已终止旧服务进程 PID:{pid}")
                except Exception:
                    pass
    except Exception:
        pass

def start_server():
    """启动后端服务"""
    _kill_existing_server()
    print("[StockLens] 启动后端服务...")
    logpath = os.path.join(os.path.dirname(__file__), "server.log")
    logfile = open(logpath, "w")
    subprocess.Popen(
        [sys.executable, SERVER_SCRIPT],
        stdout=logfile, stderr=logfile,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    for name in ("icon.ico", "icon.png"):
        p = os.path.join(os.path.dirname(__file__), name)
        if os.path.exists(p): app.setWindowIcon(QIcon(p)); break

    # 启动后端并等待就绪
    start_server()
    server_ok = False
    for i in range(15):
        try:
            r = requests.get(f"{API_BASE}/api/health", timeout=2)
            if r.status_code == 200:
                print("[StockLens] 后端就绪")
                server_ok = True
                break
        except Exception:
            pass
        _time.sleep(0.6)

    if not server_ok:
        QMessageBox.critical(None, "启动失败",
            "后端服务启动失败，请检查:\n"
            "1. 端口 8765 是否被占用\n"
            "2. 查看 server.log 日志\n"
            "3. 确认 fastapi/uvicorn 已安装")
        return 1

    sys.excepthook = lambda t, v, tb: print(f"[FATAL] {t.__name__}: {v}")

    try:
        window = MainWindow()
        window.show()
    except Exception as e:
        QMessageBox.critical(None, "启动失败", str(e))
        return 1

    sys.exit(app.exec())

if __name__ == "__main__":
    main()

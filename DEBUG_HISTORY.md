# StockLens 项目 Debug 历史记录

## 项目概览
- **项目路径**: D:\finance
- **项目名**: StockLens - 股票实时跟踪系统 (PySide6 + pyqtgraph + akshare)
- **启动方式**: `run.bat` 调用 `D:\Program Files\Tencent\Marvis\MarvisAgent\1.0.1100.151\runtime\python311\python.exe main.py`

## 核心文件
| 文件 | 作用 |
|------|------|
| `main.py` | 入口 |
| `main_window.py` | PySide6 GUI 主界面 |
| `data_source.py` | 数据源层 (akshare / tushare)，含 HTTP monkey-patch |
| `storage.py` | SQLite 自选股存储 |
| `stderr.txt` | 运行错误日志 |
| `stock_tracker.db` | 本地数据库 |

## 已排查的 Bug

### Bug 1: 无法获取数据（HTTP 层面）
**根因**: 东方财富 API 服务器存在两层封锁：
1. **TLS 指纹检测**: 标准 Python `requests` 库的 TLS 指纹被东方财富 CloudFlare 封锁 → 用 `curl_cffi` 模拟 Chrome 指纹绕过
2. **IPv6 TLS 握手异常**: `82.push2.eastmoney.com` 等 API 域名通过 IPv6 连接时 TLS 握手被服务器断开 (curl error 56)，但 IPv4 连接正常

**已做修复** (`data_source.py` 第12-63行):
- 添加了 `curl_cffi.requests` 替换 akshare 内部的 `requests`
- Monkey-patch 同时注入 `akshare.utils.request` 和 `akshare.utils.func` 两个模块
- 对 eastmoney 域名自动解析到 IPv4 并替换 URL（保留 Host 头）
- **当前状态**: IPv4 连接通了，但 SSL 证书验证失败 `curl: (60) SSL: no alternative certificate subject name matches target ipv4 address`

**下一步**: 需要使用 `CURLOPT_IPRESOLVE` (值=113, CURL_IPRESOLVE_V4=1) 让 curl 用 IPv4 DNS 解析但保持原始 hostname 用于 SNI。

### Bug 2: 北向/南向资金 API 已废弃
**根因**: akshare 1.18.63 移除了 `stock_hsgt_north_net_flow_in_em` / `stock_hsgt_south_net_flow_in_em`
**已做修复**: 改用 `ak.stock_hsgt_hist_em(symbol="北向资金")` 和 `ak.stock_hsgt_hist_em(symbol="南向资金")`，列名为 `当日成交净买额`

### Bug 3: 新闻 API 列名变更
**根因**: `stock_info_global_em()` 新版去掉了"来源"列，列名变为 `['标题', '摘要', '发布时间', '链接']`
**已做修复**: 添加了列存在性检查

### Bug 4: 错误日志变量作用域
**根因**: `print(f"...{e}")` 在 except 块外部访问 `e`，可能导致 `UnboundLocalError`
**已做修复**: 使用 `last_err` 变量保存异常，同时处理了空结果的情况

### Bug 5: market_cap 双重调用
**根因**: `_f_opt(...) / 1e8 if _f_opt(...) else None` 调用了两次 `_f_opt`
**已做修复**: 提取为 `market_cap_raw` 变量

## Python 环境信息
- **Python 路径**: `D:\Program Files\Tencent\Marvis\MarvisAgent\1.0.1100.151\runtime\python311\python.exe`
- **关键依赖**:
  - PySide6 6.11.1 ✓
  - akshare 1.18.63 ✓
  - curl_cffi 0.15.0 ✓ (已安装，可模拟浏览器 TLS)
  - pyqtgraph 0.14.0 ✓
  - pandas 3.0.3 ✓
  - numpy 2.4.6 ✓

## 关键发现
1. **akshare 的 `request_with_retry` 函数**位于 `akshare/utils/request.py`，使用标准 `requests` 库
2. **akshare 的 `fetch_paginated_data`** 位于 `akshare/utils/func.py`，在模块级别 import `request_with_retry`，因此必须同时 patch func 模块
3. **东方财富 push2 API** 使用 `82.push2.eastmoney.com` 等带前缀的子域名，解析到不同 CDN 节点
4. **TCP 连接 IPv4/IPv6 都正常 (port 443)**，但 IPv6 的 TLS 握手失败，直接 IPv4 IP 访问成功
5. **`quote.eastmoney.com`** 页面访问正常 (curl_cffi chrome124 impersonate)
6. **新浪财经** (`hq.sinajs.cn`) 返回 403 (需要 Referer)

## 测试命令
```bash
# 测试数据源
cd /d/finance && "D:\Program Files\Tencent\Marvis\MarvisAgent\1.0.1100.151\runtime\python311\python.exe" -c "
from data_source import get_source
src = get_source()
print(src.get_major_indices())
"
```

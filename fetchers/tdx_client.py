# -*- coding: utf-8 -*-
"""
🔌 fetchers/tdx_client.py — PyTDX 底层协议引擎 (TCP 直连券商主站)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
V8.4 新增 · 降维打击级数据通道

职责：
  - 通过通达信 TCP 协议直连券商行情服务器，绕过一切 HTTP WAF 封禁。
  - 提供实时行情 (get_tdx_quotes) 和历史 K 线 (get_tdx_kline_bars) 两个核心接口。
  - 作为 akshare_client.py 容灾链路的 Primary 数据源。

核心守则：
  - 所有函数独立 try...except 兜底，绝对不阻塞调用方。
  - 连接失败时自动尝试下一个备用节点。
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── 稳定行情服务器节点池（招商/华泰/国信等主站）──
_TDX_HOSTS = [
    ("119.147.212.81", 7709),   # 招商证券深圳
    ("106.120.74.86", 7709),    # 北京主站
    ("113.105.73.88", 7709),    # 华泰证券
    ("119.147.212.82", 7709),   # 招商证券深圳2
    ("218.75.126.9", 7709),     # 国信证券
    ("115.238.90.165", 7709),   # 华泰证券2
    ("124.160.88.183", 7709),   # 通达信默认
    ("60.12.136.250", 7709),    # 浙商证券
    ("218.108.98.244", 7709),   # 通达信备用
]


def _get_tdx_market(code: str) -> int:
    """
    将 A 股代码转换为 PyTDX 市场代码。
    规则：6/9/5 开头 → 1 (上海)；0/3 开头 → 0 (深圳)。
    """
    if code.startswith(("6", "9", "5")):
        return 1  # 上海
    return 0  # 深圳


def get_tdx_quotes(stock_code: str) -> Optional[dict]:
    """
    通过 PyTDX TCP 协议获取单只 A 股的实时盘口行情。

    Returns:
        成功时返回 dict:
          {
            "price": float,       # 最新价
            "last_close": float,  # 昨收价
            "open": float,        # 开盘价
            "high": float,        # 最高价
            "low": float,         # 最低价
            "vol": int,           # 成交量（手）
            "amount": float,      # 成交额（元）
            "turnover": float,    # 换手率（需外部补充，此处为 0）
          }
        失败时返回 None。
    """
    try:
        from pytdx.hq import TdxHq_API
    except ImportError:
        logger.warning("[TDX] pytdx 未安装，跳过 TCP 直连通道。")
        return None

    market = _get_tdx_market(stock_code)

    for host, port in _TDX_HOSTS:
        try:
            api = TdxHq_API()
            if api.connect(host, port):
                try:
                    data = api.get_security_quotes([(market, stock_code)])
                    if data is not None and len(data) > 0:
                        row = data[0]
                        result = {
                            "price": float(row.get("price", 0)),
                            "last_close": float(row.get("last_close", 0)),
                            "open": float(row.get("open", 0)),
                            "high": float(row.get("high", 0)),
                            "low": float(row.get("low", 0)),
                            "vol": int(row.get("vol", 0)),
                            "amount": float(row.get("amount", 0)),
                            "turnover": 0.0,  # TDX 盘口不含换手率，需外部计算
                        }
                        logger.info(
                            "[TDX] ✅ %s 实时行情获取成功 (节点: %s:%d) | 价=%.2f",
                            stock_code, host, port, result["price"],
                        )
                        return result
                finally:
                    api.disconnect()
        except Exception as exc:
            logger.debug("[TDX] 节点 %s:%d 连接失败: %s", host, port, exc)
            continue

    logger.warning("[TDX] 所有节点均失败，%s 实时行情获取失败。", stock_code)
    return None


def get_tdx_kline_bars(stock_code: str, count: int = 800) -> list[dict]:
    """
    通过 PyTDX TCP 协议获取 A 股日线 K 线数据（最近 count 根）。

    PyTDX get_security_bars 参数：
      category: 9=日线, 8=15分线, 7=30分线, ... (我们固定用 9=日线)
      market:   0=深圳, 1=上海
      code:     股票代码
      start:    起始位置（0 = 最近一天）
      count:    获取条数（单次最大约 800）

    Returns:
        list[dict]，每个 dict 包含：
          {"date": str, "open": float, "close": float, "high": float, "low": float,
           "vol": int, "amount": float, "turnover": float}
        失败时返回空列表 []。
    """
    try:
        from pytdx.hq import TdxHq_API
    except ImportError:
        logger.warning("[TDX] pytdx 未安装，跳过 K 线 TCP 通道。")
        return []

    market = _get_tdx_market(stock_code)
    category = 9  # 9 = 日线

    for host, port in _TDX_HOSTS:
        try:
            api = TdxHq_API()
            if api.connect(host, port):
                try:
                    # PyTDX 单次最多约 800 条，若需更多需分页
                    # 我们最多拉 3 年 ≈ 750 个交易日，800 够用
                    data = api.get_security_bars(category, market, stock_code, 0, count)
                    if data is not None and len(data) > 0:
                        bars = []
                        for row in data:
                            bars.append({
                                "date": str(row.get("datetime", "")).split(" ")[0],
                                "open": float(row.get("open", 0)),
                                "close": float(row.get("close", 0)),
                                "high": float(row.get("high", 0)),
                                "low": float(row.get("low", 0)),
                                "vol": int(row.get("vol", 0)),
                                "amount": float(row.get("amount", 0)),
                                "turnover": 0.0,  # TDX K 线不含换手率
                            })
                        logger.info(
                            "[TDX] ✅ %s K线获取成功 (节点: %s:%d) | %d 条日线",
                            stock_code, host, port, len(bars),
                        )
                        return bars
                finally:
                    api.disconnect()
        except Exception as exc:
            logger.debug("[TDX] K线节点 %s:%d 连接失败: %s", host, port, exc)
            continue

    logger.warning("[TDX] 所有节点均失败，%s K线数据获取失败。", stock_code)
    return []

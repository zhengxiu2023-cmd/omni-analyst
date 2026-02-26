# -*- coding: utf-8 -*-
"""
🧱 core/models.py — Omni-Analyst v7.5 核心数据模型契约 (Singularity)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
架构规范：
  - 所有层与层之间的数据传递，必须使用此文件中定义的 Dataclass。
  - 禁止在层间传递原始 dict 代替 Dataclass。
  - 字段名与 DATA_CONTRACTS.md 保持严格一致，不得私自修改。

对应文档：DATA_CONTRACTS.md > 第 1 节 "核心数据模型契约"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union


# ===========================================================================
# 1.1 StockInfo — 基础行情与历史极值模型
# 由 fetchers/akshare_client.py 和 fetchers/eastmoney 接口填充
# ===========================================================================
@dataclass
class StockInfo:
    """单只股票的完整基础行情与风控关键数据。"""

    # --- 必填基础字段（初始化时必须提供）---
    code: str                            # 股票代码，如 "000001"（不含市场前缀）
    name: str                            # 股票中文名称，如 "平安银行"
    price: float                         # 最新价（单位：元）
    turnover: float                      # 最新换手率（单位：%）
    pe_ttm: Union[str, float]            # 滚动市盈率；数据缺失时为字符串 "N/A"
    pb: Union[str, float]                # 市净率；数据缺失时为字符串 "N/A"
    total_mv: float                      # 总市值（单位：元）

    # --- 财务进阶指标（由 fetchers 填充，默认缺省）---
    roe: Union[str, float] = "N/A"       # 净资产收益率（%）
    gross_margin: Union[str, float] = "N/A" # 毛利率（%）

    # --- 历史穿透字段（由 kline 数据计算后回填，初始有默认值）---
    min_price_3y: float = 0.0            # 近 3 年最低价（前复权），用于底部计算
    price_percentile: float = 0.0       # 当前价格处于近 3 年的百分位（范围 0.0~100.0）
    rise_from_bottom: float = 0.0       # 距近 3 年低点的反弹幅度（单位：%）

    # --- 判定状态字段（由 risk_auditor 或对应 fetcher 填充）---
    holder_trend: str = "数据缺失"       # 最新股东户数变化趋势描述（如 "减少 5.2% (主力吸筹)"）
    eps_forecast: str = "提取失败"       # 预测/反算 EPS 描述字符串


# ===========================================================================
# 1.1.1 CompetitorFinancials — 竞对财报横评模型
# ===========================================================================
@dataclass
class CompetitorFinancials:
    """目标公司及其同板块竞争对手的核心财报摘要"""
    code: str                            # 股票代码
    name: str                            # 股票名称
    income_statement_8q: str             # 最近 8 期利润表摘要 (营收/净利润等)
    balance_sheet_8q: str                # 资产负债摘要
    # 支持更多字段扩展，可选存入
    cash_flow_8q: str = ""               # 经营现金流净额 (可选存入)


# ===========================================================================
# 1.2 NewsItem — 情报雷达模型
# 由 fetchers/akshare_client.py、fetchers/cctv_news.py 等填充
# LLM/正则打分后回填 score 与 llm_reasoning
# ===========================================================================
@dataclass
class NewsItem:
    """单条情报/新闻的标准化数据容器。"""

    # --- 必填基础字段 ---
    time: str                            # 发布时间/发生时间，格式不限（如 "2025-02-26" 或 "今日盘面"）
    title: str                           # 资讯标题或内容简述
    source: str                          # 情报来源，如 "龙虎榜"、"新闻联播"、"全球快讯"

    # --- 评分字段（初始默认待打分状态）---
    tags: list[str] = field(default_factory=list)  # 命中的词库标签，如 ["🚀 [Type1:颠覆奇点]"]
    score: int = 0                       # 奇点共振评分：0=垃圾/陷阱，1=普通关注，2=超景气核心共振
    llm_reasoning: str = ""             # 大模型给出的判分理由（用于审计师面板展示与二次判定）


# ===========================================================================
# 1.3 RiskStatus — 风控熔断模型
# 由 core/risk_auditor.py 基于 StockInfo 计算并填充
# ===========================================================================
@dataclass
class RiskStatus:
    """对单只股票执行物理风控熔断判定后的结果汇总。"""

    is_safe: bool = True                 # 整体是否安全：触发任何红线即置为 False
    market_vol_desc: str = ""           # 大盘流动性状态描述，如 "1.52 万亿 (疯牛)"
    death_turnover_warn: str = "[安全]"  # 死亡换手率警告文案（触发则写入具体数值）
    extreme_rise_warn: str = ""         # 近 60 日极端透支警告文案（未触发则为空字符串）
    st_warning: str = "[通过]"           # 财务暴雷/ST 警告文案（触发则写入红色警报文案）

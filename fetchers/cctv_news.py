# -*- coding: utf-8 -*-
"""
📡 fetchers/cctv_news.py — 宏观新闻联播采集器
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
职责：
  - 调用 akshare 的 news_cctv 接口，抓取近 N 日的新闻联播内容。
  - 将每条新闻封装为 core.models.NewsItem，score/tags 由上层评分引擎填充。
  - 防御性编程：任何接口异常均静默捕获，返回空列表，绝不阻塞主进程。

数据契约对应：core/models.py > NewsItem
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

import akshare as ak

from core.models import NewsItem

logger = logging.getLogger(__name__)


def fetch_cctv_news(days_back: int = 3) -> list[NewsItem]:
    """
    抓取近 days_back 日的新闻联播内容，封装为 NewsItem 列表。

    策略：
      - 从今天往前依次尝试，遇到第一个有数据的日期就停止（避免非播出日的空返回）。
      - 实际上新闻联播每天播出，但接口偶发性缺失时优雅跳过，不报错。

    Args:
        days_back: 向前追溯的天数，默认 3 天（T, T-1, T-2）。

    Returns:
        list[NewsItem]：封装好的新闻联播情报列表，score 默认为 1 待上层打分。
        若所有日期均获取失败，返回空列表 []。
    """
    results: list[NewsItem] = []

    for offset in range(days_back):
        date_str: str = (datetime.now() - timedelta(days=offset)).strftime("%Y%m%d")
        items: Optional[list[NewsItem]] = _fetch_single_day(date_str, offset)

        if items is not None:
            results.extend(items)
            # 策略：抓到第一个有数据的日期即停止，避免重复历史
            break

    if not results:
        logger.warning("[新闻联播] 近 %d 日均未抓取到有效数据。", days_back)

    return results


def _fetch_single_day(date_str: str, days_offset: int) -> Optional[list[NewsItem]]:
    """
    抓取指定日期的新闻联播内容，失败返回 None（区别于空列表）。

    Args:
        date_str:    日期字符串，格式 "YYYYMMDD"。
        days_offset: 距今偏移天数，用于填充 source 字段（如 "新闻联播(T-1)"）。

    Returns:
        list[NewsItem] 或 None（接口异常时）。
    """
    try:
        df = ak.news_cctv(date=date_str)

        if df is None or df.empty:
            logger.debug("[新闻联播] %s 无数据（可能为非播出日）。", date_str)
            return None

        # 根据偏移量生成 source 标签（T-0 当日直接显示 "新闻联播"）
        source_label: str = (
            "新闻联播" if days_offset == 0 else f"新闻联播(T-{days_offset})"
        )

        items: list[NewsItem] = []
        for _, row in df.iterrows():
            title: str = str(row.get("title", "")).strip()
            content: str = str(row.get("content", "")).strip()

            # 跳过空标题的行（偶发数据脏行）
            if not title:
                continue

            item = NewsItem(
                time=date_str,
                title=title,
                source=source_label,
                # content 作为标签携带（后续评分引擎读 title + content 综合判断）
                tags=[],
                score=0,           # 评分由 llm_engine / 正则引擎在上层填充
                llm_reasoning="",
            )
            # 将 content 暂存到 llm_reasoning，后续评分完成后会被覆盖
            # 这样避免在 NewsItem 增加额外字段破坏数据契约
            item.llm_reasoning = content[:200] if content else ""
            items.append(item)

        logger.info("[新闻联播] %s 已抓取 %d 条。", date_str, len(items))
        return items if items else None

    except Exception as exc:
        logger.error("[新闻联播] %s 抓取失败: %s", date_str, exc)
        return None

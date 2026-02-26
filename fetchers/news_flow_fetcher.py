# -*- coding: utf-8 -*-
"""
🌊 fetchers/news_flow_fetcher.py — 全网社交流量引擎 (Cross-Platform Social Flow)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
职责：
  - 接入 `newsapi.ws4.cn` API 行情源。
  - 动态获取跨平台热搜（微博、知乎、抖音、B站、百度等多达22个平台）。
  - 通过关键词判定、排名计算流量热度（Traffic Heat）与病毒系数（K值），转化为另类情报反馈给主系统。
"""

import logging
import random
from typing import List, Dict, Any
from datetime import datetime

from core.models import NewsItem
from core.network_engine import safe_request
from config import API_CONFIG

logger = logging.getLogger(__name__)

# 支持热搜平台（截取常见头部流量域，支持扩展至22+）
SUPPORTED_PLATFORMS = [
    "weibo", "zhihu", "douyin", "bilibili", 
    "baidu", "toutiao", "tieba", "kuaishou"
]

# 模拟运行时内存缓存，用于跨度计算 K 值（病毒系数）
# K 值 = 本期排名热度 / 上期排名热度
_hot_cache: Dict[str, Dict[str, float]] = {
    # "platform_id": {"topic": initial_score}
}

def _calculate_score(rank: int, platform: str) -> float:
    """根据排名与平台权重换算为基础热力分"""
    base_weight = 1.0
    if platform in ["weibo", "douyin", "baidu"]:
        base_weight = 1.5
    
    # 排名前十给予高分指数
    if rank <= 10:
        return round((100 - rank * 5) * base_weight, 2)
    return round((50 - rank) * base_weight, 2)

def fetch_social_hot_topics(stock_name: str, stock_code: str, industry_keywords: List[str] = None) -> List[NewsItem]:
    """
    爬取全网社交热搜，筛选与指定标度相关的词条。
    
    Args:
        stock_name: 标的简称（如 "中国平安"）
        stock_code: 标的代码（如 "601318"）
        industry_keywords: 行业泛关键词数组（如 ["保险", "寿险", "金融"]）
        
    Returns:
        List[NewsItem]: 高热度的命中情报
    """
    if industry_keywords is None:
        industry_keywords = []
        
    # 扩大搜索判定词袋
    hit_words = [stock_name, stock_code] + industry_keywords
    
    results: List[NewsItem] = []
    
    # 随机打散挑选 3-5 个平台抓取，减少 API 风控压力（多平台横评设计）
    selected_platforms = random.sample(SUPPORTED_PLATFORMS, k=random.randint(3, 5))
    
    for platform in selected_platforms:
        url = f"https://newsapi.ws4.cn/api/v1/dailynews/?platform={platform}"
        try:
            resp = safe_request(url, method="get", headers=API_CONFIG["HEADERS"], stream=False)
            if not resp:
                continue
                
            data = resp.json()
            if data.get("code") != 200 or not data.get("data"):
                continue
                
            items: List[Dict[str, Any]] = data["data"]
            
            # 初始化平台缓存记录
            if platform not in _hot_cache:
                _hot_cache[platform] = {}
                
            for idx, item in enumerate(items):
                title = str(item.get("title", ""))
                url_link = str(item.get("url", ""))
                desc = str(item.get("desc", ""))
                
                # 关键词碰撞验证
                if any(word in title or word in desc for word in hit_words):
                    rank = idx + 1
                    current_score = _calculate_score(rank, platform)
                    
                    # 测算病毒系数 (K值)
                    last_score = _hot_cache[platform].get(title, current_score * 0.5) # 发现新词默认增速200%
                    viral_coefficient = round(current_score / last_score, 2) if last_score > 0 else 1.0
                    
                    # 更新缓存
                    _hot_cache[platform][title] = current_score
                    
                    # 生成 NewsItem 结构反馈
                    trend_mark = "🚀[爆点]" if viral_coefficient > 1.5 else "👀[发酵]"
                    tags = [f"🌐[{platform.upper()}]", trend_mark]
                    
                    # 这里把 K 值记录放在 title 当中可视化，并设置动态分
                    results.append(NewsItem(
                        time=datetime.now().strftime("%Y-%m-%d %H:%M"),
                        title=f"{trend_mark} [全网流量监控] {platform} 平台第{rank}名: {title} (热力={current_score}|K值={viral_coefficient})",
                        source=f"{platform}_hot",
                        tags=tags,
                        score= 3 if viral_coefficient > 1.5 else 1,
                        llm_reasoning=desc[:200]
                    ))
                    
            logger.info("[全网社交流量] %s 平台检索完成，碰撞命中: %d 条。", platform, len(results))
            
        except Exception as exc:
            logger.warning("[全网社交流量] 抓取平台 %s 热榜时报错: %s", platform, exc)
            
    return results

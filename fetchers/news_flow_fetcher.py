# -*- coding: utf-8 -*-
import logging
from typing import List, Dict

from core.models import NewsItem
from core.network_engine import safe_request

logger = logging.getLogger(__name__)

# 支持的平台及权重
PLATFORMS = {
    'weibo': {'name': '微博热搜', 'weight': 10},
    'douyin': {'name': '抖音热榜', 'weight': 9},
    'zhihu': {'name': '知乎热榜', 'weight': 7},
    'xueqiu': {'name': '雪球', 'weight': 8},
}

KEYWORDS = [
    '股', '股市', '股票', 'A股', '港股', '美股', '创业板', '科创板', 
    '涨停', '跌停', '大涨', '暴涨', '飙升', '暴跌', '涨幅', '跌幅', '翻倍',
    '概念', '龙头', '题材', '白马', '蓝筹', '上市', 'IPO', '重组',
    '牛市', '熊市', '反弹', '回调', '震荡', '突破', '新高', '主力', '游资',
    '北向', '外资', '机构', '资金', '板块', '赛道', '轮动', '热点',
    '芯片', '半导体', '新能源', '人工智能', '大模型'
]

def fetch_social_hot_topics() -> List[NewsItem]:
    """
    通过外部API获取四大社交平台的流量热榜，并提取股票相关的词条，计算简单的流量热度得分。
    """
    base_url = "https://newsapi.ws4.cn/api/v1/dailynews/"
    results: List[NewsItem] = []
    
    for platform, info in PLATFORMS.items():
        try:
            resp = safe_request(base_url, method="get", params={"platform": platform})
            if resp is None:
                continue
                
            data = resp.json()
            if data.get('status') != '200':
                logger.warning("[社交流量] %s API返回状态异常", info['name'])
                continue
                
            news_list = data.get('data', [])
            platform_name = info['name']
            weight = info['weight']
            
            for index, item in enumerate(news_list):
                rank = index + 1
                title = item.get('title', '')
                content = item.get('content', '')
                text = f"{title} {content}"
                
                matched_keywords = [kw for kw in KEYWORDS if kw in text]
                if matched_keywords:
                    # 简化版流量热度得分 = 排名的倒数分值 + 平台权重 + 关键词个数权重
                    rank_score = max(0, 100 - rank * 2)
                    keyword_score = len(matched_keywords) * 5
                    total_score = rank_score + (weight * 10) + keyword_score
                    
                    results.append(NewsItem(
                        time=item.get('publish_time', '当前社交热榜'),
                        title=f"流量爆发！【{platform_name}】{title} (热度得分: {total_score})",
                        source=platform_name,
                        tags=[f"🔥 [社交流量共振]"],
                        score=1 if total_score > 120 else 0,
                        llm_reasoning=f"命中股市高热关键词 {matched_keywords}，全网流量汇聚。"
                    ))
                    
        except Exception as e:
            logger.warning("[社交流量] 获取 %s 平台热榜失败: %s", info['name'], e)
            
    # 按流量热度排序，保留前 15 条
    results.sort(key=lambda x: int(x.title.split("热度得分: ")[1].split(")")[0]) if "热度得分:" in x.title else 0, reverse=True)
    logger.info("[社交流量] 获取全网热搜完毕，提取股市相关情报: %d 条。", len(results))
    return results[:15]

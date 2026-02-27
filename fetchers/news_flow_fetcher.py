# -*- coding: utf-8 -*-
"""
🌊 fetchers/news_flow_fetcher.py — 超景气底料雷达 (Hyper-Prosperity Radar)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
职责：
  - 核心使命是对抗噪音，聚合大众轨(微博/抖音)与逻辑轨(知乎/雪球)的热搜。
  - 引入本地神经提纯引擎 (LLM) 进行三维框架判别 ([科技突变]/[政经大局]/[供需极值])。
  - 通过代码反查跨服榜单，确认 [真·现象级破圈] 抑或 [逻辑孕育期]。
  - 数据不入库，直接生成极易吞咽的 Markdown 喂入目标公司的 00_参数面板。
"""

import logging
import json
import os
import requests
from typing import List, Dict, Any
from datetime import datetime

from core.models import HyperProsperityEvent
from core.network_engine import safe_request
from config import API_CONFIG, LLM_CONFIG, EXPORT_CONFIG

logger = logging.getLogger(__name__)

MASS_TRACK = ["weibo", "douyin", "kuaishou"]
LOGIC_TRACK = ["zhihu", "xueqiu", "cls", "toutiao"]

# K值缓存 {platform: {title: last_rank_score}}
_hot_cache: Dict[str, Dict[str, float]] = {}

def _fetch_platform_hot(platform: str, limit: int = 50) -> List[Dict]:
    """获取指定平台的 Top 50 热榜"""
    url = f"https://newsapi.ws4.cn/api/v1/dailynews/?platform={platform}"
    results = []
    try:
        resp = safe_request(url, method="get", headers=API_CONFIG["HEADERS"], stream=False)
        if resp and resp.status_code == 200:
            data = resp.json()
            if data.get("code") == 200 and data.get("data"):
                for idx, item in enumerate(data["data"][:limit]):
                    title = str(item.get("title", "")).strip()
                    desc = str(item.get("desc", "")).strip()
                    if title:
                        results.append({
                            "platform": platform,
                            "rank": idx + 1,
                            "title": title,
                            "desc": desc
                        })
    except Exception as exc:
        logger.warning(f"[热榜采集] 平台 {platform} 采集失败: {exc}")
    return results

def _calculate_base_score(rank: int, max_rank: int = 50) -> float:
    """热度分：排名越靠前分数越高"""
    if rank <= 0 or rank > max_rank:
        return 1.0
    return round(100.0 - (rank / max_rank) * 90.0, 2)

def _evaluate_with_local_llm(events: List[Dict]) -> List[Dict]:
    """
    通过本地 LLM 对初步命中的热榜事件进行提纯与框架映射。
    传入包含 title 和 desc 的字典列表。
    """
    if not events:
        return []
        
    prompt_text = "请作为超景气价值投机分析师，评估以下新闻事件。判断它们是否属于[科技突变]、[政经大局]或[供需极值]。若是噪音或八卦请丢弃(Discard)。\n\n事件列表：\n"
    for idx, e in enumerate(events):
        prompt_text += f"{idx+1}. 标题：{e['title']} | 摘要：{e['desc'][:100]}\n"
        
    prompt_text += """
请严格以 JSON 数组格式返回被选中的事件（丢弃的不返回）。格式：
[
  {
    "original_title": "完全对应原文标题",
    "category": "[科技突变/政经大局/供需极值 三选一]",
    "summary": "1句话事件核心摘要",
    "key_signals": ["信息素1", "信息素2"]
  }
]
"""
    
    payload = {
        "model": LLM_CONFIG["MODEL_NAME"],
        "prompt": prompt_text,
        "stream": False,
        "format": "json"
    }
    
    try:
        resp = requests.post(LLM_CONFIG["OLLAMA_API"], json=payload, timeout=LLM_CONFIG.get("TIMEOUT", 60))
        resp.raise_for_status()
        res_json = resp.json().get("response", "[]")
        
        # 简单清洗
        import re
        clean_text = res_json.strip()
        match = re.search(r"```(?:json)?\s*(.*?)\s*```", clean_text, re.DOTALL)
        if match:
            clean_text = match.group(1).strip()
            
        return json.loads(clean_text)
    except Exception as exc:
        logger.warning(f"[LLM引擎] 批量提纯事件失败: {exc}")
        return []

def fetch_social_hot_topics(stock_name: str, stock_code: str, industry_keywords: List[str] = None, save_dir: str = None) -> List[HyperProsperityEvent]:
    """
    核心执行器：获取双轨热榜 -> 关键词初筛 -> LLM 漏斗过滤 -> 计算共振/K值 -> 追加至面板
    """
    if not industry_keywords:
        industry_keywords = []
        
    hit_words = [stock_name, stock_code] + industry_keywords
    logger.info(f"[雷达引擎] 启动全网双轨扫描，搜索词囊: {hit_words}")
    
    # 获取原始榜单
    mass_items = []
    logic_items = []
    for p in MASS_TRACK:
        mass_items.extend(_fetch_platform_hot(p))
    for p in LOGIC_TRACK:
        logic_items.extend(_fetch_platform_hot(p))
        
    all_items = mass_items + logic_items
    
    # 关键词初筛（降噪防 LLM 过载）
    candidate_items = []
    for item in all_items:
        if any(word in item['title'] or word in item['desc'] for word in hit_words):
            candidate_items.append(item)
            
    # 去重处理（相同标题可能在多个平台）
    unique_candidates = {item['title']: item for item in candidate_items}.values()
    
    if not unique_candidates:
        logger.info("[雷达引擎] 初筛无相关事件命中。")
        return []
        
    logger.info(f"[雷达引擎] 初筛得到 {len(unique_candidates)} 条潜在事件，投入 LLM 洗筹...")
    
    llm_results = _evaluate_with_local_llm(list(unique_candidates))
    
    final_events = []
    markdown_lines = []
    
    for l_res in llm_results:
        target_title = l_res.get("original_title", "")
        if not target_title:
            continue
            
        # 反查双轨共振
        in_mass = [i for i in mass_items if target_title in i['title']]
        in_logic = [i for i in logic_items if target_title in i['title']]
        
        is_mass_hit = len(in_mass) > 0
        is_logic_hit = len(in_logic) > 0
        
        if is_mass_hit and is_logic_hit:
            resonance = "[真·现象级破圈]"
            evidence = f"大众轨(如 {in_mass[0]['platform']} Top{in_mass[0]['rank']}) 与 逻辑轨(如 {in_logic[0]['platform']} Top{in_logic[0]['rank']}) 强烈共振"
        elif is_logic_hit:
            resonance = "[逻辑孕育期]"
            evidence = f"局限于逻辑轨(如 {in_logic[0]['platform']} Top{in_logic[0]['rank']}) 发酵，等待破圈"
        elif is_mass_hit:
            resonance = "[大众情绪狂热]"
            evidence = f"仅存在于大众轨(如 {in_mass[0]['platform']} Top{in_mass[0]['rank']})，缺乏专业逻辑支撑"
        else:
            continue # 未能匹配上
            
        # 计算 K 值（取综合热度）
        current_heat = sum(_calculate_base_score(i['rank']) for i in in_mass + in_logic)
        # 用标题作 cache key
        last_heat = _hot_cache.get("GLOBAL", {}).get(target_title, current_heat * 0.5)
        # 兜底避免 0
        if last_heat <= 0:
            last_heat = 1.0
        k_value = round(current_heat / last_heat, 2)
        
        # 更新缓存
        if "GLOBAL" not in _hot_cache:
            _hot_cache["GLOBAL"] = {}
        _hot_cache["GLOBAL"][target_title] = current_heat
        
        evt = HyperProsperityEvent(
            title=l_res.get("summary", target_title),
            category=l_res.get("category", "[未分类]"),
            resonance=resonance,
            k_value=k_value,
            key_signals=l_res.get("key_signals", [])
        )
        final_events.append(evt)
        
        momentum_desc = f"K值 = {k_value} "
        if k_value > 1.5:
            momentum_desc += "[增量爆发期]"
        elif k_value < 0.8:
            momentum_desc += "[衰退期]"
        else:
            momentum_desc += "[高位震荡期]"
            
        def _sa(v, default="[数据未获取]"):
            return default if v is None or str(v).strip() in ("", "None", "nan") else str(v)
            
        sig_str = ", ".join([_sa(s) for s in evt.key_signals]) if evt.key_signals else "[数据未获取]"
        
        md_block = f"""
### 📡 超景气底层情报雷达 (Hyper-Prosperity Radar)
- **核心事件**: {_sa(evt.title)}
- **框架映射**: {_sa(evt.category)}
- **共振验证**: {_sa(evt.resonance)} ({_sa(evidence)})
- **情绪动量**: {_sa(momentum_desc)}
- **关键信息素**: [{sig_str}]
"""     
        markdown_lines.append(md_block)
        logger.info(f"[雷达引擎] 捕获大事件: {_sa(evt.title)} ({_sa(evt.resonance)})")

    if markdown_lines and save_dir:
        panel_path = os.path.join(save_dir, EXPORT_CONFIG["PANEL_FILENAME"])
        try:
            with open(panel_path, "a", encoding="utf-8") as f:
                f.write("\n" + "".join(markdown_lines) + "\n")
            logger.info(f"[雷达引擎] 已追加 {len(markdown_lines)} 条硬核雷达摘要至面板")
        except Exception as e:
            logger.error(f"[雷达引擎] 无法写入参数面板: {e}")
            
    return final_events

def execute_radar_scan(stock_code: str, stock_name: str) -> str:
    """
    专为 V8.3 集成提供的入口，返回合并好的超景气流量摘要字符串 (Markdown)，供上层直接注入参数面板。
    
    Args:
        stock_code: 股票代码
        stock_name: 股票名称
        
    Returns:
        Formatted markdown string combining all hyper-prosperity events found.
    """
    try:
        events = fetch_social_hot_topics(stock_name, stock_code, save_dir=None)
        if not events:
            return "未监测到现象级破圈信号或大局共振（当前热度未能穿透 LLM 提纯漏斗）。"
            
        summary_lines = []
        for evt in events:
            k_val = f"K={evt.k_value}" if evt.k_value > 0 else ""
            signals_str = ", ".join(evt.key_signals) if evt.key_signals else "无"
            summary_lines.append(
                f"- **{evt.category}** {evt.resonance} {k_val}: {evt.title} "
                f"*(提取信息素: {signals_str})*"
            )
        return "\n".join(summary_lines)
    except Exception as exc:
        logger.error(f"[雷达扫描] 执行异常: {exc}")
        return f"[流量雷达提取失败: {exc}]"

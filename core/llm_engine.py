# -*- coding: utf-8 -*-
"""
🧠 core/llm_engine.py — 本地大模型提纯引擎 (Local LLM Engine)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
职责：
  - 封装对本地 Ollama API 的调用，屏蔽底层细节。
  - 将 DATA_CONTRACTS 中定义的 System Prompt 注入每次请求，
    确保大模型始终在"超景气价值投机"的判分框架下运作。
  - 解析 Ollama 返回的 JSON 格式评分结果，映射为 NewsItem 的 score 字段。
  - 全面的兜底机制：任何异常均静默捕获，返回安全默认值，绝不阻塞主线程。

配置来源：config.py > LLM_CONFIG（OLLAMA_API / MODEL_NAME / SYSTEM_PROMPT / TIMEOUT）
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

import requests
import requests.exceptions

from config import LLM_CONFIG

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 内部数据结构：LLM 单次评分结果
# ---------------------------------------------------------------------------
@dataclass
class LLMScore:
    """
    Ollama 单次情报评分的结构化结果。

    Attributes:
        score:     奇点共振评分（0 垃圾/1 普通/2 核心拐点），对应 NewsItem.score。
        reasoning: 大模型给出的极简判分理由，对应 NewsItem.llm_reasoning。
        success:   本次 LLM 调用是否成功解析（用于上层判断是否降级到正则）。
    """
    score: int = 0
    reasoning: str = "LLM引擎解析失败或超时"
    success: bool = False


# ---------------------------------------------------------------------------
# 模块公共接口
# ---------------------------------------------------------------------------
def evaluate_intel(text: str, title: Optional[str] = None) -> LLMScore:
    """
    调用本地 Ollama 对一条情报进行超景气评分。

    Args:
        text:  情报的正文内容（如新闻全文、公告摘要）。
        title: 情报标题（可选），会和 text 拼接后一起送入模型，提升上下文精度。

    Returns:
        LLMScore：包含 score (0/1/2)、reasoning 和 success 标志。
        若 LLM 不可用或解析失败，返回 score=0 的安全默认值。
    """
    # 如果 LLM 功能被配置为关闭，直接返回默认值（降级到正则模式）
    if not LLM_CONFIG.get("ENABLE", True):
        logger.debug("[LLM引擎] 功能已在配置中禁用，跳过调用。")
        return LLMScore(reasoning="LLM功能已关闭，使用正则降级模式")

    # 拼接用户输入：标题权重高，放在最前
    user_prompt: str = f"标题：{title}\n正文：{text}" if title else f"内容：{text}"

    # 组装 Ollama API Payload
    # format="json" 强制 Ollama 输出合法 JSON，避免 markdown 代码块干扰解析
    payload: dict = {
        "model": LLM_CONFIG["MODEL_NAME"],
        "system": LLM_CONFIG["SYSTEM_PROMPT"],
        "prompt": user_prompt,
        "stream": False,        # 单次请求模式，不使用流式返回
        "format": "json",       # 强制 JSON 格式输出（Ollama >= 0.1.23 支持）
    }

    try:
        response = requests.post(
            LLM_CONFIG["OLLAMA_API"],
            json=payload,
            timeout=LLM_CONFIG["TIMEOUT"],
        )
        response.raise_for_status()

        # 解析 Ollama 的响应体，取出 "response" 字段（即模型生成的文本）
        raw_text: str = response.json().get("response", "")
        return _parse_llm_response(raw_text)

    except requests.exceptions.Timeout:
        # 超时是最常见的失败场景，单独记录以便监控
        logger.warning("[LLM引擎] 请求超时 (%.1fs)，已降级处理。", LLM_CONFIG["TIMEOUT"])
        return LLMScore(reasoning="LLM引擎超时，已降级到正则模式")

    except requests.exceptions.ConnectionError:
        # Ollama 服务未启动或端口未开放
        logger.warning("[LLM引擎] 无法连接到 Ollama 服务 (%s)，请确认服务已启动。", LLM_CONFIG["OLLAMA_API"])
        return LLMScore(reasoning="Ollama服务未启动或连接被拒")

    except requests.exceptions.RequestException as exc:
        logger.error("[LLM引擎] 网络请求异常: %s", exc)
        return LLMScore(reasoning=f"网络请求异常: {type(exc).__name__}")

    except Exception as exc:  # 兜底：捕获一切未预料的异常
        logger.error("[LLM引擎] 未知异常: %s", exc)
        return LLMScore(reasoning=f"未知异常: {type(exc).__name__}")


def check_availability() -> bool:
    """
    快速检查本地 Ollama 服务是否在线（用于启动时的健康检测）。

    Returns:
        True 表示 Ollama 服务可用，False 表示不可用（需降级到正则模式）。
    """
    from config import API_CONFIG
    try:
        resp = requests.get(
            API_CONFIG["OLLAMA_HEALTH"],
            timeout=API_CONFIG["LLM_HEALTH_TIMEOUT"],
        )
        return resp.status_code == 200
    except requests.exceptions.RequestException:
        return False


# ---------------------------------------------------------------------------
# 私有辅助函数
# ---------------------------------------------------------------------------
def _parse_llm_response(raw_text: str) -> LLMScore:
    """
    解析 Ollama 返回的 JSON 字符串，提取 score 和 reasoning。

    期望格式（来自 System Prompt 约定）：
        {"score": 2, "reasoning": "发现Type2现货断供信号，提到上游全面封盘"}

    容错处理：
        - 若字段缺失，score 默认为 0，reasoning 保留原始文本便于排查。
        - score 超出 [0, 2] 范围时，强制截断到合法区间。

    Args:
        raw_text: Ollama 返回的原始文本（应为 JSON 字符串）。

    Returns:
        解析后的 LLMScore 对象。
    """
    if not raw_text.strip():
        logger.debug("[LLM解析] 模型返回空文本。")
        return LLMScore(reasoning="模型返回内容为空")

    try:
        # 抗幻觉防御：如果小模型依然输出了 ```json ... ``` 块，正则剥离掉它
        clean_text = raw_text.strip()
        match = re.search(r"```(?:json)?\s*(.*?)\s*```", clean_text, re.DOTALL)
        if match:
            clean_text = match.group(1).strip()
            
        data: dict = json.loads(clean_text)

        # 提取 score，并强制约束在 [0, 2] 区间
        raw_score = data.get("score", 0)
        score: int = max(0, min(2, int(raw_score)))

        reasoning: str = str(data.get("reasoning", "")).strip() or "模型未给出理由"

        logger.debug("[LLM解析] score=%d | reasoning=%s", score, reasoning)
        return LLMScore(score=score, reasoning=reasoning, success=True)

    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        # JSON 解析失败时保留原始文本，便于调试 Prompt 是否需要调整
        logger.warning("[LLM解析] JSON解析失败: %s | 原始返回: %.100s", exc, raw_text)
        return LLMScore(reasoning=f"JSON解析失败，原始输出: {raw_text[:80]}")

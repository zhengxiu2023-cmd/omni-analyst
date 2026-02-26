# -*- coding: utf-8 -*-
"""
🛡️ core/network_engine.py — 防弹网络层 (Military-Grade Network Engine)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
职责：
  - 提供带指数退避（Exponential Backoff）的统一重试请求方法。
  - 提供专用于大文件（PDF）的分块流式（Chunk Streaming）下载器，防内存溢出。
  - 捕获所有网络层异常，失败时静默返回 None，绝不崩溃主进程。

配置来源：config.py > API_CONFIG（REQUEST_RETRIES / DEFAULT_TIMEOUT / STREAM_TIMEOUT）
"""

import time
import random
import logging
from typing import Optional, Iterator

import requests
import requests.exceptions
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config import API_CONFIG

# 模块级别 logger，统一由上层 utils/logger.py 配置格式
logger = logging.getLogger(__name__)

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
]


def safe_request(
    url: str,
    method: str = "get",
    stream: bool = False,
    headers: Optional[dict] = None,
    **kwargs,
) -> Optional[requests.Response]:
    """
    军工级网络请求方法，内置指数退避重试。

    Args:
        url:     目标 URL。
        method:  HTTP 方法，支持 "get" 和 "post"。
        stream:  是否启用流式传输模式（用于大文件 Chunk 下载）。
        headers: 自定义请求头；若为 None 则使用 config 中的默认 Headers。
        **kwargs: 透传给 requests 的其他参数（params / data / json 等）。

    Returns:
        成功时返回 requests.Response 对象；
        超出最大重试次数后静默返回 None。
    """
    # 读取配置：重试次数和超时时间
    max_retries: int = API_CONFIG.get("REQUEST_RETRIES", 3)
    timeout: int = API_CONFIG.get("STREAM_TIMEOUT", 30) if stream else API_CONFIG.get("DEFAULT_TIMEOUT", 10)

    # 如果调用方未显式传入 headers，使用 config 中的默认浏览器 UA 或生成一个
    if headers is None:
        headers = API_CONFIG.get("HEADERS", {})
        if "User-Agent" not in headers:
            headers["User-Agent"] = random.choice(USER_AGENTS)
    else:
        # 如果传入了 headers 但没有 UA，也加上随机的 UA 以防爬
        if "User-Agent" not in headers:
            headers["User-Agent"] = random.choice(USER_AGENTS)

    # 强制抖动防爬
    jitter = random.uniform(1.0, 3.0)
    logger.debug(f"[网络层] 请求前强制防爬抖动 {jitter:.2f}s: {url}")
    time.sleep(jitter)

    @retry(
        stop=stop_after_attempt(max_retries),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type((
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            requests.exceptions.HTTPError,
            requests.exceptions.ChunkedEncodingError,
        )),
        reraise=True
    )
    def _execute_request():
        if method.lower() == "post":
            response = requests.post(
                url,
                headers=headers,
                timeout=timeout,
                stream=stream,
                **kwargs,
            )
        else:
            response = requests.get(
                url,
                headers=headers,
                timeout=timeout,
                stream=stream,
                **kwargs,
            )
        response.raise_for_status()
        return response

    try:
        return _execute_request()
    except Exception as exc:
        logger.error(
            "[网络层] 已达最大重试次数 (%d)，放弃请求: %s | 原因: %s",
            max_retries,
            url,
            exc,
        )
        return None


def stream_download(url: str, chunk_size: int = 8192) -> Iterator[bytes]:
    """
    大文件分块流式下载生成器（专为巨潮 PDF 设计，防止 OOM）。

    用法示例：
        with open("report.pdf", "wb") as f:
            for chunk in stream_download(url):
                f.write(chunk)

    Args:
        url:        文件下载 URL。
        chunk_size: 每次读取的字节块大小，默认 8 KB。

    Yields:
        bytes: 每个数据块的字节内容。
        若请求失败则不产生任何 yield（生成器直接终止）。
    """
    response = safe_request(url, method="get", stream=True)

    if response is None:
        logger.error("[流式下载] 无法建立连接，下载终止: %s", url)
        return  # 生成器提前退出，调用方会得到空迭代

    try:
        for chunk in response.iter_content(chunk_size=chunk_size):
            # iter_content 在网络中断时可能产生空块，过滤掉
            if chunk:
                yield chunk
    except requests.exceptions.RequestException as exc:
        logger.error("[流式下载] 数据传输中断: %s | 原因: %s", url, exc)
    finally:
        # 确保底层连接在生成器结束后立即释放
        response.close()

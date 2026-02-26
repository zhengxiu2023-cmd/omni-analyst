# -*- coding: utf-8 -*-
"""
🪵 utils/logger.py — 统一终端日志格式化工具
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
设计哲学：保持终端的绝对纯净。
  - INFO  级别：只输出干净的业务信息，无时间戳，无模块路径。
  - WARNING 级别：带上时间戳 + 模块名，便于定位问题。
  - ERROR   级别：带上完整上下文（模块名+行号），便于排障。

用法：
    from utils.logger import setup_logging
    setup_logging()   # 在 main.py 最顶部调用一次即可

    import logging
    logger = logging.getLogger(__name__)
    logger.info("✅ 正在抓取 [龙虎榜]...")
    logger.warning("⚠️ 接口超时，准备重试...")
    logger.error("❌ 无法连接到 Ollama：连接被拒")
"""

import logging
import sys


# ---------------------------------------------------------------------------
# 自定义 Formatter：根据日志级别动态切换格式
# ---------------------------------------------------------------------------
class _TieredFormatter(logging.Formatter):
    """
    分级格式化器：不同日志级别使用不同的输出格式。

    INFO  → "消息内容"               (最干净，直接展示业务信息)
    WARN  → "[WARN  模块名] 消息"    (带模块名，便于定位)
    ERROR → "[ERROR 模块名:行号] 消息" (带完整上下文)
    """

    _FMT_INFO: str = "%(message)s"
    _FMT_WARN: str = "\033[33m[WARN  %(name)s]\033[0m %(message)s"
    _FMT_ERROR: str = "\033[31m[ERROR %(name)s:%(lineno)d]\033[0m %(message)s"

    def format(self, record: logging.LogRecord) -> str:
        if record.levelno >= logging.ERROR:
            fmt = self._FMT_ERROR
        elif record.levelno >= logging.WARNING:
            fmt = self._FMT_WARN
        else:
            fmt = self._FMT_INFO

        formatter = logging.Formatter(fmt)
        return formatter.format(record)


def setup_logging(level: int = logging.INFO) -> None:
    """
    初始化全局日志配置。在 main.py 启动时调用一次即可。

    Args:
        level: 根 Logger 的最低日志级别，默认 INFO。
               调试阶段可传入 logging.DEBUG 获取更多内部信息。
    """
    root_logger = logging.getLogger()

    # 防止重复初始化（幂等）
    if root_logger.handlers:
        return

    root_logger.setLevel(level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(_TieredFormatter())

    root_logger.addHandler(handler)

    # 静默掉第三方库的嘈杂日志（urllib3、akshare 等）
    logging.getLogger("urllib3").setLevel(logging.ERROR)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("akshare").setLevel(logging.WARNING)
    logging.getLogger("pymongo").setLevel(logging.WARNING)

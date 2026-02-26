# -*- coding: utf-8 -*-
"""
🔌 core/db_client.py — MongoDB 可选持久化客户端
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
职责：
  - 封装 MongoDB 写入操作，将审计完成的 StockInfo + RiskStatus 沉淀为历史档案。
  - 完全可选：MongoDB 未启动或 pymongo 未安装时静默失败，绝不影响主流程。
  - 提供一个简洁的 save_target_to_db() 接口，屏蔽连接细节。

配置来源：config.py > MONGO_CONFIG
"""

import logging
from datetime import datetime

from config import MONGO_CONFIG
from core.models import RiskStatus, StockInfo

logger = logging.getLogger(__name__)

# 尝试导入 pymongo，未安装时优雅降级
try:
    from pymongo import MongoClient
    from pymongo.errors import ServerSelectionTimeoutError

    _MONGO_AVAILABLE = True
except ImportError:
    MongoClient = None  # type: ignore
    ServerSelectionTimeoutError = Exception  # type: ignore
    _MONGO_AVAILABLE = False
    logger.info("[DB] pymongo 未安装，MongoDB 持久化功能不可用（可选功能）。")


# 模块级连接单例，避免每次写入都重新建立连接
_collection = None
_connection_failed: bool = False  # 标记连接是否已确认失败，避免反复重试


def _get_collection():
    """
    获取 MongoDB Collection 单例。

    首次调用时尝试连接，失败后设置 _connection_failed 标记，
    后续调用直接返回 None，避免每次写入都触发超时等待。

    Returns:
        pymongo.Collection 或 None（不可用时）。
    """
    global _collection, _connection_failed

    # 已确认连接失败，不再重试
    if _connection_failed:
        return None

    # 已有可用连接，直接复用
    if _collection is not None:
        return _collection

    # pymongo 未安装，直接放弃
    if not _MONGO_AVAILABLE:
        _connection_failed = True
        return None

    try:
        client = MongoClient(
            MONGO_CONFIG["URI"],
            serverSelectionTimeoutMS=MONGO_CONFIG["TIMEOUT_MS"],
        )
        # 发起一次轻量请求以验证实际连通性
        client.server_info()

        _collection = client[MONGO_CONFIG["DB_NAME"]][MONGO_CONFIG["COLLECTION_NAME"]]
        logger.info(
            "[DB] MongoDB 连接成功: %s / %s",
            MONGO_CONFIG["DB_NAME"],
            MONGO_CONFIG["COLLECTION_NAME"],
        )
        return _collection

    except ServerSelectionTimeoutError:
        _connection_failed = True
        logger.info("[DB] MongoDB 服务未启动，持久化功能自动禁用（不影响主流程）。")
        return None

    except Exception as exc:
        _connection_failed = True
        logger.warning("[DB] MongoDB 连接异常: %s", exc)
        return None


def save_target_to_db(stock_info: StockInfo, risk_status: RiskStatus) -> bool:
    """
    将单只股票的审计结果写入 MongoDB 历史靶标库。

    文档结构：以 (code, timestamp) 为自然主键，每次审计都插入一条新文档，
    保留完整的时序历史，方便后续趋势分析。

    Args:
        stock_info:  已完整填充的 StockInfo 对象。
        risk_status: 已完成熔断判定的 RiskStatus 对象。

    Returns:
        True 表示写入成功，False 表示 MongoDB 不可用或写入失败。
    """
    collection = _get_collection()

    if collection is None:
        return False

    try:
        document = {
            # --- 基础行情字段 ---
            "code": stock_info.code,
            "name": stock_info.name,
            "price": stock_info.price,
            "turnover": stock_info.turnover,
            "pe_ttm": stock_info.pe_ttm,
            "pb": stock_info.pb,
            "total_mv": stock_info.total_mv,
            # --- 历史分位字段 ---
            "min_price_3y": stock_info.min_price_3y,
            "price_percentile": stock_info.price_percentile,
            "rise_from_bottom": stock_info.rise_from_bottom,
            # --- 判定状态字段 ---
            "holder_trend": stock_info.holder_trend,
            "eps_forecast": stock_info.eps_forecast,
            # --- 风控熔断结果 ---
            "is_safe": risk_status.is_safe,
            "market_vol_desc": risk_status.market_vol_desc,
            "death_turnover_warn": risk_status.death_turnover_warn,
            "extreme_rise_warn": risk_status.extreme_rise_warn,
            "st_warning": risk_status.st_warning,
            # --- 元数据 ---
            "audit_timestamp": datetime.now(),
        }

        result = collection.insert_one(document)
        logger.info(
            "[DB] ✅ 已将 %s(%s) 审计数据写入 MongoDB (id=%s)。",
            stock_info.name,
            stock_info.code,
            result.inserted_id,
        )
        return True

    except Exception as exc:
        logger.error("[DB] 写入失败: %s", exc)
        return False

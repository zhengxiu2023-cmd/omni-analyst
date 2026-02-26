# -*- coding: utf-8 -*-
"""
🟡 fetchers/akshare_client.py — 行情底座与市场情报采集器
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
职责：
  - fetch_radar_news()     : 抓取龙虎榜、机构调研、全球快讯、板块异动，封装为 NewsItem 列表。
  - fetch_stock_info()     : 多源容灾获取单只股票行情，封装为 StockInfo 对象。
  - fetch_kline_extremes() : 获取近 3 年前复权 K 线，计算历史分位与风控指标，回填 StockInfo。

核心守则：
  - 网络请求必须通过 core.network_engine.safe_request 发起，享受防弹重试。
  - 所有输出必须是 NewsItem 或 StockInfo 对象，禁止返回裸 dict。
  - 每个函数独立 try...except 兜底，绝不相互污染。

配置来源：config.py > API_CONFIG / RISK_THRESHOLDS / EM_UT_TOKEN 等。
"""

import logging
from datetime import datetime, timedelta
from typing import Union

import akshare as ak
import pandas as pd

from config import (
    API_CONFIG,
    CNINFO_CATEGORIES,
    EM_MKT_UT_TOKEN,
    EM_REALTIME_FIELDS,
    EM_UT_TOKEN,
    RISK_THRESHOLDS,
)
from core.models import NewsItem, StockInfo
from core.network_engine import safe_request

logger = logging.getLogger(__name__)


# ===========================================================================
# 公共辅助：东财市场标识映射
# ===========================================================================
def _get_market_prefix(code: str) -> str:
    """根据股票代码首字符推断东财市场前缀（"1"=沪市, "0"=深/创）。"""
    return "1" if str(code).startswith("6") else "0"


def _safe_float(value, default: float = 0.0) -> float:
    """安全类型转换：将任意值转为 float，失败时返回默认值。"""
    try:
        if value is None or str(value) in ("", "nan", "None", "-"):
            return default
        return float(value)
    except (ValueError, TypeError):
        return default


def _safe_numeric(value) -> Union[str, float]:
    """
    将 PE/PB 等可能缺失的数值转为 float 或 'N/A' 字符串。
    符合 DATA_CONTRACTS.md 中 pe_ttm / pb 字段的类型约定。
    """
    try:
        if value is None or str(value) in ("", "nan", "None", "-", "N/A"):
            return "N/A"
        return round(float(value), 2)
    except (ValueError, TypeError):
        return "N/A"


# ===========================================================================
# 1. fetch_radar_news — 情报雷达：龙虎榜 / 机构调研 / 全球快讯 / 板块异动
# ===========================================================================
def fetch_radar_news() -> list[NewsItem]:
    """
    从四大情报源抓取市场异动信息，统一封装为 NewsItem 列表。

    情报源（提取自 souji0_1.py module_1_intel_radar）：
      1. 机构调研（接待机构数 > 100 家）
      2. 龙虎榜机构净买入（> 6000 万元）
      3. 财联社全球产业快讯
      4. 强势板块异动（涨跌幅 > 4.5%）

    Returns:
        list[NewsItem]：汇总的情报列表，score/tags 由 llm_engine 在上层填充。
        任意单个来源失败不影响其他来源的结果汇总。
    """
    results: list[NewsItem] = []

    # --- 情报源 1：机构调研穿透（聪明钱前瞻信号）---
    try:
        df_jg = ak.stock_jgdy_tj_em()
        # 筛选接待机构数超过 100 家的高热度调研
        hot_jg = df_jg[df_jg["接待机构数量"] > 100].head(4)
        for _, row in hot_jg.iterrows():
            company: str = str(row.get("公司名称", ""))
            count: int = int(row.get("接待机构数量", 0))
            date_val = row.get("最新调研日期", "")
            results.append(
                NewsItem(
                    time=str(date_val),
                    title=(
                        f"绝对暗流！【{company}】近期遭 {count} 家顶级机构踏破门槛调研，"
                        "警惕主力吸筹前哨。"
                    ),
                    source="机构调研穿透",
                    tags=["🎯 [机构建仓前兆]"],
                    score=1,
                )
            )
        logger.info("[雷达] 机构调研: 命中 %d 条。", len(hot_jg))
    except Exception as exc:
        logger.warning("[雷达] 机构调研接口失败: %s", exc)

    # --- 情报源 2：龙虎榜机构席位暴力净买入（真金白银强信号）---
    try:
        start_d: str = (datetime.now() - timedelta(days=15)).strftime("%Y%m%d")
        end_d: str = datetime.now().strftime("%Y%m%d")
        df_lhb = ak.stock_lhb_jgmmtj_em(start_date=start_d, end_date=end_d)

        lhb_matched: int = 0  # 用局部计数变量，避免 df_lhb 为空时 hot_lhb 未定义
        if not df_lhb.empty:
            df_lhb["机构净买额"] = pd.to_numeric(df_lhb["机构净买额"], errors="coerce")
            # 筛选机构净买入超过 6000 万的标的
            threshold: float = 6_000_0000  # 6000 万元（单位：元）
            hot_lhb = df_lhb[df_lhb["机构净买额"] > threshold].head(5)
            lhb_matched = len(hot_lhb)
            for _, row in hot_lhb.iterrows():
                name: str = str(row.get("股票名称", ""))
                code: str = str(row.get("股票代码", ""))
                amount_yi: float = _safe_float(row.get("机构净买额", 0)) / 1e8
                results.append(
                    NewsItem(
                        time=f"近期龙虎榜({start_d}~{end_d})",
                        title=(
                            f"真金白银强盖章！【{name}({code})】遭机构席位暴力净买入 "
                            f"{amount_yi:.2f} 亿元！"
                        ),
                        source="龙虎榜雷达",
                        tags=["🔥 [席位暴力抢筹]"],
                        score=2,
                    )
                )
        logger.info("[雷达] 龙虎榜: 命中 %d 条。", lhb_matched)
    except Exception as exc:
        logger.warning("[雷达] 龙虎榜接口失败: %s", exc)

    # --- 情报源 3：财联社全球产业快讯（海外科技奇点 & 现货断裂）---
    try:
        df_cls = ak.stock_info_global_cls().head(120)
        for _, row in df_cls.iterrows():
            title: str = str(row.get("标题", "")).strip()
            content: str = str(row.get("内容", "")).strip()
            pub_time: str = str(row.get("发布时间", ""))

            if not title:
                continue

            results.append(
                NewsItem(
                    time=pub_time,
                    title=title,
                    source="全球产业快讯",
                    tags=[],
                    score=0,
                    # content 暂存于 llm_reasoning 供评分引擎读取
                    llm_reasoning=content[:200],
                )
            )
        logger.info("[雷达] 全球快讯: 抓取 %d 条。", len(df_cls))
    except Exception as exc:
        logger.warning("[雷达] 全球快讯接口失败: %s", exc)

    # --- 情报源 4：强势板块异动（资金主线与市场共振信号）---
    try:
        df_board = ak.stock_board_industry_name_em()
        # 涨跌幅超过 4.5% 视为主线资金暴动信号
        hot_boards = df_board[df_board["涨跌幅"] > 4.5].head(3)
        for _, row in hot_boards.iterrows():
            board_name: str = str(row.get("板块名称", ""))
            change_pct: float = _safe_float(row.get("涨跌幅", 0))
            results.append(
                NewsItem(
                    time="今日盘面",
                    title=(
                        f"主线确认！【{board_name}】今日暴涨 {change_pct:.2f}%，"
                        "属于全市场绝对资金共识！"
                    ),
                    source="板块资金雷达",
                    tags=["📈 [资金共振高潮]"],
                    score=2,
                )
            )
        logger.info("[雷达] 板块异动: 命中 %d 条。", len(hot_boards))
    except Exception as exc:
        logger.warning("[雷达] 板块异动接口失败: %s", exc)

    logger.info("[雷达] 情报汇总完成，共 %d 条原始情报。", len(results))
    return results


# ===========================================================================
# 2. fetch_stock_info — 多源容灾获取单只股票基础行情
# ===========================================================================
def fetch_stock_info(code: str) -> StockInfo:
    """
    多源容灾获取单只股票行情，封装为 StockInfo 对象。

    数据源优先级（提取自 souji0_1.py _get_stock_info）：
      主力源 — 东财 Push2 实时行情接口（价格/PE/PB/换手/总市值）
      兜底源 — akshare stock_individual_info_em（仅补充名称与价格）

    Args:
        code: 6 位 A 股代码字符串（不含市场前缀）。

    Returns:
        StockInfo：字段尽量填满；所有接口失败时返回 code 为名称的最小安全对象。
    """
    # 预设最小安全返回值，避免外部使用 None 判断
    fallback = StockInfo(
        code=code,
        name=code,          # 名称兜底使用代码本身
        price=0.0,
        turnover=0.0,
        pe_ttm="N/A",
        pb="N/A",
        total_mv=0.0,
    )

    market_prefix: str = _get_market_prefix(code)
    secid: str = f"{market_prefix}.{code}"

    # -------------------------------------------------------------------------
    # 主力源：东财 Push2 实时行情（safe_request 享受防弹重试）
    # -------------------------------------------------------------------------
    try:
        params = {
            "secid": secid,
            "fields": EM_REALTIME_FIELDS,
            "ut": EM_UT_TOKEN,
            "fltt": 2,
            "invt": 2,
        }
        resp = safe_request(
            API_CONFIG["EASTMONEY_PUSH2"],
            method="get",
            params=params,
            # Push2 轻量接口用完整 Headers 防反爬
            headers=API_CONFIG["HEADERS"],
        )

        if resp is not None:
            data: dict = resp.json().get("data") or {}
            # f60=最新价，存在且非 None 视为有效数据
            if data.get("f60") is not None:
                name: str = str(data.get("f58", code))
                price: float = _safe_float(data.get("f60"))
                turnover: float = _safe_float(data.get("f168"))
                total_mv: float = _safe_float(data.get("f116"))
                pe_ttm = _safe_numeric(data.get("f162"))
                pb = _safe_numeric(data.get("f167"))

                logger.info(
                    "[行情] [Push2] %s(%s) | 价=%.2f PE=%s PB=%s 换手=%.2f%%",
                    name, code, price, pe_ttm, pb, turnover,
                )
                return StockInfo(
                    code=code,
                    name=name,
                    price=price,
                    turnover=turnover,
                    pe_ttm=pe_ttm,
                    pb=pb,
                    total_mv=total_mv,
                )
    except Exception as exc:
        logger.warning("[行情] [Push2] %s 失败: %s", code, exc)

    # -------------------------------------------------------------------------
    # 二级源：akshare 实时行情快照（不走东财 Push2 反爬层，字段丰富）
    # -------------------------------------------------------------------------
    try:
        df_spot = ak.stock_zh_a_spot_em()
        row_match = df_spot[df_spot["代码"] == code]
        if not row_match.empty:
            row = row_match.iloc[0]
            name = str(row.get("名称", code))
            price = _safe_float(row.get("最新价"))
            turnover = _safe_float(row.get("换手率"))
            total_mv = _safe_float(row.get("总市值"))
            pe_ttm = _safe_numeric(row.get("市盈率-动态"))
            pb = _safe_numeric(row.get("市净率"))

            logger.info(
                "[行情] [akshare快照] %s(%s) | 价=%.2f PE=%s PB=%s 换手=%.2f%%",
                name, code, price, pe_ttm, pb, turnover,
            )
            return StockInfo(
                code=code,
                name=name,
                price=price,
                turnover=turnover,
                pe_ttm=pe_ttm,
                pb=pb,
                total_mv=total_mv,
            )
    except Exception as exc:
        logger.warning("[行情] [akshare快照] %s 失败: %s", code, exc)

    # -------------------------------------------------------------------------
    # 三级兜底：akshare stock_individual_info_em（仅补充名称与价格）
    # -------------------------------------------------------------------------
    try:
        info_df = ak.stock_individual_info_em(symbol=code)
        if not info_df.empty:
            name_rows = info_df[info_df["item"] == "股票简称"]
            name = str(name_rows["value"].values[0]) if not name_rows.empty else code

            price_rows = info_df[info_df["item"] == "最新"]
            price = _safe_float(
                price_rows["value"].values[0] if not price_rows.empty else 0
            )

            logger.info("[行情] [三级兜底] %s(%s) 名称补全完成。", name, code)
            fallback.name = name
            fallback.price = price
            return fallback
    except Exception as exc:
        logger.error("[行情] [三级兜底] %s 失败: %s", code, exc)

    # -------------------------------------------------------------------------
    # 四级终极兜底：akshare 新浪实时行情（彻底摆脱东财限制，确保能取到名字和价格）
    # -------------------------------------------------------------------------
    try:
        df_sina = ak.stock_zh_a_spot()
        # 新浪接口的代码带有 sh/sz 前缀
        row_match = df_sina[df_sina["代码"] == secid.replace(".", "").lower()]
        
        if not row_match.empty:
            row = row_match.iloc[0]
            name = str(row.get("名称", code))
            price = _safe_float(row.get("最新价"))

            logger.info("[行情] [四级新浪兜底] %s(%s) 名称与价格补全完成。", name, code)
            fallback.name = name
            fallback.price = price
            return fallback
    except Exception as exc:
        logger.error("[行情] [四级新浪兜底] %s 也失败，返回最小安全对象: %s", code, exc)

    return fallback


# ===========================================================================
# 3. fetch_kline_extremes — 近 3 年 K 线历史分位与风控指标计算
# ===========================================================================
def fetch_kline_extremes(code: str, stock_info: StockInfo) -> StockInfo:
    """
    获取近 3 年前复权日线 K 线，计算历史极值与风控红线，回填到 StockInfo。

    计算字段（提取自 souji0_1.py _generate_parameters 的 kline 段）：
      - min_price_3y       : 近 3 年最低价（前复权）
      - price_percentile   : 当前价在近 3 年区间的百分位（0~100）
      - rise_from_bottom   : 距近 3 年低点的反弹幅度（%）

    风控红线（写入 StockInfo 可供 risk_auditor 使用）：
      - 近 5 日最大换手率 > DEATH_TURNOVER_PCT → holder_trend 追加死亡换手警告

    Args:
        code:       6 位 A 股代码。
        stock_info: 已由 fetch_stock_info 填充的 StockInfo 对象（直接修改并返回）。

    Returns:
        修改后的 StockInfo（历史分位字段已回填）；若接口失败则原样返回。
    """
    market_prefix: str = _get_market_prefix(code)
    secid: str = f"{market_prefix}.{code}"

    start_date: str = (datetime.now() - timedelta(days=365 * 3)).strftime("%Y%m%d")
    end_date: str = datetime.now().strftime("%Y%m%d")

    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6",
        # f51=日期,f52=开,f53=收,f54=高,f55=低,f56=量,f57=额,f58=振幅,f59=涨跌幅,f60=涨跌额,f61=换手
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": 101,     # 101=日线
        "fqt": 1,       # 1=前复权
        "beg": start_date,
        "end": end_date,
        "ut": EM_UT_TOKEN,
    }

    try:
        resp = safe_request(
            API_CONFIG["EASTMONEY_KLINE"],
            method="get",
            params=params,
            headers=API_CONFIG["HEADERS"],
        )

        if resp is None:
            logger.warning("[K线] %s: safe_request 返回 None，跳过历史分位计算。", code)
            return stock_info

        kline_data: dict = resp.json().get("data") or {}
        klines: list[str] = kline_data.get("klines") or []

        if not klines:
            logger.warning("[K线] %s: 无 K 线数据（%d 条），跳过分位计算。", code, len(klines))
            return stock_info

        # 解析 K 线：每条格式为 "日期,开,收,高,低,量,额,振幅,涨跌幅,涨跌额,换手"
        # 索引：  0   1  2  3  4  5  6   7    8    9    10
        lows: list[float] = []
        highs: list[float] = []
        turnovers: list[float] = []

        for kline in klines:
            parts = kline.split(",")
            if len(parts) < 11:
                continue
            lows.append(_safe_float(parts[4]))
            highs.append(_safe_float(parts[3]))
            turnovers.append(_safe_float(parts[10]))

        if not lows:
            return stock_info

        p_now: float = stock_info.price
        p_min_3y: float = min(lows)
        p_max_3y: float = max(highs)

        # 百分位：当前价在近 3 年高低区间的位置
        price_percentile: float = (
            (p_now - p_min_3y) / (p_max_3y - p_min_3y) * 100
            if p_max_3y != p_min_3y
            else 0.0
        )

        # 底部反弹幅度
        rise_from_bottom: float = (
            (p_now - p_min_3y) / p_min_3y * 100 if p_min_3y > 0 else 0.0
        )

        # 回填历史分位字段
        stock_info.min_price_3y = round(p_min_3y, 2)
        stock_info.price_percentile = round(price_percentile, 1)
        stock_info.rise_from_bottom = round(rise_from_bottom, 1)

        logger.info(
            "[K线] %s: 共 %d 条 | 最低=%.2f | 分位=%.1f%% | 反弹=%.1f%%",
            code, len(klines), p_min_3y, price_percentile, rise_from_bottom,
        )

        # ── 风控红线：死亡换手检测（近 5 日最大换手率）──
        if len(turnovers) >= 5:
            max_turnover_5d: float = max(turnovers[-5:])
            death_threshold: float = RISK_THRESHOLDS["DEATH_TURNOVER_PCT"]

            # 排除新股（沪深新股名称以大写 N/C 开头）和 ST 股，换手率规律特殊
            name_upper: str = stock_info.name.upper()
            is_new_or_st: bool = (
                name_upper.startswith("N")
                or name_upper.startswith("C")
                or "ST" in stock_info.name
            )

            if max_turnover_5d > death_threshold and not is_new_or_st:
                stock_info.holder_trend = (
                    f"⚠️ 死亡换手警报！近5日极大换手率 {max_turnover_5d:.1f}% "
                    f"(红线: {death_threshold:.0f}%)，建议立即清仓规避。"
                )
                logger.warning(
                    "[风控] %s 触发死亡换手红线: %.1f%%", code, max_turnover_5d,
                )

    except Exception as exc:
        logger.error("[K线] %s 历史分位计算失败: %s", code, exc)

    return stock_info


# ===========================================================================
# 4. fetch_market_volume — 获取大盘总成交额（供 RiskStatus 使用）
# ===========================================================================
def fetch_market_volume() -> float:
    """
    获取今日沪深两市总成交额（单位：万亿元）。

    使用东财大盘接口（提取自 souji0_1.py _generate_parameters 的 market_vol 段）。

    Returns:
        float: 今日总成交额（万亿元），失败时返回默认常态值 1.0。
    """
    default_vol: float = 1.0  # 默认常态震荡值，失败时兜底

    try:
        params = {
            "fltt": 2,
            "invt": 2,
            "fields": "f12,f6",        # f12=代码, f6=全天成交额
            "secids": "1.000001,0.399001,1.000016,0.399006",
            "ut": EM_MKT_UT_TOKEN,
        }
        resp = safe_request(
            API_CONFIG["EASTMONEY_MARKET_VOL"],
            method="get",
            params=params,
            headers=API_CONFIG["HEADERS"],
        )

        if resp is None:
            return default_vol

        diff: list = resp.json().get("data", {}).get("diff", []) or []
        total_vol_yuan: float = sum(
            _safe_float(d.get("f6", 0)) for d in diff if d
        )
        total_vol_tr: float = total_vol_yuan / 1e12  # 转换为万亿

        logger.info("[大盘] 今日总成交额: %.2f 万亿", total_vol_tr)
        return total_vol_tr

    except Exception as exc:
        logger.error("[大盘] 成交额获取失败，使用默认值 %.1f 万亿: %s", default_vol, exc)
        return default_vol

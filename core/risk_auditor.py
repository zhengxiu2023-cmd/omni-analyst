# -*- coding: utf-8 -*-
"""
⚡ core/risk_auditor.py — 风控熔断与参数面板组装引擎
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
职责：
  - evaluate_risk()       : 依据硬编码阈值执行物理熔断判定，输出 RiskStatus。
  - generate_panel_markdown(): 智能合并生成"00_参数面板_发给AI.md"，
                               保留用户手动修改的低信心字段，刷新高信心字段。

核心契约：
  - 输入 StockInfo + market_vol → 输出 RiskStatus（evaluate_risk）。
  - 输入 StockInfo + RiskStatus → 写文件（generate_panel_markdown）。
  - 对已存在的参数面板执行"智能合并"，而非粗暴覆盖。

配置来源：config.py > RISK_THRESHOLDS / EXPORT_CONFIG
数据契约：core/models.py > StockInfo / RiskStatus
"""

import logging
import os
from datetime import datetime

from config import EXPORT_CONFIG, RISK_THRESHOLDS
from core.models import RiskStatus, StockInfo

logger = logging.getLogger(__name__)


# ===========================================================================
# 1. evaluate_risk — 物理熔断判定
# ===========================================================================
def evaluate_risk(stock_info: StockInfo, market_vol: float) -> RiskStatus:
    """
    依据 RISK_THRESHOLDS 对股票执行全面的物理风控熔断判定。

    判定维度（对应 DATA_CONTRACTS.md 2.1 节）：
      1. 死亡换手率：当日换手率 > DEATH_TURNOVER_PCT(40%)
      2. 极端透支：底部反弹幅度 > EXTREME_RISE_60D_PCT(150%)（使用 rise_from_bottom 近似）
      3. 大盘流动性：market_vol 与 FRENZY/FREEZE 阈值比较，生成 F 乘数描述
      4. ST/负净资产暴雷：名字含 ST 或 PB 为负数

    任意红线触发 → is_safe = False。

    Args:
        stock_info:  已完整填充的 StockInfo 对象（含实时行情与历史分位）。
        market_vol:  今日两市总成交额（单位：万亿元），由 fetch_market_volume() 提供。

    Returns:
        RiskStatus：包含完整判定结果的风控状态对象。
    """
    status = RiskStatus()  # 初始安全默认值

    # ── 判定 1：死亡换手率 ──
    death_threshold: float = RISK_THRESHOLDS["DEATH_TURNOVER_PCT"]

    # 排除新股（名称含 N/C）和 ST 股本身换手率规律不同
    is_new_listing: bool = (
        stock_info.name.startswith("N")
        or stock_info.name.startswith("C")
    )

    if stock_info.turnover > death_threshold and not is_new_listing:
        status.is_safe = False
        status.death_turnover_warn = (
            f"⚠️ [触发死亡换手清仓线！今日换手率 {stock_info.turnover:.2f}% "
            f"> 红线 {death_threshold:.0f}%]"
        )
        logger.warning(
            "[风控] %s(%s) 触发死亡换手: %.2f%%",
            stock_info.name, stock_info.code, stock_info.turnover,
        )
    else:
        status.death_turnover_warn = "[安全]"

    # ── 判定 2：极端透支（使用底部反弹幅度近似 60 日涨幅）──
    extreme_threshold: float = RISK_THRESHOLDS["EXTREME_RISE_60D_PCT"]

    if stock_info.rise_from_bottom > extreme_threshold:
        status.is_safe = False
        status.extreme_rise_warn = (
            f"⚠️ [极端透支警报！自底部已暴涨 {stock_info.rise_from_bottom:.1f}% "
            f"> 红线 {extreme_threshold:.0f}%，极度透支！]"
        )
        logger.warning(
            "[风控] %s(%s) 触发极端透支: +%.1f%%",
            stock_info.name, stock_info.code, stock_info.rise_from_bottom,
        )

    # ── 判定 3：大盘流动性 F 乘数描述 ──
    frenzy_tr: float = RISK_THRESHOLDS["MARKET_VOL_FRENZY_TR"]
    freeze_tr: float = RISK_THRESHOLDS["MARKET_VOL_FREEZE_TR"]

    if market_vol >= frenzy_tr:
        status.market_vol_desc = (
            f"{market_vol:.2f} 万亿 (疯牛/核心起舞 → F乘数: x1.2)"
        )
    elif market_vol <= freeze_tr:
        status.market_vol_desc = (
            f"{market_vol:.2f} 万亿 (冰点/流动性枯竭 → F乘数: x0.8)"
        )
    else:
        status.market_vol_desc = (
            f"{market_vol:.2f} 万亿 (常态震荡 → F乘数: x1.0)"
        )

    # ── 判定 4：ST 暴雷 / 负净资产 ──
    pb_is_negative: bool = False
    try:
        pb_val = float(stock_info.pb)  # type: ignore[arg-type]
        pb_is_negative = pb_val < 0
    except (ValueError, TypeError):
        pass  # pb 为 "N/A" 等字符串时跳过检查

    if "ST" in stock_info.name or pb_is_negative:
        status.is_safe = False
        status.st_warning = (
            "⚠️ [财务暴雷判定：是(ST/负净资产，建议立即熔断回避！)]"
        )
        logger.warning(
            "[风控] %s(%s) 触发 ST/负净资产红线。",
            stock_info.name, stock_info.code,
        )
    else:
        status.st_warning = "[通过]"

    logger.info(
        "[风控] %s(%s) 判定完成 | is_safe=%s",
        stock_info.name, stock_info.code, status.is_safe,
    )
    return status


# ===========================================================================
# 2. generate_panel_markdown — 智能合并生成参数面板
# ===========================================================================
def generate_panel_markdown(
    stock_info: StockInfo,
    risk_status: RiskStatus,
    catalyst_str: str,
    pdf_rag_info: list[str],
    save_dir: str,
) -> None:
    """
    生成或智能合并"00_参数面板_发给AI.md"。

    智能合并规则（提取自 souji0_1.py _generate_parameters 的合并逻辑）：
      - 字段分为"高信心"和"低信心"两类。
      - 高信心字段（价格、PE、换手率等）：始终用程序抓取的新值覆盖。
      - 低信心字段（EPS预测、产品趋势等）：
          * 若旧面板中该字段与默认占位符相同（用户未改动）→ 用新值覆盖。
          * 若旧面板中该字段已被用户手动修改 → 保留用户修改，不覆盖！

    EPS 反算逻辑：通过 P / PE_TTM 反算 EPS_TTM（参考旧代码）。

    Args:
        stock_info:   完整的行情与历史分位数据。
        risk_status:  熔断判定结果。
        catalyst_str: 催化剂/行业背景描述字符串（由 main.py 拼接后传入）。
        pdf_rag_info: pdf_extractor 提取的增量关键句列表（可为空）。
        save_dir:     目标股文件夹路径（面板文件写入此处）。
    """
    panel_path: str = os.path.join(save_dir, EXPORT_CONFIG["PANEL_FILENAME"])

    # ── EPS 反算（PE_TTM × 当前价格的逆运算）──
    eps_forecast: str = "提取失败，需结合券商研报自行研判"
    try:
        if stock_info.pe_ttm != "N/A" and stock_info.price > 0:
            pe_val = float(stock_info.pe_ttm)  # type: ignore[arg-type]
            if pe_val > 0:
                eps_ttm = round(stock_info.price / pe_val, 2)
                eps_forecast = (
                    f"EPS_TTM ≈ {eps_ttm} 元/股（由 P÷PE_TTM 反算，"
                    "未来年度预测需参考最新券商研报）"
                )
    except (ValueError, TypeError):
        pass

    # ── 构建字段列表（key, value, confidence）──
    # confidence = "high" → 程序可靠获取，直接覆盖
    # confidence = "low"  → 依赖用户补充，保留用户手改
    p_now: float = stock_info.price
    fields: list[tuple[str, str, str]] = [
        (
            "标的名称/代码",
            f"{stock_info.name} ({stock_info.code}) | 风控: {risk_status.st_warning}",
            "high",
        ),
        (
            "当前价格 (P_now)",
            f"{p_now:.2f} 元",
            "high" if p_now > 0 else "low",
        ),
        (
            "近3年最低价 (P_min_3y, 前复权)",
            (
                f"{stock_info.min_price_3y:.2f} 元 "
                f"(自底部已反弹 {stock_info.rise_from_bottom:.1f}%)"
                f"{risk_status.extreme_rise_warn}"
            ) if stock_info.min_price_3y > 0 else "[K线接口异常，历史数据待下次刷新]",
            "high" if stock_info.min_price_3y > 0 else "low",
        ),
        (
            "当前价格历史分位 (Price_Percentile)",
            f"{stock_info.price_percentile:.1f}%" if stock_info.min_price_3y > 0
            else "[K线接口异常，分位数据待下次刷新]",
            "high" if stock_info.min_price_3y > 0 else "low",
        ),
        (
            "最新滚动市盈率 (PE_TTM)",
            str(stock_info.pe_ttm),
            "high" if stock_info.pe_ttm != "N/A" else "low",
        ),
        (
            "最新市净率 (PB)",
            str(stock_info.pb),
            "high" if stock_info.pb != "N/A" else "low",
        ),
        (
            "总市值",
            f"{stock_info.total_mv / 1e8:.2f} 亿元" if stock_info.total_mv > 0 else "N/A",
            "high" if stock_info.total_mv > 0 else "low",
        ),
        (
            "未来三年预期每股收益 (EPS_Y1/Y2/Y3)",
            eps_forecast,
            "low",   # 反算值精度低，保留用户手改
        ),
        (
            "核心产品现货/期货价格趋势或订单销量",
            "[请结合源头情报或 PDF 纪要人工填入：例如产品正在涨价，或产能满载]",
            "low",
        ),
        (
            "今日换手率 (Turnover)",
            f"{stock_info.turnover:.2f}% | {risk_status.death_turnover_warn}",
            "high" if stock_info.turnover > 0 else "low",
        ),
        (
            "两市今日总成交额 (Market_Vol / F乘数)",
            risk_status.market_vol_desc,
            "high",
        ),
        (
            "最新股东户数变化趋势",
            stock_info.holder_trend,
            "high" if "缺失" not in stock_info.holder_trend else "low",
        ),
        (
            "核心催化剂与行业背景",
            catalyst_str,
            "high",
        ),
    ]

    # ── 读取旧面板（如存在），解析为 {字段名: 旧值} 字典 ──
    old_fields: dict[str, str] = {}
    old_rag_block: str = ""          # 保留旧面板中已有的 RAG 增量内容

    if os.path.exists(panel_path):
        old_fields, old_rag_block = _parse_existing_panel(panel_path)
        logger.info("[面板] 发现旧面板，启动智能合并模式。")

    # ── 智能合并：逐字段判断是否保留用户手改 ──
    merged_lines: list[str] = [
        f"# 📊 超景气价值投机 · 风控参数面板",
        f"> **标的**: {stock_info.name}({stock_info.code})  "
        f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}  "
        f"**引擎**: Omni-Analyst v7.5",
        "",
        "---",
        "",
    ]

    for key, new_val, confidence in fields:
        if confidence == "low" and key in old_fields:
            old_val: str = old_fields[key]
            # 旧值与新默认值不同 & 旧值非模板占位符 → 用户手改，保留
            placeholder_identifiers = ["[请结合", "提取失败", "反算", "N/A"]
            is_placeholder = any(p in old_val for p in placeholder_identifiers)
            if old_val and old_val != new_val and not is_placeholder:
                merged_lines.append(f"**{key}：** {old_val}  *(↑ 已保留您的手工修改)*")
                continue

        merged_lines.append(f"**{key}：** {new_val}")

    # ── 追加增量 RAG 提纯数据 ──
    if pdf_rag_info:
        merged_lines.append("")
        merged_lines.append("---")
        merged_lines.append(
            f"### 📄 PDF 增量 RAG 提纯数据 ({datetime.now().strftime('%Y-%m-%d')}):"
        )
        for sentence in pdf_rag_info:
            merged_lines.append(f"- {sentence}")

    # 追加旧面板中已有的 RAG 块（历史版本不丢失）
    if old_rag_block:
        merged_lines.append("")
        merged_lines.append(old_rag_block.strip())

    # ── 写文件 ──
    final_content: str = "\n".join(merged_lines) + "\n"

    try:
        os.makedirs(save_dir, exist_ok=True)
        with open(panel_path, "w", encoding="utf-8") as f:
            f.write(final_content)
        logger.info(
            "[面板] ✅ 参数面板已写入: %s (%d 行)",
            panel_path,
            len(merged_lines),
        )
    except Exception as exc:
        logger.error("[面板] 写入失败: %s", exc)


# ===========================================================================
# 私有：解析旧面板文件
# ===========================================================================
def _parse_existing_panel(panel_path: str) -> tuple[dict[str, str], str]:
    """
    解析已存在的参数面板 Markdown 文件。

    Returns:
        (fields_dict, rag_block)：
          - fields_dict: {字段名: 字段值} 字典（从 **key：** value 格式解析）
          - rag_block:   旧面板中 "### 📄" 标记之后的 RAG 内容块（原样保留）
    """
    fields: dict[str, str] = {}
    rag_block: str = ""
    rag_marker: str = "### 📄"

    try:
        with open(panel_path, "r", encoding="utf-8") as f:
            content: str = f.read()

        # 提取旧 RAG 块（"### 📄" 标记之后的所有内容）
        rag_idx: int = content.find(rag_marker)
        if rag_idx != -1:
            rag_block = content[rag_idx:]

        # 解析字段：格式为 "**字段名：** 字段值"
        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("**") and "：** " in line:
                # 去掉末尾的手改标注（如 "*(↑ 已保留您的手工修改)*"）
                line_clean = line.split("*(↑")[0].strip()
                parts = line_clean.split("：** ", 1)
                if len(parts) == 2:
                    key: str = parts[0].replace("**", "").strip()
                    val: str = parts[1].strip()
                    fields[key] = val

    except Exception as exc:
        logger.warning("[面板] 读取旧面板失败（将重建）: %s", exc)

    return fields, rag_block

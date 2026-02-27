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
    radar_summary: str,
    competitors_summary: str,
    pdf_rag_info: list[str],
    save_dir: str,
) -> None:
    """
    生成或智能合并"00_参数面板_发给AI.md"。
    完全遵守 V8.3 契约。
    """
    panel_path: str = os.path.join(save_dir, EXPORT_CONFIG["PANEL_FILENAME"])
    
    def _sa(v, default="[数据未获取]"):
        return default if v is None or str(v).strip() in ("", "None", "nan", "N/A") else str(v)

    # ── 构建字段集合，分为高信心与低信心 ──
    # format: (key_name, computed_value, default_placeholder, is_high_confidence, suffix)
    
    p_now = stock_info.price
    p_min_3y = stock_info.min_price_3y
    
    # Defaults
    eps_placeholder = "[API获取失败/暂缺]"
    old_eps_placeholders = ["[API获取失败/暂缺]", "预测EPS: 需参考券商研报原件 (接口已停用)"]
    product_placeholder = "[用户填写，如：主营产品价格近一月暴涨20% / 价格持续阴跌 / 产销持平]"
    holder_placeholder = "[API获取失败/暂缺]"
    
    # catalyst (V8.11: 注入主营业务垫底)
    clean_radar = radar_summary.strip('\n ') if radar_summary else ""
    base_catalyst = f"主营业务: {stock_info.core_business}\n" if hasattr(stock_info, "core_business") and stock_info.core_business else ""
    if clean_radar:
        catalyst_val = f"\n{base_catalyst}{clean_radar}"
    else:
        catalyst_val = f"\n{base_catalyst}[近期无舆情爆发，请结合盘面或自行补充]"

    # If holder config is empty, fallback to placeholder
    eps_val = stock_info.eps_forecast if stock_info.eps_forecast not in ["提取失败", ""] else eps_placeholder
    holder_val = stock_info.holder_trend if "缺失" not in stock_info.holder_trend else holder_placeholder

    fields = [
        (
            "标的名称/代码",
            f"{stock_info.name} ({stock_info.code})",
            "",
            True,
            ""
        ),
        (
            "当前价格 (P_now)",
            f"{p_now:.2f}",
            "",
            p_now > 0,
            ""
        ),
        (
            "近3年最低价 (P_min_3y, 前复权)",
            f"{p_min_3y:.2f}",
            "",
            p_min_3y > 0,
            ""
        ),
        (
            "当前价格历史分位 (Price_Percentile)",
            f"{stock_info.price_percentile:.1f}",
            "",
            p_min_3y > 0,
            "% *(用于识别长期箱体底部的深跌错杀)*"
        ),
        (
            "最新静态/动态市盈率 (PE_TTM)",
            str(stock_info.pe_ttm),
            "",
            stock_info.pe_ttm != "N/A",
            ""
        ),
        (
            "最新市净率 (PB)",
            str(stock_info.pb),
            "",
            stock_info.pb != "N/A",
            " *(针对周期反转/核心资产必填)*"
        ),
        (
            "未来三年预期每股收益 (EPS_Y1, EPS_Y2, EPS_Y3)",
            eps_val,
            eps_placeholder,
            False,
            " *(用于精准推演远期动态PE与戴维斯双击)*"
        ),
        (
            "核心产品现货/期货价格趋势 或 订单销量",
            product_placeholder,
            product_placeholder,
            False,
            " *(决定景气度是否能拿满分的生死指标)*"
        ),
        (
            "今日换手率 (Turnover)",
            f"{stock_info.turnover:.2f}",
            "",
            stock_info.turnover > 0,
            "%"
        ),
        (
            "两市今日总成交额 (Market_Vol)",
            risk_status.market_vol_desc.split(" ")[0] if risk_status.market_vol_desc else "N/A",
            "",
            True,
            " 万亿" # Added text outside
        ),
        (
            "最新股东户数变化",
            holder_val,
            holder_placeholder,
            False,
            " *(主力吸筹/派发的照妖镜)*"
        ),
        (
            "核心催化剂/行业背景",
            catalyst_val,
            "[用户可选填]",
            radar_summary != "",
            ""
        )
    ]
    
    # ── 读取旧面板（如存在），解析为 {字段名: 旧值} 字典 ──
    old_fields: dict[str, str] = {}
    old_rag_block: str = ""

    if os.path.exists(panel_path):
        old_fields, old_rag_block = _parse_existing_panel(panel_path)
        logger.info("[面板] 发现旧面板，启动智能合并模式。")

    merged_lines: list[str] = [
        "## 📋 [必填] 标的参数面板 (Data Injection Panel)",
        "*(用户需提供以下“硬数据”，若留空，AI 将基于最新公开数据进行推演并标注估算风险)*",
        ""
    ]

    for key, new_val, placeholder, is_high_conf, suffix in fields:
        final_val = new_val
        
        # Merge logic for low confidence
        if not is_high_conf and key in old_fields:
            old_val = old_fields[key]
            # V8.11 Fix: specifically override old defunct EPS placeholders
            if "EPS" in key:
                if old_val and not any(p in old_val for p in old_eps_placeholders):
                    final_val = old_val
            else:
                if old_val and placeholder and placeholder not in old_val:
                    final_val = old_val

        # Add to lines
        # Special logic to prevent double suffix if old_val actually contains the suffix already, 
        # but safely we just put suffix. Old parsing strips suffix if possible.
        merged_lines.append(f"* **{key}：** {final_val}{suffix}")

    # ── 追加补充区域 ──
    merged_lines.append("")
    merged_lines.append("---")
    merged_lines.append("### 📎 [系统自动附加] 深度审计底料 (Supplemental Data)")
    
    roe_val = _sa(stock_info.roe)
    gm_val = _sa(stock_info.gross_margin)
    merged_lines.append(f"**1. 核心盈利能力:** ROE={roe_val} | 毛利率={gm_val}")
    
    merged_lines.append("**2. 横向竞争格局:**")
    comp_text = competitors_summary if competitors_summary else "[竞对数据暂时缺失]"
    merged_lines.append(comp_text)
    merged_lines.append("")
    
    merged_lines.append("**3. 增量硬核信号 (RAG Extracted):**")
    if pdf_rag_info:
        for sentence in pdf_rag_info:
            merged_lines.append(f"- {sentence}")
    elif old_rag_block:
        merged_lines.append(old_rag_block.strip())
    else:
        merged_lines.append("无增量信号")
        
    final_content: str = "\n".join(merged_lines) + "\n"

    try:
        os.makedirs(save_dir, exist_ok=True)
        with open(panel_path, "w", encoding="utf-8") as f:
            f.write(final_content)
        logger.info("[面板] ✅ 参数面板已写入: %s", panel_path)
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
    rag_marker: str = "**3. 增量硬核信号 (RAG Extracted):**"

    try:
        with open(panel_path, "r", encoding="utf-8") as f:
            content: str = f.read()

        # 提取旧 RAG 块
        rag_idx: int = content.find(rag_marker)
        if rag_idx != -1:
            rag_block = content[rag_idx + len(rag_marker):].strip()

        # 解析字段：格式为 "* **字段名：** 字段值"
        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("* "):
                line = line[2:].strip()
                
            if line.startswith("**") and "：** " in line:
                # 去掉末尾的固定后缀提示（如 "*(用于..."）
                line_clean = line.split("*(")[0].strip()
                parts = line_clean.split("：** ", 1)
                if len(parts) == 2:
                    key: str = parts[0].replace("**", "").strip()
                    val: str = parts[1].strip()
                    fields[key] = val

    except Exception as exc:
        logger.warning("[面板] 读取旧面板失败（将重建）: %s", exc)

    return fields, rag_block

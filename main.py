# -*- coding: utf-8 -*-
"""
🌌 main.py — Omni-Analyst v7.5 Singularity · 终端主入口
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
系统最高指挥官：负责初始化、CLI 交互循环、以及串联所有底层模块。

数据流向（对应 ARCHITECTURE.md）：
  用户输入 → fetchers 采集 → core.llm_engine / 正则 评分
           → core.risk_auditor 熔断 → fetchers.cninfo_spider PDF下载
           → utils.pdf_extractor RAG提取 → core.risk_auditor 生成面板
           → core.db_client 可选入库 → 终端展示 + 文件输出
"""

import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# ── 最优先：初始化日志系统，后续所有模块的日志格式均由此决定 ──
from utils.logger import setup_logging
setup_logging()

logger = logging.getLogger(__name__)

# ── 项目内部模块导入 ──
import config
from core.db_client import save_target_to_db
from core.llm_engine import LLMScore, check_availability, evaluate_intel
from core.models import NewsItem, StockInfo
from core.risk_auditor import evaluate_risk, generate_panel_markdown
from fetchers.akshare_client import (
    fetch_kline_extremes,
    fetch_market_volume,
    fetch_radar_news,
    fetch_stock_info,
)
# V8.4: PyTDX 底层协议引擎已在 akshare_client 内部接入，无需在 main.py 显式调用
from fetchers.cninfo_spider import download_company_reports
from fetchers.news_flow_fetcher import execute_radar_scan
from fetchers.financial_fetcher import fetch_target_and_peers_financials
from core.models import CompetitorFinancials
from utils.pdf_extractor import extract_rag_info_from_pdf

# ── 模块级状态：LLM 是否可用（启动时检测一次，全局共享）──
_USE_LLM: bool = False


# ===========================================================================
# Banner 与启动初始化
# ===========================================================================
_BANNER = """
█████████████████████████████████████████████████████████████████
 🌌  The Omni-Analyst  v7.5  S I N G U L A R I T Y
     超景气价值投机 · 情报收割与风控引擎 · 重构淬炼版
█████████████████████████████████████████████████████████████████
"""

_MENU = """
  [1]  📡  奇点雷达   (多维情报共振 · 龙虎榜 · 新闻联播)
  [2]  📥  奇点打包   (风控熔断 · 历史分位 · PDF底稿抽吸)
       └─ 支持批量输入！多只股票用逗号/空格分隔
  [0]  ⏹   切断数据连线 (退出)
"""


def _startup_check() -> None:
    """程序启动时执行一次性初始化检测，打印系统状态。"""
    global _USE_LLM

    # 1. 确保产出物目录存在
    output_dir: str = config.EXPORT_CONFIG["OUTPUT_DIR"]
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # 2. 检测 Ollama 本地大模型服务
    _USE_LLM = check_availability()
    if _USE_LLM:
        print(f"  🧠 [LLM] 神经引擎已接入 → {config.LLM_CONFIG['MODEL_NAME']} 在线，启用深度语义评分")
    else:
        print("  ⚙️  [LLM] Ollama 服务未检测到，降级使用物理正则法则")


# ===========================================================================
# 菜单 1：奇点雷达
# ===========================================================================
def _run_radar() -> None:
    """
    奇点雷达主逻辑：多维情报聚合 → 双引擎评分 → 终端展示 → 导出日报。
    """
    print("\n" + "★" * 65)
    print("  🌌 [奇点雷达] 启动！向全网倾泻侦测探针...")
    print("★" * 65)

    # ── 情报采集：四大数据源并行描述 ──
    print("  [1/3] 📡 正在聚合 [龙虎榜 / 机构调研 / 板块异动 / 全球快讯]...")
    market_news: list[NewsItem] = fetch_radar_news()

    print("  [2/3] 🏛️  正在逆推 [国家宏观意志] (新闻联播 T-3日穿透)...")
    cctv_news: list[NewsItem] = fetch_cctv_news(days_back=3)

    all_news: list[NewsItem] = market_news + cctv_news
    print(f"  [3/3] 🔬 开始双引擎提纯，原始情报 {len(all_news)} 条...")

    if not all_news:
        print("\n☕ 矩阵静默。当前未采集到任何原始情报，请检查网络连接。")
        return

    # ── 双引擎评分：正则预筛 → LLM 深度打分 ──
    valuable: list[NewsItem] = []
    for item in all_news:
        _score_news_item(item)
        if item.score > 0:
            valuable.append(item)

    if not valuable:
        print("\n☕ 双引擎扫描完毕，当日未发现【超景气级别】情报，空仓等待。")
        return

    # 按评分降序排列
    valuable.sort(key=lambda x: x.score, reverse=True)

    # ── 终端展示 Top 20 ──
    print("\n" + "!" * 65)
    print(f"  🚨 提炼完毕：{len(valuable)} 条高能 Alpha 情报")
    print("!" * 65)

    for item in valuable[:20]:
        stars: str = "★" * item.score
        tags_str: str = " | ".join(item.tags) if item.tags else "─"
        print(f"\n[{item.time}] {stars} | 来源: {item.source} | {tags_str}")
        print(f"  📌 {item.title}")
        if item.llm_reasoning:
            print(f"  🧠 判分理由: {item.llm_reasoning}")
        print("  " + "─" * 60)

    print("\n  💡 审计师指令：重点突击 [★★] 标的，找到代码后执行 [模块 2]。")

    # ── 按 DATA_CONTRACTS 云端交接契约格式导出日报文件 ──
    _export_daily_report(valuable)


def _score_news_item(item: NewsItem) -> None:
    """
    对单条 NewsItem 执行双引擎评分（正则预筛 + LLM 深度打分）。

    策略（对应 DATA_CONTRACTS.md 核心战略变更）：
      1. 优先 LLM 语义评分（若可用）。
      2. LLM 不可用或超时，降级到正则词库初筛。
      3. 命中陷阱词（KW_TRAP）立即跳过，score 保持 0。

    直接修改传入的 item 对象（score / tags / llm_reasoning）。
    """
    # 拼接全文用于匹配
    full_text: str = item.title + " " + item.llm_reasoning

    # ── 陷阱词强制剔除（无论哪个引擎都需要先过一遍）──
    if re.search(config.KW_TRAP, full_text):
        item.score = 0
        return

    if _USE_LLM and item.score == 0:
        # LLM 语义深度评分
        result: LLMScore = evaluate_intel(text=item.llm_reasoning, title=item.title)
        if result.success:
            item.score = result.score
            item.llm_reasoning = result.reasoning
            if result.score >= 1:
                item.tags.append("🧠 [LLM:语义精准评分]")
            return
        # LLM 失败则 fallthrough 到正则

    # ── 正则词库初筛（降级/兜底路径）──
    tags: list[str] = []
    score: int = 0

    if re.search(config.KW_TYPE1_TECH, full_text):
        tags.append("🚀 [Type1:颠覆奇点]")
    if re.search(config.KW_TYPE2_CYCLE, full_text):
        tags.append("🔥 [Type2:现货断裂]")
    if re.search(config.KW_POLICY_HARD, full_text):
        tags.append("🏛️ [Type3:宏观真金]")

    if len(tags) >= 2:
        score = 2
    elif len(tags) == 1:
        score = 1

    item.score = score
    item.tags.extend(tags)


def _export_daily_report(items: list[NewsItem]) -> None:
    """
    按 DATA_CONTRACTS.md 云端大模型交接契约格式，
    将 Score >= 1 的情报写入每日 Alpha 情报简报。

    输出路径：{OUTPUT_DIR}/Daily_Alpha_Intel_{date}.md
    """
    date_str: str = datetime.now().strftime("%Y-%m-%d")
    filename: str = config.EXPORT_CONFIG["DAILY_REPORT_NAME"].format(date=date_str)
    output_path: str = os.path.join(config.EXPORT_CONFIG["OUTPUT_DIR"], filename)

    score2 = [i for i in items if i.score >= 2]
    score1 = [i for i in items if i.score == 1]

    lines: list[str] = [
        f"# 📡 每日超景气 Alpha 核心情报简报",
        f"**生成时间:** {date_str}",
        f"**初筛引擎:** Omni-Analyst v7.5 (Local {config.LLM_CONFIG['MODEL_NAME'] if _USE_LLM else '物理正则降级'})",
        "",
        ("> **To The Hyper-Prosperity Auditor (云端大模型):** 以下情报已通过本地物理/神经双引擎过滤，"
         "去除了垃圾噪音。请依据《超景气价值投机》框架，对以下信息进行二阶与三阶推演，"
         "寻找潜在的\u300c笨韭双击\u300d目标，并生成深度审计研判。"),
        "",
        "## 🔴 核心奇点共振 (Score 2 - 现象级拐点)",
        "*提取逻辑：具备 0-1 颠覆、现货暴涨断裂、或国家级重磅政策特征。*",
        "",
    ]

    for idx, item in enumerate(score2, start=1):
        tags_str: str = " | ".join(f"`{t}`" for t in item.tags) if item.tags else "─"
        lines += [
            f"**[情报 {idx}]**",
            f"* **时间/来源:** {item.time} | {item.source}",
            f"* **标题内容:** {item.title}",
            f"* **AI 判分理由:** {item.llm_reasoning or '─'}",
            f"* **命中标签:** {tags_str}",
            "",
        ]

    if not score2:
        lines.append("*（本日无现象级拐点情报）*\n")

    lines += [
        "## 🟡 潜伏观察区 (Score 1 - 普通关注)",
        "*(格式同上，仅列出有资金异动或浅层催化的信息)*",
        "",
    ]

    for idx, item in enumerate(score1, start=1):
        tags_str = " | ".join(f"`{t}`" for t in item.tags) if item.tags else "─"
        lines += [
            f"**[情报 {idx}]**",
            f"* **时间/来源:** {item.time} | {item.source}",
            f"* **标题内容:** {item.title}",
            f"* **AI 判分理由:** {item.llm_reasoning or '─'}",
            f"* **命中标签:** {tags_str}",
            "",
        ]

    if not score1:
        lines.append("*（本日无普通关注情报）*\n")

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"\n  📁 日报已导出 → [{output_path}]")
    except Exception as exc:
        logger.error("日报写入失败: %s", exc)


# ===========================================================================
# 菜单 2：奇点打包（深度底稿流水线）
# ===========================================================================
def _run_package() -> None:
    """
    奇点打包主逻辑：
    用户输入代码 → 多股批量 → 每股走完完整的 8 步审计流水线。
    """
    raw = input("\n  🎯 输入A股代码（支持批量，用逗号/空格分隔）: ").strip()
    if not raw:
        print("  ⚠️  未输入任何代码，返回主菜单。")
        return

    # 批量切割（对应 config.RE_INPUT_SPLITTER）
    codes: list[str] = [
        s.strip() for s in re.split(config.RE_INPUT_SPLITTER, raw) if s.strip()
    ]

    print(f"\n  🚀 批量任务启动，共 {len(codes)} 只股票: {codes}")

    market_vol: float = fetch_market_volume()

    for idx, code in enumerate(codes, start=1):
        print(f"\n{'=' * 60}")
        print(f"  📌 [{idx}/{len(codes)}] 正在处理: {code}")
        print("=" * 60)
        _audit_single_stock(code, market_vol)

    print(f"\n  🎉 批量任务全部完成！共处理 {len(codes)} 只股票。")
    print(f"  💡 终极指令：将『00_参数面板_发给AI.md』全文喂给云端大模型审计师！")


def _audit_single_stock(code: str, market_vol: float) -> None:
    """
    单只股票的完整 8 步审计流水线。

    Step 1: 获取基础行情 (StockInfo)
    Step 2: 获取历史 K 线极值与风控预判
    Step 3: 物理熔断判定 (RiskStatus)
    Step 4: 打印风控速报
    Step 5: 确定保存目录
    Step 6: 下载巨潮 PDF 底稿（目标股 + 竞对股）
    Step 7: 提取 RAG 增量信息
    Step 8: 生成/合并风控面板 + 可选 DB 入库

    Args:
        code:       6 位 A 股代码。
        market_vol: 今日两市总成交额（万亿元），已在上层获取。
    """

    # ── Step 1: 基础行情 ──
    print(f"\n  🔍 [Step 1/8] 多源容灾获取行情...")
    stock_info: StockInfo = fetch_stock_info(code)

    if stock_info.price == 0.0 and stock_info.name == code:
        print(f"  ⚠️  [盲降模式] 所有行情接口均失败，仅执行 PDF 下载。")
        blind_mode = True
    else:
        blind_mode = False
        print(f"  ✅  {stock_info.name}({code}) | 价={stock_info.price:.2f} "
              f"PE={stock_info.pe_ttm} PB={stock_info.pb} 换手={stock_info.turnover:.2f}%")

    # ── Step 2: K 线历史分位 & 风控预判 ──
    if not blind_mode:
        print(f"\n  📊 [Step 2/8] 穿透近 3 年前复权 K 线，计算历史分位...")
        stock_info = fetch_kline_extremes(code, stock_info)
        print(f"  ✅  最低价={stock_info.min_price_3y:.2f} | "
              f"分位={stock_info.price_percentile:.1f}% | "
              f"自底反弹={stock_info.rise_from_bottom:.1f}%")

    # ── Step 3: 物理熔断判定 ──
    print(f"\n  ⚡ [Step 3/8] 执行 Phase V 物理熔断判定...")
    risk_status = evaluate_risk(stock_info, market_vol)

    # ── Step 4: 风控速报 ──
    print(f"\n  🚨 [Step 4/8] 风控速报:")
    safe_icon = "✅ [全线通过]" if risk_status.is_safe else "❌ [触发红线，建议回避！]"
    print(f"  整体安全状态: {safe_icon}")
    print(f"  大盘流动性: {risk_status.market_vol_desc}")
    print(f"  换手风控: {risk_status.death_turnover_warn}")
    if risk_status.extreme_rise_warn:
        print(f"  透支风控: {risk_status.extreme_rise_warn}")
    print(f"  ST暴雷: {risk_status.st_warning}")

    # ── Step 5: 确定保存目录 ──
    print(f"\n  📂 [Step 5/8] 初始化底稿目录...")
    save_dir: str = os.path.join(
        config.EXPORT_CONFIG["OUTPUT_DIR"],
        f"{stock_info.name}_{code}",
    )
    
    # ── 动作 B: 强制接入“超景气社交流量雷达 (LLM 提纯)” ──
    print(f"\n  📡 [Step 6/8] 启动超景气流量雷达与本地神经引擎定性...")
    radar_summary = ""
    try:
        radar_summary = execute_radar_scan(code, stock_info.name)
    except Exception as e:
        logger.error(f"[流量雷达] 提取失败: {e}")
        radar_summary = "[流量雷达数据暂时缺失，请用户结合市场盘面自行判定]"
        
    # ── 动作 C: 强制接入“竞对提取与横向比对” ──
    print(f"\n  ⚔️ [Step 7/8] 锁定同业标的，开启横向身位与财报对比...")
    competitors_summary = ""
    comp_financials = []
    industry_reports_text = []
    try:
        comp_financials, industry_reports_text = fetch_target_and_peers_financials(target_code=code, target_name=stock_info.name, save_dir=save_dir)
        competitors_summary = _format_competitors_to_md(comp_financials, industry_reports_text)
    except Exception as e:
        logger.error(f"[竞对横评] 提取失败: {e}")
        competitors_summary = "[竞对数据暂时缺失]"

    # ── Step 8: PDF RAG 提取（仅对年报/调研类）──
    print(f"\n  🔬 [Step 8/8] 扫描 PDF 目录，提取增量 RAG 硬核信号...")
    rag_sentences: list[str] = _extract_rag_from_dir(save_dir)
    if rag_sentences:
        print(f"  ✅  共提取 {len(rag_sentences)} 条关键句。")
    else:
        print("  ─  本次无新 PDF 或未命中关键词，RAG 内容为空。")

    # ── 终极面板组装 ──
    print(f"\n  ⚙️  融合组装参数面板，执行智能合并...")

    generate_panel_markdown(
        stock_info=stock_info,
        risk_status=risk_status,
        radar_summary=radar_summary,
        competitors_summary=competitors_summary,
        pdf_rag_info=rag_sentences,
        save_dir=save_dir,
    )

    # 可选 MongoDB 入库
    saved_to_db: bool = save_target_to_db(stock_info, risk_status)
    if saved_to_db:
        print("  ☁️  [MongoDB] 审计数据已同步至历史靶标库。")

    panel_path: str = os.path.join(save_dir, config.EXPORT_CONFIG["PANEL_FILENAME"])
    print(f"\n  🎉 [{stock_info.name}({code})] 战术底料打包完成！")
    print(f"  💡 请前往 [{save_dir}] 查收，并将面板文件喂给云端 AI 审计师：")
    print(f"     → {panel_path}")


    return ""


# ===========================================================================
# 辅助：竞对财报转 Markdown
# ===========================================================================
def _format_competitors_to_md(comp_financials: list[CompetitorFinancials], industry_reports_text: list[str] = None) -> str:
    """
    将横向比对数据格式化为可读的 Markdown 文本摘要。
    包含第二级容灾降级的数字对比表格，以及可选的行业大环境研报摘要。
    """
    if not comp_financials:
        return "[竞对数据暂时缺失]"
        
    lines = []
    def _sa(v, default="[未获取]"):
        return default if v is None or str(v).strip() in ("", "None", "nan", "N/A") else str(v)
        
    lines.append("| 公司名称 | 代码 | 最新季报期 | 营业收入 | 净利润 |")
    lines.append("| :--- | :--- | :--- | :--- | :--- |")

    has_real_peers = False
    for res in comp_financials:
        if res.code == "000001" and res.name == "THS兜底平替占位":
            continue
            
        if res.code != comp_financials[0].code:
            has_real_peers = True
            
        try:
            if res.income_statement_8q and len(res.income_statement_8q) > 0:
                latest_q = res.income_statement_8q[0]
                lines.append(f"| **{_sa(res.name)}** | `{_sa(res.code)}` | {_sa(latest_q.get('date'))} | {_sa(latest_q.get('revenue'))} | {_sa(latest_q.get('net_profit'))} |")
            else:
                lines.append(f"| **{_sa(res.name)}** | `{_sa(res.code)}` | [无数据] | - | - |")
        except Exception as e:
            logger.warning(f"格式化竞对 {res.name} 数据出错: {e}")
            lines.append(f"| **{_sa(res.name)}** | `{_sa(res.code)}` | [提取异常] | - | - |")

    if not has_real_peers and len(comp_financials) > 0:
        lines.append("")
        lines.append("*(获取同业竞对代码失败：东财/同花顺API遭封禁，当前仅展示目标股数据)*")

    if industry_reports_text:
        lines.append("")
        lines.append("#### 🏛️ [容灾降级] 宏观行业研报摘要")
        for text in industry_reports_text:
            lines.append(f"- {text}")
            
    return "\n".join(lines)


# ===========================================================================
# 辅助：扫描目录提取所有 PDF 的 RAG 信息
# ===========================================================================
def _extract_rag_from_dir(save_dir: str) -> list[str]:
    """
    扫描 save_dir 目录下所有 PDF 文件，仅对年报/调研类执行 RAG 提取，
    汇总返回去重后的关键句列表。
    """
    all_sentences: list[str] = []
    seen: set[str] = set()

    try:
        for filename in os.listdir(save_dir):
            if not filename.endswith(".pdf"):
                continue
            # 仅对年报和调研类做 RAG 提取，跳过季报（低价值噪音多）
            if not any(kw in filename for kw in ("年报", "年度", "调研")):
                continue

            pdf_path: str = os.path.join(save_dir, filename)
            sentences: list[str] = extract_rag_info_from_pdf(pdf_path)
            for s in sentences:
                if s not in seen:
                    seen.add(s)
                    all_sentences.append(s)

    except Exception as exc:
        logger.warning("[RAG] 目录扫描失败: %s", exc)

    return all_sentences


# ===========================================================================
# CLI 主循环
# ===========================================================================
def main() -> None:
    """程序入口：打印 Banner，执行启动检测，进入主 CLI 循环。"""
    print(_BANNER)
    _startup_check()

    while True:
        print(_MENU)
        choice: str = input("  👉 输入指令数字: ").strip()

        if choice == "1":
            _run_radar()
        elif choice == "2":
            _run_package()
        elif choice == "0":
            print("\n  ⏹  数据连线已切断，祝猎猎顺利！\n")
            sys.exit(0)
        else:
            print("  ⚠️  无效指令，请输入 0 / 1 / 2。")


if __name__ == "__main__":
    main()

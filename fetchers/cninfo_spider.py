# -*- coding: utf-8 -*-
"""
🔍 fetchers/cninfo_spider.py — 巨潮资讯网财报爬虫
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
职责：
  - 从巨潮资讯网（cninfo.com.cn）检索并下载目标股/竞对股的财报 PDF。
  - 使用 core.network_engine.stream_download 进行 Chunk 流式写入，防 OOM。
  - 使用 core.network_engine.safe_request 发起 JSON 检索请求，享受防弹重试。
  - 基于 INVESTOR_DOC_KEYWORDS 对调研纪要进行精准后过滤，修复旧代码的漏抓问题。
  - 支持增量下载：文件已存在则跳过，避免重复下载。

配置来源：
  config.py > API_CONFIG / CNINFO_CATEGORIES / INVESTOR_DOC_KEYWORDS
               RE_ILLEGAL_FILENAME_CHARS

数据契约对应：
  下载完成后调用 utils/pdf_extractor 提取增量 RAG 内容，追加写入参数面板。
"""

import logging
import os
import re
import time
from pathlib import Path

from config import (
    API_CONFIG,
    CNINFO_CATEGORIES,
    EXPORT_CONFIG,
    INVESTOR_DOC_KEYWORDS,
    RE_ILLEGAL_FILENAME_CHARS,
)
from core.network_engine import safe_request, stream_download
from utils.pdf_extractor import extract_rag_info_from_pdf

logger = logging.getLogger(__name__)

# 两次 PDF 下载之间的礼貌延迟（秒），避免触发巨潮反爬限速
_DOWNLOAD_DELAY: float = 1.2

# 巨潮深市/沪市列表接口（用于获取 orgId）
_CNINFO_STOCK_LIST_URLS: dict[str, str] = {
    "szse": API_CONFIG["CNINFO_STOCK_LIST_SZ"],
    "sse": API_CONFIG["CNINFO_STOCK_LIST_SH"],
}

# 内存中缓存股票 orgId 列表，避免重复拉取（进程级缓存）
_org_cache: dict[str, str] = {}


# ===========================================================================
# 公共接口
# ===========================================================================
def download_company_reports(
    code: str,
    name: str,
    save_dir: str,
    is_rival: bool = False,
) -> None:
    """
    从巨潮资讯网检索并流式下载指定股票的财报 PDF。

    目标股（is_rival=False）下载清单：
      - 年度报告（最近 2 份）
      - 半年度报告（最近 2 份）
      - 三季度报告（最近 2 份）
      - 一季度报告（最近 1 份）
      - 投资者调研纪要（关键词搜索 + INVESTOR_DOC_KEYWORDS 精准后过滤，最多 5 份）

    竞对股（is_rival=True）下载清单：
      - 年度报告（最近 1 份）
      - 三季度报告（最近 1 份）

    Args:
        code:     6 位 A 股代码（不含市场前缀）。
        name:     股票中文名称（用于日志与文件夹命名）。
        save_dir: 本地保存目录（必须已存在）。
        is_rival: True 表示竞对股（下载范围缩减），False 表示目标股（全量下载）。
    """
    logger.info(
        "[巨潮] 开始下载 %s(%s) | 模式=%s",
        name, code, "竞对" if is_rival else "目标",
    )

    if is_rival:
        # 竞对股：仅年报 + 最新季报
        _download_category(code, name, CNINFO_CATEGORIES["ANNUAL_REPORT"], 1, save_dir)
        _download_category(code, name, CNINFO_CATEGORIES["Q3_REPORT"], 1, save_dir)
    else:
        # 目标股：全量底稿
        _download_category(code, name, CNINFO_CATEGORIES["ANNUAL_REPORT"], 2, save_dir)
        _download_category(code, name, CNINFO_CATEGORIES["SEMI_ANNUAL"], 2, save_dir)
        _download_category(code, name, CNINFO_CATEGORIES["Q3_REPORT"], 2, save_dir)
        _download_category(code, name, CNINFO_CATEGORIES["Q1_REPORT"], 1, save_dir)
        # 投资者调研纪要：searchkey 全文搜索 + 关键词精准后过滤
        _download_category(
            code, name, category="", limit=5, save_dir=save_dir,
            searchkey="调研", use_investor_filter=True,
        )

    logger.info("[巨潮] %s(%s) 全部下载任务完成。", name, code)


# ===========================================================================
# 私有：获取股票 orgId（巨潮接口的必要参数）
# ===========================================================================
def _get_org_id(code: str) -> str:
    """
    从巨潮资讯网获取股票的 orgId。

    orgId 是巨潮公告检索接口的必要参数，用于精准定位公司。
    深市（非 6 开头）查 szse 列表，沪市（6 开头）查 sse 列表。

    Args:
        code: 6 位 A 股代码。

    Returns:
        orgId 字符串；若查询失败返回空字符串（接口仍可降级使用空 orgId）。
    """
    if code in _org_cache:
        return _org_cache[code]

    # 根据股票代码选择查询的交易所列表
    exchange_key: str = "sse" if str(code).startswith("6") else "szse"
    list_url: str = _CNINFO_STOCK_LIST_URLS[exchange_key]

    try:
        resp = safe_request(list_url, method="get")
        if resp is None:
            return ""

        stock_list: list[dict] = resp.json().get("stockList", [])
        logger.debug("[巨潮] %s 股票库加载: %d 条。", exchange_key.upper(), len(stock_list))

        # 建立 code -> orgId 的映射并缓存，避免重复请求
        for stock in stock_list:
            stk_code: str = stock.get("code", "")
            org_id: str = stock.get("orgId", "")
            if stk_code:
                _org_cache[stk_code] = org_id

        return _org_cache.get(code, "")

    except Exception as exc:
        logger.error("[巨潮] orgId 获取失败(%s): %s", code, exc)
        return ""


# ===========================================================================
# 私有：执行单一类别的公告检索与下载
# ===========================================================================
def _download_category(
    code: str,
    name: str,
    category: str,
    limit: int,
    save_dir: str,
    searchkey: str = "",
    use_investor_filter: bool = False,
) -> None:
    """
    检索巨潮公告列表，并对前 limit 条符合条件的公告进行流式 PDF 下载。

    Args:
        code:                6 位股票代码。
        name:                股票名称（日志用）。
        category:            巨潮公告类别代码（如 "category_ndbg_szsh"），空字符串表示全文搜索。
        limit:               最多下载的 PDF 数量。
        save_dir:            本地保存目录。
        searchkey:           全文搜索关键词（如 "调研"），传空字符串表示不使用关键词搜索。
        use_investor_filter: 是否启用 INVESTOR_DOC_KEYWORDS 精准后过滤（针对调研纪要）。
    """
    org_id: str = _get_org_id(code)
    # stock 参数格式为 "code,orgId"（orgId 为空时巨潮接口降级处理仍可用）
    stock_param: str = f"{code},{org_id}"

    # 根据代码自动适配交易所列（szse=深交所，sse=上交所）
    column: str = "sse" if str(code).startswith("6") else "szse"

    payload: dict = {
        "pageNum": 1,
        "pageSize": 20,
        "column": column,
        "tabName": "fulltext",
        "stock": stock_param,
        "isHLtitle": "true",
    }
    if category:
        payload["category"] = category
    if searchkey:
        payload["searchkey"] = searchkey

    try:
        resp = safe_request(API_CONFIG["CNINFO_URL"], method="post", data=payload)
        if resp is None:
            logger.warning("[巨潮] %s(%s) 公告列表请求失败。", name, code)
            return

        announcements: list[dict] = resp.json().get("announcements") or []
        if not announcements:
            logger.debug("[巨潮] %s(%s) 类别=%s 无公告结果。", name, code, category or searchkey)
            return

    except Exception as exc:
        logger.error("[巨潮] %s(%s) 公告列表解析失败: %s", name, code, exc)
        return

    downloaded_count: int = 0

    for ann in announcements:
        if downloaded_count >= limit:
            break

        raw_title: str = (
            str(ann.get("secName", "")) + "_" + str(ann.get("announcementTitle", ""))
        )
        adjunct_url: str = ann.get("adjunctUrl", "")

        if not adjunct_url:
            continue

        # ── 过滤噪音公告 ──
        # 英文版和摘要版不作为底稿使用
        if "英文" in raw_title or "摘要" in raw_title:
            continue

        # 投资者调研纪要精准后过滤（修复旧代码 searchkey 被接口端误杀的问题）
        if use_investor_filter:
            if not any(kw in raw_title for kw in INVESTOR_DOC_KEYWORDS):
                logger.debug("[巨潮] 跳过非调研类公告: %s", raw_title[:40])
                continue

        # ── 文件名安全化 ──
        clean_title: str = re.sub(RE_ILLEGAL_FILENAME_CHARS, "", raw_title)
        clean_title = clean_title.replace(" ", "_").replace("\n", "").strip()
        # 截断过长文件名，避免 Windows 路径限制
        if len(clean_title) > 120:
            clean_title = clean_title[:120]

        pdf_path: str = os.path.join(save_dir, f"{clean_title}.pdf")

        # ── 增量跳过：文件已存在 ──
        if os.path.exists(pdf_path):
            logger.info("[巨潮] ⏭️  已存在，跳过: %s", clean_title[:50])
            downloaded_count += 1
            continue

        # ── 流式下载（防 OOM 核心）──
        download_url: str = API_CONFIG["CNINFO_DL_BASE"] + adjunct_url
        logger.info("[巨潮] ⬇️  开始下载: %s...", clean_title[:50])

        try:
            written_bytes: int = 0
            with open(pdf_path, "wb") as pdf_file:
                for chunk in stream_download(download_url):
                    pdf_file.write(chunk)
                    written_bytes += len(chunk)

            if written_bytes == 0:
                # 下载器未产生任何 chunk，说明连接失败，清理空文件
                os.remove(pdf_path)
                logger.warning("[巨潮] %s 下载失败（0 字节）。", clean_title[:50])
                continue

            logger.info(
                "[巨潮] ✅ 下载完成: %s (%.1f KB)",
                clean_title[:50],
                written_bytes / 1024,
            )

            # ── 下载成功后立即提取 RAG 增量信息 ──
            _append_rag_to_panel(
                pdf_path=pdf_path,
                raw_title=raw_title,
                save_dir=save_dir,
            )

            downloaded_count += 1

        except Exception as exc:
            logger.error("[巨潮] %s 写入失败: %s", clean_title[:50], exc)
            # 清理写了一半的残损文件
            if os.path.exists(pdf_path):
                os.remove(pdf_path)

        # 礼貌延迟，避免触发巨潮限速
        time.sleep(_DOWNLOAD_DELAY)


# ===========================================================================
# 私有：提取 RAG 信息并追加写入参数面板
# ===========================================================================
def _append_rag_to_panel(pdf_path: str, raw_title: str, save_dir: str) -> None:
    """
    调用 pdf_extractor 提取关键句，追加写入 00_参数面板_发给AI.md。

    仅对年报和调研纪要类 PDF 进行 RAG 提取（其他季报跳过，降低噪音）。

    Args:
        pdf_path:  刚下载完成的 PDF 本地路径。
        raw_title: 原始公告标题（用于判断是否需要提取 + 追加标题）。
        save_dir:  参数面板所在的目录。
    """
    # 只对含"年报"或"调研"关键词的 PDF 做 RAG 提取
    should_extract: bool = any(kw in raw_title for kw in ("年报", "调研", "年度"))
    if not should_extract:
        return

    rag_sentences: list[str] = extract_rag_info_from_pdf(pdf_path)
    if not rag_sentences:
        return

    panel_path: str = os.path.join(save_dir, EXPORT_CONFIG["PANEL_FILENAME"])
    short_title: str = raw_title[:30]

    try:
        with open(panel_path, "a", encoding="utf-8") as f:
            f.write(f"\n\n### 📄 {short_title} - 增量 RAG 提纯数据:\n")
            for sentence in rag_sentences:
                f.write(f"- {sentence}\n")
        logger.info("[RAG] 已将 %d 条关键句追加至参数面板。", len(rag_sentences))
    except Exception as exc:
        logger.error("[RAG] 写入参数面板失败: %s", exc)

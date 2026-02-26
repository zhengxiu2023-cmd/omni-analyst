# -*- coding: utf-8 -*-
"""
📄 utils/pdf_extractor.py — 本地 PDF 文本提取工具
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
职责：
  - 使用 PyMuPDF (fitz) 替代旧版 pypdf，提取财报/调研纪要的关键增量信息。
  - 基于 config.KW_PDF_RAG 正则匹配产能、订单、突破等业务强信号句子。
  - 最多读取 config.PDF_MAX_PAGES 页，返回最多 config.PDF_MAX_SENTENCES 条不重复句子。
  - 防御性编程：文件损坏、加密、格式异常均静默返回 []，绝不崩主进程。

依赖：PyMuPDF (pip install pymupdf)
配置来源：config.py > KW_PDF_RAG / PDF_MAX_PAGES / PDF_MAX_SENTENCES
"""

import logging
import re
from pathlib import Path

from config import KW_PDF_RAG, PDF_MAX_PAGES, PDF_MAX_SENTENCES

logger = logging.getLogger(__name__)

# 尝试导入 PyMuPDF，若未安装则优雅降级
try:
    import fitz  # PyMuPDF

    _FITZ_AVAILABLE = True
except ImportError:
    fitz = None  # type: ignore
    _FITZ_AVAILABLE = False
    logger.warning(
        "[PDF提取] PyMuPDF 未安装，pdf_extractor 将不可用。"
        "请执行: pip install pymupdf"
    )


def extract_rag_info_from_pdf(pdf_path: str) -> list[str]:
    """
    从本地 PDF 文件中提取符合超景气判断标准的关键业务句子。

    提取逻辑：
      1. 使用 PyMuPDF 读取前 PDF_MAX_PAGES 页的文本内容。
      2. 按句号/感叹号/换行符分割为独立句子。
      3. 使用 KW_PDF_RAG 正则过滤出含有价值信号的句子（产能/满产/订单/突破等）。
      4. 去重、过滤过短或过长的噪音句子。
      5. 最多返回 PDF_MAX_SENTENCES 条。

    Args:
        pdf_path: 本地 PDF 文件的绝对/相对路径。

    Returns:
        list[str]：提取到的关键句子列表；任何错误均返回空列表 []。
    """
    if not _FITZ_AVAILABLE:
        logger.debug("[PDF提取] PyMuPDF 不可用，跳过提取: %s", pdf_path)
        return []

    path = Path(pdf_path)
    if not path.exists() or not path.is_file():
        logger.warning("[PDF提取] 文件不存在或路径无效: %s", pdf_path)
        return []

    try:
        doc = fitz.open(str(path))
    except Exception as exc:
        logger.error("[PDF提取] 无法打开 PDF（可能已加密或损坏）: %s | %s", pdf_path, exc)
        return []

    try:
        extracted: list[str] = []
        seen: set[str] = set()  # 去重集合

        # 只读取前 N 页，避免超大财报的性能陷阱
        max_pages: int = min(PDF_MAX_PAGES, doc.page_count)

        for page_num in range(max_pages):
            try:
                page = doc.load_page(page_num)
                text: str = page.get_text("text")  # 纯文本提取模式
            except Exception as exc:
                logger.debug("[PDF提取] 第 %d 页提取失败: %s", page_num + 1, exc)
                continue

            if not text.strip():
                continue

            # 按句号/感叹号/问号/换行分割
            sentences: list[str] = re.split(r"[。！？\n]", text)

            for sentence in sentences:
                sentence = sentence.strip()

                # 过滤：长度不足 10 字或超过 150 字的视为噪音
                if len(sentence) < 10 or len(sentence) > 150:
                    continue

                # 使用 KW_PDF_RAG 正则匹配业务强信号
                if not re.search(KW_PDF_RAG, sentence):
                    continue

                # 去重
                if sentence in seen:
                    continue

                seen.add(sentence)
                extracted.append(sentence)

                # 达到最大条数提前退出
                if len(extracted) >= PDF_MAX_SENTENCES:
                    break

            if len(extracted) >= PDF_MAX_SENTENCES:
                break

        logger.info(
            "[PDF提取] %s: 读取 %d 页，命中 %d 条关键句。",
            path.name,
            max_pages,
            len(extracted),
        )
        return extracted

    except Exception as exc:
        logger.error("[PDF提取] 解析过程异常: %s | %s", pdf_path, exc)
        return []

    finally:
        # 确保文档句柄始终释放
        try:
            doc.close()
        except Exception:
            pass

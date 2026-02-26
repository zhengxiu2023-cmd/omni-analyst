🌌 The Omni-Analyst v7.5 - Singularity (重构淬炼版)

📌 项目简介

本项目是超景气价值投机框架的前置“数据收割与提纯引擎”。主要负责在物理层和网络层过滤市场噪音，抓取高维 Alpha 数据，并将其结构化为 AI 审计师可直接吞咽的硬核风控面板。
v7.5 版本是对原“意大利面条”代码的彻底解耦与重构，并原生集成了基于 Ollama 的本地小模型（如 Qwen2.5）进行语义提纯。

🛠️ 技术栈选型 (Tech Stack)

核心语言: Python 3.10+ (强类型提示，易于重构)

网络与防弹层: requests (带 urllib3 重试机制与 Chunk 流式下载)

数据源: akshare (主流数据), 东财 Push2 API (实时行情兜底)

AI 提纯引擎: Ollama 运行时 + 本地 qwen2.5:7b (语义评分与降噪)

文档解析: PyMuPDF (fitz) (替代老旧的 pypdf，提取财报与调研纪要增量信息)

数据库 (可选): pymongo (本地历史靶标沉淀)

🚀 快速启动指南

确保已安装 Ollama 并拉取模型: ollama run qwen2.5:7b

安装依赖: pip install -r requirements.txt

终端运行: python main.py
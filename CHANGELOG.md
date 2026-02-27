# Changelog

## [v8.2.0] - 2026-02-27
- 修复网络故障，结合本地神经引擎重构超景气流量雷达，引入竞对研报抽吸模块。

## [v8.0.0] - 2026-02-26
- 新增：社交流量获取引擎 (`news_flow_fetcher.py`)，支持跨平台社交热榜数据流量流分析。
- 分支强化：修复了网络连接重置（RemoteDisconnected）故障，核心引擎 `network_engine.py` 引入 UA 轮换、随机防爬抖动与 `tenacity` 指数退避重试。
- 分支强化：优化 `akshare_client.py` 接入物理隔离的 Try-List 备用容灾体系（东财 -> 腾讯 -> 新浪）。
- 新增：竞对财报横评能力 (`financial_fetcher.py`)，支持抓取目标公司及其板块竞争对手近 8 期核心财报。
- 扩展：完善了 `StockInfo` 数据契约，补齐动态 ROE、毛利率等财务监控度指标。
- 扩展：新增 `CompetitorFinancials` 数据模型契约。

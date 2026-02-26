📜 数据契约与配置规范 (Data Contracts)

To Antigravity (Developer):
本文档定义了系统流转的核心数据结构和全局配置项。请在编写具体逻辑时，严格使用本契约中定义的 Dataclass 和配置键名。
核心战略变更： 信息提纯必须以 LLM 语义判断为主，正则判断仅作为初筛或降级方案。

1. 核心数据模型契约 (对应 core/models.py)

所有数据在 fetchers (采集层) 获取后，必须第一时间转化为以下标准 Dataclass 才能向下游流转。

1.1 StockInfo (基础行情与极值模型)

from dataclasses import dataclass, field
from typing import Optional

@dataclass
class StockInfo:
code: str                  # 股票代码 (如 "000001")
name: str                  # 股票名称
price: float               # 最新价
turnover: float            # 换手率 (%)
pe_ttm: str | float        # 滚动市盈率 (缺失则为 "N/A")
pb: str | float            # 市净率 (缺失则为 "N/A")
total_mv: float            # 总市值 (元)
# --- 以下为历史穿透字段 ---
min_price_3y: float = 0.0  # 近3年最低价(前复权)
price_percentile: float = 0.0 # 当前价格处于近3年的百分位 (0~100)
rise_from_bottom: float = 0.0 # 底部反弹幅度 (%)
# --- 以下为判定状态字段 ---
holder_trend: str = "数据缺失" # 股东户数变化趋势
eps_forecast: str = "提取失败" # 预测 EPS

1.2 NewsItem (情报雷达模型)

@dataclass
class NewsItem:
time: str                  # 发布时间/发生时间
title: str                 # 资讯标题/内容简述
source: str                # 情报来源 (如 "龙虎榜", "新闻联播", "全球快讯")
tags: list[str] = field(default_factory=list) # 命中的标签数组
score: int = 0             # 奇点共振评分 (0=垃圾/陷阱, 1=普通关注, 2=超景气核心共振)
llm_reasoning: str = ""    # 大模型给出的判分逻辑 (用于审计师面板的展示与二次判定)

1.3 RiskStatus (风控熔断模型)

@dataclass
class RiskStatus:
is_safe: bool = True       # 整体是否安全 (触发任何红线即为 False)
market_vol_desc: str = ""  # 大盘流动性状态 (如 "1.52 万亿 (疯牛)")
death_turnover_warn: str = "[安全]" # 死亡换手警告文案
extreme_rise_warn: str = "" # 极端透支警告文案
st_warning: str = "[通过]" # 财务暴雷/ST警告文案

2. 全局配置字典契约 (对应 config.py)

Antigravity 在编写 config.py 时，必须包含且不限于以下常量组：

2.1 物理熔断阈值 (Risk Thresholds)

Phase V 风控硬编码红线

RISK_THRESHOLDS = {
"DEATH_TURNOVER_PCT": 40.0,       # 单日换手率清仓线 (%)
"EXTREME_RISE_60D_PCT": 150.0,    # 60日极限透支线 (%)
"MARKET_VOL_FRENZY_TR": 1.5,      # 疯牛乘数触发线 (万亿)
"MARKET_VOL_FREEZE_TR": 0.8,      # 冰点乘数触发线 (万亿)
}

2.2 词库引擎 (Regex Engine Dictionaries - 仅作极速预筛/降级用)

剥离出来的正则，主要用于过滤明显无关的信息，减轻 LLM 运算压力

KW_TYPE1_TECH = r"(AGI|大模型|固态电池|人形机器人|脑机接口|量子计算|常温超导|颠覆性|代际差|参数碾压|彻底解决|全球首个|革命性|卡脖子|自主可控|算力)"
KW_TYPE2_CYCLE = r"(现货.*暴涨|全线提价|封盘不报|停止报价|排产满载|库存告急|产能.*出清|供不应求|运价飙升|断供|翻倍|历史新高|满负荷)"
KW_POLICY_HARD = r"(万亿.*下达|专项债资金到位|并购重组|发改委.*核准|重磅突发|国常会|特别国债|免税|补贴落地|政策强心剂)"
KW_TRAP = r"(科学家.*论文|有望在未来|或将|规划纲要|意见征求稿|平稳运行|专家预测|实验室阶段|逐步向好|理性看待)"

2.3 外部接口配置 (API Endpoints & Headers)

API_CONFIG = {
"CNINFO_URL": "http://www.cninfo.com.cn/new/hisAnnouncement/query",
"CNINFO_DL_BASE": "http://static.cninfo.com.cn/",
"EASTMONEY_PUSH2": "https://push2.eastmoney.com/api/qt/stock/get",
"EASTMONEY_KLINE": "https://push2his.eastmoney.com/api/qt/stock/kline/get",
"HEADERS": {
"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
"X-Requested-With": "XMLHttpRequest"
},
"REQUEST_RETRIES": 3,
"STREAM_TIMEOUT": 60,
"DEFAULT_TIMEOUT": 15
}

2.4 本地 LLM 引擎配置 (The Core Logic Engine)

核心升级：将《超景气价值投机》框架的灵魂硬编码进 System Prompt

LLM_CONFIG = {
"ENABLE": True,
"OLLAMA_API": "http://localhost:11434/api/generate",
"MODEL_NAME": "qwen2.5:7b",
"TIMEOUT": 5.0,  # 给予 LLM 充足的思考时间
"SYSTEM_PROMPT": """
你是一个冷酷的“超景气价值投机”情报过滤引擎。你的任务是鉴别输入的新闻/情报是否具备真实的爆炸性价值。
请严格按照以下标准进行评分，并给出极其简短的理由：

【2分 - 现象级核心拐点 (必抓)】:

Type 1: 0-to-1 颠覆性技术突破，且已开始商业化落地（有实际产品/订单，非纯实验室阶段）。

Type 2: 1-to-10 产业化加速，必须出现“现货暴涨、封盘不报、供不应求、满负荷排产”等硬核供需断裂信号。

Type 3: 宏观级突发事件（如导致供应链断裂的战争/制裁），或国家级真金白银政策落地（有明确资金体量）。

【1分 - 普通关注】:
一般性的行业利好、普通的业绩预增、没有具体资金落地的政策指导。

【0分 - 垃圾/鱼尾陷阱 (剔除)】:
专家预测、科学家论文发表、"有望在未来..."、"规划纲要"等画大饼词汇；或者只是个别企业的常规性运作。

输出格式要求严格如下（JSON格式）：
{"score": 2, "reasoning": "发现Type2现货断供信号，提到上游全面封盘"}
"""
}

2.5 产出物导出配置 (Export Config)

EXPORT_CONFIG = {
"OUTPUT_DIR": "company_info",
"DAILY_REPORT_NAME": "Daily_Alpha_Intel_{date}.md", # 每天生成一份，专供云端 LLM 吞咽
}

3. 云端大模型交接契约 (Cloud LLM Handoff Protocol)

当执行“奇点雷达”扫描结束后，系统必须按照以下 Markdown 模板格式，将高分 (Score >= 1) 的情报导出。此格式专为喂给网页版 Gemini/高级模型设计，以触发其“超景气审计师”系统提示词。

# 📡 每日超景气 Alpha 核心情报简报 
**生成时间:** YYYY-MM-DD
**初筛引擎:** Omni-Analyst v7.5 (Local Qwen2.5:7b)

> **To The Hyper-Prosperity Auditor (云端大模型):** > 以下情报已通过本地物理/神经双引擎过滤，去除了垃圾噪音。请依据《超景气价值投机》框架，对以下信息进行二阶与三阶推演，寻找潜在的“笨韭双击”目标，并生成深度审计研判。

## 🔴 核心奇点共振 (Score 2 - 现象级拐点)
*提取逻辑：具备 0-1 颠覆、现货暴涨断裂、或国家级重磅政策特征。*

**[情报 1]**
* **时间/来源:** 2024-xx-xx | 全球产业快讯
* **标题内容:** [具体的新闻内容]
* **本地 AI 判分理由:** [llm_reasoning 字段内容，如：确认Type2现货断供]
* **命中标签:** `[🚀 Type1:颠覆奇点]`

*(以此类推)*

## 🟡 潜伏观察区 (Score 1 - 普通关注)
*(格式同上，仅列出有资金异动或浅层催化的信息)*


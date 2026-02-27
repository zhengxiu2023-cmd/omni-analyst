# -*- coding: utf-8 -*-
"""
⚙️ config.py — Omni-Analyst v7.5 全局配置中心 (Singularity)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
架构规范：
  - 所有外部 URL、超时时间、正则词库必须在此定义。
  - 业务代码中严禁硬编码任何配置项。
  - 所有常量均使用 UPPER_SNAKE_CASE 命名。

维护责任：
  - 本文件对应 DATA_CONTRACTS.md > 第 2 节 "全局配置字典契约"。
"""

# ===========================================================================
# 2.1 物理熔断阈值 (Risk Thresholds)
# Phase V 风控硬编码红线
# ===========================================================================
RISK_THRESHOLDS: dict[str, float] = {
    "DEATH_TURNOVER_PCT": 40.0,       # 单日换手率清仓线 (%)
    "EXTREME_RISE_60D_PCT": 150.0,    # 60日极限透支线 (%)
    "MARKET_VOL_FRENZY_TR": 1.5,      # 疯牛乘数触发线 (万亿)
    "MARKET_VOL_FREEZE_TR": 0.8,      # 冰点乘数触发线 (万亿)
}

# ===========================================================================
# 2.2 词库引擎 (Regex Engine Dictionaries)
# 仅作极速预筛/降级方案，主判断交由 LLM（详见 DATA_CONTRACTS.md 核心战略变更）
# 提取自 souji0_1.py OmniTerminal.__init__ & _filter_and_append
# ===========================================================================

# Type 1: 0-to-1 颠覆性技术突破关键词
KW_TYPE1_TECH: str = (
    r"(AGI|大模型|固态电池|人形机器人|脑机接口|量子计算|常温超导"
    r"|颠覆性|代际差|参数碾压|彻底解决|全球首个|革命性|卡脖子|自主可控|算力)"
)

# Type 2: 1-to-10 产业化加速 / 现货供需断裂关键词
KW_TYPE2_CYCLE: str = (
    r"(现货.*暴涨|全线提价|封盘不报|停止报价|排产满载|库存告急"
    r"|产能.*出清|供不应求|运价飙升|断供|翻倍|历史新高|满负荷)"
)

# Type 3: 宏观级真金白银政策关键词
KW_POLICY_HARD: str = (
    r"(万亿.*下达|专项债资金到位|并购重组|发改委.*核准|重磅突发"
    r"|国常会|特别国债|免税|补贴落地|政策强心剂)"
)

# 鱼尾陷阱 / 画大饼词库（命中即剔除）
KW_TRAP: str = (
    r"(科学家.*论文|有望在未来|或将|规划纲要|意见征求稿"
    r"|平稳运行|专家预测|实验室阶段|逐步向好|理性看待)"
)

# PDF 增量 RAG 提纯关键词（用于从财报中提取硬核业务信号）
KW_PDF_RAG: str = (
    r"(产能|满产|开发|研发|突破|供不应求|订单|大幅增长)"
)

# 批量输入分隔符正则（解析逗号/空格/分号等多种分隔方式）
RE_INPUT_SPLITTER: str = r"[,，\s;]+"

# 文件名非法字符清洗正则（用于 PDF 文件名安全化）
RE_ILLEGAL_FILENAME_CHARS: str = r'[\\/*?:"<>|]'

# 投资者纪要类型关键词（后处理过滤，精准识别调研纪要类公告）
INVESTOR_DOC_KEYWORDS: list[str] = [
    "投资者关系活动记录",
    "投资者调研",
    "调研接待",
    "问卷调查",
    "投资者问卷",
]

# ===========================================================================
# 2.3 外部接口配置 (API Endpoints & Headers)
# 提取自 souji0_1.py OmniTerminal.__init__ 及各模块内的硬编码 URL
# ===========================================================================
API_CONFIG: dict = {
    # --- 巨潮资讯 (Cninfo) ---
    "CNINFO_URL": "http://www.cninfo.com.cn/new/hisAnnouncement/query",
    "CNINFO_DL_BASE": "http://static.cninfo.com.cn/",
    "CNINFO_STOCK_LIST_SZ": "http://www.cninfo.com.cn/new/data/szse_stock.json",
    "CNINFO_STOCK_LIST_SH": "http://www.cninfo.com.cn/new/data/sse_stock.json",

    # --- 东方财富 Push2 实时行情 ---
    "EASTMONEY_PUSH2": "https://push2.eastmoney.com/api/qt/stock/get",

    # --- 东方财富 Push2His 历史 K 线 ---
    "EASTMONEY_KLINE": "https://push2his.eastmoney.com/api/qt/stock/kline/get",

    # --- 东方财富大盘总成交额接口 ---
    "EASTMONEY_MARKET_VOL": "https://push2.eastmoney.com/api/qt/ulist.np/get",

    # --- Ollama 本地服务健康检查 ---
    "OLLAMA_HEALTH": "http://localhost:11434/",

    # --- 通用请求头（模拟浏览器，绕过部分反爬） ---
    "HEADERS": {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "X-Requested-With": "XMLHttpRequest",
    },

    # --- 超时与重试配置 ---
    "MAX_RETRIES": 3,
    "RETRY_BACKOFF": 1.5,
    "STREAM_TIMEOUT": 120,    # V8.13 护盾：大文件下载流式超时 (秒) 扩大以防挂死
    "DEFAULT_TIMEOUT": 15,     # 常规 API 请求超时 (秒)
    "LLM_HEALTH_TIMEOUT": 1,   # Ollama 健康检查的超时 (秒)，快速判断是否可用
}

# --- 东方财富接口专用请求 Token（从 souji0_1.py 提取，勿随意更改）---
EM_UT_TOKEN: str = "fa5fd1943c7b386f172d6893dbfba10b"
EM_MKT_UT_TOKEN: str = "b2884a393a59ad64002292a3e90d46a5"

# --- 东财 Push2 行情字段映射（f 字段说明，方便后续维护）---
# f43=最高 f44=最低 f45=开盘 f46=昨收 f57=股票名称缩写 f58=股票中文名
# f60=最新价(实时盘中) f116=总市值 f162=PE_TTM f167=PB f168=换手率
EM_REALTIME_FIELDS: str = "f43,f44,f45,f46,f57,f58,f60,f116,f162,f167,f168"

# --- 东财 K 线字段映射 ---
# fields1: f1=市场,f2=最新价,f3=涨跌幅,f4=涨跌额,f5=成交量,f6=成交额
# fields2: f51=日期,f52=开,f53=收,f54=高,f55=低,f56=成交量,f57=成交额
#          f58=振幅,f59=涨跌幅,f60=涨跌额,f61=换手率
EM_KLINE_FIELDS1: str = "f1,f2,f3,f4,f5,f6"
EM_KLINE_FIELDS2: str = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"

# --- 东财大盘指数 SecIDs（沪深两市综合+创业板）---
EM_MARKET_SECIDS: str = "1.000001,0.399001,1.000016,0.399006"

# ===========================================================================
# 2.4 本地 LLM 引擎配置 (The Core Logic Engine)
# 核心升级：将《超景气价值投机》框架的灵魂硬编码进 System Prompt
# ===========================================================================
LLM_CONFIG: dict = {
    "ENABLE": True,
    "OLLAMA_API": "http://localhost:11434/api/generate",
    "MODEL_NAME": "qwen3:8b",
    "TIMEOUT": 5.0,    # 给予 LLM 充足的思考时间 (秒)
    "SYSTEM_PROMPT": """你是一个冷酷的"超景气价值投机"情报过滤引擎。你的任务是鉴别输入的新闻是否具备真实的爆炸性价值。
严禁为主观口号、专家预测、毫无资金落地的政策打高分！

【打分规则】
2分 (核心拐点)：Type 1 颠覆性技术且已商业化；Type 2 现货暴涨/供不应求/封盘不报；Type 3 国家级资金落地的重磅政策。
1分 (普通关注)：一般行业利好、普通的业绩预增。
0分 (垃圾陷阱)：画大饼、专家预测、规划纲要、有望在未来、实验室阶段。

【思维链与输出要求】
请先在内部思考以下问题，然后严格按照 JSON 格式输出：
1. is_junk (bool): 是否包含画大饼/专家预测等垃圾词汇？
2. has_hard_money (bool): 如果是政策，是否有明确的资金体量落地？
3. supply_demand_break (bool): 是否出现现货暴涨/供不应求等断裂信号？
最后给出 score (0/1/2) 和 reasoning (极简理由)。

【Few-Shot 示例】
输入：专家预测固态电池有望在2030年实现量产，研发稳步推进。
输出：{"is_junk": true, "has_hard_money": false, "supply_demand_break": false, "score": 0, "reasoning": "专家预测+有望在未来，无商业落地，Type 0 垃圾。"}

输入：氧化锗报价今日大涨15%，受出口管制影响，大厂库存见底全面封盘。
输出：{"is_junk": false, "has_hard_money": false, "supply_demand_break": true, "score": 2, "reasoning": "现货暴涨15%且厂家封盘，Type 2 供需断裂极值信号。"}

请严格输出合法的纯 JSON 字符串，不要使用 ```json 等 Markdown 标记包裹！"""
}

# ===========================================================================
# 2.5 产出物导出配置 (Export Config)
# ===========================================================================
EXPORT_CONFIG: dict = {
    "OUTPUT_DIR": "company_info",       # 产出物根目录（相对于项目运行路径）
    "PANEL_FILENAME": "00_参数面板_发给AI.md",   # 每只股票的风控参数面板文件名
    "DAILY_REPORT_NAME": "Daily_Alpha_Intel_{date}.md",  # 每日奇点情报简报模板
}

# ===========================================================================
# 2.6 巨潮资讯报告类别代码 (Cninfo Category Codes)
# 提取自 souji0_1.py module_2_audit_prep，方便集中维护
# ===========================================================================
CNINFO_CATEGORIES: dict[str, str] = {
    "ANNUAL_REPORT": "category_ndbg_szsh",      # 年度报告
    "SEMI_ANNUAL": "category_bndbg_szsh",        # 半年度报告
    "Q3_REPORT": "category_sjdbg_szsh",          # 三季度报告
    "Q1_REPORT": "category_yjdbg_szsh",          # 一季度报告
    "INVESTOR_SURVEY": "category_rcys_szsh",     # 投资者调研记录（备用）
}

# PDF RAG 提取：最多读取的 PDF 首页数
PDF_MAX_PAGES: int = 5

# PDF RAG 提取：每份 PDF 最多保留的有效句子数
PDF_MAX_SENTENCES: int = 5

# ===========================================================================
# 2.7 MongoDB 配置 (可选持久化)
# ===========================================================================
MONGO_CONFIG: dict = {
    "URI": "mongodb://localhost:27017/",
    "DB_NAME": "omni_analyst",
    "COLLECTION_NAME": "omni_targets",
    "TIMEOUT_MS": 2000,   # 服务可用性检测超时 (毫秒)
}

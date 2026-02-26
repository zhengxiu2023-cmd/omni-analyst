# -*- coding: utf-8 -*-
"""
集成测试脚本 - 系统性验证所有模块导入与核心逻辑
运行：python _integration_test.py
"""
import sys
import os
sys.path.insert(0, r'e:\Antigravity  project\Analyst v1.0')

PASS = 0
FAIL = 0

def check(name, fn):
    global PASS, FAIL
    try:
        result = fn()
        print(f"  [PASS] {name}" + (f": {result}" if result else ""))
        PASS += 1
    except Exception as e:
        print(f"  [FAIL] {name}: {type(e).__name__}: {e}")
        FAIL += 1

print("\n=== 模块导入测试 ===")
check("import config", lambda: __import__("config"))
check("import core.models", lambda: __import__("core.models"))
check("import core.network_engine", lambda: __import__("core.network_engine"))
check("import core.llm_engine", lambda: __import__("core.llm_engine"))
check("import core.db_client", lambda: __import__("core.db_client"))
check("import core.risk_auditor", lambda: __import__("core.risk_auditor"))
check("import fetchers.cctv_news", lambda: __import__("fetchers.cctv_news"))
check("import fetchers.akshare_client", lambda: __import__("fetchers.akshare_client"))
check("import fetchers.cninfo_spider", lambda: __import__("fetchers.cninfo_spider"))
check("import utils.pdf_extractor", lambda: __import__("utils.pdf_extractor"))
check("import utils.logger", lambda: __import__("utils.logger"))

print("\n=== 数据模型实例化测试 ===")
from core.models import StockInfo, NewsItem, RiskStatus
check("StockInfo 实例化", lambda: StockInfo(code="000001", name="平安银行", price=12.5,
      turnover=1.2, pe_ttm=8.5, pb=0.7, total_mv=2e11))
check("NewsItem 实例化", lambda: NewsItem(time="2026-02-26", title="测试", source="测试源"))
check("RiskStatus 实例化", lambda: RiskStatus())

print("\n=== 配置读取测试 ===")
import config
check("RISK_THRESHOLDS 死亡换手线", lambda: f"{config.RISK_THRESHOLDS['DEATH_TURNOVER_PCT']}%")
check("API_CONFIG CNINFO_URL", lambda: config.API_CONFIG["CNINFO_URL"][:30])
check("LLM_CONFIG MODEL_NAME", lambda: config.LLM_CONFIG["MODEL_NAME"])
check("EXPORT_CONFIG OUTPUT_DIR", lambda: config.EXPORT_CONFIG["OUTPUT_DIR"])

print("\n=== 风控逻辑单元测试 ===")
from core.risk_auditor import evaluate_risk
from core.models import StockInfo, RiskStatus

# 测试1：ST股应触发红线
st_stock = StockInfo(code="000001", name="ST某股", price=5.0,
                     turnover=2.0, pe_ttm="N/A", pb=-0.5, total_mv=1e9)
check("ST股触发红线", lambda: evaluate_risk(st_stock, 1.2).is_safe == False)

# 测试2：死亡换手触发
hot_stock = StockInfo(code="000002", name="测试股", price=20.0,
                      turnover=55.0, pe_ttm=15.0, pb=2.0, total_mv=1e10)
risk = evaluate_risk(hot_stock, 1.2)
check("死亡换手触发", lambda: evaluate_risk(hot_stock, 1.2).is_safe == False)

# 测试3：正常股票安全通过
safe_stock = StockInfo(code="000003", name="健康股", price=30.0,
                       turnover=1.5, pe_ttm=20.0, pb=2.0, total_mv=5e10,
                       rise_from_bottom=30.0)
check("健康股通过风控", lambda: evaluate_risk(safe_stock, 1.2).is_safe == True)

# 测试4：疯牛市场 F乘数描述
check("疯牛市场描述", lambda: "疯牛" in evaluate_risk(safe_stock, 1.6).market_vol_desc)
check("冰点市场描述", lambda: "冰点" in evaluate_risk(safe_stock, 0.5).market_vol_desc)

print("\n=== 正则词库测试 ===")
import re
check("KW_TYPE1 AGI命中", lambda: bool(re.search(config.KW_TYPE1_TECH, "AGI大模型颠覆性突破")))
check("KW_TYPE2 暴涨命中", lambda: bool(re.search(config.KW_TYPE2_CYCLE, "现货暴涨封盘不报")))
check("KW_TRAP 陷阱词命中", lambda: bool(re.search(config.KW_TRAP, "专家预测有望在未来实现")))
check("RE_INPUT_SPLITTER 批量分割",
      lambda: len(re.split(config.RE_INPUT_SPLITTER, "000001,000002 600036；601318")) >= 4)

print("\n=== LLM 引擎降级测试 ===")
from core.llm_engine import _parse_llm_response, LLMScore
check("合法JSON解析", lambda: _parse_llm_response('{"score":2,"reasoning":"现货断供"}').score == 2)
check("score 区间截断", lambda: _parse_llm_response('{"score":99}').score == 2)
check("空字符串兜底", lambda: _parse_llm_response("").success == False)
check("非JSON兜底", lambda: _parse_llm_response("模型乱输出").success == False)

print("\n=== PDF提取器测试 ===")
from utils.pdf_extractor import extract_rag_info_from_pdf
check("不存在文件返回[]", lambda: extract_rag_info_from_pdf("不存在.pdf") == [])
check("空路径返回[]", lambda: extract_rag_info_from_pdf("") == [])

print(f"\n{'='*50}")
print(f"  测试结果: {PASS} 通过 / {FAIL} 失败")
if FAIL == 0:
    print("  全部通过！系统可以进入验收阶段。")
else:
    print("  存在失败项，需要排查修复。")
print('='*50)

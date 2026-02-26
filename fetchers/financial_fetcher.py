# -*- coding: utf-8 -*-
import logging
import pandas as pd
import akshare as ak
from typing import List

from core.models import CompetitorFinancials

logger = logging.getLogger(__name__)

def fetch_target_and_peers_financials(target_code: str) -> List[CompetitorFinancials]:
    """
    新增季报抓取模块，能获取目标公司及其同板块竞争对手的近 8 期核心财报数据
    首先找到目标公司的行业，并在该行业中挑出市值最相近的 1-2 个竞争对手。
    然后抓取季报(利润表、资产负债、现金流量)，组装为 CompetitorFinancials 列表。
    """
    results: List[CompetitorFinancials] = []
    
    try:
        # 1. 查找目标公司的板块以及市值
        info_df = ak.stock_individual_info_em(symbol=target_code)
        target_name = target_code
        industry = ""
        total_mv = 0.0
        
        if not info_df.empty:
            name_rows = info_df[info_df["item"] == "股票简称"]
            target_name = str(name_rows["value"].values[0]) if not name_rows.empty else target_code
            
            ind_rows = info_df[info_df["item"] == "行业"]
            industry = str(ind_rows["value"].values[0]) if not ind_rows.empty else ""
            
            mv_rows = info_df[info_df["item"] == "总市值"]
            if not mv_rows.empty:
                try:
                    total_mv = float(mv_rows["value"].values[0])
                except:
                    pass

        target_peers = [(target_code, target_name)]
        logger.info("[竞对财报] 目标公司 %s(%s), 行业: %s, 市值: %.2f", target_name, target_code, industry, total_mv)
        
        # 2. 从同行业寻找1-2个竞争对手（若找不到行业，直接跳过竞对搜索）
        if industry and total_mv > 0:
            try:
                board_cons = ak.stock_board_industry_cons_em(symbol=industry)
                if not board_cons.empty:
                    # 剔除目标，并在有市值的标的中寻找
                    board_cons = board_cons[board_cons["代码"] != target_code]
                    board_cons["市值差异"] = abs(pd.to_numeric(board_cons["总市值"], errors="coerce") - total_mv)
                    board_cons = board_cons.dropna(subset=["市值差异"]).sort_values("市值差异").head(2)
                    for _, row in board_cons.iterrows():
                        target_peers.append((str(row["代码"]), str(row["名称"])))
            except Exception as e:
                logger.warning("[竞对财报] 获取板块 [%s] 成份股异常，跳过竞对匹配: %s", industry, e)
        
        # 3. 开始对他们获取财报 (最近 8 期)
        for code, name in target_peers:
            try:
                income_str = ""
                balance_str = ""
                cash_str = ""
                
                # 利润表
                df_income = ak.stock_financial_report_sina(stock=code, symbol="利润表")
                if df_income is not None and not df_income.empty:
                    head_df = df_income.head(8)
                    if '营业总收入' in head_df.columns and '净利润' in head_df.columns:
                        income_str = head_df[['报告日', '营业总收入', '净利润']].to_string(index=False)
                
                # 资产负债表
                df_balance = ak.stock_financial_report_sina(stock=code, symbol="资产负债表")
                if df_balance is not None and not df_balance.empty:
                    head_df = df_balance.head(8)
                    if '资产总计' in head_df.columns and '负债合计' in head_df.columns:
                        balance_str = head_df[['报告日', '资产总计', '负债合计']].to_string(index=False)
                        
                # 现金流量表
                df_cash = ak.stock_financial_report_sina(stock=code, symbol="现金流量表")
                if df_cash is not None and not df_cash.empty:
                    head_df = df_cash.head(8)
                    if '经营活动产生的现金流量净额' in head_df.columns:
                        cash_str = head_df[['报告日', '经营活动产生的现金流量净额']].to_string(index=False)
                
                if income_str or balance_str:
                    comp = CompetitorFinancials(
                        code=code,
                        name=name,
                        income_statement_8q=income_str or "获取缺失",
                        balance_sheet_8q=balance_str or "获取缺失",
                        cash_flow_8q=cash_str or "获取缺失"
                    )
                    results.append(comp)
                else:
                    logger.warning("[竞对财报] %s(%s) 的新浪季报由于空缺被跳过", name, code)
                    
            except Exception as e:
                logger.warning("[竞对财报] 获取 %s(%s) 季报抛出异常，忽略: %s", name, code, e)

    except Exception as e:
        logger.error("[竞对财报] 竞对匹配及获取整体逻辑出现阻断性异常: %s", e)

    return results

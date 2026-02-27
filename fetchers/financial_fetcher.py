# -*- coding: utf-8 -*-
"""
📊 fetchers/financial_fetcher.py — 竞对财报提取器 (Competitor Financials Extractor)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
职责：
  - 由于单标的分析缺乏横向比较，本模块负责找到标的同板块的 1-2 位实力竞对。
  - 获取目标及竞对最近 8 期的季报（营业总收入增长率、净利润增长率、经营现金流）。
  - 为 LLM 提供深度的周期财务切片。
"""

import logging
import os
import pandas as pd
import akshare as ak
from typing import List, Tuple

from config import EXPORT_CONFIG

from core.models import CompetitorFinancials
from fetchers.cninfo_spider import download_company_reports, download_industry_reports

logger = logging.getLogger(__name__)

def _safe_float_str(val, default="N/A") -> str:
    """安全的将可能带有 NaN 的数据提取为保留两位小数的字符串"""
    try:
        if pd.isna(val) or val is None or val == "-":
            return default
        return f"{float(val):.2f}"
    except (ValueError, TypeError):
        return default

def _get_target_industry_peers(stock_code: str, top_n: int = 2) -> Tuple[List[dict], str]:
    """
    寻找同板块市值最近的竞对标的，并返回板块名称。
    """
    try:
        # 获取标的基本信息以确定板块
        info_df = ak.stock_individual_info_em(symbol=stock_code)
        if info_df.empty:
            return [], ""
            
        industry_row = info_df[info_df["item"] == "行业"]
        if industry_row.empty:
            return [], ""
            
        industry_name = str(industry_row["value"].values[0])
        
        # 获取板块内所有成分股
        cons_df = ak.stock_board_industry_cons_em(symbol=industry_name)
        if cons_df.empty:
            return [], industry_name
            
        # 寻找目标公司的市值 (假设使用总市值进行找平)
        target_row = cons_df[cons_df["代码"] == stock_code]
        if target_row.empty:
            return []
            
        target_mv = target_row["总市值"].values[0]
        
        # 将除去自己之外的同业按市值偏离度排序
        peers_df = cons_df[cons_df["代码"] != stock_code].copy()
        
        if peers_df.empty:
            return []
            
        peers_df.loc[:, "mv_diff"] = abs(peers_df["总市值"] - target_mv)
        peers_df = peers_df.sort_values(by="mv_diff").head(top_n)
        
        peers = []
        for _, row in peers_df.iterrows():
            peers.append({
                "code": str(row["代码"]),
                "name": str(row["名称"])
            })
            
        logger.info("[竞对发现] %s 属于板块 '%s', 找到贴身竞对: %s", stock_code, industry_name, [p["name"] for p in peers])
        return peers, industry_name
        
    except Exception as exc:
        logger.warning("[竞对发现] 寻找 %s 的竞对失败: %s", stock_code, exc)
        return [], ""

def _fetch_single_8q(stock_code: str) -> dict:
    """提取单只股票最近 8 期的核心利润/资产/现金流切片。"""
    result = {
        "income_statement_8q": [],
        "balance_sheet_8q": [],
        "cash_flow_8q": []
    }
    
    try:
        # 统一使用同源的 EastMoney 综合财务表
        df_fin = ak.stock_financial_abstract(symbol=stock_code)
        
        if df_fin is not None and not df_fin.empty:
            # 过滤出日期列（排除选项、指标列）
            date_columns = [col for col in df_fin.columns if col not in ['选项', '指标']]
            date_columns = date_columns[:8] # 最近 8 期
            
            for date_str in date_columns:
                revenue_val = "N/A"
                net_profit_val = "N/A"
                total_assets_val = "N/A"
                total_liab_val = "N/A"
                cash_flow_val = "N/A"
                
                # 营业总收入
                rev_row = df_fin[df_fin["指标"] == "营业总收入"]
                if not rev_row.empty:
                    revenue_val = _safe_float_str(rev_row[date_str].values[0])
                    
                # 净利润
                net_row = df_fin[df_fin["指标"] == "净利润"]
                if not net_row.empty:
                    net_profit_val = _safe_float_str(net_row[date_str].values[0])
                    
                # 资产总计
                assets_row = df_fin[df_fin["指标"] == "资产总计"]
                if not assets_row.empty:
                    total_assets_val = _safe_float_str(assets_row[date_str].values[0])
                    
                # 负债合计
                liab_row = df_fin[df_fin["指标"] == "负债合计"]
                if not liab_row.empty:
                    total_liab_val = _safe_float_str(liab_row[date_str].values[0])
                    
                # 经营现金流
                cash_row = df_fin[df_fin["指标"] == "经营活动产生的现金流量净额"]
                if not cash_row.empty:
                    cash_flow_val = _safe_float_str(cash_row[date_str].values[0])
                
                result["income_statement_8q"].append({
                    "date": date_str,
                    "revenue": revenue_val,
                    "net_profit": net_profit_val,
                })
                
                result["balance_sheet_8q"].append({
                    "date": date_str,
                    "total_assets": total_assets_val,
                    "total_liabilities": total_liab_val,
                })
                
                result["cash_flow_8q"].append({
                    "date": date_str,
                    "operating_cash_flow": cash_flow_val,
                })

        logger.debug("[季报抓取] %s 近 8 期季报指标提取完成。", stock_code)
        
    except Exception as exc:
        logger.warning("[季报抓取] 获取 %s 的季报失败: %s", stock_code, exc)
        
    return result

def fetch_target_and_peers_financials(target_code: str, save_dir: str = None) -> List[CompetitorFinancials]:
    """
    拉取目标公司及其 1-2 位板块竞对的最近 8 期主要财务表指标，并联动下载目标、竞对及行业板块高价值深度研报。防爆捕获，失败不可阻断主流程。
    """
    final_results: List[CompetitorFinancials] = []
    
    # 获取目标自身名称
    target_name = target_code
    try:
        df_name = ak.stock_info_a_code_name()
        match = df_name[df_name["code"] == target_code]
        if not match.empty:
            target_name = str(match["name"].values[0])
    except:
        pass

    # 1. 解析目标股的 8 期
    target_data = _fetch_single_8q(target_code)
    final_results.append(CompetitorFinancials(
        code=target_code,
        name=target_name,
        income_statement_8q=target_data.get("income_statement_8q", []),
        balance_sheet_8q=target_data.get("balance_sheet_8q", []),
        cash_flow_8q=target_data.get("cash_flow_8q", [])
    ))
    
    # 下载目标自身的研报 PDF
    if save_dir:
        download_company_reports(target_code, target_name, save_dir, is_rival=False)
        
    # 2. 挖掘竞对并提取 8 期
    peers, industry_name = _get_target_industry_peers(target_code, top_n=2)
    for peer in peers:
        peer_code = peer["code"]
        peer_name = peer["name"]
        
        try:
            peer_data = _fetch_single_8q(peer_code)
            final_results.append(CompetitorFinancials(
                code=peer_code,
                name=peer_name,
                income_statement_8q=peer_data.get("income_statement_8q", []),
                balance_sheet_8q=peer_data.get("balance_sheet_8q", []),
                cash_flow_8q=peer_data.get("cash_flow_8q", [])
            ))
            
            # 下载竞对的研报 PDF
            if save_dir:
                download_company_reports(peer_code, peer_name, save_dir, is_rival=True)
        except Exception as exc:
            logger.warning("[竞对抓取] 提取竞对 %s(%s) 失败，已沙盒隔离防崩溃: %s", peer_name, peer_code, exc)
            continue
            
    # 下载行业板块研报 PDF （被削弱为不下载宽泛行业，已在 cninfo 侧处理为空跑）
    if save_dir and industry_name:
        download_industry_reports(industry_name, save_dir, limit=3)
        
    return final_results

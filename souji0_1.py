import akshare as ak
import pandas as pd
import requests
import os
import time
import re
import json
from datetime import datetime, timedelta

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

try:
    from pymongo import MongoClient
except ImportError:
    MongoClient = None
import warnings

# 屏蔽底层干扰信号，保持终端绝对纯净
warnings.filterwarnings('ignore')

# ==========================================
# The Omni-Analyst: 终极情报终端 v7.0 Singularity (奇点降临版)
# [风控接管] 死亡换手/极端暴涨/ST暴雷 自动熔断判定
# [宏观定标] F乘数(流动性)智能物理判定
# [流式引擎] 军工级大文件 Chunk 下载，杜绝内存溢出
# ==========================================

class OmniTerminal:
    def __init__(self):
        self.cninfo_url = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
        self.cninfo_dl_base = "http://static.cninfo.com.cn/"
        self.cninfo_stock_list = []  # 巨潮股票列表缓存，包含 orgId
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "X-Requested-With": "XMLHttpRequest"
        }
        
        # 审计师核心雷达词库
        self.kw_type1_tech = r"(AGI|大模型|固态电池|人形机器人|脑机接口|量子计算|常温超导|颠覆性|代际差|参数碾压|彻底解决|全球首个|革命性|卡脖子|自主可控|算力)"
        self.kw_type2_cycle = r"(现货.*暴涨|全线提价|封盘不报|停止报价|排产满载|库存告急|产能.*出清|供不应求|运价飙升|断供|翻倍|历史新高|满负荷)"
        self.kw_policy_hard = r"(万亿.*下达|专项债资金到位|并购重组|发改委.*核准|重磅突发|国常会|特别国债|免税|补贴落地|政策强心剂)"
        self.kw_trap = r"(科学家.*论文|有望在未来|或将|规划纲要|意见征求稿|平稳运行|专家预测|实验室阶段|逐步向好|理性看待)"
        
        self.db_collection = None
        if MongoClient:
            try:
                client = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=2000)
                client.server_info() 
                self.db_collection = client['omni_analyst']['omni_targets']
            except Exception:
                self.db_collection = None

        self.use_llm = False
        self.llm_url = "http://localhost:11434/api/generate"
        try:
            if requests.get("http://localhost:11434/", timeout=1).status_code == 200:
                self.use_llm = True
        except: pass

    def _safe_request(self, url, method="get", max_retries=3, stream=False, **kwargs):
        """军工级网络重试引擎，支持流式下载，自动传递 headers"""
        # 流式下载（大文件 PDF）使用更长的超时时间
        timeout = 60 if stream else 15
        # 确保 headers 被正确传递
        if 'headers' not in kwargs:
            kwargs['headers'] = self.headers
        for attempt in range(max_retries):
            try:
                if method == "get":
                    response = requests.get(url, timeout=timeout, stream=stream, **kwargs)
                else:
                    response = requests.post(url, timeout=timeout, stream=stream, **kwargs)
                response.raise_for_status()
                return response
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"  ⚠️ 网络重试 ({attempt+1}/{max_retries}): {type(e).__name__}")
                    time.sleep(2 ** attempt)
                else:
                    return None

    def _get_stock_info(self, code):
        """多源容灾获取器：精准获取单只股票信息"""
        result = {"name": code, "price": 0.0, "turnover": 0.0,
                  "pe_ttm": "N/A", "pb": "N/A", "success": False}

        # 源 1（主力）：东财 push2 轻量单股实时行情接口
        # 稳定返回：价格/PE/PB/换手率，无需拉全量
        try:
            market = "1" if str(code).startswith("6") else "0"
            secid = f"{market}.{code}"
            em_url = "https://push2.eastmoney.com/api/qt/stock/get"
            params = {
                "secid": secid,
                "fields": "f43,f44,f45,f46,f57,f58,f60,f116,f162,f167,f168",
                # f43=最高 f44=最低 f45=开盘 f46=昨收 f60=最新价(盘中实时) f116=总市值
                # f162=PE_TTM f167=PB f168=换手率
                "ut": "fa5fd1943c7b386f172d6893dbfba10b",
                "fltt": 2, "invt": 2
            }
            resp = requests.get(em_url, params=params,
                                headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                if data.get("f60") is not None:
                    result['name'] = data.get('f58', code)
                    result['price'] = float(data.get('f60', 0))
                    result['turnover'] = float(data.get('f168', 0))
                    pe = data.get('f162')
                    pb = data.get('f167')
                    result['pe_ttm'] = round(float(pe), 2) if pe and pe != "-" else "N/A"
                    result['pb'] = round(float(pb), 2) if pb and pb != "-" else "N/A"
                    result['success'] = True
                    # 保存总市值用于后续计算 EPS
                    result['total_mv'] = float(data.get('f116', 0) or 0)
                    print(f"  ✅ [容灾源1] 东财实时行情命中: {result['name']} | "
                          f"价格={result['price']} PE_TTM={result['pe_ttm']} PB={result['pb']} "
                          f"换手={result['turnover']}%")
        except Exception as e:
            print(f"  ⚠️ [容灾源1] 东财实时接口失败: {type(e).__name__}")

        # 源 2（名称兜底）：stock_individual_info_em 只用于补充中文名称
        if result['name'] == code:
            try:
                info_df = ak.stock_individual_info_em(symbol=code)
                if not info_df.empty:
                    name_row = info_df[info_df['item'] == '股票简称']
                    if not name_row.empty:
                        result['name'] = name_row['value'].values[0]
                    if result['price'] == 0.0:
                        p_row = info_df[info_df['item'] == '最新']
                        if not p_row.empty:
                            result['price'] = float(p_row['value'].values[0])
                    if not result['success']:
                        result['success'] = result['price'] > 0
                    print(f"  ✅ [容灾源2] 个股信息兜底: 名称={result['name']}")
            except Exception as e:
                print(f"  ⚠️ [容灾源2] 个股信息接口失败: {type(e).__name__}")

        return result


    def _get_cninfo_orgid(self, code):
        """从巨潮资讯网获取股票的 orgId，用于构建正确的 stock 查询参数"""
        # 缓存机制：只拉取一次股票列表
        if not self.cninfo_stock_list:
            try:
                res = self._safe_request('http://www.cninfo.com.cn/new/data/szse_stock.json', method='get')
                if res:
                    self.cninfo_stock_list = res.json().get('stockList', [])
                    print(f"  📊 巨潮股票库已加载: {len(self.cninfo_stock_list)} 只")
            except Exception:
                pass
        # 查找目标股票的 orgId
        for s in self.cninfo_stock_list:
            if s.get('code') == str(code):
                return s.get('orgId', '')
        return ''

    # ----------------------------------------
    # 模块一：先知矩阵 (源头情报极度挖掘)
    # ----------------------------------------
    def module_1_intel_radar(self):
        print("\n" + "★"*75)
        print(" 🌌 [奇点雷达] 启动！正在向全网倾泻侦测探针，挖掘高维 Alpha 数据源...")
        if self.use_llm: print(" 🧠 [LLM] 检测到本地 Ollama 引擎，神经元网络已接管过滤机制！")
        else: print(" ⚙️ [LLM] 未检测到本地大模型，降级使用物理正则法则。")
        if self.db_collection is not None: print(" 🔌 [MongoDB] 本地云端数据库连接成功。")
        print("★"*75)
        valuable_news = []
        
        try:
            print(" [1/4] 💰 正在透视 [龙虎榜抢筹] 与 [聪明钱调研] ...")
            try:
                df_jg = ak.stock_jgdy_tj_em()
                hot_jg = df_jg[df_jg['接待机构数量'] > 100].head(4)
                for _, row in hot_jg.iterrows():
                    msg = f"绝对暗流！【{row['公司名称']}】近期遭 {row['接待机构数量']} 家顶级机构踏破门槛调研。"
                    valuable_news.append({'time': row['最新调研日期'], 'tags': "🎯 [机构建仓前兆]", 'title': msg, 'source': "调研穿透", 'score': 1})
                
                start_d = (datetime.now() - timedelta(days=15)).strftime("%Y%m%d")
                end_d = datetime.now().strftime("%Y%m%d")
                df_lhb = ak.stock_lhb_jgmmtj_em(start_date=start_d, end_date=end_d)
                if not df_lhb.empty:
                    df_lhb['机构净买额'] = pd.to_numeric(df_lhb['机构净买额'], errors='coerce')
                    hot_lhb = df_lhb[df_lhb['机构净买额'] > 60000000].head(5) 
                    for _, row in hot_lhb.iterrows():
                        msg = f"真金白银强盖章！【{row['股票名称']}({row['股票代码']})】近期遭机构席位暴力净买入 {(row['机构净买额']/100000000):.2f} 亿元！"
                        valuable_news.append({'time': "近期龙虎榜", 'tags': "🔥 [席位暴力抢筹]", 'title': msg, 'source': "龙虎榜雷达", 'score': 2})
            except: pass

            print(" [2/4] 🏭 正在嗅探 [产业现货断裂] 与 [海外科技奇点]...")
            try:
                df_cls = ak.stock_info_global_cls().head(120)
                for _, row in df_cls.iterrows():
                    self._filter_and_append(row['标题'] + " " + row['内容'], row['发布时间'], row['标题'], "全网快讯", valuable_news)
            except: pass

            print(" [3/4] 🏛️ 正在逆推 [国家宏观意志] (T-3日穿透)...")
            for days_back in range(3):
                date_str = (datetime.now() - timedelta(days=days_back)).strftime("%Y%m%d")
                try:
                    df_cctv = ak.news_cctv(date=date_str)
                    if not df_cctv.empty:
                        for _, row in df_cctv.iterrows():
                            self._filter_and_append(row['content'], date_str, row['title'], f"新闻联播(T-{days_back})", valuable_news)
                        break 
                except: continue
            
            print(" [4/4] 💸 正在监控 [A股主线资金] 暴动板块...")
            try:
                df_board = ak.stock_board_industry_name_em()
                hot_boards = df_board[df_board['涨跌幅'] > 4.5].head(3)
                for _, row in hot_boards.iterrows():
                    msg = f"主线确认！【{row['板块名称']}】今日暴涨 {row['涨跌幅']}%, 属于全市场绝对共识！"
                    valuable_news.append({'time': "今日盘面", 'tags': "📈 [资金共振高潮]", 'title': msg, 'source': "资金雷达", 'score': 2})
            except: pass

            if not valuable_news:
                print("\n☕ 矩阵静默。当前未挖掘到具备【超景气级别】的信息，空仓等待。")
                return
                
            valuable_news.sort(key=lambda x: x.get('score', 0), reverse=True)
                
            print("\n" + "!"*75)
            print(f" 🚨 挖掘完毕：提炼出 {len(valuable_news)} 条【高能 Alpha 源头情报】")
            print("!"*75)
            for news in valuable_news[:20]:
                star_str = "★" * news.get('score', 1)
                print(f"[{news['time']}] {star_str} | 来源: {news['source']} | {news['tags']}\n📌 {news['title']}\n" + "-"*60)
            
            print("\n💡 审计师指令：重点打击带有 [★★] 星号的标的。找到目标代码后，执行 [模块 2]。")
                
        except Exception as e:
            print(f"矩阵遭遇未知异常: {e}")

    def _filter_and_append(self, text, time_str, title, source, result_list):
        tags = []
        score = 1
        text_full = str(text) + str(title)
        
        if self.use_llm:
            if re.search(self.kw_trap, text_full): return 
            prompt = f"请判断下面的新闻标题是否包含：1.改变行业的颠覆性技术突破。2.严重的供需断裂或现货暴涨满产。3.国家级宏观政策刺激。\n如果包含，只输出'<SCORE:2>'。如果不相关或平庸，只输出'<SCORE:0>'。\n标题：{title}"
            try:
                res = requests.post(self.llm_url, json={"model": "qwen2.5:7b", "prompt": prompt, "stream": False}, timeout=1.5)
                if res.status_code == 200:
                    ans = res.json().get('response', '')
                    if "<SCORE:0>" in ans: return
                    elif "<SCORE:2>" in ans:
                        score = 2
                        tags.append("🧠 [LLM:核心奇点共振]")
            except: pass
            
        if not tags: # Fallback to regex
            if re.search(self.kw_trap, text_full): return 
            if re.search(self.kw_type1_tech, text_full): tags.append("🚀 [Type1:颠覆奇点]")
            if re.search(self.kw_type2_cycle, text_full): tags.append("🔥 [Type2:现货断裂]")
            if re.search(self.kw_policy_hard, text_full): tags.append("🏛️ [Type3:宏观真金]")
            if len(tags) >= 2: score = 2
        
        if tags and not any(title == item['title'] for item in result_list):
            result_list.append({'time': time_str, 'tags': " | ".join(tags), 'title': title, 'source': source, 'score': score})

    # ----------------------------------------
    # 模块二：极客级深度底料打包 (风控全自动接管)
    # ----------------------------------------
    def module_2_audit_prep(self, target_code):
        print(f"\n📥 [深度穿透审计准备] -> 量子锁死目标: {target_code}")
        try:
            # ===== 阶段 1: 多源容灾获取股票基础信息 =====
            print("\n🔍 [阶段1] 启动多源容灾获取器...")
            stock_info = self._get_stock_info(target_code)
            spot_df = stock_info.get('spot_df', pd.DataFrame())  # 可能为空

            target_name = stock_info['name']
            blind_mode = not stock_info['success']

            if blind_mode:
                print("⚠️ [盲降模式] 所有行情接口均失败，启用强制下载模式。")
                print("   → 将跳过参数面板组装，但财报 PDF 下载不受影响。")
                target_name = target_code  # 用代码作为名称

            # 构造一个兼容的 target_info Series（用于 _generate_parameters）
            target_info = pd.Series({
                '名称': target_name,
                '最新价': stock_info['price'],
                '换手率': stock_info['turnover'],
                '市盈率-动态': stock_info['pe_ttm'],
                '市净率-动态': stock_info['pb'],
            })

            # ===== 阶段 2: 行业与竞对探测（允许失败） =====
            rival_code, rival_name, core_industry = None, None, "未知"
            if not blind_mode:
                try:
                    ind_info = ak.stock_individual_info_em(symbol=target_code)
                    core_industry = ind_info[ind_info['item'] == '行业']['value'].values[0]
                    all_boards = ak.stock_board_industry_name_em()
                    matched_board = next((b for b in all_boards['板块名称'] if core_industry in b or b in core_industry), None)
                    
                    if matched_board:
                        cons_df = ak.stock_board_industry_cons_em(symbol=matched_board).sort_values(by='总市值', ascending=False)
                        for _, r in cons_df.iterrows():
                            if str(r['代码']) != target_code:
                                rival_code, rival_name = str(r['代码']), r['名称']
                                print(f"🎯 寻敌雷达锁定: 【{matched_board}】最强对手 -> {rival_name}({rival_code})")
                                break
                except:
                    pass

            core_business = f"主营板块: 【{core_industry}】"
            recent_news_str = ""
            if not blind_mode:
                try:
                    profile = ak.stock_profile_cninfo(symbol=target_code)
                    if not profile.empty and '主营业务' in profile.columns:
                        business_desc = str(profile['主营业务'].iloc[0]).replace('\n', '')
                        core_business += f" | 业务穿透: {business_desc[:80]}..."
                    
                    news_df = ak.stock_news_em(symbol=target_code).head(2)
                    if not news_df.empty:
                        recent_news_str = " | ".join(news_df['新闻标题'].tolist())
                except: pass
            
            final_catalyst_str = core_business
            if recent_news_str:
                final_catalyst_str += f"\n**系统自动捕获近期催化剂：** {recent_news_str}"

            # 统一输出目录: company_info/{name}_{code}/ （不含日期，方便增量更新）
            base_dir = "company_info"
            save_dir = os.path.join(base_dir, f"{target_name}_{target_code}")
            if not os.path.exists(save_dir):
                os.makedirs(save_dir)
            else:
                print(f"  📂 已存在文件夹【{save_dir}】，将进行增量合并。")

            # ===== 阶段 3: 参数面板组装（允许失败，不阻断后续） =====
            if not blind_mode:
                try:
                    self._generate_parameters(target_code, target_name, target_info, final_catalyst_str, save_dir, spot_df)
                except Exception as e:
                    print(f"⚠️ 参数面板组装受损 (不影响财报下载): {e}")
            else:
                print("⏭️ [盲降] 跳过参数面板组装。")

            # ===== 阶段 4: 强制执行财报下载（永远执行，不受前面影响） =====
            print("\n📥 正在使用流式引擎抽取巨潮 PDF 底稿 (杜绝内存溢出)...")
            self._dl_cninfo(target_code, "category_ndbg_szsh", 2, save_dir)   # 年报（最近2份）
            self._dl_cninfo(target_code, "category_bndbg_szsh", 2, save_dir)  # 半年报
            self._dl_cninfo(target_code, "category_sjdbg_szsh", 2, save_dir)  # 三季报
            self._dl_cninfo(target_code, "category_yjdbg_szsh", 1, save_dir)  # 一季报
            self._dl_cninfo(target_code, "", 5, save_dir, searchkey="调研")  # 投资者纪要/调研接待（全分类搜索）
            if rival_code:
                self._dl_cninfo(rival_code, "category_ndbg_szsh", 1, save_dir)  # 竞对年报
                self._dl_cninfo(rival_code, "category_sjdbg_szsh", 1, save_dir)  # 竞对最新季报

            print(f"\n🎉 战术底料打包完成！请前往路径查收: [{save_dir}]")
            print("💡 终极指令：直接全选复制『00_参数面板_发给AI.md』的内容，作为硬数据喂给我！")

        except Exception as e:
            print(f"系统遭遇异常: {e}")

    def _generate_parameters(self, code, name, stock_data, core_business, save_dir, spot_df=None):
        print("⚙️ 正在执行高维参数重组与 Phase V 风控接管...")
        try:
            # 1. 宏观流动性 F 乘数智能判定（用 push2 大盘接口，直接可靠）
            raw_market_vol = 1.0
            try:
                em_mkt_url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
                em_mkt_params = {
                    "fltt": 2, "invt": 2,
                    "fields": "f12,f6",  # f6=全天成交额
                    "secids": "1.000001,0.399001,1.000016,0.399006",
                    "ut": "b2884a393a59ad64002292a3e90d46a5"
                }
                mkt_resp = requests.get(em_mkt_url, params=em_mkt_params,
                                        headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
                mkt_data = mkt_resp.json().get("data", {}).get("diff", [])
                raw_market_vol = sum(float(d.get("f6", 0) or 0) for d in mkt_data if d) / 1e12
                print(f"  ✅ push2 大盘总成交额: {raw_market_vol:.2f} 万亿")
            except Exception:
                pass  # 保留默认常态值 1.0
            if raw_market_vol >= 1.5:
                market_vol_status = f"{raw_market_vol:.2f} 万亿 (疯牛/核心起舞 -> F乘数硬编码: x1.2)"
            elif raw_market_vol <= 0.8:
                market_vol_status = f"{raw_market_vol:.2f} 万亿 (冰点/流动性枯竭 -> F乘数硬编码: x0.8)"
            else:
                market_vol_status = f"{raw_market_vol:.2f} 万亿 (常态震荡 -> F乘数硬编码: x1.0)"

            # 安全获取腨情字段（兼容容灾源和全量行情将不同的字段函）
            def _safe_val(series, *keys, default='N/A'):
                for k in keys:
                    v = series.get(k, None) if hasattr(series, 'get') else getattr(series, k, None)
                    if v is not None and str(v) not in ('', 'nan', 'None', 'N/A'):
                        return v
                return default

            p_now_raw = _safe_val(stock_data, '最新价', '收盘', default=0)
            try:
                p_now = float(p_now_raw)
            except (ValueError, TypeError):
                p_now = 0.0
            turnover = _safe_val(stock_data, '换手率', default='N/A')
            pe_ttm = _safe_val(stock_data, '市盈率-动态', '市盈率', default='N/A')
            pb = _safe_val(stock_data, '市净率-动态', '市净率', default='N/A')
            
            # 2. 深度历史重构 - push2 kline 三年日线（带重试）
            p_min_3y, rise_from_bottom, price_percentile = p_now, 0.0, 100.0
            death_turnover_warning = "[安全]"
            extreme_rise_warning = ""

            try:
                market_prefix = "1" if str(code).startswith("6") else "0"
                secid = f"{market_prefix}.{code}"
                kline_url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
                kline_params = {
                    "secid": secid, "fields1": "f1,f2,f3,f4,f5,f6",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                    "klt": 101, "fqt": 1,
                    "beg": (datetime.now() - timedelta(days=365*3)).strftime("%Y%m%d"),
                    "end": datetime.now().strftime("%Y%m%d"),
                    "ut": "fa5fd1943c7b386f172d6893dbfba10b"
                }
                # 带重试的 kline 获取（最多 3 次）
                klines = []
                for attempt in range(3):
                    try:
                        sess = requests.Session()
                        kline_resp = sess.get(kline_url, params=kline_params,
                                              headers={"User-Agent": "Mozilla/5.0"},
                                              timeout=15)
                        kline_data = kline_resp.json().get("data", {}) or {}
                        klines = kline_data.get("klines", []) or []
                        if klines:
                            break
                    except Exception:
                        time.sleep(1)
                if klines:
                    lows  = [float(k.split(',')[4]) for k in klines]
                    highs = [float(k.split(',')[3]) for k in klines]
                    turnover_list = [float(k.split(',')[10]) for k in klines if len(k.split(',')) > 10]
                    p_min_3y = min(lows)
                    p_max_3y = max(highs)
                    price_percentile = ((p_now - p_min_3y) / (p_max_3y - p_min_3y) * 100) if p_max_3y != p_min_3y else 0
                    rise_from_bottom = ((p_now - p_min_3y) / p_min_3y * 100) if p_min_3y > 0 else 0
                    if turnover_list:
                        max_t5 = max(turnover_list[-5:])
                        if max_t5 > 40 and 'N' not in name and 'C' not in name:
                            death_turnover_warning = f"⚠️ [触发死亡换手清仓线! 近5日极大换手率达 {max_t5}%]"
                    if len(lows) >= 60:
                        min_60d = min(lows[-60:])
                        rise_60d = ((p_now - min_60d) / min_60d * 100) if min_60d > 0 else 0
                        if rise_60d > 150:
                            extreme_rise_warning = f" (⚠️ 警报：近60日已极端暴涨 {rise_60d:.1f}%，极度透支！)"
                    print(f"  ✅ kline 三年历史({len(klines)}条): 最低={p_min_3y}, 分位={price_percentile:.1f}%")
                else:
                    print("  ⚠️ kline 三次重试均失败，歷史分位使用默认值")
            except Exception as ek:
                print(f"  ⚠️ kline 历史接口失败: {type(ek).__name__}")

            holder_trend = "数据缺失"
            try:
                gdhs_df = ak.stock_zh_a_gdhs_detail_em(symbol=code)
                if not gdhs_df.empty:
                    # 实际列名是 '股东户数-增减比例'
                    change_col = next((col for col in ['股东户数-增减比例', '户数变化比例', '本次变动比例', '变动比例'] if col in gdhs_df.columns), None)
                    if change_col:
                        latest_change = gdhs_df.iloc[-1][change_col]  # 最新一期（最后一行）
                        if isinstance(latest_change, str): latest_change = float(latest_change.replace('%', '').strip())
                        latest_change = float(latest_change)
                        if latest_change > 5: holder_trend = f"增加 {latest_change:.2f}% (⚠️ 主力派发/散户接盘警报)"
                        elif latest_change < -5: holder_trend = f"减少 {abs(latest_change):.2f}% (📈 主力吸筹/筹码高度集中)"
                        else: holder_trend = f"{latest_change:.2f}% (筹码平稳)"
                    else:
                        holder_trend = f"列名未匹配, 可用列: {list(gdhs_df.columns)}"
            except: pass

            eps_forecast = "[提取失败，需自行研判]"
            try:
                # 通过 PE_TTM 和当前股价反算 EPS_TTM
                if pe_ttm != 'N/A' and p_now > 0:
                    pe_val = float(pe_ttm)
                    if pe_val > 0:
                        eps_ttm = round(p_now / pe_val, 2)
                        eps_forecast = f"EPS_TTM={eps_ttm} (由 P/PE 反算，未来年度预测需参考券商研报)"
                        print(f"  ✅ EPS_TTM反算: {eps_ttm}")
            except Exception as e:
                print(f"  [EPS] 警告: {e}")

            # 财务雷区判定
            st_warning = "⚠️ [财务暴雷判定: 是(ST/负净资产，需即刻熔断！)]" if ('ST' in name or str(pb).startswith('-')) else "[通过]"

            # 构建参数字段列表，每个字段带信心等级 (high=程序获取到有效数据, low=使用默认值/失败)
            fields = [
                ("标的名称/代码", f"{name} ({code}) {st_warning}", "high"),
                ("当前价格 (P_now)", f"{p_now:.2f}", "high" if p_now > 0 else "low"),
                ("近3年最低价 (P_min_3y, 前复权)", f"{p_min_3y:.2f} (自底部已反弹 {rise_from_bottom:.1f}%){extreme_rise_warning}",
                 "high" if p_min_3y != p_now else "low"),
                ("当前价格历史分位 (Price_Percentile)", f"{price_percentile:.1f}%",
                 "high" if p_min_3y != p_now else "low"),
                ("最新静态/动态市盈率 (PE_TTM)", f"{pe_ttm}", "high" if pe_ttm != 'N/A' else "low"),
                ("最新市净率 (PB)", f"{pb}", "high" if pb != 'N/A' else "low"),
                ("未来三年预期每股收益 (EPS_Y1, EPS_Y2, EPS_Y3)", f"{eps_forecast}",
                 "low" if "提取失败" in eps_forecast or "反算" in eps_forecast else "high"),
                ("核心产品现货/期货价格趋势 或 订单销量",
                 "[请结合源头情报或 PDF 纪要人工填入：例如产品正在涨价，或产能满载]", "low"),
                ("今日换手率 (Turnover)", f"{turnover}% {death_turnover_warning}",
                 "high" if turnover not in (0, 0.0, 'N/A') else "low"),
                ("两市今日总成交额 (Market_Vol)", f"{market_vol_status}", "high" if raw_market_vol != 1.0 else "low"),
                ("最新股东户数变化", f"{holder_trend}", "high" if "缺失" not in holder_trend else "low"),
                ("核心催化剂/行业背景", f"{core_business}", "high"),
            ]

            # 读取旧面板文件（如果存在），解析为 {字段名: 值} 字典
            panel_path = os.path.join(save_dir, "00_参数面板_发给AI.md")
            old_fields = {}
            old_rag = ""
            if os.path.exists(panel_path):
                try:
                    with open(panel_path, "r", encoding="utf-8") as rf:
                        old_content = rf.read()
                    # 提取增量 RAG 数据
                    rag_marker = "### 📄"
                    rag_idx = old_content.find(rag_marker)
                    if rag_idx != -1:
                        old_rag = "\n" + old_content[rag_idx:]
                    # 解析旧字段
                    for line in old_content.split("\n"):
                        line = line.strip()
                        if line.startswith("**") and "：**" in line:
                            parts = line.split("：**", 1)
                            key = parts[0].replace("**", "").strip()
                            val = parts[1].strip() if len(parts) > 1 else ""
                            old_fields[key] = val
                except: pass

            # 智能合并：逐字段判定是否用新值覆盖
            merged_lines = []
            for key, new_val, confidence in fields:
                if confidence == "low" and key in old_fields:
                    old_val = old_fields[key]
                    # 旧值与默认模板不同 → 说明用户手工修改过，保留旧值
                    if old_val and old_val != new_val:
                        merged_lines.append(f"**{key}：** {old_val}")
                        continue
                merged_lines.append(f"**{key}：** {new_val}")

            # 处理催化剂换行（系统自动捕获近期催化剂在 core_business 之后）
            md = "\n".join(merged_lines) + "\n"

            with open(panel_path, "w", encoding="utf-8") as f:
                f.write(md)
                if old_rag:
                    f.write(old_rag)
            print(f"✅ 参数面板已智能合并更新 (时间: {datetime.now().strftime('%Y-%m-%d %H:%M')})")
            print(f"   → 低信心字段已保留您的手工修改，高信心字段已刷新")
            
            if self.db_collection is not None:
                try:
                    self.db_collection.insert_one({
                        "code": code, "name": name, "timestamp": datetime.now(),
                        "price": p_now, "pe_ttm": pe_ttm, "pb": pb,
                        "turnover": turnover, "rise_from_bottom": rise_from_bottom,
                        "eps_forecast": eps_forecast
                    })
                    print("☁️ [MongoDB] 数据已同步至靶标库。")
                except Exception: pass
        except Exception as e:
            print(f"⚠️ 核心参数组装受损: {e}")

    def _dl_cninfo(self, code, cat, limit, save_dir, searchkey=None):
        # 获取 orgId 用于构建正确的 stock 参数（格式: "code,orgId"）
        org_id = self._get_cninfo_orgid(code)
        stock_param = f"{code},{org_id}" if org_id else f"{code},"
        # 根据股票代码自动适配交易所
        column = "sse" if str(code).startswith('6') else "szse"
        payload = {"pageNum": 1, "pageSize": 20, "column": column, "tabName": "fulltext", 
                   "stock": stock_param, "isHLtitle": "true"}
        if cat:  # 不为空才传 category
            payload["category"] = cat
        if searchkey:
            payload["searchkey"] = searchkey
        try:
            res_obj = self._safe_request(self.cninfo_url, method="post", data=payload)
            if not res_obj: return
            res = res_obj.json()
            
            if not res.get('announcements'): return
            count = 0
            # 投资者纪要类别关键词列表（后处理过滤，避免 searchkey 过滤导致无结果）
            INVESTOR_KEYWORDS = ['投资者关系活动记录', '投资者调研', '调研接待', '问卷调查', '投资者问卷']
            is_investor_cat = cat in ('category_rcys_szsh',) or (searchkey and '投资者' in searchkey)
            for ann in res['announcements']:
                if count >= limit: break
                raw_title = ann['secName'] + "_" + ann['announcementTitle']
                clean_title = re.sub(r'[\\/*?:"<>|]', "", raw_title).replace(" ", "_").replace("\n", "")

                if "英文" in clean_title or "摘要" in clean_title: continue
                # 投资者类别：如果标题不包含任何投资者关键词则跳过
                if is_investor_cat:
                    if not any(kw in raw_title for kw in INVESTOR_KEYWORDS):
                        continue

                # 如果文件已存在（增量合并），跳过
                pdf_path = os.path.join(save_dir, f"{clean_title}.pdf")
                if os.path.exists(pdf_path):
                    print(f"  ⏭️ 已存在，跳过: {clean_title[:35]}...")
                    count += 1
                    continue
                
                print(f"  ⬇️ 流式写入中: {clean_title[:35]}...pdf")
                # 启用军工级 Stream 写入，防内存崩塌
                pdf_res = self._safe_request(self.cninfo_dl_base + ann['adjunctUrl'], method="get", stream=True)
                if pdf_res:
                    pdf_path = os.path.join(save_dir, f"{clean_title}.pdf")
                    with open(pdf_path, 'wb') as f:
                        for chunk in pdf_res.iter_content(chunk_size=8192):
                            f.write(chunk)
                            
                    if PdfReader and ("年报" in raw_title or "调研" in raw_title):
                        try:
                            reader = PdfReader(pdf_path)
                            extracted = []
                            for i in range(min(5, len(reader.pages))):
                                text = reader.pages[i].extract_text()
                                if text:
                                    sentences = re.split(r'[。！\n]', text)
                                    for s in sentences:
                                        if re.search(r'(产能|满产|开发|研发|突破|供不应求|订单|大幅增长)', s):
                                            c_s = s.strip()
                                            if len(c_s) > 10 and len(c_s) < 100 and c_s not in extracted:
                                                extracted.append(c_s)
                            if extracted:
                                with open(os.path.join(save_dir, "00_参数面板_发给AI.md"), "a", encoding="utf-8") as mdf:
                                    mdf.write(f"\n\n### 📄 {clean_title[:30]} - 增量 RAG 提纯数据:\n")
                                    for info in extracted[:5]:
                                        mdf.write(f"- {info}\n")
                        except Exception: pass
                count += 1
                time.sleep(1.0)
        except Exception as e:
            print(f"  [x] 下载线程中断: {e}")

if __name__ == "__main__":
    terminal = OmniTerminal()
    while True:
        print("\n" + "█"*65 + "\n 🌌 Omni-Analyst v7.0 Singularity (奇点降临版)\n" + "█"*65)
        print(" [1] 📡 奇点雷达 (多维情报共振 ★★★ + 龙虎榜真金刺透)")
        print(" [2] 📥 奇点打包 (死亡换手自动熔断 + 宏观流动性自动定标)")
        print("     └─ 支持批量输入！多只股票用逗号/空格分隔")
        print(" [0] 切断数据连线 (退出)")
        c = input("\n👉 输入指令数字: ").strip()
        
        if c == '1': terminal.module_1_intel_radar()
        elif c == '2':
            raw_input = input("🎯 输入A股代码/名称 (多只用逗号或空格分隔, 如: 000001,002648 601318): ").strip()
            # 解析批量输入：支持逗号、空格、顷号、中文逗号分隔
            codes = [s.strip() for s in re.split(r'[,，\s;]+', raw_input) if s.strip()]
            if not codes:
                print("⚠️ 未输入任何股票代码")
                continue
            print(f"\n🚀 批量任务启动，共 {len(codes)} 只股票: {codes}")
            for i, code in enumerate(codes, 1):
                print(f"\n{'='*50}")
                print(f"📌 [{i}/{len(codes)}] 正在处理: {code}")
                print(f"{'='*50}")
                terminal.module_2_audit_prep(code)
            print(f"\n🎉 批量任务全部完成！共处理 {len(codes)} 只股票")
        elif c == '0': break
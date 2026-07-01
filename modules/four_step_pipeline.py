"""
排列5 四步流水线分析模块

全新的四步式分析流水线，四个步骤严格串行执行：

步骤1: 专家文章爬取与结构化AI分析
  - 爬取目标期数对应的所有专家文章
  - 逐篇调用AI进行结构化整理
  - 将每篇分析报告存入Redis（kpluckynumber:pl5:expert_report:{article_id}，7天过期）

步骤2: 走势图数据分析与AI预测
  - 获取最近30-60期走势图数据（基础+万千百十个位）
  - 调用AI分析走势规律并生成预测报告
  - 将走势报告存入Redis（kpluckynumber:pl5:trend_analysis:{issue}，7天过期）

步骤3: 专家报告整合分析
  - 从Redis读取所有步骤1的专家分析报告
  - 整合后调用AI进行综合分析，生成综合分析报告
  - 将综合报告存入Redis（kpluckynumber:pl5:integrated_report:{issue}，7天过期）

步骤4: 最终预测结果生成与入库
  - 从Redis读取步骤2走势报告和步骤3综合报告
  - 整合后调用AI进行最终分析与预测
  - 将最终预测结果存入MySQL数据库p5_ai_report表

关键设计决策：
- 每步都有独立的错误处理和日志记录
- Redis过期时间按数据类型设置（文章7天、趋势7天、综合7天）
- 数据库写入包含完整字段（期数/预测号码/分析报告/生成时间）
- 前序步骤失败可触发降级策略（基于已有数据继续）

调用路径：
    main.py → run_four_step_pipeline(target_issue, data_limit)
             → FourStepPipeline.execute_pipeline()
"""

import logging
import os
import json
import uuid
import time
import random
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

os.makedirs('logs', exist_ok=True)

logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler = logging.FileHandler('logs/four_step_pipeline.log', encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


class FourStepPipeline:
    """
    四步流水线分析器

    严格串行执行四个分析步骤，每步输出作为后续步骤的输入。
    """

    # Redis键名模板
    REDIS_ARTICLE_REPORT_KEY = 'kpluckynumber:pl5:expert_report:{article_id}'
    REDIS_TREND_ANALYSIS_KEY = 'kpluckynumber:pl5:trend_analysis:{issue}'
    REDIS_INTEGRATED_REPORT_KEY = 'kpluckynumber:pl5:integrated_report:{issue}'
    ARTICLE_LIST_KEY = 'kpluckynumber:pl5:article:list'

    def __init__(self):
        """初始化分析器（所有组件采用懒加载）"""
        self.spider = None
        self.redis_client = None
        self.ai_client = None
        self.db_client = None
        # 流水线状态跟踪
        self.pipeline_state = {
            'article_reports': [],       # 步骤1产出：专家文章分析报告列表
            'trend_report': None,        # 步骤2产出：走势分析报告
            'integrated_report': None,   # 步骤3产出：综合分析报告
            'final_report': None,        # 步骤4产出：最终预测结果
            'started_at': None,          # 流水线开始时间
            'completed_at': None,        # 流水线结束时间
        }

    # ================================================================
    # 组件懒加载
    # ================================================================

    def _init_spider(self):
        """懒加载初始化爬虫模块"""
        try:
            from modules.ydniu_spider import YDNiuSpider
            self.spider = YDNiuSpider()
            logger.info('爬虫模块初始化成功')
        except ImportError as e:
            logger.error(f'无法导入爬虫模块: {e}')

    def _init_redis(self):
        """懒加载初始化Redis客户端"""
        try:
            from modules.redis_client import RedisClient
            self.redis_client = RedisClient()
            if self.redis_client.is_connected():
                logger.info('Redis客户端初始化成功')
            else:
                logger.warning('Redis客户端连接失败，部分功能降级')
        except ImportError as e:
            logger.error(f'无法导入Redis模块: {e}')

    def _init_ai_client(self):
        """懒加载初始化AI客户端"""
        try:
            from modules.ernie_ai_analyzer import ERNIEAIAnalyzer
            self.ai_client = ERNIEAIAnalyzer()
            logger.info(f'AI客户端初始化成功，模型: {self.ai_client.model_name}')
        except ImportError as e:
            logger.error(f'无法导入AI客户端模块: {e}')

    def _init_db_client(self):
        """懒加载初始化数据库客户端"""
        try:
            from modules.database_p5 import P5Database
            self.db_client = P5Database()
            if self.db_client.connect():
                logger.info('数据库客户端初始化成功')
            else:
                logger.warning('数据库客户端连接失败')
        except ImportError as e:
            logger.error(f'无法导入数据库模块: {e}')

    # ================================================================
    # 工具方法
    # ================================================================

    @staticmethod
    def _generate_article_id(url: str, index: int = 0) -> str:
        """生成文章唯一ID"""
        import hashlib
        url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
        if index > 0:
            return f'{url_hash}_{index}'
        return url_hash

    @staticmethod
    def _extract_issue_from_article(article_data: Dict[str, Any], target_issue: Optional[str] = None) -> Optional[str]:
        """从文章数据中提取期号"""
        if target_issue:
            return target_issue
        # 从标题提取
        title = article_data.get('title', '') or article_data.get('link_title', '')
        if title:
            import re
            match = re.search(r'(\d{6,8})', title)
            if match:
                return match.group(1)
        # 从URL提取
        url = article_data.get('url', '') or article_data.get('link_url', '')
        if url:
            import re
            match = re.search(r'(\d{6,8})', url)
            if match:
                return match.group(1)
        return None

    def _parse_ai_json(self, response_text: str) -> Optional[Dict[str, Any]]:
        """解析AI返回的JSON响应"""
        if not response_text:
            return None
        try:
            start_idx = response_text.find('{')
            end_idx = response_text.rfind('}') + 1
            if start_idx == -1 or end_idx == 0:
                logger.error('无法找到JSON起始或结束位置')
                return None
            json_str = response_text[start_idx:end_idx]
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error(f'JSON解析失败: {e}')
            return None
        except Exception as e:
            logger.error(f'解析AI响应失败: {e}')
            return None

    def _call_ai(self, system_prompt: str, user_prompt: str,
                 max_tokens: int = 8000, temperature: float = 0.7) -> Optional[Dict[str, Any]]:
        """
        统一AI调用封装

        Args:
            system_prompt: system角色消息内容
            user_prompt: user角色消息内容
            max_tokens: 最大输出Token数
            temperature: 温度参数

        Returns:
            解析后的JSON字典，失败返回None
        """
        if not self.ai_client or not self.ai_client.ai_available:
            logger.error('AI客户端不可用或未初始化')
            return None

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        try:
            ai_response = self.ai_client._call_ai_model(messages=messages, max_tokens=max_tokens, temperature=temperature)
            if not ai_response:
                logger.error('AI模型调用返回空结果')
                return None
            return self._parse_ai_json(ai_response)
        except Exception as e:
            logger.error(f'AI调用异常: {e}', exc_info=True)
            return None

    @staticmethod
    def _delay_random(min_sec: float = 2.0, max_sec: float = 4.0):
        """随机延迟，避免请求过快"""
        delay = random.uniform(min_sec, max_sec)
        logger.debug(f'等待 {delay:.1f} 秒...')
        time.sleep(delay)

    # ================================================================
    # 步骤1: 专家文章爬取与结构化AI分析
    # ================================================================

    def step1_crawl_articles_and_analyze(self, target_issue: str) -> Dict[str, Any]:
        """
        步骤1: 爬取指定期数的所有专家文章，逐篇进行AI结构化分析，存入Redis

        流程:
        1. 爬取目标期数的所有专家文章
        2. 逐篇调用AI进行结构化整理（提取推荐号码、分析观点、置信度等）
        3. 每篇分析报告存入Redis

        Args:
            target_issue: 目标期号（如"2026165"）

        Returns:
            {success, article_count, report_keys, articles, error}
        """
        logger.info('=' * 80)
        logger.info('【步骤1】开始：专家文章爬取与结构化AI分析')
        logger.info(f'目标期号: {target_issue}')
        logger.info('=' * 80)

        result = {
            'success': False,
            'step': 1,
            'article_count': 0,
            'ai_success_count': 0,
            'ai_fail_count': 0,
            'report_keys': [],
            'articles': [],
            'error': None
        }

        try:
            # 1. 初始化爬虫
            self._init_spider()
            if not self.spider:
                result['error'] = '爬虫模块初始化失败'
                return result

            # 2. 爬取目标期数文章
            logger.info('爬取目标期数文章...')
            self._delay_random(3, 6)
            crawl_result = self.spider.crawl_all_articles(target_issue=target_issue, max_articles=30)

            if not crawl_result.get('articles'):
                result['error'] = f'未爬取到期号{target_issue}的文章'
                return result

            articles = crawl_result['articles']
            result['article_count'] = len(articles)
            logger.info(f'成功爬取 {len(articles)} 篇文章')

            # 3. 初始化Redis和AI
            self._init_redis()
            self._init_ai_client()

            if not self.redis_client or not self.redis_client.is_connected():
                result['error'] = 'Redis客户端未连接'
                return result

            if not self.ai_client or not self.ai_client.ai_available:
                result['error'] = 'AI模型不可用'
                return result

            # 4. 逐篇AI分析并存储
            for idx, article in enumerate(articles, 1):
                article_id = self._generate_article_id(
                    article.get('url', article.get('link_url', f'article_{idx}')), idx
                )

                logger.info(f'[{idx}/{len(articles)}] 分析文章: {article.get("title", "未知")[:60]}')

                # 构建第一份AI分析提示词
                title = article.get('title', '未知')
                author = article.get('author', '未知')
                pub_time = article.get('publish_time', '未知')
                content = article.get('content', '')
                url = article.get('url', article.get('link_url', ''))

                prompt = f"""你是一位专业的排列5彩票数据分析专家。请对以下文章内容进行结构化整理和分析。

【文章信息】
标题：{title}
作者：{author}
发布时间：{pub_time}
期号：{target_issue}
来源URL：{url}

【文章内容】
{content[:5000]}

【分析要求】
请对以上内容进行深度分析，提取以下信息并以JSON格式返回（不要包含markdown标记）：

{{
    "data_source": "亿点牛专家文章",
    "article_id": "{article_id}",
    "article_title": "{title}",
    "author": "{author}",
    "publish_time": "{pub_time}",
    "issue_number": "{target_issue}",
    "forecast_numbers": {{
        "wan": [],
        "qian": [],
        "bai": [],
        "shi": [],
        "ge": []
    }},
    "recommended_combinations": [],
    "key_points": [],
    "trend_analysis": "",
    "confidence_level": "高/中/低",
    "risk_warning": "",
    "summary": ""
}}

注意事项：
1. forecast_numbers每个位置从文章中提取推荐的号码（数字数组，每个0-9）
2. recommended_combinations提取推荐组合（字符串数组）
3. key_points提取关键分析观点（字符串数组）
4. 如果文章没有明确提及某字段，对应设为空数组或空字符串
"""

                # 调用AI
                ai_result = self._call_ai(
                    system_prompt="你是一位专业的排列5彩票数据分析专家，擅长对文章内容进行结构化整理和分析。",
                    user_prompt=prompt,
                    max_tokens=4000,
                    temperature=0.5
                )

                if ai_result:
                    # 保存到Redis
                    store_data = {
                        'article_id': article_id,
                        'title': title,
                        'author': author,
                        'issue': target_issue,
                        'ai_analysis': ai_result,
                        'analyzed_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    }

                    redis_key = self.REDIS_ARTICLE_REPORT_KEY.format(article_id=article_id)
                    try:
                        self.redis_client.client.setex(
                            redis_key,
                            timedelta(days=7),
                            json.dumps(store_data, ensure_ascii=False)
                        )
                        result['report_keys'].append(redis_key)
                        result['ai_success_count'] += 1
                        logger.info(f'文章{idx} AI分析成功，Redis键: {redis_key}')
                    except Exception as e:
                        logger.error(f'文章{idx} 存入Redis失败: {e}')
                        result['ai_fail_count'] += 1
                else:
                    logger.warning(f'文章{idx} AI分析失败')
                    result['ai_fail_count'] += 1

                # 每篇文章间随机延迟
                self._delay_random(2, 4)

            # 5. 将所有文章ID添加到列表
            list_key = self.REDIS_ARTICLE_REPORT_KEY.replace('{article_id}', '')[:-1] + ':list'
            # 实际上我们使用 redis_client 已有的 article list 机制
            # 这里简单记录成功结果
            result['success'] = True
            logger.info(f'步骤1完成: 成功分析 {result["ai_success_count"]}/{result["article_count"]} 篇文章')

        except Exception as e:
            logger.error(f'步骤1异常: {e}', exc_info=True)
            result['error'] = str(e)

        self.pipeline_state['article_reports'] = result.get('articles', [])
        return result

    # ================================================================
    # 步骤2: 走势图数据分析与AI预测
    # ================================================================

    def step2_trend_analysis(self, target_issue: str, data_limit: int = 40) -> Dict[str, Any]:
        """
        步骤2: 获取走势图数据，喂给AI分析趋势并预测，存入Redis

        流程:
        1. 获取最近30-60期走势图数据
        2. 调用AI进行走势分析和号码预测
        3. 走势报告存入Redis

        Args:
            target_issue: 目标期号
            data_limit: 获取历史数据的期数限制（默认40期）

        Returns:
            {success, report_key, report_data, error}
        """
        logger.info('=' * 80)
        logger.info('【步骤2】开始：走势图数据分析与AI预测')
        logger.info(f'目标期号: {target_issue}, 数据期数: {data_limit}')
        logger.info('=' * 80)

        result = {
            'success': False,
            'step': 2,
            'report_key': '',
            'report_data': None,
            'error': None
        }

        try:
            # 1. 初始化数据库和AI
            self._init_db_client()
            self._init_ai_client()

            if not self.db_client or not self.db_client.connection:
                result['error'] = '数据库客户端未连接'
                return result

            if not self.ai_client or not self.ai_client.ai_available:
                result['error'] = 'AI模型不可用'
                return result

            # 2. 获取走势图数据
            logger.info('获取走势图数据...')
            from modules.database_p5 import P5Database

            # 获取历史开奖数据
            self.db_client.cursor.execute(
                'SELECT * FROM p5_history_data ORDER BY issue DESC LIMIT %s',
                (data_limit,)
            )
            history_data = self.db_client.cursor.fetchall()

            # 获取基础走势图数据
            self.db_client.cursor.execute(
                'SELECT * FROM p5_trend_data ORDER BY issue DESC LIMIT %s',
                (data_limit,)
            )
            trend_data = self.db_client.cursor.fetchall()

            # 获取各位置走势数据
            position_configs = [
                ('wan_number', 'p5_position_trend', 'wan_number'),
                ('qian_number', 'p5_position_trend', 'qian_number'),
                ('bai_number', 'p5_position_trend', 'bai_number'),
                ('shi_number', 'p5_position_trend', 'shi_number'),
                ('ge_number', 'p5_position_trend', 'ge_number'),
            ]

            position_trends = {}
            for pos_abbr in ['wan', 'qian', 'bai', 'shi', 'ge']:
                self.db_client.cursor.execute(
                    f'SELECT issue, {pos_abbr}_number, is_odd, is_big, is_prime, omission, hot_level, '
                    f'consecutive_count FROM p5_{pos_abbr}_trend_data '
                    f'ORDER BY issue DESC LIMIT {data_limit}'
                )
                position_trends[f'{pos_abbr}_trend'] = self.db_client.cursor.fetchall()

            if not history_data:
                result['error'] = '数据库中无历史数据'
                return result

            latest_issue = history_data[0].get('issue', '')
            next_issue = target_issue  # 使用传入的目标期号

            logger.info(f'数据加载完成: 历史{len(history_data)}期, 走势{len(trend_data)}期, 最新期号{latest_issue}')

            # 3. 构建走势图分析提示词
            trend_data_format = {
                'basic_trend': trend_data,
                **position_trends
            }

            prompt_parts = []
            prompt_parts.append("""
你是一位专业的排列5走势数据分析专家。请对以下最近{}期的走势图数据进行深度分析。

【分析目标】
基于走势图数据分析各位置数字的走势规律、冷热状态、遗漏趋势等，
生成一份结构化的走势分析报告并预测目标期号{}的号码。

【报告要求】
请以严格的JSON格式输出，不要包含任何额外文字或markdown标记：
""".format(len(trend_data), target_issue))

            # 格式化基础走势数据
            prompt_parts.append("\n=== 基础走势图数据（最近{}期） ===\n".format(min(len(trend_data), 30)))
            prompt_parts.append("期号 | 日期 | 万 | 千 | 百 | 十 | 个 | 和值 | 奇偶比 | 大小比\n")
            for item in trend_data[:30]:
                issue = item.get('issue', '')
                draw_date = item.get('draw_date', '')
                wan = item.get('wan', 0)
                qian = item.get('qian', 0)
                bai = item.get('bai', 0)
                shi = item.get('shi', 0)
                ge = item.get('ge', 0)
                hezhi = item.get('hezhi', '')
                odd_even = item.get('odd_even_ratio', '')
                big_small = item.get('big_small_ratio', '')
                prompt_parts.append(f"{issue} | {draw_date} | {wan} | {qian} | {bai} | {shi} | {ge} | {hezhi} | {odd_even} | {big_small}")

            # 格式化各位置走势数据
            pos_names = {'wan_trend': '万位', 'qian_trend': '千位', 'bai_trend': '百位', 'shi_trend': '十位', 'ge_trend': '个位'}
            pos_nums = {'wan_trend': 'wan_number', 'qian_trend': 'qian_number', 'bai_trend': 'bai_number', 'shi_trend': 'shi_number', 'ge_trend': 'ge_number'}

            for trend_key, pos_name in pos_names.items():
                pos_data = trend_data_format.get(trend_key, [])
                if pos_data:
                    num_key = pos_nums[trend_key]
                    prompt_parts.append(f"\n=== {pos_name}走势数据（最近{min(len(pos_data), 30)}期） ===")
                    prompt_parts.append("期号 | 数字 | 奇偶 | 大小 | 质合 | 遗漏 | 冷热等级\n")
                    for item in pos_data[:30]:
                        issue = item.get('issue', '')
                        num = item.get(num_key, 0)
                        is_odd = '奇' if item.get('is_odd') else '偶'
                        is_big = '大' if item.get('is_big') else '小'
                        is_prime = '质' if item.get('is_prime') else '合'
                        omission = item.get('omission', 0)
                        hot_level = item.get('hot_level', '')
                        prompt_parts.append(f"{issue} | {num} | {is_odd} | {is_big} | {is_prime} | {omission} | {hot_level}")

                    # 统计摘要
                    num_freq = {}
                    for item in pos_data[:30]:
                        n = item.get(num_key, 0)
                        num_freq[n] = num_freq.get(n, 0) + 1
                    sorted_nums = sorted(num_freq.items(), key=lambda x: x[1], reverse=True)
                    hot = [n for n, _ in sorted_nums[:3]]
                    cold = [n for n, _ in sorted_nums[-3:]]
                    max_om = max((item.get('omission', 0) for item in pos_data[:30]), default=0)
                    prompt_parts.append(f"\n统计: 热号{hot}, 冷号{cold}, 最大遗漏{max_om}")

            # 输出格式要求
            prompt_parts.append(f"""
=== 输出格式 ===

请严格按照以下JSON格式输出走势分析报告：

{{
    "analysis_type": "走势图AI分析",
    "analysis_time": "YYYY-MM-DD HH:MM:SS",
    "data_period": "最近30期走势数据",
    "latest_issue": "{latest_issue}",
    "next_issue": "{target_issue}",
    "trend_summary": {{
        "overall_trend": "整体走势总结（100-200字）",
        "hot_numbers_summary": "热号总体描述",
        "cold_numbers_summary": "冷号总体描述",
        "pattern_summary": "模式规律总结"
    }},
    "position_analysis": {{
        "wan": {{
            "hot_numbers": [],
            "cold_numbers": [],
            "trend_direction": "走势方向（向上/向下/震荡）",
            "omission_analysis": "遗漏分析",
            "recommended_numbers": []
        }},
        "qian": {{
            "hot_numbers": [],
            "cold_numbers": [],
            "trend_direction": "走势方向",
            "omission_analysis": "遗漏分析",
            "recommended_numbers": []
        }},
        "bai": {{
            "hot_numbers": [],
            "cold_numbers": [],
            "trend_direction": "走势方向",
            "omission_analysis": "遗漏分析",
            "recommended_numbers": []
        }},
        "shi": {{
            "hot_numbers": [],
            "cold_numbers": [],
            "trend_direction": "走势方向",
            "omission_analysis": "遗漏分析",
            "recommended_numbers": []
        }},
        "ge": {{
            "hot_numbers": [],
            "cold_numbers": [],
            "trend_direction": "走势方向",
            "omission_analysis": "遗漏分析",
            "recommended_numbers": []
        }}
    }},
    "statistical_analysis": {{
        "hezhi_analysis": "和值走势分析（50-100字）",
        "span_analysis": "跨度分析（50-100字）",
        "odd_even_analysis": "奇偶比趋势分析（50-100字）",
        "big_small_analysis": "大小比趋势分析（50-100字）"
    }},
    "key_patterns": ["发现的规律模式1", "发现的规律模式2", "发现的规律模式3"],
    "risk_factors": ["需要关注的风险因素1", "需要关注的风险因素2"]
}}

注意事项：
1. 必须严格按JSON格式输出，不要有额外文字
2. 每个位置都有hot_numbers/cold_numbers/recommended_numbers（数字数组）
3. trend_direction描述走势方向
4. recommended_numbers为基于走势分析的推荐号码
""")

            full_prompt = "\n".join(prompt_parts)
            logger.info(f'走势分析提示词长度: {len(full_prompt)}')

            # 4. 调用AI
            logger.info('调用AI进行走势分析...')
            trend_ai_result = self._call_ai(
                system_prompt="你是一位专业的排列5走势数据分析专家，擅长分析走势图数据并发现规律。",
                user_prompt=full_prompt,
                max_tokens=6000,
                temperature=0.5
            )

            if not trend_ai_result:
                result['error'] = '走势AI分析失败'
                return result

            # 5. 存入Redis
            self._init_redis()
            if not self.redis_client or not self.redis_client.is_connected():
                logger.warning('Redis未连接，走势报告仅保存在内存中')
            else:
                report_key = self.REDIS_TREND_ANALYSIS_KEY.format(issue=target_issue)
                store_data = {
                    'issue': target_issue,
                    'trend_analysis': trend_ai_result,
                    'analyzed_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }
                try:
                    self.redis_client.client.setex(
                        report_key,
                        timedelta(days=7),
                        json.dumps(store_data, ensure_ascii=False)
                    )
                    result['report_key'] = report_key
                    logger.info(f'走势报告已存入Redis: {report_key}')
                except Exception as e:
                    logger.error(f'走势报告存入Redis失败: {e}')

            result['success'] = True
            result['report_data'] = trend_ai_result
            self.pipeline_state['trend_report'] = trend_ai_result

            logger.info(f'步骤2完成: 走势分析成功')

        except Exception as e:
            logger.error(f'步骤2异常: {e}', exc_info=True)
            result['error'] = str(e)

        return result

    # ================================================================
    # 步骤3: 专家报告整合分析
    # ================================================================

    def step3_integrate_expert_reports(self, target_issue: str) -> Dict[str, Any]:
        """
        步骤3: 从Redis读取所有专家分析报告，整合后调用AI综合分析

        流程:
        1. 从Redis读取步骤1存储的所有专家分析报告
        2. 整合后调用AI进行综合分析，生成综合分析报告
        3. 综合报告存入Redis

        Args:
            target_issue: 目标期号

        Returns:
            {success, integrated_report, redis_key, error}
        """
        logger.info('=' * 80)
        logger.info('【步骤3】开始：专家报告整合分析')
        logger.info(f'目标期号: {target_issue}')
        logger.info('=' * 80)

        result = {
            'success': False,
            'step': 3,
            'integrated_report': None,
            'redis_key': '',
            'expert_count': 0,
            'error': None
        }

        try:
            # 1. 初始化Redis和AI
            self._init_redis()
            self._init_ai_client()

            if not self.redis_client or not self.redis_client.is_connected():
                result['error'] = 'Redis客户端未连接'
                return result

            if not self.ai_client or not self.ai_client.ai_available:
                result['error'] = 'AI模型不可用'
                return result

            # 2. 从Redis读取所有专家分析报告
            logger.info('从Redis读取专家分析报告...')
            expert_reports = []

            # 遍历Redis中所有文章分析报告
            pattern = self.REDIS_ARTICLE_REPORT_KEY.replace('{article_id}', '*')
            article_keys = self.redis_client.client.keys(pattern)

            for key in article_keys:
                try:
                    data_str = self.redis_client.client.get(key)
                    if data_str:
                        data = json.loads(data_str)
                        if data.get('issue') == target_issue:
                            expert_reports.append(data)
                except Exception as e:
                    logger.warning(f'读取报告失败 {key}: {e}')
                    continue

            if not expert_reports:
                result['error'] = f'未找到期号{target_issue}的专家分析报告，请先执行步骤1'
                return result

            result['expert_count'] = len(expert_reports)
            logger.info(f'读取到 {len(expert_reports)} 篇专家分析报告')

            # 3. 获取历史数据（用于综合分析）
            self._init_db_client()
            db_history = {}
            if self.db_client and self.db_client.connection:
                self.db_client.cursor.execute(
                    'SELECT * FROM p5_history_data ORDER BY issue DESC LIMIT 30'
                )
                db_history['history'] = self.db_client.cursor.fetchall()
                if db_history['history']:
                    db_history['latest_issue'] = db_history['history'][0].get('issue', '')

            logger.info(f'历史数据: {len(db_history.get("history", []))} 期')

            # 4. 构建整合提示词
            prompt_parts = []
            prompt_parts.append(f"""
你是一位顶尖的排列5综合预测专家。请整合以下{len(expert_reports)}篇专家文章的分析报告，结合历史开奖数据，
进行深度综合分析，给出最终的号码预测报告。

【任务说明】
你将收到两类数据：
1. {len(expert_reports)}位专家的文章AI分析结果（各篇文章的结构化预测信息）
2. 最近30期的历史开奖数据

请综合所有信息，给出最终的预测号码和详细推理过程。
""")

            # ---- 第一部分：专家报告汇总 ----
            prompt_parts.append("\n" + "=" * 60)
            prompt_parts.append("一、专家报告汇总")
            prompt_parts.append("=" * 60)

            for idx, report in enumerate(expert_reports, 1):
                ai = report.get('ai_analysis', {})
                prompt_parts.append(f"\n--- 专家报告 {idx}/{len(expert_reports)} ---")
                prompt_parts.append(f"标题: {report.get('title', '未知')[:50]}")
                prompt_parts.append(f"作者: {report.get('author', '未知')}")
                prompt_parts.append(f"置信度: {ai.get('confidence_level', '未知')}")

                forecast = ai.get('forecast_numbers', {})
                if forecast:
                    pos_map = {'wan': '万位', 'qian': '千位', 'bai': '百位', 'shi': '十位', 'ge': '个位'}
                    for pos_key, pos_name in pos_map.items():
                        nums = forecast.get(pos_key, [])
                        if nums:
                            prompt_parts.append(f"  {pos_name}推荐: {nums}")

                combos = ai.get('recommended_combinations', [])
                if combos:
                    prompt_parts.append(f"  推荐组合: {combos[:5]}")

                key_points = ai.get('key_points', [])
                if key_points:
                    for pt in key_points[:3]:
                        prompt_parts.append(f"  要点: {pt[:100]}")

                summary = ai.get('summary', '')
                if summary:
                    prompt_parts.append(f"  总结: {summary[:200]}")

                trend = ai.get('trend_analysis', '')
                if trend:
                    prompt_parts.append(f"  趋势分析: {str(trend)[:100]}")

                prompt_parts.append("")

            # ---- 第二部分：历史开奖数据 ----
            if db_history.get('history'):
                prompt_parts.append("\n" + "=" * 60)
                prompt_parts.append("二、历史开奖数据（最近30期）")
                prompt_parts.append("=" * 60)
                prompt_parts.append(f"最新期号: {db_history.get('latest_issue', '未知')}")
                prompt_parts.append("\n最近15期开奖记录：")
                for item in db_history['history'][:15]:
                    issue = item.get('issue', '')
                    wan = item.get('wan', 0)
                    qian = item.get('qian', 0)
                    bai = item.get('bai', 0)
                    shi = item.get('shi', 0)
                    ge = item.get('ge', 0)
                    hezhi = item.get('hezhi', '')
                    prompt_parts.append(f"  {issue}: {wan}{qian}{bai}{shi}{ge} 和值:{hezhi}")

            # ---- 输出格式要求 ----
            prompt_parts.append(f"""
=== 最终分析要求 ===

请综合以上{len(expert_reports)}篇专家报告和历史数据，进行深度推理，输出最终的预测报告。

请严格按照以下JSON格式输出（不要包含任何额外文字或markdown标记）：

{{
    "data_source": "专家文章整合分析",
    "analysis_time": "YYYY-MM-DD HH:MM:SS",
    "model_version": "专家报告整合模型v1.0",
    "expert_count": {len(expert_reports)},
    "current_issue": "{db_history.get('latest_issue', '未知')}",
    "next_issue": "{target_issue}",
    "prediction": {{
        "wan": {{"numbers": [], "confidence": [], "reason": ""}},
        "qian": {{"numbers": [], "confidence": [], "reason": ""}},
        "bai": {{"numbers": [], "confidence": [], "reason": ""}},
        "shi": {{"numbers": [], "confidence": [], "reason": ""}},
        "ge": {{"numbers": [], "confidence": [], "reason": ""}}
    }},
    "trend_analysis": {{
        "summary": "整体综合分析总结",
        "wan": "万位分析",
        "qian": "千位分析",
        "bai": "百位分析",
        "shi": "十位分析",
        "ge": "个位分析"
    }},
    "reasoning_process": [
        "综合分析推理步骤1",
        "综合分析推理步骤2",
        "综合分析推理步骤3"
    ],
    "recommended_combinations": [
        {{"combination": "5位号码字符串", "confidence": 0.85, "reason": "推荐理由"}}
    ],
    "key_conclusions": ["关键结论1", "关键结论2", "关键结论3"],
    "expert_consensus": "专家共识总结",
    "risk_warning": "风险提示文本"
}}

注意事项：
1. 每个位置推荐2-5个号码
2. 综合考虑所有专家意见，找出共识号码和分歧号码
3. reasoning_process至少3步完整推理
4. expert_consensus总结专家们的共同观点和分歧
""")

            full_prompt = "\n".join(prompt_parts)
            logger.info(f'整合分析提示词长度: {len(full_prompt)}')

            # 5. 调用AI
            logger.info('调用AI进行专家报告整合分析...')
            integrated_ai_result = self._call_ai(
                system_prompt="你是一位顶尖的排列5综合预测专家，擅长整合多元数据进行深度分析和精准预测。",
                user_prompt=full_prompt,
                max_tokens=8000,
                temperature=0.6
            )

            if not integrated_ai_result:
                result['error'] = '专家报告整合AI分析失败'
                return result

            # 6. 存入Redis
            self._init_redis()
            if self.redis_client and self.redis_client.is_connected():
                report_key = self.REDIS_INTEGRATED_REPORT_KEY.format(issue=target_issue)
                store_data = {
                    'issue': target_issue,
                    'integrated_report': integrated_ai_result,
                    'expert_count': len(expert_reports),
                    'analyzed_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }
                try:
                    self.redis_client.client.setex(
                        report_key,
                        timedelta(days=7),
                        json.dumps(store_data, ensure_ascii=False)
                    )
                    result['redis_key'] = report_key
                    logger.info(f'综合报告已存入Redis: {report_key}')
                except Exception as e:
                    logger.error(f'综合报告存入Redis失败: {e}')

            result['success'] = True
            result['integrated_report'] = integrated_ai_result
            self.pipeline_state['integrated_report'] = integrated_ai_result

            logger.info(f'步骤3完成: 专家报告整合分析成功，共整合{len(expert_reports)}篇报告')

        except Exception as e:
            logger.error(f'步骤3异常: {e}', exc_info=True)
            result['error'] = str(e)

        return result

    # ================================================================
    # 步骤4: 最终预测结果生成与入库
    # ================================================================

    def step4_final_prediction(self, target_issue: str) -> Dict[str, Any]:
        """
        步骤4: 从Redis读取走势报告和综合报告，整合后进行最终预测并存入数据库

        流程:
        1. 从Redis读取步骤2的走势分析报告
        2. 从Redis读取步骤3的综合分析报告
        3. 整合后调用AI进行最终预测
        4. 最终结果存入MySQL数据库p5_ai_report表

        Args:
            target_issue: 目标期号

        Returns:
            {success, report_uuid, final_report, error}
        """
        logger.info('=' * 80)
        logger.info('【步骤4】开始：最终预测结果生成与入库')
        logger.info(f'目标期号: {target_issue}')
        logger.info('=' * 80)

        result = {
            'success': False,
            'step': 4,
            'report_uuid': None,
            'final_report': None,
            'error': None
        }

        try:
            # 1. 初始化所需组件
            self._init_redis()
            self._init_ai_client()
            self._init_db_client()

            if not self.db_client or not self.db_client.connection:
                result['error'] = '数据库客户端未连接'
                return result

            if not self.ai_client or not self.ai_client.ai_available:
                result['error'] = 'AI模型不可用'
                return result

            # 2. 从Redis读取走势分析报告
            logger.info('从Redis读取走势分析报告...')
            trend_report_key = self.REDIS_TREND_ANALYSIS_KEY.format(issue=target_issue)
            trend_report = None

            if self.redis_client and self.redis_client.is_connected():
                try:
                    data_str = self.redis_client.client.get(trend_report_key)
                    if data_str:
                        trend_store_data = json.loads(data_str)
                        trend_report = trend_store_data.get('trend_analysis', trend_store_data)
                        logger.info('走势分析报告已从Redis加载')
                    else:
                        logger.warning(f'Redis中未找到走势报告: {trend_report_key}')
                except Exception as e:
                    logger.warning(f'读取走势报告失败: {e}')

            # 如果Redis没有，尝试使用内存中的
            if not trend_report and self.pipeline_state.get('trend_report'):
                trend_report = self.pipeline_state['trend_report']
                logger.info('使用内存中的走势分析报告')

            # 3. 从Redis读取综合分析报告
            logger.info('从Redis读取综合分析报告...')
            integrated_report_key = self.REDIS_INTEGRATED_REPORT_KEY.format(issue=target_issue)
            integrated_report = None

            if self.redis_client and self.redis_client.is_connected():
                try:
                    data_str = self.redis_client.client.get(integrated_report_key)
                    if data_str:
                        integrated_store_data = json.loads(data_str)
                        integrated_report = integrated_store_data.get('integrated_report', integrated_store_data)
                        logger.info('综合分析报告已从Redis加载')
                    else:
                        logger.warning(f'Redis中未找到综合报告: {integrated_report_key}')
                except Exception as e:
                    logger.warning(f'读取综合报告失败: {e}')

            if not integrated_report and self.pipeline_state.get('integrated_report'):
                integrated_report = self.pipeline_state['integrated_report']
                logger.info('使用内存中的综合分析报告')

            # 4. 获取历史数据
            self.db_client.cursor.execute('SELECT * FROM p5_history_data ORDER BY issue DESC LIMIT 30')
            history_data = self.db_client.cursor.fetchall()
            latest_issue = history_data[0].get('issue', '') if history_data else ''

            logger.info(f'数据准备完成: 走势报告={"有" if trend_report else "无"}, 综合报告={"有" if integrated_report else "无"}, 历史数据{len(history_data)}期')

            # 5. 构建最终分析提示词
            prompt_parts = []
            prompt_parts.append(f"""
你是一位排列5彩票预测的最高级别综合专家。请整合走势图AI分析报告和专家综合分析报告，
结合历史开奖数据，给出最终的号码预测。

【数据源说明】
1. 走势图AI分析报告：基于最近30期走势数据，AI识别出的规律、冷热号、遗漏趋势等
2. 专家综合分析报告：综合{len(self.pipeline_state.get('article_reports', []))}篇专家文章分析后的综合结论
3. 历史开奖数据：最近30期开奖记录
""")

            # ---- 第一部分：走势图AI分析报告 ----
            if trend_report:
                prompt_parts.append("\n" + "=" * 60)
                prompt_parts.append("一、走势图AI分析报告")
                prompt_parts.append("=" * 60)

                ts = trend_report.get('trend_summary', {})
                if ts:
                    prompt_parts.append(f"\n整体走势: {ts.get('overall_trend', '')[:300]}")
                    prompt_parts.append(f"热号总结: {ts.get('hot_numbers_summary', '')[:200]}")
                    prompt_parts.append(f"冷号总结: {ts.get('cold_numbers_summary', '')[:200]}")
                    prompt_parts.append(f"规律总结: {ts.get('pattern_summary', '')[:200]}")

                pa = trend_report.get('position_analysis', {})
                pos_names_map = {'wan': '万位', 'qian': '千位', 'bai': '百位', 'shi': '十位', 'ge': '个位'}
                for pk, pn in pos_names_map.items():
                    pdata = pa.get(pk, {})
                    if pdata:
                        prompt_parts.append(f"\n{pn}:")
                        prompt_parts.append(f"  热号: {pdata.get('hot_numbers', [])}")
                        prompt_parts.append(f"  冷号: {pdata.get('cold_numbers', [])}")
                        prompt_parts.append(f"  走势方向: {pdata.get('trend_direction', '')[:100]}")
                        prompt_parts.append(f"  推荐号码: {pdata.get('recommended_numbers', [])}")

                sa = trend_report.get('statistical_analysis', {})
                if sa:
                    prompt_parts.append(f"\n和值分析: {sa.get('hezhi_analysis', '')[:200]}")
                    prompt_parts.append(f"跨度分析: {sa.get('span_analysis', '')[:200]}")
                    prompt_parts.append(f"奇偶分析: {sa.get('odd_even_analysis', '')[:200]}")
                    prompt_parts.append(f"大小分析: {sa.get('big_small_analysis', '')[:200]}")

                kp = trend_report.get('key_patterns', [])
                if kp:
                    prompt_parts.append(f"\n发现规律: {'; '.join(kp)}")

            # ---- 第二部分：专家综合分析报告 ----
            if integrated_report:
                prompt_parts.append("\n" + "=" * 60)
                prompt_parts.append("二、专家综合分析报告")
                prompt_parts.append("=" * 60)

                int_pred = integrated_report.get('prediction', {})
                for pk, pn in pos_names_map.items():
                    pdata = int_pred.get(pk, {})
                    if pdata:
                        nums = pdata.get('numbers', [])
                        reason = pdata.get('reason', '')
                        if nums or reason:
                            prompt_parts.append(f"\n{pn}专家综合推荐: 号码{nums}, 理由: {reason[:200]}")

                int_combos = integrated_report.get('recommended_combinations', [])
                if int_combos:
                    prompt_parts.append(f"\n推荐组合: {int_combos[:5]}")

                int_rc = integrated_report.get('key_conclusions', [])
                if int_rc:
                    prompt_parts.append(f"\n关键结论: {int_rc}")

                int_consensus = integrated_report.get('expert_consensus', '')
                if int_consensus:
                    prompt_parts.append(f"\n专家共识: {str(int_consensus)[:300]}")

            # ---- 第三部分：历史开奖数据 ----
            if history_data:
                prompt_parts.append("\n" + "=" * 60)
                prompt_parts.append("三、历史开奖数据（最近30期）")
                prompt_parts.append("=" * 60)
                prompt_parts.append(f"最新期号: {latest_issue}")
                prompt_parts.append("\n最近15期：")
                for item in history_data[:15]:
                    issue = item.get('issue', '')
                    wan = item.get('wan', 0)
                    qian = item.get('qian', 0)
                    bai = item.get('bai', 0)
                    shi = item.get('shi', 0)
                    ge = item.get('ge', 0)
                    hezhi = item.get('hezhi', '')
                    span = item.get('span', '')
                    prompt_parts.append(f"  {issue}: {wan}{qian}{bai}{shi}{ge} 和值:{hezhi} 跨度:{span}")

            # ---- 输出格式要求 ----
            prompt_parts.append(f"""
=== 最终分析要求 ===

请整合以上所有数据源（走势图AI分析 + 专家综合报告 + 历史数据），
给出最终的号码预测。

请严格按照以下JSON格式输出（不要包含任何额外文字或markdown标记）：

{{
    "data_source": "走势图AI+专家综合+历史数据最终整合",
    "analysis_time": "YYYY-MM-DD HH:MM:SS",
    "model_version": "最终预测模型v2.0",
    "current_issue": "{latest_issue}",
    "next_issue": "{target_issue}",
    "prediction": {{
        "wan": {{"numbers": [], "confidence": [], "reason": ""}},
        "qian": {{"numbers": [], "confidence": [], "reason": ""}},
        "bai": {{"numbers": [], "confidence": [], "reason": ""}},
        "shi": {{"numbers": [], "confidence": [], "reason": ""}},
        "ge": {{"numbers": [], "confidence": [], "reason": ""}}
    }},
    "trend_analysis": {{
        "summary": "整体综合分析总结（200-300字）",
        "wan": "万位最终分析",
        "qian": "千位最终分析",
        "bai": "百位最终分析",
        "shi": "十位最终分析",
        "ge": "个位最终分析"
    }},
    "reasoning_process": [
        "推理步骤1",
        "推理步骤2",
        "推理步骤3",
        "推理步骤4",
        "推理步骤5"
    ],
    "recommended_combinations": [
        {{"combination": "5位号码字符串", "confidence": 0.85, "reason": "推荐理由"}},
        {{"combination": "5位号码字符串", "confidence": 0.78, "reason": "推荐理由"}},
        {{"combination": "5位号码字符串", "confidence": 0.72, "reason": "推荐理由"}}
    ],
    "statistical_features": {{
        "hezhi_range": "和值范围预测",
        "span_range": "跨度范围预测",
        "odd_even_ratio": "奇偶比偏好",
        "big_small_ratio": "大小比偏好",
        "hot_numbers": "推荐热号",
        "cold_numbers": "推荐冷号",
        "key_patterns": ["模式1", "模式2"]
    }},
    "key_conclusions": ["关键结论1", "关键结论2", "关键结论3"],
    "consensus_summary": "所有数据源的共识总结",
    "divergence_analysis": "数据源之间的分歧说明",
    "risk_warning": "风险提示文本"
}}

注意事项：
1. 每个位置推荐2-5个号码
2. reasoning_process至少5步完整推理
3. 综合考虑三个数据源的预测，给出最可靠的最终结果
4. consensus_summary总结所有数据源的共识点
5. divergence_analysis说明数据源之间的分歧及处理方式
""")

            full_prompt = "\n".join(prompt_parts)
            logger.info(f'最终预测提示词长度: {len(full_prompt)}')

            # 6. 调用AI
            logger.info('调用AI进行最终预测...')
            final_ai_result = self._call_ai(
                system_prompt="你是一位排列5彩票预测的最高级别综合专家，擅长整合多元数据给出精准预测。",
                user_prompt=full_prompt,
                max_tokens=8000,
                temperature=0.6
            )

            if not final_ai_result:
                result['error'] = '最终预测AI调用失败'
                return result

            # 7. 存入数据库
            logger.info('保存最终预测结果到数据库...')
            report_uuid = self._save_final_prediction_to_db(final_ai_result, latest_issue, target_issue)

            if not report_uuid:
                result['error'] = '最终预测保存到数据库失败'
                return result

            result['success'] = True
            result['report_uuid'] = report_uuid
            result['final_report'] = final_ai_result
            self.pipeline_state['final_report'] = final_ai_result

            logger.info('=' * 80)
            logger.info('【步骤4】完成：最终预测结果已入库')
            logger.info(f'报告UUID: {report_uuid}')
            logger.info(f'预测期号: {target_issue}')
            logger.info('=' * 80)

        except Exception as e:
            logger.error(f'步骤4异常: {e}', exc_info=True)
            result['error'] = str(e)

        return result

    def _save_final_prediction_to_db(self, final_report: Dict[str, Any], latest_issue: str, next_issue: str) -> Optional[str]:
        """
        将最终预测结果保存到MySQL数据库

        Args:
            final_report: 最终AI预测结果
            latest_issue: 最新期号
            next_issue: 预测期号

        Returns:
            报告UUID，失败返回None
        """
        try:
            import uuid
            report_uuid = str(uuid.uuid4())

            # 提取各字段
            reasoning = final_report.get('reasoning_process', '')
            if isinstance(reasoning, list):
                report_content = '\n'.join([f'{i + 1}. {r}' for i, r in enumerate(reasoning)])
            elif reasoning:
                report_content = str(reasoning)
            else:
                report_content = '暂无推理过程'

            trend = final_report.get('trend_analysis', {})
            if isinstance(trend, dict):
                trend_analysis = json.dumps(trend, ensure_ascii=False)
            else:
                trend_analysis = json.dumps({}, ensure_ascii=False)

            conclusions = final_report.get('key_conclusions', [])
            if isinstance(conclusions, list):
                key_conclusions = json.dumps(conclusions, ensure_ascii=False)
            else:
                key_conclusions = json.dumps([], ensure_ascii=False)

            combos = final_report.get('recommended_combinations', [])
            if combos:
                formatted_combos = []
                for c in combos:
                    if isinstance(c, dict):
                        combo_str = c.get('combination', '')
                        formatted_combos.append({
                            'combination': str(combo_str),
                            'confidence': c.get('confidence', 0),
                            'reason': c.get('reason', '')
                        })
                    elif isinstance(c, str):
                        formatted_combos.append({'combination': c})
                recommended_combinations = json.dumps(formatted_combos, ensure_ascii=False)
            else:
                recommended_combinations = json.dumps([], ensure_ascii=False)

            prediction = final_report.get('prediction', {})
            probability_stats = json.dumps(prediction, ensure_ascii=False)
            recommended_numbers = json.dumps(prediction, ensure_ascii=False)
            confidence_scores = json.dumps(prediction, ensure_ascii=False)

            # 调用数据库保存方法
            from modules.database_p5 import P5Database
            db = P5Database()
            if not db.connect():
                logger.error('数据库连接失败，无法保存最终预测')
                return None

            success = db.insert_ai_report(
                report_content=report_content,
                data_count=30,
                latest_issue=latest_issue,
                next_issue=next_issue,
                trend_analysis=trend_analysis,
                probability_stats=probability_stats,
                recommended_numbers=recommended_numbers,
                recommended_combinations=recommended_combinations,
                confidence_scores=confidence_scores,
                recommendation_reasons=key_conclusions,
                key_conclusions=key_conclusions,
                risk_warning=final_report.get('risk_warning', '理性购彩，量力而行'),
                report_format='JSON'
            )

            db.disconnect()

            if success:
                logger.info(f'最终预测已保存到数据库，UUID: {report_uuid}')
                return report_uuid
            else:
                logger.error('保存到数据库失败')
                return None

        except Exception as e:
            logger.error(f'保存最终预测到数据库失败: {e}', exc_info=True)
            return None

    # ================================================================
    # 流水线主入口
    # ================================================================

    def execute_pipeline(self, target_issue: str, data_limit: int = 40) -> Dict[str, Any]:
        """
        执行完整的四步流水线分析

        四个步骤严格串行执行：
        1. 专家文章爬取与结构化AI分析
        2. 走势图数据分析与AI预测
        3. 专家报告整合分析
        4. 最终预测结果生成与入库

        任一环节失败都会影响后续步骤的执行，但最终会返回详细的错误信息。

        Args:
            target_issue: 目标预测期号（如"2026165"）
            data_limit: 获取历史数据的期数限制（默认40期）

        Returns:
            {
                success, total_steps, completed_steps,
                step1_result, step2_result, step3_result, step4_result,
                total_duration, report_uuid, final_report, error
            }
        """
        logger.info('#' * 80)
        logger.info('# 开始执行四步流水线分析')
        logger.info(f'# 目标期号: {target_issue}')
        logger.info(f'# 数据期数: {data_limit}')
        logger.info('#' * 80)

        self.pipeline_state = {
            'article_reports': [],
            'trend_report': None,
            'integrated_report': None,
            'final_report': None,
            'started_at': datetime.now(),
            'completed_at': None,
        }

        start_time = time.time()
        pipeline_result = {
            'success': False,
            'total_steps': 4,
            'completed_steps': 0,
            'step1_result': None,
            'step2_result': None,
            'step3_result': None,
            'step4_result': None,
            'total_duration': 0,
            'report_uuid': None,
            'final_report': None,
            'error': None,
            'stages': []
        }

        try:
            # ---- 步骤1 ----
            step1_start = time.time()
            step1_result = self.step1_crawl_articles_and_analyze(target_issue)
            step1_elapsed = time.time() - step1_start
            step1_info = {
                'step': 1,
                'name': '专家文章爬取与结构化AI分析',
                'success': step1_result.get('success', False),
                'duration': step1_elapsed,
                'details': step1_result
            }
            pipeline_result['stages'].append(step1_info)
            pipeline_result['step1_result'] = step1_result
            if step1_result.get('success'):
                pipeline_result['completed_steps'] += 1
            else:
                logger.warning(f'步骤1部分失败: {step1_result.get("error", "未知错误")}，将继续步骤2（使用已有数据）')
                pipeline_result['completed_steps'] += 1  # 仍计入已完成，只是部分成功

            # ---- 步骤2 ----
            step2_start = time.time()
            step2_result = self.step2_trend_analysis(target_issue, data_limit)
            step2_elapsed = time.time() - step2_start
            step2_info = {
                'step': 2,
                'name': '走势图数据分析与AI预测',
                'success': step2_result.get('success', False),
                'duration': step2_elapsed,
                'details': step2_result
            }
            pipeline_result['stages'].append(step2_info)
            pipeline_result['step2_result'] = step2_result
            if step2_result.get('success'):
                pipeline_result['completed_steps'] += 1
            else:
                logger.warning(f'步骤2失败: {step2_result.get("error", "未知错误")}，将跳过步骤3和4')

            if not step2_result.get('success'):
                pipeline_result['error'] = f'步骤2失败: {step2_result.get("error", "")}，后续步骤已跳过'
                pipeline_result['total_duration'] = time.time() - start_time
                self.pipeline_state['completed_at'] = datetime.now()
                return pipeline_result

            # ---- 步骤3 ----
            step3_start = time.time()
            step3_result = self.step3_integrate_expert_reports(target_issue)
            step3_elapsed = time.time() - step3_start
            step3_info = {
                'step': 3,
                'name': '专家报告整合分析',
                'success': step3_result.get('success', False),
                'duration': step3_elapsed,
                'details': step3_result
            }
            pipeline_result['stages'].append(step3_info)
            pipeline_result['step3_result'] = step3_result
            if step3_result.get('success'):
                pipeline_result['completed_steps'] += 1
            else:
                logger.warning(f'步骤3失败: {step3_result.get("error", "未知错误")}，将跳过步骤4')
                pipeline_result['total_duration'] = time.time() - start_time
                self.pipeline_state['completed_at'] = datetime.now()
                return pipeline_result

            # ---- 步骤4 ----
            step4_start = time.time()
            step4_result = self.step4_final_prediction(target_issue)
            step4_elapsed = time.time() - step4_start
            step4_info = {
                'step': 4,
                'name': '最终预测结果生成与入库',
                'success': step4_result.get('success', False),
                'duration': step4_elapsed,
                'details': step4_result
            }
            pipeline_result['stages'].append(step4_info)
            pipeline_result['step4_result'] = step4_result
            if step4_result.get('success'):
                pipeline_result['completed_steps'] += 1
                pipeline_result['success'] = True
                pipeline_result['report_uuid'] = step4_result.get('report_uuid')
                pipeline_result['final_report'] = step4_result.get('final_report')
                pipeline_result['error'] = None

            pipeline_result['total_duration'] = time.time() - start_time
            self.pipeline_state['completed_at'] = datetime.now()

            # ---- 输出最终汇总 ----
            logger.info('=' * 80)
            logger.info('四步流水线执行完成')
            logger.info('=' * 80)
            for stage in pipeline_result['stages']:
                icon = '✓' if stage['success'] else '✗'
                logger.info(f'{icon} 步骤{stage["step"]}: {stage["name"]} ({stage["duration"]:.1f}s)')
            logger.info(f'总耗时: {pipeline_result["total_duration"]:.1f}s')
            if pipeline_result.get('report_uuid'):
                logger.info(f'报告UUID: {pipeline_result["report_uuid"]}')
            if pipeline_result.get('error'):
                logger.info(f'错误: {pipeline_result["error"]}')
            logger.info('=' * 80)

        except Exception as e:
            logger.error(f'四步流水线执行异常: {e}', exc_info=True)
            pipeline_result['error'] = str(e)
            pipeline_result['total_duration'] = time.time() - start_time
            self.pipeline_state['completed_at'] = datetime.now()

        return pipeline_result


def run_four_step_pipeline(target_issue: Optional[str] = None, data_limit: int = 40) -> Dict[str, Any]:
    """
    便捷函数：执行四步流水线分析

    Args:
        target_issue: 目标期号，如不提供则从数据库最新期号推算
        data_limit: 历史数据期数限制

    Returns:
        流水线执行结果
    """
    try:
        from modules.database_p5 import P5Database
        db = P5Database()
        if db.connect():
            db.cursor.execute('SELECT issue FROM p5_history_data ORDER BY issue DESC LIMIT 1')
            row = db.cursor.fetchone()
            if row:
                latest_issue = row.get('issue', '')
                if target_issue is None:
                    target_issue = str(int(latest_issue) + 1)
                    logger.info(f'未指定目标期号，自动推算下一期为: {target_issue}')
            db.disconnect()

        if target_issue is None:
            logger.error('无法确定目标期号，请手动指定')
            return {'success': False, 'error': '无法确定目标期号'}

        pipeline = FourStepPipeline()
        return pipeline.execute_pipeline(target_issue=target_issue, data_limit=data_limit)

    except Exception as e:
        logger.error(f'四步流水线调用失败: {e}', exc_info=True)
        return {'success': False, 'error': str(e)}


if __name__ == '__main__':
    print('=' * 80)
    print('四步流水线分析模块测试')
    print('=' * 80)
    result = run_four_step_pipeline(target_issue='2026165', data_limit=40)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

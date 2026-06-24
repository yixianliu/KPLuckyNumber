"""
文章内容AI分析模块

实现完整的文章分析流程：
1. 爬取文章内容
2. 第一次AI分析：结构化整理
3. 存储到Redis
4. 第二次AI分析：整合数据并预测
5. 存储最终报告到数据库
"""

import logging
import os
import json
import re
from datetime import datetime
from typing import Dict, List, Any, Optional

os.makedirs('logs', exist_ok=True)

logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler = logging.FileHandler('logs/article_analyzer.log', encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


class ArticleAnalyzer:
    """
    文章内容分析器

    整合爬虫、AI分析、Redis存储、数据库存储的完整流程
    """

    def __init__(self):
        self.spider = None
        self.redis_client = None
        self.ai_client = None
        self.db_client = None

    def _init_spider(self):
        """初始化爬虫模块"""
        try:
            from modules.ydniu_spider import YDNiuSpider
            self.spider = YDNiuSpider()
            logger.info('爬虫模块初始化成功')
        except ImportError:
            logger.error('无法导入爬虫模块')

    def _init_redis(self):
        """初始化Redis客户端"""
        try:
            from modules.redis_client import RedisClient
            self.redis_client = RedisClient()
            if self.redis_client.is_connected():
                logger.info('Redis客户端初始化成功')
            else:
                logger.warning('Redis客户端连接失败')
        except ImportError:
            logger.error('无法导入Redis模块')

    def _init_ai_client(self):
        """初始化AI客户端"""
        try:
            from modules.ernie_ai_analyzer import ERNIEAIAnalyzer
            self.ai_client = ERNIEAIAnalyzer()
            logger.info('AI客户端初始化成功')
        except ImportError:
            logger.error('无法导入AI客户端模块')

    def _init_db_client(self):
        """初始化数据库客户端"""
        try:
            from modules.database_p5 import P5Database
            self.db_client = P5Database()
            if self.db_client.connect():
                logger.info('数据库客户端初始化成功')
            else:
                logger.warning('数据库客户端连接失败')
        except ImportError:
            logger.error('无法导入数据库模块')

    def _build_first_analysis_prompt(self, article_data: Dict[str, Any]) -> str:
        """
        构建第一次AI分析的提示词（结构化整理）

        Args:
            article_data: 文章数据

        Returns:
            提示词
        """
        prompt_parts = []

        prompt_parts.append("""
你是一位专业的排列5彩票数据分析专家。请对以下文章内容进行结构化整理和分析。

【文章信息】
""")

        prompt_parts.append(f"标题：{article_data.get('title', '未知')}")
        prompt_parts.append(f"作者：{article_data.get('author', '未知')}")
        prompt_parts.append(f"发布时间：{article_data.get('publish_time', '未知')}")
        prompt_parts.append(f"来源URL：{article_data.get('url', '未知')}")
        prompt_parts.append(f"爬取时间：{article_data.get('crawl_time', '未知')}")

        prompt_parts.append("\n【文章内容】")
        prompt_parts.append(article_data.get('content', ''))

        prompt_parts.append("""
【分析要求】
请对上述文章内容进行深度分析，提取以下信息并以JSON格式返回：

{
    "data_source": "亿点牛文章分析",
    "article_info": {
        "title": "文章标题",
        "author": "作者",
        "publish_time": "发布时间",
        "url": "文章URL"
    },
    "analysis_time": "YYYY-MM-DD HH:MM:SS",
    "issue_number": "提取的期号（如2026165）",
    "forecast_numbers": {
        "wan": [万位推荐号码列表],
        "qian": [千位推荐号码列表],
        "bai": [百位推荐号码列表],
        "shi": [十位推荐号码列表],
        "ge": [个位推荐号码列表]
    },
    "recommended_combinations": ["推荐组合1", "推荐组合2"],
    "key_points": [
        "关键点1",
        "关键点2",
        "关键点3"
    ],
    "trend_analysis": "趋势分析总结",
    "confidence_level": "置信度（高/中/低）",
    "risk_warning": "风险提示",
    "summary": "文章内容总结"
}

注意事项：
1. 如果文章中没有明确提到号码预测，forecast_numbers字段设为空数组
2. 提取所有可能的期号信息
3. 总结文章的核心观点和分析逻辑
4. 置信度根据文章的专业性和数据支持程度判断
""")

        return "\n".join(prompt_parts)

    def _build_second_analysis_prompt(self, 
                                      redis_data: Dict[str, Any],
                                      first_ai_result: Dict[str, Any],
                                      db_history: Dict[str, Any]) -> str:
        """
        构建第二次AI分析的提示词（整合数据并预测）

        Args:
            redis_data: Redis中的文章分析数据
            first_ai_result: 第一次AI分析结果
            db_history: 数据库历史数据

        Returns:
            提示词
        """
        prompt_parts = []

        prompt_parts.append("""
你是一位专业的排列5彩票数据深度分析专家。请基于以下多源数据进行综合分析和预测下一期号码。

【数据来源说明】
本次分析整合了以下三类数据：
1. 文章内容分析：从亿点牛网站爬取并经过AI结构化整理的文章内容
2. 历史开奖数据：最近30期历史开奖记录及各位置走势统计
3. 初步分析结果：AI模型对文章内容的初步分析

请综合以上所有数据，进行深度分析，生成下一期的号码预测报告。
""")

        prompt_parts.append("\n" + "=" * 60)
        prompt_parts.append("一、文章内容分析数据")
        prompt_parts.append("=" * 60)

        if redis_data:
            prompt_parts.append(f"数据来源：{redis_data.get('data_source', '未知')}")
            prompt_parts.append(f"分析时间：{redis_data.get('analysis_time', '未知')}")
            prompt_parts.append(f"期号：{redis_data.get('issue_number', '未知')}")
            
            if redis_data.get('forecast_numbers'):
                nums = redis_data['forecast_numbers']
                prompt_parts.append("\n【文章推荐号码】")
                for pos_name, pos_key in zip(['万位', '千位', '百位', '十位', '个位'], 
                                             ['wan', 'qian', 'bai', 'shi', 'ge']):
                    if nums.get(pos_key):
                        prompt_parts.append(f"  {pos_name}：{nums[pos_key]}")
            
            if redis_data.get('key_points'):
                prompt_parts.append("\n【关键点】")
                for point in redis_data['key_points']:
                    prompt_parts.append(f"  - {point}")
            
            if redis_data.get('trend_analysis'):
                prompt_parts.append(f"\n【趋势分析】\n{redis_data['trend_analysis']}")
            
            if redis_data.get('summary'):
                prompt_parts.append(f"\n【文章总结】\n{redis_data['summary']}")

        prompt_parts.append("\n" + "=" * 60)
        prompt_parts.append("二、历史开奖数据")
        prompt_parts.append("=" * 60)

        if db_history:
            prompt_parts.append(f"数据条数：{db_history.get('data_count', 0)}")
            prompt_parts.append(f"最新期号：{db_history.get('latest_issue', '未知')}")
            
            if db_history.get('recent_data'):
                recent = db_history['recent_data'][:10]
                prompt_parts.append("\n最近10期开奖记录：")
                for item in recent:
                    prompt_parts.append(f"  {item.get('issue', '')}：{item.get('numbers_display', '')}")

        prompt_parts.append("\n" + "=" * 60)
        prompt_parts.append("三、分析要求")
        prompt_parts.append("=" * 60)

        prompt_parts.append("""
请基于以上多源数据，进行深度综合分析，输出JSON格式的预测报告。

输出格式要求（必须严格按照JSON格式输出）：
{
    "data_source": "综合分析（文章内容+历史数据）",
    "analysis_time": "YYYY-MM-DD HH:MM:SS",
    "model_version": "deepseek-v3.1-250821",
    "data_period": "最近30期",
    "current_issue": "当前期号",
    "next_issue": "预测期号",
    
    "prediction": {
        "wan": {"numbers": [数字列表], "confidence": [置信度列表], "reason": "分析理由"},
        "qian": {"numbers": [数字列表], "confidence": [置信度列表], "reason": "分析理由"},
        "bai": {"numbers": [数字列表], "confidence": [置信度列表], "reason": "分析理由"},
        "shi": {"numbers": [数字列表], "confidence": [置信度列表], "reason": "分析理由"},
        "ge": {"numbers": [数字列表], "confidence": [置信度列表], "reason": "分析理由"}
    },
    
    "trend_analysis": {
        "summary": "总体趋势总结",
        "wan": "万位趋势分析",
        "qian": "千位趋势分析",
        "bai": "百位趋势分析",
        "shi": "十位趋势分析",
        "ge": "个位趋势分析"
    },
    
    "key_features": [
        "特征1描述",
        "特征2描述",
        "特征3描述"
    ],
    
    "reasoning_process": "详细的推理过程说明",
    
    "recommended_combinations": ["推荐组合1", "推荐组合2", "推荐组合3"],
    
    "data_source_summary": {
        "article_source": "文章来源",
        "article_issue": "文章期号",
        "history_data_count": 历史数据条数,
        "analysis_confidence": "分析置信度"
    },
    
    "risk_warning": "本分析基于历史数据统计和文章内容分析，不保证中奖，请理性购彩。"
}

请确保：
1. 推荐号码每个位置最多3个
2. 置信度为0-1之间的小数
3. 分析过程要综合考虑文章内容和历史数据
4. 推理过程要清晰、有条理
5. 预测期号根据当前期号自动计算
""")

        return "\n".join(prompt_parts)

    def first_ai_analysis(self, article_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        第一次AI分析：结构化整理文章内容

        Args:
            article_data: 文章数据

        Returns:
            AI分析结果
        """
        logger.info('=' * 80)
        logger.info('开始第一次AI分析：结构化整理')
        logger.info('=' * 80)

        self._init_ai_client()

        if not self.ai_client:
            logger.error('AI客户端未初始化')
            return None

        # 构建提示词
        prompt = self._build_first_analysis_prompt(article_data)
        logger.info(f'提示词长度: {len(prompt)}')

        # 调用AI模型
        messages = [
            {
                "role": "system",
                "content": "你是一位专业的排列5彩票数据分析专家，擅长对文章内容进行结构化整理和分析。请严格按照要求输出JSON格式。"
            },
            {
                "role": "user",
                "content": prompt
            }
        ]

        ai_response = self.ai_client._call_ai_model(messages=messages, max_tokens=4000, temperature=0.5)
        if not ai_response:
            logger.error('AI模型调用失败')
            return None

        # 解析AI响应
        ai_result = self.ai_client._parse_ai_response(ai_response)
        if not ai_result:
            logger.error('AI响应解析失败')
            return None

        logger.info('第一次AI分析完成')
        logger.info(f'期号: {ai_result.get("issue_number", "未知")}')
        logger.info(f'置信度: {ai_result.get("confidence_level", "未知")}')

        return ai_result

    def save_to_redis(self, issue: str, article_data: Dict[str, Any], ai_result: Dict[str, Any]) -> bool:
        """
        保存数据到Redis

        Args:
            issue: 期号
            article_data: 文章数据
            ai_result: AI分析结果

        Returns:
            是否成功
        """
        logger.info('保存数据到Redis...')

        self._init_redis()

        if not self.redis_client or not self.redis_client.is_connected():
            logger.error('Redis客户端未连接')
            return False

        try:
            # 保存文章原始数据
            self.redis_client.save_raw_data(issue, article_data)

            # 保存AI分析结果（使用统一键名）
            redis_data = {
                'issue': issue,
                'article_data': article_data,
                'ai_analysis': ai_result,
                'save_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }

            # 使用统一的键名方法：get_ai_analysis_key(issue)
            # 键名格式: kpluckynumber:pl5:ai:{issue}
            key = self.redis_client.get_ai_analysis_key(issue)
            self.redis_client.client.setex(key, 86400 * 7, json.dumps(redis_data, ensure_ascii=False))

            logger.info(f'数据已保存到Redis: {key}')
            return True

        except Exception as e:
            logger.error(f'保存到Redis失败: {e}')
            return False

    def load_from_redis(self, issue: str) -> Optional[Dict[str, Any]]:
        """
        从Redis加载数据

        Args:
            issue: 期号

        Returns:
            Redis数据
        """
        logger.info(f'从Redis加载数据: {issue}')

        self._init_redis()

        if not self.redis_client or not self.redis_client.is_connected():
            logger.error('Redis客户端未连接')
            return None

        try:
            # 使用统一的键名方法加载数据
            key = self.redis_client.get_ai_analysis_key(issue)
            data_str = self.redis_client.client.get(key)

            if data_str:
                data = json.loads(data_str)
                logger.info('成功从Redis加载数据')
                return data
            else:
                logger.warning(f'Redis中未找到数据: {key}')
                return None

        except Exception as e:
            logger.error(f'从Redis加载数据失败: {e}')
            return None

    def second_ai_analysis(self, redis_data: Dict[str, Any], db_history: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        第二次AI分析：整合数据并预测

        Args:
            redis_data: Redis中的数据
            db_history: 数据库历史数据

        Returns:
            AI分析结果
        """
        logger.info('=' * 80)
        logger.info('开始第二次AI分析：整合数据并预测')
        logger.info('=' * 80)

        self._init_ai_client()

        if not self.ai_client:
            logger.error('AI客户端未初始化')
            return None

        # 提取第一次AI分析结果
        first_ai_result = redis_data.get('ai_analysis', {})

        # 构建提示词
        prompt = self._build_second_analysis_prompt(first_ai_result, first_ai_result, db_history)
        logger.info(f'提示词长度: {len(prompt)}')

        # 调用AI模型
        messages = [
            {
                "role": "system",
                "content": "你是一位专业的排列5彩票数据深度分析专家，擅长整合多源数据进行综合分析和预测。请严格按照要求输出JSON格式。"
            },
            {
                "role": "user",
                "content": prompt
            }
        ]

        ai_response = self.ai_client._call_ai_model(messages=messages, max_tokens=8000, temperature=0.7)
        if not ai_response:
            logger.error('AI模型调用失败')
            return None

        # 解析AI响应
        ai_result = self.ai_client._parse_ai_response(ai_response)
        if not ai_result:
            logger.error('AI响应解析失败')
            return None

        logger.info('第二次AI分析完成')
        logger.info(f'预测期号: {ai_result.get("next_issue", "未知")}')

        return ai_result

    def save_to_database(self, final_report: Dict[str, Any], db_history: Dict[str, Any]) -> Optional[str]:
        """
        保存最终报告到数据库

        Args:
            final_report: 最终报告
            db_history: 数据库历史数据

        Returns:
            报告UUID
        """
        logger.info('保存最终报告到数据库...')

        self._init_db_client()

        if not self.db_client:
            logger.error('数据库客户端未初始化')
            return None

        try:
            # 生成报告UUID
            import uuid
            report_uuid = str(uuid.uuid4())

            # 构建数据库记录
            db_record = {
                'report_uuid': report_uuid,
                'report_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'data_source': final_report.get('data_source', ''),
                'model_version': final_report.get('model_version', ''),
                'data_period': final_report.get('data_period', ''),
                'current_issue': final_report.get('current_issue', ''),
                'next_issue': final_report.get('next_issue', ''),
                'trend_analysis': json.dumps(final_report.get('trend_analysis', {}), ensure_ascii=False),
                'probability_stats': json.dumps(final_report.get('prediction', {}), ensure_ascii=False),
                'recommended_numbers': json.dumps(final_report.get('prediction', {}), ensure_ascii=False),
                'recommended_combinations': json.dumps(final_report.get('recommended_combinations', []), ensure_ascii=False),
                'confidence_scores': json.dumps(final_report.get('prediction', {}), ensure_ascii=False),
                'key_conclusions': json.dumps(final_report.get('key_features', []), ensure_ascii=False),
                'report_content': final_report.get('reasoning_process', ''),
                'data_count': db_history.get('data_count', 0),
                'latest_issue': db_history.get('latest_issue', '')
            }

            # 保存到数据库（使用insert_ai_report方法，传入独立参数）
            success = self.db_client.insert_ai_report(
                report_content=db_record['report_content'],
                data_count=db_record['data_count'],
                latest_issue=db_record['latest_issue'],
                next_issue=db_record.get('next_issue'),
                trend_analysis=db_record.get('trend_analysis'),
                probability_stats=db_record.get('probability_stats'),
                recommended_numbers=db_record.get('recommended_numbers'),
                recommended_combinations=db_record.get('recommended_combinations'),
                confidence_scores=db_record.get('confidence_scores'),
                recommendation_reasons=db_record.get('key_conclusions'),
                key_conclusions=db_record.get('key_conclusions'),
                risk_warning=final_report.get('risk_warning', '理性购彩，量力而行'),
                report_format='TEXT'
            )

            if success:
                logger.info(f'最终报告已保存到数据库，UUID: {report_uuid}')
                return report_uuid
            else:
                logger.error('保存到数据库失败')
                return None

        except Exception as e:
            logger.error(f'保存到数据库失败: {e}', exc_info=True)
            return None

    def analyze_article_workflow(self, target_issue: Optional[str] = None, data_limit: int = 30) -> Dict[str, Any]:
        """
        完整的文章分析工作流

        Args:
            target_issue: 目标期号
            data_limit: 历史数据期数限制

        Returns:
            分析结果
        """
        logger.info('=' * 80)
        logger.info('开始完整文章分析工作流')
        logger.info('=' * 80)

        result = {
            'success': False,
            'step1_crawl': False,
            'step2_first_ai': False,
            'step3_redis_save': False,
            'step4_redis_load': False,
            'step5_second_ai': False,
            'step6_db_save': False,
            'error': None,
            'report_uuid': None,
            'final_report': None
        }

        try:
            # 步骤1：爬取文章内容
            logger.info('步骤1：爬取文章内容...')
            self._init_spider()

            if not self.spider:
                result['error'] = '爬虫模块初始化失败'
                return result

            crawl_result = self.spider.crawl_all_articles(target_issue=target_issue)

            if not crawl_result.get('articles'):
                result['error'] = '未爬取到文章内容'
                return result

            article_data = crawl_result['articles'][0]  # 使用第一篇文章
            result['step1_crawl'] = True
            logger.info(f'成功爬取文章: {article_data.get("title", "未知")}')

            # 增强期号提取逻辑
            # 优先使用target_issue，否则从文章标题或URL中提取
            if target_issue:
                # 验证target_issue是否有效（6-8位数字）
                if not re.match(r'^\d{6,8}$', str(target_issue)):
                    logger.warning(f'target_issue格式可能不正确: {target_issue}，将尝试从文章内容提取')
                    target_issue = None
                else:
                    issue = str(target_issue)
                    logger.info(f'使用指定的期号: {issue}')
            else:
                issue = None

            # 如果没有指定期号或格式不正确，从文章数据中提取
            if not issue:
                # 尝试从标题提取（格式：2026165期[xxx]...）
                title = article_data.get('title', '')
                title_match = re.search(r'(\d{6,8})期', title)
                if title_match:
                    issue = title_match.group(1)
                    logger.info(f'从标题提取到期号: {issue}')
                else:
                    # 尝试从URL提取
                    url = article_data.get('url', '')
                    url_match = re.search(r'(\d{6,8})', url)
                    if url_match:
                        issue = url_match.group(1)
                        logger.info(f'从URL提取到期号: {issue}')
                    else:
                        # 尝试从link_title提取
                        link_title = article_data.get('link_title', '')
                        link_match = re.search(r'(\d{6,8})期', link_title)
                        if link_match:
                            issue = link_match.group(1)
                            logger.info(f'从link_title提取到期号: {issue}')

            if not issue:
                result['error'] = '无法确定期号'
                return result

            # 步骤2：第一次AI分析
            logger.info('步骤2：第一次AI分析（结构化整理）...')
            first_ai_result = self.first_ai_analysis(article_data)

            if not first_ai_result:
                result['error'] = '第一次AI分析失败'
                return result

            result['step2_first_ai'] = True

            # 步骤3：保存到Redis
            logger.info('步骤3：保存到Redis...')
            redis_save_success = self.save_to_redis(issue, article_data, first_ai_result)

            if not redis_save_success:
                result['error'] = '保存到Redis失败'
                return result

            result['step3_redis_save'] = True

            # 步骤4：从Redis加载数据
            logger.info('步骤4：从Redis加载数据...')
            redis_data = self.load_from_redis(issue)

            if not redis_data:
                result['error'] = '从Redis加载数据失败'
                return result

            result['step4_redis_load'] = True

            # 步骤5：获取历史数据
            logger.info('步骤5：获取历史数据...')
            self._init_db_client()

            if not self.db_client:
                result['error'] = '数据库模块初始化失败'
                return result

            db_history = self.ai_client._fetch_data_from_database(limit=data_limit)

            if db_history.get('error'):
                result['error'] = db_history['error']
                return result

            # 步骤6：第二次AI分析
            logger.info('步骤6：第二次AI分析（整合数据并预测）...')
            second_ai_result = self.second_ai_analysis(redis_data, db_history)

            if not second_ai_result:
                result['error'] = '第二次AI分析失败'
                return result

            result['step5_second_ai'] = True

            # 步骤7：保存到数据库
            logger.info('步骤7：保存到数据库...')
            report_uuid = self.save_to_database(second_ai_result, db_history)

            if not report_uuid:
                result['error'] = '保存到数据库失败'
                return result

            result['step6_db_save'] = True
            result['success'] = True
            result['report_uuid'] = report_uuid
            result['final_report'] = second_ai_result

            logger.info('=' * 80)
            logger.info('完整文章分析工作流完成')
            logger.info(f'报告UUID: {report_uuid}')
            logger.info(f'预测期号: {second_ai_result.get("next_issue", "未知")}')
            logger.info('=' * 80)

        except Exception as e:
            logger.error(f'文章分析工作流失败: {e}', exc_info=True)
            result['error'] = str(e)

        return result

    def save_all_articles_to_redis(self, target_issue: Optional[str] = None, 
                                    max_articles: int = 100,
                                    extract_predictions: bool = True) -> Dict[str, Any]:
        """
        批量保存所有爬取的文章到Redis（按文章ID存储），并可选提取预测数据

        Args:
            target_issue: 目标期号
            max_articles: 最大处理文章数
            extract_predictions: 是否提取预测数据

        Returns:
            处理结果
        """
        logger.info('=' * 80)
        logger.info('开始批量保存文章到Redis')
        if extract_predictions:
            logger.info('【启用预测数据自动提取流程】')
        logger.info('=' * 80)

        result = {
            'success': False,
            'total_articles': 0,
            'saved_articles': 0,
            'failed_articles': 0,
            'extracted_predictions': 0,
            'high_quality_predictions': 0,
            'articles': [],
            'predictions': [],
            'error': None
        }

        try:
            # 步骤1：爬取文章内容
            logger.info('步骤1：爬取文章内容...')
            self._init_spider()

            if not self.spider:
                result['error'] = '爬虫模块初始化失败'
                return result

            crawl_result = self.spider.crawl_all_articles(target_issue=target_issue)

            if not crawl_result.get('articles'):
                result['error'] = '未爬取到文章内容'
                return result

            articles = crawl_result['articles'][:max_articles]
            result['total_articles'] = len(articles)
            logger.info(f'准备处理 {len(articles)} 篇文章')

            # 步骤2：初始化Redis客户端
            logger.info('步骤2：初始化Redis客户端...')
            self._init_redis()

            if not self.redis_client or not self.redis_client.is_connected():
                result['error'] = 'Redis客户端连接失败'
                return result

            # 步骤3：初始化预测数据提取器（如果启用）
            prediction_extractor = None
            if extract_predictions:
                logger.info('步骤3：初始化预测数据提取器...')
                from modules.prediction_extractor import PredictionExtractor
                prediction_extractor = PredictionExtractor()
                if not prediction_extractor.ai_available:
                    logger.warning('预测数据提取器AI不可用，跳过预测提取')
                    extract_predictions = False

            # 步骤4：批量处理文章
            logger.info('步骤4：批量处理文章（保存+预测提取）...')
            for i, article_data in enumerate(articles, 1):
                try:
                    # 提取期号
                    issue = self._extract_issue_from_article(article_data, target_issue)
                    
                    # 生成文章唯一ID
                    url = article_data.get('url', article_data.get('link_url', f'article_{i}'))
                    article_id = self.redis_client.generate_article_id(url, i)
                    
                    # 构建存储数据
                    redis_data = {
                        'issue': issue,
                        'article_data': article_data,
                        'article_index': i,
                        'crawl_time': article_data.get('crawl_time', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                    }
                    
                    # 步骤4.1：提取预测数据（如果启用）
                    prediction_result = None
                    if extract_predictions and prediction_extractor:
                        logger.info(f'  正在提取预测数据...')
                        prediction_result = prediction_extractor.extract_prediction_from_article({
                            'article_id': article_id,
                            'title': article_data.get('title', ''),
                            'content': article_data.get('content', '')
                        })
                        
                        if prediction_result['success']:
                            redis_data['prediction_data'] = prediction_result['prediction_data']
                            redis_data['quality_score'] = prediction_result['quality_score']
                            redis_data['validation_status'] = prediction_result['prediction_data'].get('validation_status')
                            
                            result['extracted_predictions'] += 1
                            if prediction_result['quality_score'] >= 0.7:
                                result['high_quality_predictions'] += 1
                            
                            result['predictions'].append({
                                'article_id': article_id,
                                'issue': prediction_result['issue'],
                                'quality_score': prediction_result['quality_score'],
                                'validation_status': prediction_result['prediction_data'].get('validation_status'),
                                'prediction_summary': prediction_extractor._summarize_prediction(prediction_result['prediction_data'])
                            })
                            
                            logger.info(f'  预测提取成功: 期号={prediction_result["issue"]}, 质量={prediction_result["quality_score"]}')
                        else:
                            redis_data['prediction_data'] = None
                            redis_data['prediction_errors'] = prediction_result['validation_errors']
                            logger.warning(f'  预测提取失败: {prediction_result["validation_errors"]}')
                    
                    # 步骤4.2：保存到Redis
                    save_success = self.redis_client.save_article_data(article_id, redis_data)
                    
                    if save_success:
                        result['saved_articles'] += 1
                        result['articles'].append({
                            'article_id': article_id,
                            'issue': issue,
                            'title': article_data.get('title', '未知')[:50],
                            'has_prediction': prediction_result['success'] if prediction_result else False,
                            'quality_score': prediction_result['quality_score'] if prediction_result else 0
                        })
                        logger.info(f'文章 {i}/{len(articles)} 处理完成: {article_id} (期号: {issue})')
                    else:
                        result['failed_articles'] += 1
                        logger.warning(f'文章 {i}/{len(articles)} 保存失败')
                        
                except Exception as e:
                    result['failed_articles'] += 1
                    logger.error(f'处理文章 {i} 时出错: {e}')
                    continue

            result['success'] = result['saved_articles'] > 0
            
            logger.info('=' * 80)
            logger.info('批量处理文章完成')
            logger.info(f'总文章数: {result["total_articles"]}')
            logger.info(f'成功保存: {result["saved_articles"]}')
            logger.info(f'失败保存: {result["failed_articles"]}')
            if extract_predictions:
                logger.info(f'预测提取成功: {result["extracted_predictions"]}')
                logger.info(f'高质量预测: {result["high_quality_predictions"]}')
            logger.info('=' * 80)

        except Exception as e:
            logger.error(f'批量保存文章失败: {e}', exc_info=True)
            result['error'] = str(e)

        return result

    def _extract_issue_from_article(self, article_data: Dict[str, Any], 
                                     target_issue: Optional[str] = None) -> str:
        """
        从文章数据中提取期号

        Args:
            article_data: 文章数据
            target_issue: 目标期号

        Returns:
            提取的期号
        """
        # 优先使用target_issue
        if target_issue and re.match(r'^\d{6,8}$', str(target_issue)):
            return str(target_issue)
        
        # 从标题提取
        title = article_data.get('title', '')
        title_match = re.search(r'(\d{6,8})期', title)
        if title_match:
            return title_match.group(1)
        
        # 从link_title提取
        link_title = article_data.get('link_title', '')
        link_match = re.search(r'(\d{6,8})期', link_title)
        if link_match:
            return link_match.group(1)
        
        # 从URL提取
        url = article_data.get('url', article_data.get('link_url', ''))
        url_match = re.search(r'(\d{6,8})', url)
        if url_match:
            return url_match.group(1)
        
        # 使用当前日期作为默认值
        return datetime.now().strftime('%Y%m%d')


def run_article_analysis(target_issue: Optional[str] = None, data_limit: int = 30) -> Dict[str, Any]:
    """
    便捷函数：执行文章分析工作流

    Args:
        target_issue: 目标期号
        data_limit: 历史数据期数限制

    Returns:
        分析结果
    """
    analyzer = ArticleAnalyzer()
    return analyzer.analyze_article_workflow(target_issue=target_issue, data_limit=data_limit)


if __name__ == '__main__':
    print('=' * 80)
    print('文章内容AI分析模块测试')
    print('=' * 80)

    analyzer = ArticleAnalyzer()
    result = analyzer.analyze_article_workflow(data_limit=10)

    if result['success']:
        print('\n分析成功！')
        print(f'报告UUID: {result["report_uuid"]}')
        print(f'最终报告: {result["final_report"]}')
    else:
        print(f'\n分析失败: {result["error"]}')
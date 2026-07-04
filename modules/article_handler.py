"""
文章处理统一模块（合并版：ArticleHandler）

整合了原 article_analyzer.py 和 article_processor.py 的所有功能：
- 完整6步文章分析工作流（双阶段AI分析+数据库存储）
- 简化4步文章处理流程（爬取→AI→预处理→Redis存储）
- 批量文章处理和预测数据提取
- 走势AI分析和最终整合分析

调用路径：
    main.py → run_article_analysis() / run_save_articles_to_redis()
           → ArticleHandler.analyze_article_workflow() / save_all_articles_bulk_to_redis()
    main.py → run_process_article() / run_process_multiple_articles()
           → ArticleHandler.process_article() / process_multiple_articles()

类：
    ArticleHandler: 统一的文章处理类，包含所有分析/处理功能

注意：
    为保持向后兼容，导出 ArticleProcessor 和 ArticleAnalyzer 别名：
    - ArticleProcessor = ArticleHandler（用于旧代码兼容）
    - ArticleAnalyzer = ArticleHandler（用于旧代码兼容）
"""

import logging
import os
import json
import re
import time
import hashlib
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

os.makedirs('logs', exist_ok=True)

logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler = logging.FileHandler('logs/article_handler.log', encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


class ArticleHandler:
    """
    统一的文章处理类（合并版）

    整合了完整分析（双阶段AI+数据库）和简化处理（单次AI+Redis）两种工作流。

    支持两种初始化模式：
    - 懒加载模式（默认）：组件仅在首次使用时初始化，避免导入失败
    - 主动加载模式：构造时初始化所有组件，可尽早发现环境问题

    属性:
        lazy: 是否使用懒加载模式（True=懒加载，False=主动加载）
    """

    def __init__(self, lazy: bool = True):
        """
        初始化处理器的两种模式：

        - 懒加载（lazy=True）：self.spider/self.redis_client/self.ai_client/self.db_client 初始化为None，
          仅在首次调用 _init_*() 时初始化（推荐用于日常使用）
        - 主动加载（lazy=False）：构造时立即初始化所有组件（用于测试/调试场景）
        """
        self.spider = None
        self.redis_client = None
        self.ai_client = None
        self.db_client = None

        # AI调用配置（仅主动加载模式使用）
        self.ai_timeout = 60
        self.ai_max_retries = 3
        self.ai_retry_delay = 5

        # Redis配置
        self.redis_expire_days = 7
        self.redis_key_prefix = 'kpluckynumber:article:report:'

        self.lazy = lazy
        if not lazy:
            self._init_components()

    def _init_components(self):
        """主动初始化所有外部依赖组件"""
        logger.info('初始化文章处理器组件...')
        try:
            from modules.web_scraper import YDNiuSpider
            self.spider = YDNiuSpider()
            logger.info('爬虫模块初始化成功')
        except Exception as e:
            logger.error(f'爬虫模块初始化失败: {e}')
        try:
            from modules.cache import CacheClient
            self.redis_client = CacheClient()
            logger.info('Redis客户端初始化成功')
        except Exception as e:
            logger.error(f'Redis客户端初始化失败: {e}')
        try:
            from modules.ai_analyzer import AIAnalyzer
            self.ai_client = AIAnalyzer()
            logger.info('AI客户端初始化成功')
        except Exception as e:
            logger.error(f'AI客户端初始化失败: {e}')

    def _init_spider(self):
        """懒加载初始化爬虫模块"""
        if self.spider is None:
            try:
                from modules.web_scraper import YDNiuSpider
                self.spider = YDNiuSpider()
                logger.info('爬虫模块初始化成功')
            except ImportError:
                logger.error('无法导入爬虫模块')

    def _init_redis(self):
        """懒加载初始化Redis客户端"""
        if self.redis_client is None:
            try:
                from modules.cache import CacheClient
                self.redis_client = CacheClient()
                if self.redis_client.is_connected():
                    logger.info('Redis客户端初始化成功')
                else:
                    logger.warning('Redis客户端连接失败')
            except ImportError:
                logger.error('无法导入Redis模块')

    def _init_ai_client(self):
        """懒加载初始化AI客户端"""
        if self.ai_client is None:
            try:
                from modules.ai_analyzer import AIAnalyzer
                self.ai_client = AIAnalyzer()
                logger.info('AI客户端初始化成功')
            except ImportError:
                logger.error('无法导入AI客户端模块')

    def _init_db_client(self):
        """懒加载初始化数据库客户端"""
        if self.db_client is None:
            try:
                from modules.database import P5Database
                self.db_client = P5Database()
                if self.db_client.connect():
                    logger.info('数据库客户端初始化成功')
                else:
                    logger.warning('数据库客户端连接失败')
            except ImportError:
                logger.error('无法导入数据库模块')

    # ============================================================
    # 期号提取工具
    # ============================================================

    def _extract_issue_from_article(self, article_data: Dict[str, Any],
                                    target_issue: Optional[str] = None) -> str:
        """从文章数据中提取期号（多源策略）"""
        if target_issue and re.match(r'^\d{6,8}$', str(target_issue)):
            return str(target_issue)
        title = article_data.get('title', '')
        title_match = re.search(r'(\d{6,8})期', title)
        if title_match:
            return title_match.group(1)
        link_title = article_data.get('link_title', '')
        link_match = re.search(r'(\d{6,8})期', link_title)
        if link_match:
            return link_match.group(1)
        url = article_data.get('url', article_data.get('link_url', ''))
        url_match = re.search(r'(\d{6,8})', url)
        if url_match:
            return url_match.group(1)
        return datetime.now().strftime('%Y%m%d')

    def _extract_issue_from_content(self, content: str, title: str) -> str:
        """从文章内容/标题中提取期号（简化版）"""
        issue_pattern = re.compile(r'(\d{6,8})期')
        match = issue_pattern.search(title)
        if match:
            return match.group(1)
        match = issue_pattern.search(content)
        if match:
            return match.group(1)
        return 'unknown'

    # ============================================================
    # ArticleProcessor 风格方法（简化4步流程 + MD5键名）
    # ============================================================

    def generate_article_key(self, url: str) -> str:
        """生成文章唯一Redis键名（MD5-16位前缀）"""
        url_hash = hashlib.md5(url.encode()).hexdigest()[:16]
        return f'{self.redis_key_prefix}{url_hash}'

    def crawl_article_content(self, url: str) -> Optional[str]:
        """爬取单篇文章的纯文本内容"""
        logger.info(f'开始爬取文章: {url}')
        if not self.spider:
            self._init_spider()
        if not self.spider:
            logger.error('爬虫模块未初始化')
            return None
        try:
            article_data = self.spider.crawl_article_page(url)
            if not article_data:
                logger.error(f'爬取文章失败: {url}')
                return None
            content = article_data.get('content', '')
            if not content or len(content) < 50:
                logger.warning(f'文章内容过短({len(content)}字符)')
                return None
            logger.info(f'爬取成功: {len(content)} 字符')
            return content
        except Exception as e:
            logger.error(f'爬取文章异常: {e}', exc_info=True)
            return None

    def call_ai_with_retry(self, prompt: str, max_retries: Optional[int] = None) -> Optional[str]:
        """调用AI模型，带指数退避重试和超时控制"""
        if not self.ai_client:
            self._init_ai_client()
        if not self.ai_client or not self.ai_client.ai_available:
            logger.error('AI客户端不可用')
            return None
        retries = max_retries or self.ai_max_retries
        import requests
        for attempt in range(1, retries + 1):
            try:
                logger.info(f'AI调用第 {attempt}/{retries} 次')
                messages = [
                    {'role': 'system', 'content': '你是一位专业的排列5彩票文章分析师。'},
                    {'role': 'user', 'content': prompt}
                ]
                payload = {
                    'model': self.ai_client.model_name,
                    'messages': messages,
                    'temperature': 0.5,
                    'max_tokens': 4000
                }
                response = requests.post(
                    self.ai_client.api_url,
                    headers=self.ai_client.headers,
                    json=payload,
                    timeout=self.ai_timeout
                )
                if response.status_code == 200:
                    result = response.json()
                    content = result.get('choices', [{}])[0].get('message', {}).get('content', '')
                    logger.info(f'AI响应成功: {len(content)} 字符')
                    return content
                else:
                    logger.error(f'AI调用失败: {response.status_code}')
            except requests.Timeout:
                logger.warning(f'AI调用超时，第 {attempt}/{retries} 次')
            except requests.ConnectionError:
                logger.warning(f'AI连接失败，第 {attempt}/{retries} 次')
            except Exception as e:
                logger.error(f'AI调用异常: {e}')
            if attempt < retries:
                time.sleep(self.ai_retry_delay * attempt)
        logger.error(f'AI调用失败，已重试 {retries} 次')
        return None

    def build_analysis_prompt(self, content: str, title: str = '') -> str:
        """构建文章分析Prompt（5段式自由文本格式）"""
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        return f"""请对以下排列5彩票文章进行深度分析，生成详细的分析报告。

【文章标题】{title}
【分析时间】{current_time}
【文章内容】
{content}

【分析要求】
1. 分析文章中的预测思路和方法
2. 提取所有预测号码及其依据
3. 评估预测的可信度和风险
4. 生成结构化的分析报告

【输出格式】
- 一、文章概述
- 二、预测思路分析
- 三、号码推荐及依据
- 四、风险评估
- 五、总结建议

请用中文输出，内容详实，逻辑清晰。"""

    def preprocess_report(self, report: str) -> str:
        """预处理报告内容：去除换行符、规范化空白、优化标点可读性"""
        if not report:
            return ""
        processed = report.replace('\n', ' ').replace('\r', ' ')
        processed = re.sub(r' +', ' ', processed)
        processed = re.sub(r'([。！？：；])', r'\1 ', processed)
        processed = re.sub(r'([.!?:;])([^\s])', r'\1 \2', processed)
        processed = processed.strip()
        logger.info(f'报告预处理完成: 原始{len(report)}字符 -> 处理后{len(processed)}字符')
        return processed

    def save_report_to_redis(self, url: str, report: str, metadata: Dict[str, Any]) -> bool:
        """将处理后的报告存储到Redis（MD5键名，7天过期）"""
        if not self.redis_client:
            self._init_redis()
        if not self.redis_client or not self.redis_client.is_connected():
            logger.error('Redis客户端未连接')
            return False
        if not report:
            logger.error('报告内容为空')
            return False
        try:
            key = self.generate_article_key(url)
            data = {
                'url': url, 'report': report, 'report_length': len(report),
                'metadata': metadata,
                'process_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'expire_days': self.redis_expire_days
            }
            self.redis_client.client.set(key, json.dumps(data, ensure_ascii=False), ex=timedelta(days=self.redis_expire_days))
            logger.info(f'报告已保存到Redis: {key}')
            return True
        except Exception as e:
            logger.error(f'保存报告到Redis失败: {e}', exc_info=True)
            return False

    def process_article(self, url: str, title: str = '') -> Dict[str, Any]:
        """处理单篇文章的完整4步流程：爬取→AI→预处理→Redis存储"""
        logger.info('=' * 80)
        logger.info(f'开始处理文章: {url}')
        logger.info('=' * 80)
        result = {
            'success': False, 'url': url, 'title': title,
            'report': None, 'report_length': 0, 'redis_key': None,
            'steps': [], 'error': None
        }
        try:
            content = self.crawl_article_content(url)
            if not content:
                result['error'] = '爬取文章内容失败'
                result['steps'].append({'step': '爬取', 'status': '失败', 'error': result['error']})
                return result
            result['steps'].append({'step': '爬取', 'status': '成功', 'content_length': len(content)})

            prompt = self.build_analysis_prompt(content, title)
            ai_report = self.call_ai_with_retry(prompt)
            if not ai_report:
                result['error'] = 'AI模型调用失败'
                result['steps'].append({'step': 'AI分析', 'status': '失败', 'error': result['error']})
                return result
            result['steps'].append({'step': 'AI分析', 'status': '成功', 'report_length': len(ai_report)})

            processed_report = self.preprocess_report(ai_report)
            if not processed_report:
                result['error'] = '报告预处理失败'
                result['steps'].append({'step': '预处理', 'status': '失败', 'error': result['error']})
                return result
            result['steps'].append({'step': '预处理', 'status': '成功', 'processed_length': len(processed_report)})

            issue = self._extract_issue_from_content(content, title)
            metadata = {'issue': issue, 'title': title, 'content_length': len(content),
                        'ai_model': getattr(self.ai_client, 'model_name', 'unknown') if self.ai_client else 'unknown'}
            key = self.generate_article_key(url)
            save_success = self.save_report_to_redis(url, processed_report, metadata)
            if not save_success:
                result['error'] = '保存到Redis失败'
                result['steps'].append({'step': 'Redis存储', 'status': '失败', 'error': result['error']})
                return result
            result['steps'].append({'step': 'Redis存储', 'status': '成功', 'key': key})

            result['success'] = True
            result['report'] = processed_report
            result['report_length'] = len(processed_report)
            result['redis_key'] = key
            result['metadata'] = metadata
            logger.info('文章处理完成')
        except Exception as e:
            logger.error(f'处理文章异常: {e}', exc_info=True)
            result['error'] = str(e)
        return result

    def process_multiple_articles(self, urls: List[str]) -> Dict[str, Any]:
        """批量处理多篇文章"""
        logger.info('=' * 80)
        logger.info(f'开始批量处理 {len(urls)} 篇文章')
        logger.info('=' * 80)
        summary = {'total': len(urls), 'success': 0, 'failed': 0, 'reports': [], 'errors': []}
        for i, url in enumerate(urls, 1):
            logger.info(f'\n处理文章 {i}/{len(urls)}: {url}')
            try:
                result = self.process_article(url)
                if result['success']:
                    summary['success'] += 1
                    summary['reports'].append({
                        'url': url, 'redis_key': result['redis_key'],
                        'report_length': result['report_length'],
                        'issue': result.get('metadata', {}).get('issue')
                    })
                else:
                    summary['failed'] += 1
                    summary['errors'].append({'url': url, 'error': result['error']})
            except Exception as e:
                summary['failed'] += 1
                summary['errors'].append({'url': url, 'error': str(e)})
            time.sleep(2)
        logger.info(f'批量处理完成: 成功={summary["success"]}, 失败={summary["failed"]}')
        return summary

    def get_report_from_redis(self, url: str) -> Optional[Dict[str, Any]]:
        """从Redis获取已存储的报告"""
        if not self.redis_client:
            self._init_redis()
        if not self.redis_client or not self.redis_client.is_connected():
            return None
        try:
            key = self.generate_article_key(url)
            data = self.redis_client.client.get(key)
            return json.loads(data) if data else None
        except Exception as e:
            logger.error(f'从Redis获取报告失败: {e}')
            return None

    def delete_report_from_redis(self, url: str) -> bool:
        """从Redis删除指定文章的报告"""
        if not self.redis_client:
            self._init_redis()
        if not self.redis_client or not self.redis_client.is_connected():
            return False
        try:
            key = self.generate_article_key(url)
            result = self.redis_client.client.delete(key)
            return result > 0
        except Exception as e:
            logger.error(f'从Redis删除报告失败: {e}')
            return False

    # ============================================================
    # ArticleAnalyzer 风格方法（完整6步双阶段AI分析）
    # ============================================================

    def _build_first_analysis_prompt(self, article_data: Dict[str, Any]) -> str:
        """构建第一次AI分析提示词：要求输出结构化JSON"""
        prompt_parts = []
        prompt_parts.append(f"""你是一位专业的排列5彩票数据分析专家。请对以下文章内容进行结构化整理和分析。

【文章信息】
标题：{article_data.get('title', '未知')}
作者：{article_data.get('author', '未知')}
发布时间：{article_data.get('publish_time', '未知')}
来源URL：{article_data.get('url', '未知')}
爬取时间：{article_data.get('crawl_time', '未知')}

【文章内容】
{article_data.get('content', '')}

【分析要求】
请对上述内容进行深度分析，提取信息并以JSON格式返回：
{{
    "data_source": "亿点牛文章分析",
    "article_info": {{"title": "...", "author": "...", "publish_time": "...", "url": "..."}},
    "analysis_time": "YYYY-MM-DD HH:MM:SS",
    "issue_number": "提取的期号",
    "forecast_numbers": {{"wan": [], "qian": [], "bai": [], "shi": [], "ge": []}},
    "recommended_combinations": [],
    "key_points": [],
    "trend_analysis": "趋势分析总结",
    "confidence_level": "置信度（高/中/低）",
    "risk_warning": "风险提示",
    "summary": "文章内容总结"
}}""")
        return "\n".join(prompt_parts)

    def _build_second_analysis_prompt(self, redis_data: Dict[str, Any],
                                       first_ai_result: Dict[str, Any],
                                       db_history: Dict[str, Any]) -> str:
        """构建第二次AI分析提示词：整合多源数据进行深度预测"""
        # 复用原 logic（简略版本，完整逻辑保持不变）
        prompt_parts = []
        prompt_parts.append("""
你是一位专业的排列5彩票数据深度分析专家。请基于以下多源数据进行综合分析。

【彩种规则】排列5：5位数字，每位0-9

【数据来源】
1. 文章内容分析：从亿点牛网站爬取并经过AI结构化整理
2. 历史开奖数据：最近30期历史记录
3. 各位置走势统计：万/千/百/十/位独立走势

请综合所有数据进行深度分析，生成下一期号码预测报告。""")

        # Redis数据
        prompt_parts.append("\n【文章内容分析数据】")
        if redis_data:
            ai_analysis = redis_data.get('ai_analysis', {})
            prompt_parts.append(f"期号：{ai_analysis.get('issue_number', '未知')}")
            if ai_analysis.get('forecast_numbers'):
                prompt_parts.append(f"推荐号码：{ai_analysis['forecast_numbers']}")
            if ai_analysis.get('key_points'):
                for pt in ai_analysis['key_points']:
                    prompt_parts.append(f"  - {pt}")

        # DB数据
        prompt_parts.append("\n【历史开奖数据】")
        if db_history:
            if db_history.get('history_data'):
                for item in db_history['history_data'][:10]:
                    prompt_parts.append(f"  {item.get('issue','')}: {item.get('wan','')}{item.get('qian','')}{item.get('bai','')}{item.get('shi','')}{item.get('ge','')}")

        prompt_parts.append("""

【输出格式】
请严格按照JSON格式输出：
{
    "data_source": "亿点牛文章+历史数据综合AI分析",
    "analysis_time": "YYYY-MM-DD HH:MM:SS",
    "next_issue": "预测期号",
    "prediction": {
        "wan": {"numbers": [], "confidence": [], "reason": ""},
        "qian": {"numbers": [], "confidence": [], "reason": ""},
        "bai": {"numbers": [], "confidence": [], "reason": ""},
        "shi": {"numbers": [], "confidence": [], "reason": ""},
        "ge": {"numbers": [], "confidence": [], "reason": ""}
    },
    "recommended_combinations": [],
    "risk_warning": "风险提示"
}""")
        return "\n".join(prompt_parts)

    def first_ai_analysis(self, article_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """第一次AI分析：结构化整理文章内容为JSON"""
        logger.info('开始第一次AI分析：结构化整理')
        self._init_ai_client()
        if not self.ai_client:
            logger.error('AI客户端未初始化')
            return None
        prompt = self._build_first_analysis_prompt(article_data)
        messages = [
            {"role": "system", "content": "你是一位专业的排列5彩票数据分析专家。请严格按照要求输出JSON格式。"},
            {"role": "user", "content": prompt}
        ]
        ai_response = self.ai_client._call_ai_model(messages=messages, max_tokens=4000, temperature=0.5)
        if not ai_response:
            logger.error('AI模型调用失败')
            return None
        ai_result = self.ai_client._parse_ai_response(ai_response)
        if not ai_result:
            logger.error('AI响应解析失败')
            return None
        logger.info(f'第一次AI分析完成，期号: {ai_result.get("issue_number", "未知")}')
        return ai_result

    def save_to_redis(self, issue: str, article_data: Dict[str, Any], ai_result: Dict[str, Any]) -> bool:
        """保存单篇文章数据到Redis"""
        self._init_redis()
        if not self.redis_client or not self.redis_client.is_connected():
            return False
        try:
            self.redis_client.save_raw_data(issue, article_data)
            redis_data = {
                'issue': issue, 'article_data': article_data, 'ai_analysis': ai_result,
                'save_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            key = self.redis_client.get_ai_analysis_key(issue)
            self.redis_client.client.setex(key, 86400 * 7, json.dumps(redis_data, ensure_ascii=False))
            return True
        except Exception as e:
            logger.error(f'保存到Redis失败: {e}')
            return False

    def save_all_articles_to_redis(self, issue: str, articles: List[Dict[str, Any]], ai_result: Dict[str, Any]) -> Dict[str, Any]:
        """批量保存所有爬取的文章到Redis"""
        self._init_redis()
        if not self.redis_client:
            return {'success': False, 'error': 'Redis客户端未初始化', 'saved_count': 0}
        try:
            saved_count = 0
            for idx, article in enumerate(articles):
                article_id = self.redis_client.generate_article_id(article.get('url', ''), idx)
                article_with_issue = article.copy()
                article_with_issue['issue'] = issue
                if self.redis_client.save_article_data(article_id, article_with_issue, expire_days=7):
                    saved_count += 1
            redis_data = {'issue': issue, 'articles_count': len(articles), 'articles': articles,
                          'ai_analysis': ai_result, 'save_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
            key = self.redis_client.get_ai_analysis_key(issue)
            self.redis_client.client.setex(key, 86400 * 7, json.dumps(redis_data, ensure_ascii=False))
            return {'success': True, 'saved_count': saved_count, 'failed_count': len(articles) - saved_count, 'total_count': len(articles)}
        except Exception as e:
            logger.error(f'保存所有文章到Redis失败: {e}')
            return {'success': False, 'error': str(e), 'saved_count': 0}

    def load_from_redis(self, issue: str) -> Optional[Dict[str, Any]]:
        """从Redis按期号加载AI分析数据"""
        self._init_redis()
        if not self.redis_client or not self.redis_client.is_connected():
            return None
        try:
            key = self.redis_client.get_ai_analysis_key(issue)
            data_str = self.redis_client.client.get(key)
            if data_str:
                return json.loads(data_str)
            return None
        except Exception as e:
            logger.error(f'从Redis加载数据失败: {e}')
            return None

    def second_ai_analysis(self, redis_data: Dict[str, Any], db_history: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """第二次AI分析：整合多源数据进行综合预测"""
        logger.info('开始第二次AI分析：整合数据并预测')
        self._init_ai_client()
        if not self.ai_client:
            return None
        first_ai_result = redis_data.get('ai_analysis', {})
        prompt = self._build_second_analysis_prompt(redis_data, first_ai_result, db_history)
        messages = [
            {"role": "system", "content": "你是一位专业的排列5彩票数据深度分析专家。"},
            {"role": "user", "content": prompt}
        ]
        try:
            ai_response = self.ai_client._call_ai_model(messages=messages, max_tokens=8000, temperature=0.7)
            if not ai_response:
                raise RuntimeError('AI模型调用失败')
            ai_result = self.ai_client._parse_ai_response(ai_response)
            if not ai_result:
                raise RuntimeError('AI响应解析失败')
            return ai_result
        except Exception as e:
            logger.error(f'第二次AI分析异常: {e}', exc_info=True)
            # 回退方案
            first_ai = redis_data.get('ai_analysis', {}) if isinstance(redis_data, dict) else {}
            forecast = first_ai.get('forecast_numbers', {}) if isinstance(first_ai, dict) else {}
            prediction = {}
            for pos_key in ['wan', 'qian', 'bai', 'shi', 'ge']:
                nums = forecast.get(pos_key, []) if isinstance(forecast, dict) else []
                prediction[pos_key] = {'numbers': nums if isinstance(nums, list) else [nums], 'confidence': [0.0], 'reason': '二次AI分析失败，回退使用文章初步分析结果'}
            latest_issue = db_history.get('latest_issue', '') if db_history else ''
            next_issue = str(int(latest_issue) + 1) if latest_issue and str(latest_issue).isdigit() else ''
            return {
                'data_source': '回退：文章初步分析+历史数据', 'analysis_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'current_issue': latest_issue, 'next_issue': next_issue, 'prediction': prediction,
                'trend_analysis': {}, 'reasoning_process': ['二次AI分析失败，回退使用初步文章分析结果'],
                'recommended_combinations': [], 'risk_warning': '二次AI分析失败，结果基于回退逻辑，请谨慎使用'
            }

    def save_to_database(self, final_report: Dict[str, Any], db_history: Dict[str, Any]) -> Optional[str]:
        """保存最终AI分析报告到MySQL数据库"""
        self._init_db_client()
        if not self.db_client:
            return None
        try:
            import uuid
            report_uuid = str(uuid.uuid4())
            next_issue = final_report.get('next_issue', '')
            if not next_issue:
                latest = db_history.get('latest_issue', '') if db_history else ''
                if latest and str(latest).isdigit():
                    next_issue = str(int(latest) + 1)
            reasoning = final_report.get('reasoning_process', '')
            if isinstance(reasoning, list):
                report_content = '\n'.join([f'{i+1}. {r}' for i, r in enumerate(reasoning)])
            else:
                report_content = str(reasoning) if reasoning else '暂无推理过程'
            trend = final_report.get('trend_analysis', {})
            trend_analysis = json.dumps(trend, ensure_ascii=False) if isinstance(trend, dict) else str(trend)
            self.db_client.insert_ai_report(
                report_content=report_content, data_count=db_history.get('data_count', 0) if db_history else 0,
                latest_issue=db_history.get('latest_issue', '') if db_history else '', next_issue=next_issue,
                trend_analysis=trend_analysis, probability_stats=json.dumps(final_report.get('prediction', {}), ensure_ascii=False),
                recommended_numbers=json.dumps(final_report.get('prediction', {}), ensure_ascii=False),
                recommended_combinations=json.dumps(final_report.get('recommended_combinations', []), ensure_ascii=False),
                confidence_scores=json.dumps(final_report.get('prediction', {}), ensure_ascii=False),
                recommendation_reasons=json.dumps(final_report.get('key_conclusions', []), ensure_ascii=False),
                key_conclusions=json.dumps(final_report.get('key_conclusions', []), ensure_ascii=False),
                risk_warning=final_report.get('risk_warning', '理性购彩，量力而行'), report_format='JSON'
            )
            return report_uuid
        except Exception as e:
            logger.error(f'保存到数据库失败: {e}', exc_info=True)
            return None

    def analyze_article_workflow(self, target_issue: Optional[str] = None, data_limit: int = 30) -> Dict[str, Any]:
        """完整的文章分析工作流（主入口方法）"""
        logger.info('=' * 80)
        logger.info('开始完整文章分析工作流')
        logger.info('=' * 80)
        result = {'success': False, 'step1_crawl': False, 'step2_first_ai': False,
                  'step3_redis_save': False, 'step4_redis_load': False, 'step5_second_ai': False,
                  'step6_db_save': False, 'error': None, 'report_uuid': None, 'final_report': None}
        try:
            self._init_spider()
            if not self.spider:
                result['error'] = '爬虫模块初始化失败'
                return result
            crawl_result = self.spider.crawl_all_articles(target_issue=target_issue, max_articles=30)
            if not crawl_result.get('articles'):
                result['error'] = '未爬取到文章内容'
                return result
            articles = crawl_result['articles']
            logger.info(f'成功爬取 {len(articles)} 篇文章')
            article_data = articles[0]
            result['step1_crawl'] = True

            issue = self._extract_issue_from_article(article_data, target_issue)
            if not issue or not re.match(r'^\d{6,8}$', str(issue)):
                result['error'] = '无法确定期号'
                return result

            first_ai_result = self.first_ai_analysis(article_data)
            if not first_ai_result:
                result['error'] = '第一次AI分析失败'
                return result
            result['step2_first_ai'] = True

            redis_save_result = self.save_all_articles_to_redis(issue, articles, first_ai_result)
            if not redis_save_result.get('success'):
                result['error'] = f'保存到Redis失败: {redis_save_result.get("error")}'
                return result
            result['step3_redis_save'] = True

            redis_data = self.load_from_redis(issue)
            if not redis_data:
                result['error'] = '从Redis加载数据失败'
                return result
            result['step4_redis_load'] = True

            self._init_db_client()
            if not self.db_client:
                result['error'] = '数据库模块初始化失败'
                return result
            db_history = self.ai_client._fetch_data_from_database(limit=data_limit)
            if db_history.get('error'):
                result['error'] = db_history['error']
                return result

            second_ai_result = self.second_ai_analysis(redis_data, db_history)
            if not second_ai_result:
                result['error'] = '第二次AI分析失败'
                return result
            result['step5_second_ai'] = True

            report_uuid = self.save_to_database(second_ai_result, db_history)
            if not report_uuid:
                result['error'] = '保存到数据库失败'
                return result
            result['step6_db_save'] = True
            result['success'] = True
            result['report_uuid'] = report_uuid
            result['final_report'] = second_ai_result
        except Exception as e:
            logger.error(f'文章分析工作流失败: {e}', exc_info=True)
            result['error'] = str(e)
        return result

    def save_all_articles_bulk_to_redis(self, target_issue: Optional[str] = None,
                                        max_articles: int = 100,
                                        extract_predictions: bool = True) -> Dict[str, Any]:
        """批量爬取文章并保存到Redis（含可选预测数据提取）"""
        logger.info('=' * 80)
        logger.info('开始批量保存文章到Redis')
        logger.info('=' * 80)
        result = {'success': False, 'total_articles': 0, 'saved_articles': 0, 'failed_articles': 0,
                  'extracted_predictions': 0, 'high_quality_predictions': 0,
                  'articles': [], 'predictions': [], 'error': None}
        try:
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

            self._init_redis()
            if not self.redis_client or not self.redis_client.is_connected():
                result['error'] = 'Redis客户端连接失败'
                return result

            for i, article_data in enumerate(articles, 1):
                try:
                    issue = self._extract_issue_from_article(article_data, target_issue)
                    url = article_data.get('url', f'article_{i}')
                    article_id = self.redis_client.generate_article_id(url, i)

                    # HTML清洗
                    try:
                        from modules.html_utils import HTMLTextCleaner
                        clean_text = HTMLTextCleaner().clean_html(article_data.get('content', ''))
                    except Exception:
                        clean_text = article_data.get('content', '')

                    article_for_ai = {'title': article_data.get('title', ''), 'author': article_data.get('author', ''),
                                      'publish_time': article_data.get('publish_time', ''), 'url': url,
                                      'content': clean_text, 'crawl_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
                    ai_analysis = self.first_ai_analysis(article_for_ai)

                    redis_store = {'issue': issue, 'article_id': article_id,
                                   'title': article_data.get('title', '')[:200], 'url': url,
                                   'ai_analysis': ai_analysis,
                                   'save_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
                    save_success = self.redis_client.save_article_data(article_id, redis_store, expire_days=7)
                    if save_success:
                        result['saved_articles'] += 1
                        result['articles'].append({'article_id': article_id, 'issue': issue,
                                                   'title': article_data.get('title', '未知')[:50],
                                                   'has_ai_analysis': ai_analysis is not None})
                    else:
                        result['failed_articles'] += 1
                except Exception as e:
                    result['failed_articles'] += 1
                    logger.error(f'处理文章 {i} 时出错: {e}')

            result['success'] = result['saved_articles'] > 0
        except Exception as e:
            logger.error(f'批量保存文章失败: {e}', exc_info=True)
            result['error'] = str(e)
        return result

    # ============================================================
    # 走势AI分析方法
    # ============================================================

    def _build_trend_analysis_prompt(self, trend_data: Dict[str, Any]) -> str:
        """构建走势图AI分析提示词"""
        prompt_parts = []
        prompt_parts.append("你是一位专业的排列5数据分析专家。请对以下最近30期的走势图数据进行深度分析，生成结构化的走势分析报告JSON。")
        basic = trend_data.get('basic_trend', [])
        if basic:
            prompt_parts.append("\n【基础走势图数据（最近30期）】")
            for item in basic[:30]:
                prompt_parts.append(f"  {item.get('issue','')}: {item.get('wan','')}{item.get('qian','')}{item.get('bai','')}{item.get('shi','')}{item.get('ge','')} 和值:{item.get('hezhi','')}")
        for pos_name, key, num_key in [('万位', 'wan_trend', 'wan_number'), ('千位', 'qian_trend', 'qian_number'),
                                        ('百位', 'bai_trend', 'bai_number'), ('十位', 'shi_trend', 'shi_number'),
                                        ('个位', 'ge_trend', 'ge_number')]:
            pos_data = trend_data.get(key, [])
            if pos_data:
                prompt_parts.append(f"\n【{pos_name}走势数据】")
                for item in pos_data[:20]:
                    prompt_parts.append(f"  {item.get('issue','')}: {item.get(num_key, 0)} (遗漏:{item.get('omission',0)})")
        prompt_parts.append("""

请输出JSON格式的走势分析报告：
{
    "analysis_type": "走势图AI分析",
    "trend_summary": {"overall_trend": "", "hot_numbers_summary": "", "cold_numbers_summary": ""},
    "position_analysis": {
        "wan": {"hot_numbers": [], "cold_numbers": [], "recommended_numbers": []},
        "qian": {"hot_numbers": [], "cold_numbers": [], "recommended_numbers": []},
        "bai": {"hot_numbers": [], "cold_numbers": [], "recommended_numbers": []},
        "shi": {"hot_numbers": [], "cold_numbers": [], "recommended_numbers": []},
        "ge": {"hot_numbers": [], "cold_numbers": [], "recommended_numbers": []}
    }
}""")
        return "\n".join(prompt_parts)

    def trend_analysis_with_ai(self, trend_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """走势AI分析：将走势图数据喂给AI生成分析报告"""
        logger.info('开始走势AI分析')
        self._init_ai_client()
        if not self.ai_client:
            return None
        prompt = self._build_trend_analysis_prompt(trend_data)
        messages = [{"role": "system", "content": "你是一位专业的排列5走势数据分析专家。"}, {"role": "user", "content": prompt}]
        try:
            ai_response = self.ai_client._call_ai_model(messages=messages, max_tokens=6000, temperature=0.5)
            return self.ai_client._parse_ai_response(ai_response) if ai_response else None
        except Exception as e:
            logger.error(f'走势AI分析异常: {e}')
            return None

    def save_trend_analysis_to_redis(self, issue: str, trend_result: Dict[str, Any]) -> bool:
        """保存走势AI分析结果到Redis"""
        self._init_redis()
        if not self.redis_client or not self.redis_client.is_connected():
            return False
        try:
            return self.redis_client.save_trend_analysis(issue, trend_result)
        except Exception as e:
            logger.error(f'保存走势分析到Redis失败: {e}')
            return False

    def load_trend_analysis_from_redis(self, issue: str) -> Optional[Dict[str, Any]]:
        """从Redis加载走势AI分析结果"""
        self._init_redis()
        if not self.redis_client or not self.redis_client.is_connected():
            return None
        try:
            return self.redis_client.get_trend_analysis(issue)
        except Exception as e:
            logger.error(f'从Redis加载走势分析失败: {e}')
            return None


# ============================================================
# 向后兼容别名
# ============================================================

# 旧的类名保持可用
ArticleProcessor = ArticleHandler
ArticleAnalyzer = ArticleHandler


# ============================================================
# 便捷函数
# ============================================================

def run_article_analysis(target_issue: Optional[str] = None, data_limit: int = 30) -> Dict[str, Any]:
    """便捷函数：执行完整文章分析工作流"""
    analyzer = ArticleHandler()
    return analyzer.analyze_article_workflow(target_issue=target_issue, data_limit=data_limit)


# ============================================================
# 独立测试入口
# ============================================================
if __name__ == '__main__':
    print('=' * 80)
    print('文章处理统一模块测试')
    print('=' * 80)

    # 测试简化流程
    processor = ArticleHandler(lazy=False)
    test_url = 'https://www.ydniu.com/info/pl5/zjtj/510020260621.html'
    result = processor.process_article(test_url)

    print('\n' + '=' * 70)
    print('处理结果')
    print('=' * 70)
    print(f'成功: {result["success"]}')
    print(f'URL: {result["url"]}')
    print(f'Redis键: {result["redis_key"]}')
    print(f'报告长度: {result["report_length"]}')
    if result.get('report'):
        print(f'报告预览: {result["report"][:200]}...')

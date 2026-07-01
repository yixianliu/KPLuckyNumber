"""
文章处理流程模块（简化版单篇/批量文章处理）

完整自动化流程（4步）：
1. 爬取指定文章内容（完整文本，基于ydniu_spider）
2. AI模型处理分析（超时控制、重试机制）
3. 报告预处理（去除换行符，保持语义完整）
4. Redis存储（MD5哈希键名、7天过期）

调用路径：
    main.py → run_process_article() / run_process_multiple_articles()
           → ArticleProcessor.process_article() / process_multiple_articles()

与 article_analyzer.py 的区别：
    - ArticleProcessor: 简化版4步流程（爬取→AI→预处理→Redis），无二次AI分析/数据库存储
    - ArticleAnalyzer: 完整版6步流程（爬取→第一次AI→Redis→第二次AI→综合分析→数据库存储）
    - 两者共享: 爬虫(YDNiuSpider)、Redis客户端、AI客户端、期号提取逻辑

已知冗余（待后续重构）:
    - _extract_issue_from_content() 与 article_analyzer.py::_extract_issue_from_article() 功能重复
    - call_ai_with_retry() 与 ERNIEAIAnalyzer._call_ai_model() 功能重复
    - build_analysis_prompt() 与 article_analyzer.py::_build_first_analysis_prompt() 功能类似
    - 当前保持独立是因为两个类服务于不同的CLI入口（process-article vs article）

技术要求：
- 错误处理机制（网络异常、页面结构变化）
- AI调用超时控制和重试机制（默认3次重试，5秒间隔递增）
- Redis合理键名策略（MD5-16位前缀）和过期时间（7天）
- 完整日志记录到 logs/article_processor.log
- 去除换行符不影响可读性
"""

import logging
import os
import json
import re
import time
import hashlib
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple

os.makedirs('logs', exist_ok=True)

logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler = logging.FileHandler('logs/article_processor.log', encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


# 说明：文章处理过程中涉及外部依赖（爬虫/Redis/AI），为避免导入时失败，
# 本模块在初始化组件时使用延迟导入（在函数内部导入），请保持该实现风格。但
# ArticleProcessor.__init__() 中会主动初始化组件（不同于 ArticleAnalyzer 的懒加载模式）。
# 若需更改为懒加载，将 _init_components() 调用移到各方法首次使用时。

class ArticleProcessor:
    """
    文章处理流程管理器（简化版单篇/批量处理）

    完整流程：爬取 → AI分析 → 预处理 → Redis存储

    配置:
    - ai_timeout: AI调用超时（秒），默认60
    - ai_max_retries: 最大重试次数，默认3
    - ai_retry_delay: 重试间隔基础值（秒），实际间隔 = delay × attempt
    - redis_expire_days: Redis过期天数，默认7
    - redis_key_prefix: 键名前缀 'kpluckynumber:article:report:'

    调用方:
    - main.py: run_process_article() / run_process_multiple_articles()
    - CLI: python main.py process-article --url "..." --title "..."
    - CLI: python main.py process-articles --max 10
    """

    def __init__(self):
        """初始化组件：爬虫、Redis、AI客户端（主动加载模式）"""
        self.spider = None
        self.redis_client = None
        self.ai_client = None

        # AI调用配置
        self.ai_timeout = 60
        self.ai_max_retries = 3
        self.ai_retry_delay = 5

        # Redis配置
        self.redis_expire_days = 7
        self.redis_key_prefix = 'kpluckynumber:article:report:'

        # 初始化所有外部组件
        self._init_components()
    
    def _init_components(self):
        """
        初始化所有外部依赖组件（主动加载模式）

        初始化对象:
        - YDNiuSpider: 文章爬虫（ydniu.com）
        - RedisClient: Redis缓存客户端
        - ERNIEAIAnalyzer: AI分析器（调用AGNES API）

        注意: 与 ArticleAnalyzer 采用懒加载（首次使用时初始化）不同，
              ArticleProcessor 在构造时主动初始化所有组件，
              可尽早发现环境依赖问题。
        """
        logger.info('初始化文章处理器组件...')
        
        # 初始化爬虫
        try:
            from modules.ydniu_spider import YDNiuSpider
            self.spider = YDNiuSpider()
            logger.info('爬虫模块初始化成功')
        except Exception as e:
            logger.error(f'爬虫模块初始化失败: {e}')
        
        # 初始化Redis客户端
        try:
            from modules.redis_client import RedisClient
            self.redis_client = RedisClient()
            logger.info('Redis客户端初始化成功')
        except Exception as e:
            logger.error(f'Redis客户端初始化失败: {e}')
        
        # 初始化AI客户端
        try:
            from modules.ernie_ai_analyzer import ERNIEAIAnalyzer
            self.ai_client = ERNIEAIAnalyzer()
            logger.info('AI客户端初始化成功')
        except Exception as e:
            logger.error(f'AI客户端初始化失败: {e}')
    
    def generate_article_key(self, url: str) -> str:
        """
        生成文章唯一Redis键名

        算法: MD5(url) → 取前16位十六进制 → 拼接前缀
        格式: kpluckynumber:article:report:{md5_16}

        Args:
            url: 文章URL

        Returns:
            Redis键名，如 "kpluckynumber:article:report:a1b2c3d4e5f6g7h8"
        """
        url_hash = hashlib.md5(url.encode()).hexdigest()[:16]
        return f'{self.redis_key_prefix}{url_hash}'

    def crawl_article_content(self, url: str) -> Optional[str]:
        """
        爬取单篇文章的纯文本内容

        通过YDNiuSpider获取文章页面，自动清洗HTML标签，
        返回纯文本内容。

        Args:
            url: 文章URL

        Returns:
            纯文本内容，爬取失败或内容<50字符返回None
        """
        logger.info(f'开始爬取文章: {url}')
        
        if not self.spider:
            logger.error('爬虫模块未初始化')
            return None
        
        try:
            # 爬取文章页面
            article_data = self.spider.crawl_article_page(url)
            
            if not article_data:
                logger.error(f'爬取文章失败: {url}')
                return None
            
            # 获取文本内容（已去除HTML）
            content = article_data.get('content', '')
            
            if not content or len(content) < 50:
                logger.warning(f'文章内容过短({len(content)}字符)，尝试备用方案')
                return None
            
            logger.info(f'爬取成功: {len(content)} 字符')
            return content
            
        except Exception as e:
            logger.error(f'爬取文章异常: {e}', exc_info=True)
            return None
    
    def call_ai_with_retry(self, prompt: str, max_retries: Optional[int] = None) -> Optional[str]:
        """
        调用AI模型，带指数退避重试和超时控制

        重试策略:
        - 最多重试 max_retries 次（默认3次）
        - 超时时间: ai_timeout（默认60秒）
        - 重试延迟: ai_retry_delay × attempt（指数递增: 5s → 10s → 15s）
        - 捕获三种异常: Timeout / ConnectionError / 通用异常

        注意: 此方法与 ERNIEAIAnalyzer._call_ai_model() 功能重复，
             保持独立是为避免 ArticleProcessor 对 ERNIEAIAnalyzer 接口的硬依赖。

        Args:
            prompt: 完整的提示词文本
            max_retries: 最大重试次数，None则使用默认ai_max_retries

        Returns:
            AI原始响应文本，所有重试失败返回None
        """
        if not self.ai_client or not self.ai_client.ai_available:
            logger.error('AI客户端不可用')
            return None
        
        retries = max_retries or self.ai_max_retries
        
        for attempt in range(1, retries + 1):
            try:
                logger.info(f'AI调用第 {attempt}/{retries} 次')
                
                messages = [
                    {'role': 'system', 'content': '你是一位专业的排列5彩票文章分析师，请生成详细的分析报告。'},
                    {'role': 'user', 'content': prompt}
                ]
                
                # 调用AI模型（使用requests实现超时）
                import requests
                
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
            
            # 重试延迟
            if attempt < retries:
                time.sleep(self.ai_retry_delay * attempt)
        
        logger.error(f'AI调用失败，已重试 {retries} 次')
        return None
    
    def build_analysis_prompt(self, content: str, title: str = '') -> str:
        """
        构建文章分析Prompt（5段式分析要求）

        AI输出要求结构:
        - 一、文章概述
        - 二、预测思路分析
        - 三、号码推荐及依据
        - 四、风险评估
        - 五、总结建议

        注意: 此方法与 article_analyzer.py::_build_first_analysis_prompt() 类似但输出格式不同，
            本方法要求自由文本格式，后者要求结构化JSON格式。

        Args:
            content: 文章纯文本内容
            title: 文章标题

        Returns:
            完整的prompt文本
        """
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        prompt = f"""请对以下排列5彩票文章进行深度分析，生成详细的分析报告。

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
请生成一份详细的分析报告，包含以下部分：
- 一、文章概述
- 二、预测思路分析
- 三、号码推荐及依据
- 四、风险评估
- 五、总结建议

请用中文输出，内容详实，逻辑清晰。"""
        
        return prompt
    
    def preprocess_report(self, report: str) -> str:
        """
        预处理报告内容：去除换行符、规范化空白、优化标点可读性

        步骤:
        1. 去除所有换行符（\\n、\\r → 空格）
        2. 合并多余空格（多个连续空格 → 单个空格）
        3. 中文标点后补空格（。！？：；）
        4. 英文标点后补空格（.!?:;）
        5. 去除首尾空格

        Args:
            report: AI返回的原始报告文本

        Returns:
            处理后的单行（或少行）文本
        """
        if not report:
            return ""
        
        # 步骤1：去除所有换行符
        processed = report.replace('\n', ' ')
        processed = processed.replace('\r', ' ')
        
        # 步骤2：去除多余空格（保留合理间距）
        processed = re.sub(r' +', ' ', processed)
        
        # 步骤3：确保标点符号后有空格，提高可读性
        # 在中文标点后添加空格
        processed = re.sub(r'([。！？：；])', r'\1 ', processed)
        # 在英文标点后添加空格（如果没有）
        processed = re.sub(r'([.!?:;])([^\s])', r'\1 \2', processed)
        
        # 步骤4：去除首尾空格
        processed = processed.strip()
        
        logger.info(f'报告预处理完成: 原始{len(report)}字符 → 处理后{len(processed)}字符')
        
        return processed
    
    def save_report_to_redis(self, url: str, report: str, metadata: Dict[str, Any]) -> bool:
        """
        将处理后的报告存储到Redis（JSON格式，含自动过期）

        存储数据结构:
        {
            'url': 文章URL,
            'report': 预处理后的报告文本,
            'report_length': 报告字符数,
            'metadata': {issue, title, content_length, ai_model},
            'process_time': 处理时间字符串,
            'expire_days': 7
        }

        键名: kpluckynumber:article:report:{MD5前16位}
        过期: 7天（redis_expire_days）

        Args:
            url: 文章URL（用于生成键名）
            report: 预处理后的报告文本
            metadata: 元数据（期号、标题、内容长度、AI模型名等）

        Returns:
            是否保存成功
        """
        if not self.redis_client or not self.redis_client.is_connected():
            logger.error('Redis客户端未连接')
            return False
        
        if not report:
            logger.error('报告内容为空')
            return False
        
        try:
            # 生成键名
            key = self.generate_article_key(url)
            
            # 构建存储数据（不包含原始HTML）
            data = {
                'url': url,
                'report': report,
                'report_length': len(report),
                'metadata': metadata,
                'process_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'expire_days': self.redis_expire_days
            }
            
            # 保存到Redis
            self.redis_client.client.set(
                key,
                json.dumps(data, ensure_ascii=False),
                ex=timedelta(days=self.redis_expire_days)
            )
            
            logger.info(f'报告已保存到Redis: {key} (过期时间: {self.redis_expire_days}天)')
            return True
            
        except Exception as e:
            logger.error(f'保存报告到Redis失败: {e}', exc_info=True)
            return False
    
    def process_article(self, url: str, title: str = '') -> Dict[str, Any]:
        """
        处理单篇文章的完整4步流程

        步骤:
        步骤1: crawl_article_content(url) → 爬取文章纯文本
        步骤2: call_ai_with_retry(build_analysis_prompt()) → AI分析
        步骤3: preprocess_report() → 去除换行符、规范化文本
        步骤4: save_report_to_redis() → 存储到Redis（7天过期）

        返回结构:
        {
            'success': bool,
            'url': 文章URL,
            'title': 文章标题,
            'report': 预处理后的报告文本,
            'report_length': 报告字符数,
            'redis_key': Redis键名,
            'steps': [{每步状态}],
            'error': 错误信息(仅失败时)
        }

        Args:
            url: 文章完整URL
            title: 文章标题（可选，不提供则从页面提取）

        Returns:
            处理结果字典（无论成功失败都返回结构化结果）
        """
        logger.info('=' * 80)
        logger.info(f'开始处理文章: {url}')
        logger.info('=' * 80)
        
        result = {
            'success': False,
            'url': url,
            'title': title,
            'report': None,
            'report_length': 0,
            'redis_key': None,
            'steps': [],
            'error': None
        }
        
        try:
            # 步骤1：爬取文章内容
            logger.info('步骤1：爬取文章内容...')
            content = self.crawl_article_content(url)
            
            if not content:
                result['error'] = '爬取文章内容失败'
                result['steps'].append({'step': '爬取', 'status': '失败', 'error': result['error']})
                return result
            
            result['steps'].append({'step': '爬取', 'status': '成功', 'content_length': len(content)})
            logger.info(f'爬取成功: {len(content)} 字符')
            
            # 步骤2：构建Prompt并调用AI
            logger.info('步骤2：调用AI模型分析...')
            prompt = self.build_analysis_prompt(content, title)
            ai_report = self.call_ai_with_retry(prompt)
            
            if not ai_report:
                result['error'] = 'AI模型调用失败'
                result['steps'].append({'step': 'AI分析', 'status': '失败', 'error': result['error']})
                return result
            
            result['steps'].append({'step': 'AI分析', 'status': '成功', 'report_length': len(ai_report)})
            logger.info(f'AI分析完成: {len(ai_report)} 字符')
            
            # 步骤3：预处理报告（去除换行符）
            logger.info('步骤3：预处理报告（去除换行符）...')
            processed_report = self.preprocess_report(ai_report)
            
            if not processed_report:
                result['error'] = '报告预处理失败'
                result['steps'].append({'step': '预处理', 'status': '失败', 'error': result['error']})
                return result
            
            result['steps'].append({'step': '预处理', 'status': '成功', 'processed_length': len(processed_report)})
            
            # 步骤4：提取期号（用于元数据）
            issue = self._extract_issue_from_content(content, title)
            
            # 步骤5：保存到Redis
            logger.info('步骤4：保存到Redis...')
            metadata = {
                'issue': issue,
                'title': title,
                'content_length': len(content),
                'ai_model': getattr(self.ai_client, 'model_name', 'unknown')
            }
            
            key = self.generate_article_key(url)
            save_success = self.save_report_to_redis(url, processed_report, metadata)
            
            if not save_success:
                result['error'] = '保存到Redis失败'
                result['steps'].append({'step': 'Redis存储', 'status': '失败', 'error': result['error']})
                return result
            
            result['steps'].append({'step': 'Redis存储', 'status': '成功', 'key': key})
            
            # 设置成功结果
            result['success'] = True
            result['report'] = processed_report
            result['report_length'] = len(processed_report)
            result['redis_key'] = key
            result['metadata'] = metadata
            
            logger.info('=' * 80)
            logger.info('文章处理完成')
            logger.info(f'报告长度: {len(processed_report)} 字符')
            logger.info(f'Redis键名: {key}')
            logger.info(f'过期时间: {self.redis_expire_days}天')
            logger.info('=' * 80)
            
        except Exception as e:
            logger.error(f'处理文章异常: {e}', exc_info=True)
            result['error'] = str(e)
        
        return result
    
    def process_multiple_articles(self, urls: List[str]) -> Dict[str, Any]:
        """
        批量处理多篇文章（逐篇调用process_article，含间隔控制）

        流程:
        1. 逐篇调用process_article(url)
        2. 每篇处理间隔2秒（避免频繁请求）
        3. 汇总统计成功/失败数量

        Args:
            urls: 文章URL列表

        Returns:
            {
                'total': 总文章数,
                'success': 成功数,
                'failed': 失败数,
                'reports': [{url, redis_key, report_length, issue}, ...],
                'errors': [{url, error}, ...]
            }
        """
        logger.info('=' * 80)
        logger.info(f'开始批量处理 {len(urls)} 篇文章')
        logger.info('=' * 80)
        
        summary = {
            'total': len(urls),
            'success': 0,
            'failed': 0,
            'reports': [],
            'errors': []
        }
        
        for i, url in enumerate(urls, 1):
            logger.info(f'\n处理文章 {i}/{len(urls)}: {url}')
            
            try:
                result = self.process_article(url)
                
                if result['success']:
                    summary['success'] += 1
                    summary['reports'].append({
                        'url': url,
                        'redis_key': result['redis_key'],
                        'report_length': result['report_length'],
                        'issue': result['metadata'].get('issue')
                    })
                    logger.info(f'文章 {i} 处理成功')
                else:
                    summary['failed'] += 1
                    summary['errors'].append({
                        'url': url,
                        'error': result['error']
                    })
                    logger.warning(f'文章 {i} 处理失败: {result["error"]}')
                    
            except Exception as e:
                summary['failed'] += 1
                summary['errors'].append({'url': url, 'error': str(e)})
                logger.error(f'处理文章 {i} 异常: {e}')
            
            # 处理间隔，避免频繁请求
            time.sleep(2)
        
        logger.info('=' * 80)
        logger.info(f'批量处理完成: 成功={summary["success"]}, 失败={summary["failed"]}')
        logger.info('=' * 80)
        
        return summary
    
    def _extract_issue_from_content(self, content: str, title: str) -> str:
        """
        从文章内容/标题中提取期号

        提取策略（按优先级）:
        1. 从标题匹配: r'(\d{6,8})期' 格式（如"2026165期xxx"）
        2. 从内容匹配: 同样的正则模式
        3. 均未匹配返回: 'unknown'

        注意: 与 article_analyzer.py::_extract_issue_from_article() 功能重复，
            且 article_analyzer 版本多支持从link_title和URL提取。

        Args:
            content: 文章纯文本内容
            title: 文章标题

        Returns:
            期号字符串（如"2026165"），无法提取返回"unknown"
        """
        # 先从标题提取
        issue_pattern = re.compile(r'(\d{6,8})期')
        match = issue_pattern.search(title)
        if match:
            return match.group(1)
        
        # 从内容提取
        match = issue_pattern.search(content)
        if match:
            return match.group(1)
        
        return 'unknown'
    
    def get_report_from_redis(self, url: str) -> Optional[Dict[str, Any]]:
        """
        从Redis获取已存储的报告

        Args:
            url: 文章URL（用于反查键名）

        Returns:
            报告数据字典，未找到返回None
        """
        if not self.redis_client or not self.redis_client.is_connected():
            logger.error('Redis客户端未连接')
            return None

        try:
            key = self.generate_article_key(url)
            data = self.redis_client.client.get(key)

            if data:
                return json.loads(data)
            else:
                logger.warning(f'Redis中未找到报告: {key}')
                return None

        except Exception as e:
            logger.error(f'从Redis获取报告失败: {e}')
            return None

    def delete_report_from_redis(self, url: str) -> bool:
        """
        从Redis删除指定文章的报告

        Args:
            url: 文章URL

        Returns:
            是否删除成功（key存在且被删除返回True）
        """
        if not self.redis_client or not self.redis_client.is_connected():
            logger.error('Redis客户端未连接')
            return False

        try:
            key = self.generate_article_key(url)
            result = self.redis_client.client.delete(key)
            logger.info(f'从Redis删除报告: {key} (结果: {result})')
            return result > 0

        except Exception as e:
            logger.error(f'从Redis删除报告失败: {e}')
            return False


# ============================================================
# 独立测试入口：可运行 python -m modules.article_processor
# ============================================================
if __name__ == '__main__':
    processor = ArticleProcessor()

    # 测试单篇文章处理流程
    test_url = 'https://www.ydniu.com/info/pl5/zjtj/510020260621.html'
    result = processor.process_article(test_url)

    print('\n' + '=' * 70)
    print('处理结果')
    print('=' * 70)
    print(f'成功: {result["success"]}')
    print(f'URL: {result["url"]}')
    print(f'Redis键: {result["redis_key"]}')
    print(f'报告长度: {result["report_length"]}')
    if result['report']:
        print(f'报告预览: {result["report"][:200]}...')

    # 测试从Redis读取（验证存储成功）
    if result['success'] and result['redis_key']:
        print('\n从Redis读取报告:')
        report_data = processor.get_report_from_redis(test_url)
        if report_data:
            print(f'报告长度: {report_data["report_length"]}')
            print(f'期号: {report_data["metadata"].get("issue")}')
            print(f'处理时间: {report_data["process_time"]}')
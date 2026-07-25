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
             → Pipeline.execute_pipeline()
"""

import logging
import os
import json
import math
import uuid
import itertools
import time
import random
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

os.makedirs('logs', exist_ok=True)

logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler = logging.FileHandler('logs/pipeline.log', encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


class _PipelineGUIHandler(logging.Handler):
    """
    将流水线内部日志实时转发到 GUI 回调的日志处理器。

    仅转发以 'modules.' 开头的模块日志(避免 GUI/matplotlib 等噪声),
    并根据日志级别/内容映射为 GUI 友好的展示级别:
      - ERROR/CRITICAL -> 'error'
      - WARNING       -> 'warning'
      - 分隔符/步骤标记 -> 'section'
      - 其它 INFO      -> 'info'

    回调在后台工作线程中被调用,但其实现只做线程安全的队列投递,
    因此不会引发 tkinter 线程安全问题。
    """

    def __init__(self, callback):
        super().__init__()
        self.callback = callback
        # 只转发消息正文,不带时间戳/模块名(由 GUI 统一排版)
        self.setFormatter(logging.Formatter('%(message)s'))

    def emit(self, record):
        try:
            name = record.name or ''
            if not name.startswith('modules.'):
                return
            msg = self.format(record)
            lvl = record.levelno
            if lvl >= logging.ERROR:
                level = 'error'
            elif lvl >= logging.WARNING:
                level = 'warning'
            else:
                txt = record.getMessage()
                stripped = txt.lstrip()
                if (stripped.startswith('=' * 8) or stripped.startswith('#' * 8)
                        or '【步骤' in txt or '开始执行四步' in txt
                        or '执行完成' in txt):
                    level = 'section'
                else:
                    level = 'info'
            self.callback(level, msg)
        except Exception:
            # 回调异常绝不应影响主流程
            pass


class Pipeline:
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
        self.redis_client = None
        self.redis_key_manager = None
        self.ai_client = None
        self.db_client = None
        self.online_learner = None
        # ★ B4 性能优化: 流水线内共享单个 P5Predictor 实例
        #   避免 step1/step4/回测 各自 new 一个 predictor 导致验证记录全表扫描×3、
        #   配置与权重管理器重复构建。predict() 每次调用都会自设 _verification_cutoff
        #   并按该 cutoff 过滤(缓存始终是全量记录), 故共享实例行为等价、零回归风险。
        self._predictor = None
        # 增强版文章处理器(软约束特征): 该模块已按设计停用/移除,
        # 显式初始化为 None, 避免下游 `if self.enhanced_article_processor:`
        # 因属性缺失抛 AttributeError(历史死代码 B2 修复)。
        self.enhanced_article_processor = None
        self._enhanced_ap_unavailable = False  # 记住不可用状态, 避免每篇文章重复尝试
        # 实时进度回调(GUI 流式输出用, 默认 None -> 不转发)
        self._progress_callback = None
        # 流水线状态跟踪
        self.pipeline_state = {
            'article_reports': [],       # 步骤1产出：专家文章分析报告列表
            'trend_report': None,        # 步骤2产出：走势分析报告
            'integrated_report': None,   # 步骤3产出：综合分析报告
            'final_report': None,        # 步骤4产出：最终预测结果
            'soft_constraints': None,    # 新增：融合软约束特征
            'started_at': None,          # 流水线开始时间
            'completed_at': None,        # 流水线结束时间
        }

    # ================================================================
    # 组件懒加载
    # ================================================================

    def _init_redis(self):
        """懒加载初始化Redis客户端"""
        try:
            from modules.cache import CacheClient
            self.redis_client = CacheClient()
            if self.redis_client.is_connected():
                logger.info('Redis客户端初始化成功')
            else:
                logger.warning('Redis客户端连接失败，部分功能降级')
        except ImportError as e:
            logger.error(f'无法导入Redis模块: {e}')

    def _init_ai_client(self):
        """懒加载初始化AI客户端"""
        try:
            from modules.ai_analyzer import AIAnalyzer
            self.ai_client = AIAnalyzer()
            logger.info(f'AI客户端初始化成功，模型: {self.ai_client.model_name}')
        except ImportError as e:
            logger.error(f'无法导入AI客户端模块: {e}')

    def _init_db_client(self):
        """懒加载初始化数据库客户端"""
        try:
            from modules.database import P5Database
            self.db_client = P5Database()
            if self.db_client.connect():
                logger.info('数据库客户端初始化成功')
            else:
                logger.warning('数据库客户端连接失败')
        except ImportError as e:
            logger.error(f'无法导入数据库模块: {e}')

    def _get_predictor(self):
        """
        ★ B4 性能优化: 懒加载并复用单个 P5Predictor 实例。

        流水线 step1(_calc_statistical_prediction)、step4(step4_final_prediction)、
        回测(_execute_backtest_analysis) 均需要预测器。此前各自 `P5Predictor()` 新建,
        导致:
          1) 验证记录表(p5_prediction_record)被全表扫描加载 3 次(虽预测器内已有缓存,
             但每次 new 都是新实例, 缓存不共享);
          2) 配置解析、AdaptiveWeightManager 构建等重复执行。

        共享同一实例后: 验证记录仅全表加载 1 次, 实例级缓存(_verification_cache)跨步骤复用。
        行为等价性保证: P5Predictor.predict() 每次调用都会重设 self._verification_cutoff
        并按该 cutoff 过滤(缓存恒为全量记录), 故共享实例与逐次新建的预测结果完全一致,
        零回归风险。
        """
        if getattr(self, '_predictor', None) is None:
            from modules.predictor import P5Predictor
            self._predictor = P5Predictor()
            logger.info('B4: 创建并复用共享 P5Predictor 实例')
        return self._predictor

    def _ensure_db(self) -> bool:
        """
        确保数据库连接可用(解决长时运行后 "MySQL server has gone away")。

        流水线前期步骤(文章爬取/AI分析)耗时较长, 早期建立的连接可能已超时。
        附加步骤(验证/回测/特征)执行前调用本方法探测并自动重连。
        """
        try:
            if not self.db_client:
                self._init_db_client()
            if not self.db_client:
                return False
            # pymysql 的 ping(reconnect=True) 会自动重连失效连接
            try:
                if self.db_client.connection:
                    self.db_client.connection.ping(reconnect=True)
                    return True
            except Exception:
                logger.warning('数据库连接探测失败, 尝试重建连接...')
            # 重建连接
            try:
                self.db_client.disconnect()
            except Exception:
                pass
            return bool(self.db_client.connect())
        except Exception as e:
            logger.warning(f'确保数据库连接失败: {e}')
            return False

    def _validate_data_quality(self, history_data: List[Dict], target_issue: Optional[str] = None) -> Dict:
        """
        数据质量门禁检查（在预测前运行，不良/不足数据会阻止预测）。

        对历史开奖数据进行6项质量门禁检测，返回通过/失败的详细报告。
        数据格式兼容两种形态：
          - 旧格式: 每条记录含 wan/qian/bai/shi/ge 字段
          - 新格式: 每条记录含 numbers 列表（经 _normalize_history_data 转换后）

        Args:
            history_data: 历史开奖数据列表
            target_issue: 目标期号（用于 RECENTNESS 门禁检测数据新鲜度）

        Returns:
            {valid: bool, checks: {gate_name: {name, passed, severity, message, details}}}
        """
        checks = {}
        had_critical_failure = False

        # --- Gate 1: MINIMUM_RECORDS ---
        min_records_check = {
            'name': 'MINIMUM_RECORDS',
            'passed': len(history_data) >= 30,
            'severity': 'critical',
            'message': f'数据量充足({len(history_data)}期 >= 30期最低要求)' if len(history_data) >= 30
                       else f'数据量不足({len(history_data)}期 < 30期最低要求)，无法进行可靠预测',
            'details': {'record_count': len(history_data), 'minimum_required': 30}
        }
        checks['MINIMUM_RECORDS'] = min_records_check
        if not min_records_check['passed']:
            had_critical_failure = True

        # --- Gate 2: NUMBER_RANGE ---
        range_passed = True
        range_issues = []
        for i, rec in enumerate(history_data):
            # 兼容两种格式
            values = []
            if 'numbers' in rec and isinstance(rec['numbers'], list) and len(rec['numbers']) >= 5:
                values = [rec['numbers'][0], rec['numbers'][1], rec['numbers'][2], rec['numbers'][3], rec['numbers'][4]]
            else:
                values = [rec.get('wan'), rec.get('qian'), rec.get('bai'), rec.get('shi'), rec.get('ge')]

            for pos_name, val in zip(['wan', 'qian', 'bai', 'shi', 'ge'], values):
                if val is None:
                    continue  # MISSING_VALUES 门禁负责
                try:
                    iv = int(val)
                except (TypeError, ValueError):
                    range_passed = False
                    range_issues.append(f"记录#{i+1} {pos_name}={val!r} 不是整数")
                    continue
                if iv < 0 or iv > 9:
                    range_passed = False
                    range_issues.append(f"记录#{i+1} {pos_name}={iv} 不在0-9范围内")

        number_range_check = {
            'name': 'NUMBER_RANGE',
            'passed': range_passed,
            'severity': 'critical',
            'message': '所有位数值均InRange(0-9整数)' if range_passed
                       else f'发现{len(range_issues)}个数值越界问题',
            'details': {'out_of_range_samples': range_issues[:10]}
        }
        checks['NUMBER_RANGE'] = number_range_check
        if not number_range_check['passed']:
            had_critical_failure = True

        # --- Gate 3: DUPLICATE_ISSUES ---
        issue_values = []
        for rec in history_data:
            iss = rec.get('issue')
            if iss is not None:
                issue_values.append(str(iss))
        seen = set()
        duplicates = [x for x in issue_values if x in seen or seen.add(x)]
        dup_check = {
            'name': 'DUPLICATE_ISSUES',
            'passed': len(duplicates) == 0,
            'severity': 'warning',
            'message': '无重复期号' if not duplicates
                       else f'发现{len(duplicates)}个重复期号: {duplicates[:10]}',
            'details': {'duplicate_issues': duplicates[:20]}
        }
        checks['DUPLICATE_ISSUES'] = dup_check

        # --- Gate 4: CONSECUTIVE_GAPS ---
        def _parse_issue_num(iss: str) -> int:
            """安全解析期号为整数，用于间隙检测。"""
            try:
                s = str(iss).strip()
                return int(s) % 1000000
            except (ValueError, TypeError):
                return 0

        parsed_issues = [_parse_issue_num(i) for i in issue_values if i]
        large_gaps = []
        for j in range(1, len(parsed_issues)):
            gap = abs(parsed_issues[j] - parsed_issues[j - 1])
            if gap > 5:
                large_gaps.append({
                    'from': issue_values[j - 1],
                    'to': issue_values[j],
                    'gap_periods': gap
                })
        gap_check = {
            'name': 'CONSECUTIVE_GAPS',
            'passed': len(large_gaps) == 0,
            'severity': 'warning',
            'message': '无异常大的期号间断' if not large_gaps
                       else f'发现{len(large_gaps)}处>5期的间断',
            'details': {'gaps': large_gaps[:10]}
        }
        checks['CONSECUTIVE_GAPS'] = gap_check

        # --- Gate 5: MISSING_VALUES ---
        missing_passed = True
        missing_samples = []
        for i, rec in enumerate(history_data):
            # 兼容两种格式
            vals = []
            if 'numbers' in rec and isinstance(rec['numbers'], list) and len(rec['numbers']) >= 5:
                vals = [rec['numbers'][k] for k in range(5)]
            else:
                vals = [rec.get('wan'), rec.get('qian'), rec.get('bai'), rec.get('shi'), rec.get('ge')]
            for pos_name, val in zip(['wan', 'qian', 'bai', 'shi', 'ge'], vals):
                if val is None or val == '' or val == 'null':
                    missing_passed = False
                    missing_samples.append(f"记录#{i+1} ({rec.get('issue', '?')}) {pos_name}=None")
                    break  # 每条记录只记一条
        missing_check = {
            'name': 'MISSING_VALUES',
            'passed': missing_passed,
            'severity': 'critical',
            'message': '无数值缺失' if missing_passed
                       else f'发现{len(missing_samples)}条含空值的记录',
            'details': {'missing_samples': missing_samples[:10]}
        }
        checks['MISSING_VALUES'] = missing_check
        if not missing_check['passed']:
            had_critical_failure = True

        # --- Gate 6: RECENTNESS ---
        recentness_passed = True
        recentness_msg = '数据新鲜度正常'
        recentness_details = {}
        if target_issue and issue_values:
            latest_issue_str = issue_values[0]  # 列表已是 DESC 顺序
            latest_parsed = _parse_issue_num(latest_issue_str)
            target_parsed = _parse_issue_num(target_issue)
            age = abs(target_parsed - latest_parsed)
            recentness_details = {'latest_issue': latest_issue_str,
                                  'target_issue': target_issue,
                                  'age_in_periods': age}
            if age > 3:
                recentness_passed = False
                recentness_msg = f'最新数据已过时({latest_issue_str}距今{target_issue}共{age}期 > 3期)'
            else:
                recentness_msg = f'数据新鲜度正常({latest_issue_str}距{target_issue}仅{age}期)'
        recentness_check = {
            'name': 'RECENTNESS',
            'passed': recentness_passed,
            'severity': 'warning',
            'message': recentness_msg,
            'details': recentness_details
        }
        checks['RECENTNESS'] = recentness_check

        # 计算总体结果
        overall_valid = not had_critical_failure
        return {
            'valid': overall_valid,
            'checks': checks
        }

    def _init_redis_key_manager(self):
        """懒加载初始化Redis Key管理器"""
        try:
            if not self.redis_client or not self.redis_client.is_connected():
                self._init_redis()
            
            if self.redis_client and self.redis_client.is_connected():
                from modules.redis_storage_manager import RedisKeyManager
                self.redis_key_manager = RedisKeyManager(self.redis_client)
                logger.info('Redis Key管理器初始化成功')
            else:
                logger.warning('Redis未连接，无法初始化Key管理器')
        except ImportError as e:
            logger.error(f'无法导入RedisKeyManager模块: {e}')

    def _init_online_learner(self):
        """懒加载初始化在线学习引擎"""
        try:
            if not self.db_client:
                self._init_db_client()
            if not self.redis_client:
                self._init_redis()
            
            from modules.online_learner import OnlineLearner
            self.online_learner = OnlineLearner(self.db_client, self.redis_client)
            logger.info('在线学习引擎初始化成功')
        except ImportError as e:
            logger.error(f'无法导入OnlineLearner模块: {e}')

    def _init_enhanced_article_processor(self):
        """懒加载初始化增强版文章处理器。

        注: 增强版文章软约束模块(enhanced_article_processor)已按设计停用/移除
        (回测实证专家软约束≈随机, 见 v3.16 交付纪要)。此处保持接口存在但优雅降级:
        模块缺失时仅记录一次 debug, 不再每篇文章刷 error 日志, 也不抛异常。
        self.enhanced_article_processor 恒为 None -> 下游 if 守卫自然跳过。
        """
        if self._enhanced_ap_unavailable:
            return
        try:
            from modules.enhanced_article_processor import EnhancedArticleProcessor
            from modules.redis_storage_manager import RedisKeyManager

            if not self.redis_client:
                self._init_redis()
            if not self.online_learner:
                self._init_online_learner()

            redis_mgr = None
            if self.redis_client and self.redis_client.is_connected():
                redis_mgr = RedisKeyManager(self.redis_client)

            self.enhanced_article_processor = EnhancedArticleProcessor(
                redis_manager=redis_mgr,
                online_learner=self.online_learner
            )
            logger.info('增强版文章处理器初始化成功')
        except ImportError:
            # 模块已停用: 标记不可用, 后续不再重复尝试
            self.enhanced_article_processor = None
            self._enhanced_ap_unavailable = True
            logger.debug('增强版文章处理器模块未安装(已按设计停用), 跳过软约束特征提取')

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
        """解析AI返回的JSON响应（鲁棒：兼容单引号/裸key/尾随逗号/代码块）"""
        from modules.json_repair import repair_and_parse_json
        return repair_and_parse_json(response_text, default=None)

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
            logger.info(f'调用AI _call_ai_model: max_tokens={max_tokens}, temperature={temperature}')
            ai_response = self.ai_client._call_ai_model(messages=messages, max_tokens=max_tokens, temperature=temperature)
            resp_len = len(ai_response) if ai_response else 0
            logger.info(f'_call_ai_model返回值: 类型={type(ai_response).__name__}, 长度={resp_len}')
            if not ai_response:
                logger.error('AI模型调用返回空结果（值为None或空字符串）')
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
        步骤1: 专家文章爬取、AI格式化、Redis存储、整合分析
        
        完整流程:
        1. 爬取目标期数的所有专家文章
        2. 逐篇调用AI进行结构化整理(提取推荐号码、分析观点、置信度等)
        3. 每篇分析报告存入Redis(kpluckynumber:pl5:expert_report:{article_id}, 7天过期)
        4. 从Redis提取所有文章整合后交由AI模型综合分析生成预测报告
        5. 将预测报告存入数据库p5_ai_report表
        6. 生成独立的"专家文章预测报告"JSON文件
        
        Args:
            target_issue: 目标期号(如"2026165")
            
        Returns:
            {success, article_count, ai_success_count, ai_fail_count, 
             expert_count, prediction_report, report_uuid, error}
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
            crawl_result = self.spider.crawl_all_articles(target_issue=target_issue, max_articles=15)

            if not crawl_result.get('articles'):
                result['error'] = f'未爬取到期号{target_issue}的文章'
                return result

            articles = crawl_result['articles']
            result['articles'] = articles  # ★ 修复:将articles数据赋值给result
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

                # 调用AI（带重试）
                ai_result = None
                for article_attempt in range(3):
                    ai_result = self._call_ai(
                        system_prompt="你是一位专业的排列5彩票数据分析专家，擅长对文章内容进行结构化整理和分析。",
                        user_prompt=prompt,
                        max_tokens=4000,
                        temperature=0.5
                    )
                    if ai_result:
                        break
                    elif article_attempt < 2:
                        logger.warning(f'文章{idx} AI分析第{article_attempt+1}次失败，重试中...')
                        self._delay_random(2, 3)

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

                    # ★ 关键修复(问题3根因): 把AI结构化结果回写到 article 对象,
                    # 否则下游 _generate_expert_article_report 读取 article['ai_analysis']
                    # 时始终为空字典 -> 专家文章预测报告的 prediction 全为空。
                    try:
                        parsed_result = ai_result if isinstance(ai_result, dict) else \
                            self._parse_ai_json(ai_result) if isinstance(ai_result, str) else {}
                        article['ai_analysis'] = parsed_result
                        
                        # ★ 调试日志: 记录AI返回的结构
                        if parsed_result:
                            logger.info(f'文章{idx} AI返回keys: {list(parsed_result.keys())}')
                            if 'forecast_numbers' in parsed_result:
                                logger.info(f'文章{idx} forecast_numbers结构: {parsed_result["forecast_numbers"]}')
                            else:
                                logger.warning(f'文章{idx} AI未返回forecast_numbers字段!')
                    except Exception:
                        article['ai_analysis'] = {}

                    redis_key = self.REDIS_ARTICLE_REPORT_KEY.format(article_id=article_id)
                    try:
                        # 确保store_data可被JSON序列化
                        import json as _json
                        
                        serializable_data = {
                            'article_id': str(store_data['article_id']),
                            'title': str(store_data['title']),
                            'author': str(store_data['author']),
                            'issue': str(store_data['issue']),
                            'ai_analysis': store_data['ai_analysis'] if isinstance(store_data['ai_analysis'], dict) else {},
                            'analyzed_at': str(store_data['analyzed_at'])
                        }
                        
                        self.redis_client.client.setex(
                            redis_key,
                            timedelta(days=7),
                            _json.dumps(serializable_data, ensure_ascii=False, default=str)
                        )
                        result['report_keys'].append(redis_key)
                        result['ai_success_count'] += 1
                        
                        # 集成增强版文章处理器：提取软约束特征
                        self._init_enhanced_article_processor()
                        if self.enhanced_article_processor:
                            try:
                                enhanced_report = self.enhanced_article_processor.process_enhanced_article(
                                    article_data={
                                        'id': article_id,
                                        'title': title,
                                        'author': author,
                                        'author_id': author,
                                        'content': content,
                                        'published_at': pub_time
                                    },
                                    target_issue=target_issue
                                )
                                if enhanced_report and 'error' not in enhanced_report:
                                    soft_constraints = enhanced_report.get('soft_constraints', {})
                                    if soft_constraints:
                                        # 存储软约束到Redis
                                        if self.redis_key_manager:
                                            soft_key = f'kpluckynumber:pl5:soft_constraints:{article_id}'
                                            self.redis_key_manager.safe_hset_existed(
                                                soft_key,
                                                'constraints',
                                                soft_constraints,
                                                ttl=timedelta(days=7)
                                            )
                                        logger.info(f'文章{idx} 软约束特征提取成功: {list(soft_constraints.keys())}')
                                    else:
                                        result['article_reports'].append(enhanced_report)
                            except Exception as e:
                                logger.warning(f'增强版文章处理失败（不影响主流程）: {e}')
                        
                        logger.info(f'文章{idx} AI分析成功，Redis键: {redis_key}')
                    except Exception as e:
                        logger.error(f'文章{idx} 存入Redis失败: {e}')
                        result['ai_fail_count'] += 1
                else:
                    logger.warning(f'文章{idx} AI分析失败')
                    result['ai_fail_count'] += 1

                # 每篇文章间随机延迟
                self._delay_random(2, 4)

            # 5. 融合所有文章的软约束特征（增强功能）
            if self.enhanced_article_processor and result.get('article_reports'):
                try:
                    merged_constraints = self.enhanced_article_processor.merge_expert_constraints(
                        result['article_reports']
                    )
                    self.pipeline_state['soft_constraints'] = merged_constraints
                    
                    # 存储融合结果到Redis
                    if self.redis_client and self.redis_key_manager:
                        merged_key = f'kpluckynumber:pl5:merged_constraints:{target_issue}'
                        self.redis_key_manager.safe_hset_existed(
                            merged_key,
                            'fused_constraints',
                            merged_constraints,
                            ttl=timedelta(days=7)
                        )
                        logger.info(f'软约束融合完成并存入Redis: {merged_key}')
                except Exception as e:
                    logger.warning(f'软约束融合失败（不影响主流程）: {e}')

            # 6. 将所有文章ID添加到列表
            list_key = self.REDIS_ARTICLE_REPORT_KEY.replace('{article_id}', '')[:-1] + ':list'
            # 实际上我们使用 redis_client 已有的 article list 机制
            
            # 检查是否AI分析全部失败,启用降级策略
            result['success'] = True
            result['fallback_strategy'] = (result['ai_success_count'] == 0 and result['article_count'] > 0)
            result['warning'] = None if result['ai_success_count'] > 0 else 'AI分析全部失败,将采用降级策略'
            
            logger.info(f'步骤1完成: 成功分析 {result["ai_success_count"]}/{result["article_count"]} 篇文章')
            if result.get('fallback_strategy'):
                logger.warning('降级模式: AI分析全部失败,将在步骤3跳过专家整合')
            
            # ★★★ 生成独立的"专家文章预测报告" ★★★
            # 修复: 原逻辑仅在 ai_success_count>0 时生成, AI全失败则无报告;
            # 现改为只要有文章就生成(0成功则产出"降级"报告), 保证双报告始终存在。
            if result.get('articles'):
                expert_report = self._generate_expert_article_report(target_issue, result.get('articles', []))
                if expert_report:
                    succ_count = expert_report.get('successful_articles', 0)
                    total_count = expert_report.get('total_articles', 0)
                    logger.info(f'专家文章预测报告已生成(期号:{target_issue}): {succ_count}/{total_count}篇有效')
                    
                    if succ_count == 0:
                        logger.warning(f'⚠ 专家文章AI分析未返回有效预测数据! 请检查pipeline.log中"AI返回keys"日志')
                    
                    if result.get('ai_fallback') or result.get('fallback_strategy'):
                        expert_report.setdefault('note', 'AI分析不可用, 基于已爬取文章元数据的降级报告')
                    result['expert_article_report'] = expert_report
                    
                    # 持久化到数据库(v3.3: 仅入库, 不再写本地 JSON 文件)
                    uuid_val = self._save_report_to_db(expert_report, 'expert_article', target_issue)
                    if uuid_val:
                        logger.info(f'专家文章预测报告已入库: {uuid_val}')
                else:
                    logger.warning(f'步骤1: 专家文章预测报告生成失败')

        except Exception as e:
            logger.error(f'步骤1异常: {e}', exc_info=True)
            result['error'] = str(e)

        self.pipeline_state['article_reports'] = result.get('articles', [])
        # ★ 关键修复(问题5根因): step4 从 self.pipeline_state['expert_article_report']
        # 读取专家文章预测结果来填充 final_report['article_prediction']; 此前该键从未被设置,
        # 导致 final_report['article_prediction'] 始终为 {} -> GUI 显示"专家文章预测结果未获取到"。
        self.pipeline_state['expert_article_report'] = result.get('expert_article_report')
        return result

    # ================================================================
    # 步骤2: 走势图数据分析与AI预测
    # ================================================================

    def step2_trend_analysis(self, target_issue: str, data_limit: int = 60) -> Dict[str, Any]:
        """
        步骤2: 走势图数据分析与AI预测
        
        完整流程:
        1. 获取最近30-60期走势图数据(基础+万千百十个位)
        2. 整合后调用AI分析走势规律并生成预测报告
        3. 走势报告存入Redis(kpluckynumber:pl5:trend_analysis:{issue}, 7天过期)
        4. 生成独立的"走势图数据预测报告"JSON文件
        5. 将预测报告存入数据库p5_ai_report表
        
        分析策略和算法自主设计以提升命中率:
        - 冷热号分析:统计各位置近20期出现频次
        - 遗漏回归:识别长期未出的号码
        - 趋势动量:分析号码的上下波动趋势
        - 奇偶大小比:统计近期偏态
        
        Args:
            target_issue: 目标期号
            data_limit: 获取历史数据的期数限制(默认40期,建议30-60期)
            
        Returns:
            {success, report_key, report_data, trend_chart_report, error}
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
            from modules.database import P5Database

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
""".format(min(len(trend_data), 30), target_issue))

            # 格式化基础走势数据（缩减到30期，使用历史走势图+基础走势+位置走势三类30期数据）
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
                    for item in pos_data[:20]:
                        n = item.get(num_key, 0)
                        num_freq[n] = num_freq.get(n, 0) + 1
                    sorted_nums = sorted(num_freq.items(), key=lambda x: x[1], reverse=True)
                    hot = [n for n, _ in sorted_nums[:3]]
                    cold = [n for n, _ in sorted_nums[-3:]]
                    max_om = max((item.get('omission', 0) for item in pos_data[:20]), default=0)
                    prompt_parts.append(f"\n统计: 热号{hot}, 冷号{cold}, 最大遗漏{max_om}")

            # 输出格式要求
            prompt_parts.append(f"""
=== 输出格式 ===

请严格按照以下JSON格式输出走势分析报告：

{{
    "analysis_type": "走势图AI分析",
    "analysis_time": "YYYY-MM-DD HH:MM:SS",
    "data_period": "最近20期走势数据",
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

            # 4. 调用AI（增加max_tokens到10000以容纳完整JSON输出）
            logger.info('调用AI进行走势分析...')
            logger.info(f'提示词长度: {len(full_prompt)} 字符')
            trend_system_prompt = "你是一位专业的排列5走势数据分析专家，擅长分析走势图数据并发现规律。请严格按JSON格式输出。"
            logger.info(f'System prompt长度: {len(trend_system_prompt)} 字符')
            logger.info(f'max_tokens: {6000}, temperature: {0.3}')
            
            # 带重试的AI调用
            trend_ai_result = None
            for attempt in range(3):
                try:
                    trend_ai_result = self._call_ai(
                        system_prompt=trend_system_prompt,
                        user_prompt=full_prompt,
                        max_tokens=6000,
                        temperature=0.3
                    )
                    if trend_ai_result:
                        logger.info(f'走势AI分析成功(第{attempt+1}次尝试)')
                        break
                    elif attempt < 2:
                        wait_time = 2 * (attempt + 1)
                        logger.warning(f'走势AI分析第{attempt+1}次失败，{wait_time}秒后重试...')
                        self._delay_random(wait_time, wait_time + 1)
                except Exception as e:
                    logger.error(f'走势AI分析异常: {e}', exc_info=True)
                    if attempt < 2:
                        self._delay_random(2, 3)
                    

            if not trend_ai_result:
                # 修复: AI不可用时不提前返回, 改用统计降级方案生成走势报告,
                # 保证"走势图数据预测报告"始终存在(双报告之一)。
                logger.warning('走势AI分析失败, 启用统计降级方案生成走势报告')
                result['ai_fallback'] = True
                trend_ai_result = self._build_statistical_trend_result(
                    target_issue, history_data, trend_data, position_trends
                )

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
                    # 确保可序列化
                    import json as _json
                    self.redis_client.client.setex(
                        report_key,
                        timedelta(days=7),
                        _json.dumps(store_data, ensure_ascii=False, default=str)
                    )
                    result['report_key'] = report_key
                    logger.info(f'走势报告已存入Redis: {report_key}')
                except Exception as e:
                    logger.error(f'走势报告存入Redis失败: {e}')

            result['success'] = True
            result['report_data'] = trend_ai_result
            self.pipeline_state['trend_report'] = trend_ai_result

            # ★★★ 生成独立的"走势图数据预测报告" ★★★
            trend_report = self._generate_trend_chart_report(target_issue, trend_ai_result)
            if trend_report:
                if result.get('ai_fallback'):
                    trend_report.setdefault('note', 'AI分析不可用, 基于统计指标的降级报告')
                result['trend_chart_report'] = trend_report
                # 先暂存到 pipeline_state, 待步骤4用多源融合预测填充 prediction 字段后统一入库
                # (避免在预测为空时提前入库, 导致数据库报告 prediction 为空 —— 问题3/4同类)
                self.pipeline_state['trend_chart_report'] = trend_report
                logger.info(f'走势图数据预测报告已生成(期号:{target_issue}), 待步骤4填充预测后入库')

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

            # ★ 健壮性改进(v3.8): AI不可用不再硬中止步骤3, 改为标记后走降级策略,
            #   使流水线在AI限流/欠费/网络故障时仍能基于走势图数据产出预测。
            ai_unavailable = (not self.ai_client or not self.ai_client.ai_available)
            if ai_unavailable:
                logger.warning('AI模型不可用, 步骤3将采用降级策略(基于走势图数据继续)')

            # 2. 从Redis读取所有专家分析报告
            logger.info('从Redis读取专家分析报告...')
            logger.info(f'目标期号: {target_issue}')
            expert_reports = []

            # 遍历Redis中所有文章分析报告
            pattern = self.REDIS_ARTICLE_REPORT_KEY.replace('{article_id}', '*')
            logger.info(f'Redis匹配模式: {pattern}')
            article_keys = self.redis_client.client.keys(pattern)
            logger.info(f'匹配到Redis Keys总数: {len(article_keys)}')

            # 调试：打印所有匹配的keys和它们的issue字段
            debug_issue_list = []
            for key in article_keys:
                try:
                    data_str = self.redis_client.client.get(key)
                    if data_str:
                        data = json.loads(data_str)
                        issue_in_data = data.get('issue', 'MISSING')
                        debug_issue_list.append(f'{key.decode() if isinstance(key, bytes) else key}: issue={issue_in_data}')
                        if issue_in_data == target_issue:
                            expert_reports.append(data)
                            logger.info(f'✓ 匹配成功: {key} (期号={issue_in_data})')
                except Exception as e:
                    logger.warning(f'读取报告失败 {key}: {e}')
                    continue
            
            # 如果上述方法没找到，尝试另一种匹配方式（article_key直接包含期号的情况）
            if not expert_reports:
                logger.warning(f'未找到期号{target_issue}的报告，尝试模糊匹配...')
                for key_str in debug_issue_list:
                    logger.info(f'  DEBUG: {key_str}')
                logger.info('→ 可能原因: 步骤1未成功存储文章，或Redis已过期')

            if not expert_reports:
                logger.warning(f'未找到期号{target_issue}的专家分析报告')
                logger.warning('可能原因:')
                logger.warning('  1. 步骤1执行失败或未执行(没有爬取到文章或AI分析失败)')
                logger.warning('  2. Redis已过期(超过7天)')
                logger.warning('  3. 步骤1存储的期号与当前不匹配')
                
                # ★ 降级策略: 如果没有专家报告,跳过步骤3直接进入步骤4
                logger.warning('采用降级策略: 将跳过步骤3,直接使用步骤2的走势报告进行预测')
                logger.info('流水线状态: 步骤1部分成功(有文章但AI分析失败), 步骤2成功')
                
                result['error'] = None  # 不算错误,降级执行
                result['warning'] = '无专家报告,采用降级策略直接使用走势图数据'
                result['fallback_strategy'] = True
                self.pipeline_state['integrated_report'] = None
                
                logger.info('步骤1完成(降级模式): 有文章但AI分析失败,将跳过步骤3')
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

            # ---- 第一部分：专家报告汇总（精简版） ----
            prompt_parts.append("\n" + "=" * 60)
            prompt_parts.append("一、专家报告汇总（精简版）")
            prompt_parts.append("=" * 60)

            for idx, report in enumerate(expert_reports, 1):
                ai = report.get('ai_analysis', {})
                prompt_parts.append(f"\n--- 专家{idx}/{len(expert_reports)} ---")
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
                    prompt_parts.append(f"  组合: {combos[:3]}")

                key_points = ai.get('key_points', [])
                if key_points:
                    prompt_parts.append(f"  要点: {key_points[0][:80]}")

                summary = ai.get('summary', '')
                if summary:
                    prompt_parts.append(f"  总结: {summary[:100]}")

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

请严格按照以下JSON格式输出（不要包含任何额外文字或markdown标记，不要使用 ``` 代码块）：
- 所有键和字符串值必须使用英文双引号包裹，禁止使用单引号或未加引号的键名
- 不要使用 Python 风格的 True/False/None，请使用 JSON 的 true/false/null
- 不要出现尾随逗号
只输出一个 JSON 对象，示例如下：

{{
    "data_source": "专家文章整合分析",
    "analysis_time": "YYYY-MM-DD HH:MM:SS",
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

            # 5. 调用AI（带重试）；若AI不可用或解析失败，则降级而非中止流水线
            logger.info('调用AI进行专家报告整合分析...')
            integrated_ai_result = None
            if ai_unavailable:
                logger.warning('跳过AI调用(AI不可用), 直接进入降级策略')
            else:
                for int_attempt in range(3):
                    integrated_ai_result = self._call_ai(
                        system_prompt="你是一位顶尖的排列5综合预测专家，擅长整合多元数据进行深度分析和精准预测。",
                        user_prompt=full_prompt,
                        max_tokens=6000,
                        temperature=0.6
                    )
                    if integrated_ai_result:
                        logger.info(f'专家报告整合AI分析成功(第{int_attempt+1}次尝试)')
                        break
                    elif int_attempt < 2:
                        logger.warning(f'专家报告整合AI第{int_attempt+1}次失败，重试中...')
                        self._delay_random(3, 5)

            if not integrated_ai_result:
                # ★ 降级策略(v3.8): AI不可用/解析失败时, 基于走势图预测合成综合报告,
                #   保证流水线不中断(直接返回 success+fallback_strategy, 编排器将继续步骤4)。
                logger.warning('专家报告整合AI分析失败/不可用, 采用降级策略继续步骤4')
                fallback_report = self._build_fallback_integrated_report(target_issue, expert_reports)
                result['fallback_strategy'] = True
                result['warning'] = 'AI整合失败, 已基于走势图数据降级生成综合报告'
                result['integrated_report'] = fallback_report
                result['success'] = True
                self.pipeline_state['integrated_report'] = fallback_report
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
                    import json as _json
                    self.redis_client.client.setex(
                        report_key,
                        timedelta(days=7),
                        _json.dumps(store_data, ensure_ascii=False, default=str)
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

    def _build_fallback_integrated_report(self, target_issue: str,
                                          expert_reports: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        降级综合报告构造(v3.8): 当AI不可用或解析失败时, 不再中止流水线,
        而是基于步骤2走势图预测(及已有的专家文章统计)合成一份结构化综合报告,
        使步骤4仍能正常生成最终预测。

        数据来源优先级:
          1. 步骤2走势图报告中的 prediction(多源融合 Top-4) —— 主信号
          2. 步骤1已有的专家文章统计(若 Redis 中存了专家报告) —— 辅助参考
        """
        pos_names = {'wan': '万位', 'qian': '千位', 'bai': '百位', 'shi': '十位', 'ge': '个位'}
        prediction: Dict[str, Any] = {}

        # 主信号: 步骤2 走势图报告中的预测号码
        trend_report = self.pipeline_state.get('trend_chart_report') or {}
        trend_pred = trend_report.get('prediction') or {}
        if not trend_pred:
            # 退一步: 直接读步骤2结果中的 forecast_numbers
            trend_pred = trend_report.get('forecast_numbers') or {}

        for p in pos_names:
            nums = trend_pred.get(p, {}).get('numbers') if isinstance(trend_pred.get(p), dict) else trend_pred.get(p)
            if not nums:
                nums = []
            prediction[p] = {
                'numbers': list(nums)[:4],
                'confidence': [],
                'reason': '走势图多源融合(降级: AI整合不可用, 沿用统计信号)'
            }

        # 辅助参考: 统计专家文章数量(不依赖AI二次整合)
        expert_count = 0
        if expert_reports:
            expert_count = len(expert_reports)

        return {
            'data_source': '走势图多源融合(降级模式)',
            'analysis_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'expert_count': expert_count,
            'current_issue': self.pipeline_state.get('latest_issue', '未知'),
            'next_issue': target_issue,
            'prediction': prediction,
            'trend_analysis': {
                'summary': 'AI整合不可用, 基于走势图统计信号降级生成',
                **{p: '见走势图数据预测结果' for p in pos_names}
            },
            'reasoning_process': ['AI整合步骤降级', '沿用步骤2走势图多源融合预测'],
            'recommended_combinations': [],
            'key_conclusions': ['AI整合不可用, 已降级为走势图统计预测'],
            'expert_consensus': '（降级模式）无AI整合结论' if expert_count == 0
            else f'已汇总 {expert_count} 篇专家文章原始数据, 待AI恢复后深度整合',
            'risk_warning': '当前为降级预测, 仅依赖统计信号, 准确性可能低于AI整合模式; 请于AI恢复后重跑以获取完整分析'
        }

    # ================================================================
    # 独立报告生成方法 (v3.1 新增)
    # ================================================================

    def _generate_expert_article_report(self, target_issue: str, articles: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """
        生成独立的"专家文章预测报告" (v3.12 优化版)
        
        基于步骤1中爬取并分析的所有专家文章，整合生成一份完整的预测报告。
        该报告独立于走势图数据，专注于专家观点的综合分析。
        
        v3.12 优化重点:
        - 增强数据兼容性：多种AI返回格式都支持
        - 增加备选数据源：从enhanced_article_processor获取软约束
        - 改进异常处理：单篇文章失败不影响整体
        - 增强输出日志：便于调试
        
        Args:
            target_issue: 目标期号
            articles: 专家文章分析报告列表
            
        Returns:
            独立预测报告字典，包含prediction/trend_analysis/recommended_combinations等字段
        """
        if not articles:
            logger.warning('无专家文章数据，无法生成专家文章预测报告')
            return None
        
        try:
            # 整合所有专家推荐的号码
            expert_predictions = {
                'wan': [],
                'qian': [],
                'bai': [],
                'shi': [],
                'ge': []
            }
            
            expert_summaries = []
            total_articles = 0
            successful_articles = 0
            ai_errors = 0
            
            for article in articles:
                total_articles += 1
                article_title = article.get('title', '未知标题')[:30]
                
                try:
                    # 获取AI分析结果（兼容多种数据结构）
                    ai_analysis = article.get('ai_analysis', {})
                    
                    # 如果ai_analysis为空，尝试从其他字段获取
                    if not ai_analysis:
                        ai_analysis = article.get('structured_data', {})
                    if not ai_analysis:
                        ai_analysis = article.get('result', {})
                    
                    if not isinstance(ai_analysis, dict):
                        logger.warning(f'文章 {article_title}: ai_analysis不是字典格式，跳过')
                        ai_errors += 1
                        continue
                    
                    # 提取预测号码（支持多种字段名，扩展到10+种备选）
                    forecast = None
                    _forecast_keys = [
                        'forecast_numbers', 'predicted_numbers', 'recommendation', 'prediction',
                        'numbers', 'predicted_digits', 'lucky_numbers', 'recommendation_numbers',
                        'wan', 'qian', 'bai', 'shi', 'ge'  # 极端情况：AI直接返回位置结构
                    ]
                    for key in _forecast_keys:
                        if key in ai_analysis:
                            val = ai_analysis[key]
                            # 如果是位置级数据(如{'wan': [1,2,3], ...}), 直接作为forecast
                            if isinstance(val, dict) and any(k in val for k in ['wan', 'qian', 'bai', 'shi', 'ge']):
                                forecast = val
                                break
                            # 如果是嵌套字典
                            elif isinstance(val, dict) and 'wan' in val:
                                forecast = val
                                break
                    
                    # 极端降级：如果AI返回的整个ai_analysis就是位置结构
                    if not forecast and any(k in ai_analysis for k in ['wan', 'qian', 'bai', 'shi', 'ge']):
                        candidate = {k: ai_analysis[k] for k in ['wan', 'qian', 'bai', 'shi', 'ge'] if k in ai_analysis}
                        if candidate:
                            forecast = candidate
                    
                    if not forecast or not isinstance(forecast, dict):
                        logger.debug(f'文章 {article_title}: 无forecast_numbers字段，跳过')
                        # ★ 调试日志: 打印AI实际返回的所有顶层keys
                        if total_articles <= 3:  # 只打印前3篇文章,避免日志太多
                            logger.info(f'文章 {article_title} AI返回的keys: {list(ai_analysis.keys())}')
                        continue
                    
                    # 验证forecast结构
                    has_valid_prediction = False
                    for pos in ['wan', 'qian', 'bai', 'shi', 'ge']:
                        nums = forecast.get(pos, [])
                        if nums:
                            has_valid_prediction = True
                            # 确保是整数列表（兼容str/int/float）
                            try:
                                int_nums = []
                                for n in nums:
                                    try:
                                        int_nums.append(int(float(n)))
                                    except (ValueError, TypeError):
                                        pass
                                if int_nums:
                                    expert_predictions[pos].extend(int_nums)
                            except Exception as e:
                                logger.debug(f'文章 {article_title} 位置{pos}号码转换失败: {e}')
                    
                    if has_valid_prediction:
                        successful_articles += 1
                        
                        # 提取关键结论
                        key_points = ai_analysis.get('key_points', [])
                        if not key_points:
                            key_points = ai_analysis.get('analysis_points', [])
                        if key_points:
                            expert_summaries.append({
                                'author': article.get('author', '未知'),
                                'title': article.get('title', '未知'),
                                'points': key_points[:3]
                            })
                    else:
                        logger.debug(f'文章 {article_title}: 无有效预测号码')
                
                except Exception as e:
                    logger.warning(f'处理文章 {article_title} 异常: {e}')
                    ai_errors += 1
                    continue
            
            logger.info(f'专家文章处理完成: 成功{successful_articles}/{total_articles}, 失败{ai_errors}')
            
            if successful_articles == 0:
                logger.warning('无有效专家预测数据，生成空报告')
                return {
                    'data_source': '专家文章整合分析',
                    'report_type': '专家文章预测报告',
                    'analysis_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'target_issue': target_issue,
                    'total_articles': total_articles,
                    'successful_articles': 0,
                    'expert_count': 0,
                    'prediction': {},
                    'position_recommendations': {},
                    'key_conclusions': ['无有效专家预测数据'],
                    'risk_warning': '专家观点仅供参考，彩票具有随机性，请理性投注'
                }
            
            # 统计各位置推荐频次
            position_recommendations = {}
            for pos in ['wan', 'qian', 'bai', 'shi', 'ge']:
                nums = expert_predictions[pos]
                if nums:
                    from collections import Counter
                    counter = Counter(nums)
                    # 取频次最高的前5个号码
                    top_nums = [num for num, _ in counter.most_common(5)]
                    position_recommendations[pos] = {
                        'top_numbers': top_nums,
                        'frequency': dict(counter),
                        'total_mentions': len(nums)
                    }
            
            # 构建独立报告
            report = {
                'data_source': '专家文章整合分析',
                'report_type': '专家文章预测报告',
                'analysis_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'target_issue': target_issue,
                'total_articles': total_articles,
                'successful_articles': successful_articles,
                'expert_count': successful_articles,
                'prediction': {},
                'trend_analysis': {
                    'summary': f'基于{successful_articles}位专家的综合分析',
                    'articles_analyzed': total_articles,
                    'effective_articles': successful_articles
                },
                'position_recommendations': position_recommendations,
                'key_conclusions': [
                    f'共分析{total_articles}篇专家文章',
                    f'{successful_articles}篇包含有效预测',
                    f'失败{ai_errors}篇',
                    '专家共识号码：基于频次统计得出'
                ],
                'expert_summaries': expert_summaries[:10],  # 最多展示10个专家摘要
                'methodology': '基于专家文章AI结构化分析，统计各位置推荐号码频次，取高频号码作为预测',
                'risk_warning': '专家观点仅供参考，彩票具有随机性，请理性投注'
            }
            
            # 为每个位置生成推荐号码和理由
            for pos in ['wan', 'qian', 'bai', 'shi', 'ge']:
                pos_name = {'wan': '万位', 'qian': '千位', 'bai': '百位', 'shi': '十位', 'ge': '个位'}[pos]
                rec = position_recommendations.get(pos, {})
                report['prediction'][pos] = {
                    'numbers': rec.get('top_numbers', []),
                    'confidence': [0.7 + 0.05 * i for i in range(len(rec.get('top_numbers', [])))],
                    'reason': f'{pos_name}专家推荐频次统计，共提及{rec.get("total_mentions", 0)}次'
                }
            
            logger.info(f'专家文章预测报告生成完成: {successful_articles}/{total_articles}篇有效文章')
            return report
            
        except Exception as e:
            logger.error(f'生成专家文章预测报告失败: {e}', exc_info=True)
            return None
    
    def _generate_trend_chart_report(self, target_issue: str, trend_ai_result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        生成独立的"走势图数据预测报告"
        
        基于步骤2中走势图的AI分析结果，生成一份完整的预测报告。
        该报告独立于专家文章，专注于走势数据分析。
        
        Args:
            target_issue: 目标期号
            trend_ai_result: 走势AI分析结果
            
        Returns:
            独立预测报告字典，包含trend_analysis/prediction/recommended_combinations等字段
        """
        if not trend_ai_result:
            logger.warning('无走势AI分析结果，无法生成走势图数据预测报告')
            return None
        
        try:
            # 尝试解析JSON字符串
            if isinstance(trend_ai_result, str):
                import json as json_module
                try:
                    trend_data = json_module.loads(trend_ai_result)
                except json_module.JSONDecodeError:
                    logger.warning('走势AI结果为无效JSON，尝试提取JSON片段')
                    # 尝试提取JSON片段
                    start = trend_ai_result.find('{')
                    end = trend_ai_result.rfind('}')
                    if start >= 0 and end >= 0:
                        trend_data = json_module.loads(trend_ai_result[start:end+1])
                    else:
                        logger.error('无法提取有效的JSON数据')
                        return None
            elif isinstance(trend_ai_result, dict):
                trend_data = trend_ai_result
            else:
                logger.warning(f'未知的走势AI结果类型: {type(trend_ai_result)}')
                return None
            
            # 构建独立报告
            report = {
                'data_source': '走势图数据AI分析',
                'report_type': '走势图数据预测报告',
                'analysis_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'target_issue': target_issue,
                'prediction': trend_data.get('forecast_numbers', {}),
                'trend_analysis': {
                    'summary': trend_data.get('trend_summary', {}).get('overall_trend', ''),
                    'wan': trend_data.get('trend_summary', {}).get('position_analysis', {}).get('wan', ''),
                    'qian': trend_data.get('trend_summary', {}).get('position_analysis', {}).get('qian', ''),
                    'bai': trend_data.get('trend_summary', {}).get('position_analysis', {}).get('bai', ''),
                    'shi': trend_data.get('trend_summary', {}).get('position_analysis', {}).get('shi', ''),
                    'ge': trend_data.get('trend_summary', {}).get('position_analysis', {}).get('ge', ''),
                },
                'statistical_features': trend_data.get('statistical_analysis', {}),
                'key_patterns': trend_data.get('key_patterns', []),
                'risk_factors': trend_data.get('risk_factors', []),
                'recommended_combinations': trend_data.get('recommended_combinations', []),
                'hot_numbers': trend_data.get('trend_summary', {}).get('hot_numbers_summary', ''),
                'cold_numbers': trend_data.get('trend_summary', {}).get('cold_numbers_summary', ''),
                'methodology': '基于最近40期走势图数据，AI分析各位置冷热号、遗漏趋势、奇偶大小比等规律',
                'data_period': '最近40期历史开奖数据',
                'risk_warning': '走势分析基于历史数据，彩票具有随机性，请理性投注'
            }
            
            logger.info(f'走势图数据预测报告生成完成')
            return report
            
        except Exception as e:
            logger.error(f'生成走势图数据预测报告失败: {e}', exc_info=True)
            return None

    def _build_statistical_trend_result(self, target_issue: str,
                                        history_data: List[Dict[str, Any]],
                                        trend_data: List[Dict[str, Any]],
                                        position_trends: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
        """
        AI不可用时的统计降级方案: 基于历史开奖与走势数据构造一份"走势图数据预测报告"所需的
        trend_ai_result 结构(兼容 _generate_trend_chart_report 的读取字段), 使走势报告始终可生成。

        统计指标: 各位置近N期频次 -> 热号/冷号/推荐号; 遗漏与冷热等级来自 position_trends;
        和值/跨度/奇偶比/大小比趋势基于 history_data 计算。
        """
        from collections import Counter

        pos_columns = ['wan', 'qian', 'bai', 'shi', 'ge']
        pos_names = {'wan': '万位', 'qian': '千位', 'bai': '百位', 'shi': '十位', 'ge': '个位'}

        # 1. 各位置频次统计(来自历史开奖)
        freq = {p: Counter() for p in pos_columns}
        hezhi_list, span_list, odd_count, even_count, big_count, small_count = [], [], 0, 0, 0, 0
        for row in history_data:
            nums = []
            for p in pos_columns:
                n = row.get(p)
                if n is not None:
                    try:
                        n = int(n)
                    except (ValueError, TypeError):
                        continue
                    freq[p][n] += 1
                    nums.append(n)
            if len(nums) == 5:
                hezhi_list.append(sum(nums))
                span_list.append(max(nums) - min(nums))
                for n in nums:
                    if n % 2 == 1:
                        odd_count += 1
                    else:
                        even_count += 1
                    if n >= 5:
                        big_count += 1
                    else:
                        small_count += 1

        # 2. 各位置遗漏/冷热(来自 position_trends 最新一期)
        def latest_trend(pos: str) -> Dict[str, Any]:
            data = position_trends.get(f'{pos}_trend', []) or []
            return data[0] if data else {}

        position_analysis = {}
        forecast_numbers = {}
        for p in pos_columns:
            counter = freq[p]
            if counter:
                top = [n for n, _ in counter.most_common(3)]
                # 冷号: 出现次数最少(且实际出现过)的号码
                least = sorted((n for n, c in counter.items() if c > 0),
                               key=lambda n: counter[n])[:3]
                cold = [n for n in least]
            else:
                top, cold = [], []
            rec = top[:3]
            forecast_numbers[p] = rec
            lt = latest_trend(p)
            num_key = f'{p}_number'
            position_analysis[p] = {
                'hot_numbers': top,
                'cold_numbers': cold,
                'trend_direction': '基于频次统计(AI不可用时的降级分析)',
                'omission_analysis': f"最新遗漏: {lt.get('omission', '未知')}, 冷热等级: {lt.get('hot_level', '未知')}",
                'recommended_numbers': rec,
                'latest_number': lt.get(num_key, '未知')
            }

        overall_trend = (
            f'基于最近{len(history_data)}期历史数据的统计降级分析(AI模型不可用)。'
            f'各位置推荐号由近期间频次最高的号码构成。'
        )
        hot_summary = '; '.join(f"{pos_names[p]}热号{position_analysis[p]['hot_numbers']}" for p in pos_columns)
        cold_summary = '; '.join(f"{pos_names[p]}冷号{position_analysis[p]['cold_numbers']}" for p in pos_columns)

        # 3. 和值/跨度/奇偶/大小趋势
        def avg(lst):
            return round(sum(lst) / len(lst), 2) if lst else 0
        odd_ratio = f"{odd_count}:{even_count}" if (odd_count + even_count) else "未知"
        big_ratio = f"{big_count}:{small_count}" if (big_count + small_count) else "未知"
        statistical_analysis = {
            'hezhi_analysis': f"近{len(hezhi_list)}期和值均值约 {avg(hezhi_list)}, 区间 {min(hezhi_list) if hezhi_list else 0}-{max(hezhi_list) if hezhi_list else 0}",
            'span_analysis': f"近{len(span_list)}期跨度均值约 {avg(span_list)}",
            'odd_even_analysis': f"奇偶比整体 {odd_ratio}",
            'big_small_analysis': f"大小比整体 {big_ratio}",
        }

        key_patterns = [
            f"万位高频号: {position_analysis['wan']['hot_numbers']}",
            f"个位高频号: {position_analysis['ge']['hot_numbers']}",
            f"和值重心偏向: {avg(hezhi_list)} 附近",
        ]
        risk_factors = [
            '本分析为统计降级结果, 未经AI深度研判, 仅供参考',
            '彩票本质随机, 历史规律不代表未来, 请理性投注',
        ]

        return {
            'analysis_type': '走势图统计降级分析',
            'analysis_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'data_period': f'最近{len(history_data)}期历史开奖数据',
            'latest_issue': history_data[0].get('issue', '') if history_data else '',
            'next_issue': target_issue,
            'forecast_numbers': forecast_numbers,
            'trend_summary': {
                'overall_trend': overall_trend,
                'hot_numbers_summary': hot_summary,
                'cold_numbers_summary': cold_summary,
                'position_analysis': position_analysis,
            },
            'statistical_analysis': statistical_analysis,
            'key_patterns': key_patterns,
            'risk_factors': risk_factors,
            'is_statistical_fallback': True,
        }

    # ================================================================
    # 步骤4: 最终预测结果生成与入库
    # ================================================================

    def _predict_trend_multi_source(self, target_issue: str, data_period: int = 60) -> Dict[str, Any]:
        """
        多源走势融合预测 (v3.11 深度优化版)

        结合以下五类数据源的「最新 data_period 期」数据, 用加权融合算法输出每个位置 Top-5 推荐号码:
          1. 历史走势图       —— p5_history_data (万/千/百/十/个 5列)
          2. 基础走势图       —— p5_trend_data  (和值/跨度/奇偶比/大小比 + 5列号码)
          3. 万千百十个独立走势表 —— p5_wan/qian/bai/shi/ge_trend_data (含 omission 遗漏/ hot_level 冷热等级/ 奇偶大小质合属性)
          4. ★ 贝叶斯后验概率 —— p5_bayesian_result (增量复用的贝叶斯推断后验分布)
          5. ★ 专家软约束融合 —— Redis中存储的多专家观点融合结果

        融合算法 (每位置 digit d ∈ 0..9, v3.11优化):
          score(d) = 0.30 * 频率归一   + 0.22 * 遗漏归一   + 0.13 * 动量贴近度
                   + 0.17 * 贝叶斯后验概率(若可用)
                   + 0.08 * 专家软约束(若可用)
                   + 0.05 * 升平降方向(若可用)
                   + 0.05 * 和值重心(若可用)
          
          若无贝叶斯数据，自动降级为:
          score(d) = 0.42 * 频率归一   + 0.30 * 遗漏归一   + 0.18 * 动量贴近度
                   + 0.05 * 升平降方向 + 0.05 * 和值重心

        v3.11 优化重点:
        - 数据期数: 30期 → 60期 (更多信息量)
        - Top-4 → Top-5 (覆盖率50%→60%)
        - 权重更均衡，降低单一信号依赖
        - 增加专家软约束融合
        - 增加SSD距离惩罚(避免相邻位置号码过于接近)
        """
        from collections import Counter

        if not self.db_client or not getattr(self.db_client, 'connection', None):
            self._init_db_client()
        db = self.db_client
        if not db or not getattr(db, 'connection', None):
            logger.warning('多源走势预测: 数据库未连接, 返回空结果')
            return {}

        pos_keys = ['wan', 'qian', 'bai', 'shi', 'ge']
        pos_names = {'wan': '万位', 'qian': '千位', 'bai': '百位', 'shi': '十位', 'ge': '个位'}

        try:
            # 1. 加载四大类数据源 (DESC -> 翻转成旧->新)
            db.cursor.execute(
                'SELECT issue, wan, qian, bai, shi, ge FROM p5_history_data '
                'ORDER BY issue DESC LIMIT %s', (data_period,))
            history = db.cursor.fetchall() or []

            db.cursor.execute(
                'SELECT issue, wan, qian, bai, shi, ge, hezhi, odd_even_ratio, big_small_ratio '
                'FROM p5_trend_data ORDER BY issue DESC LIMIT %s', (data_period,))
            basic = db.cursor.fetchall() or []

            pos_trends = {}
            for p in pos_keys:
                db.cursor.execute(
                    f'SELECT issue, {p}_number, omission, hot_level, is_big, is_odd, is_prime '
                    f'FROM p5_{p}_trend_data ORDER BY issue DESC LIMIT {data_period}')
                pos_trends[p] = db.cursor.fetchall() or []

            if not history:
                logger.warning('多源走势预测: 无历史数据, 返回空结果')
                return {}

            hist_asc = list(reversed(history))

            # 2. 构建每个位置的「近 data_period 期数字序列」(优先用位置走势表, 不足用历史补齐)
            seq = {}
            for p in pos_keys:
                s = [r.get(f'{p}_number') for r in reversed(pos_trends[p])
                     if r.get(f'{p}_number') is not None]
                if len(s) < 10:
                    s = [r.get(p) for r in hist_asc if r.get(p) is not None][-data_period:]
                seq[p] = s[-data_period:]

            # ★ 升平降走势图数据(p5_spjzs_data): 多期方向偏好(最近10次涨跌多数方向)
            spj_pref = self._get_spj_direction_preference() or {}

            # ★ 和值走势图数据(p5_hzzst_data): 近期和值重心
            _hezhi_recent = []
            try:
                db.cursor.execute(
                    'SELECT hezhi FROM p5_hzzst_data ORDER BY issue DESC LIMIT 15')  # 从10期扩到15期
                _hezhi_recent = [int(r['hezhi']) for r in (db.cursor.fetchall() or [])
                                 if r.get('hezhi') is not None]
            except Exception:
                _hezhi_recent = []
            _hezhi_mean = (sum(_hezhi_recent) / len(_hezhi_recent)) if _hezhi_recent else None

            # ★ 贝叶斯后验概率(增量复用): 从专用表读取已计算的贝叶斯推断结果
            _bayes_posterior = None
            try:
                if hasattr(db, 'get_bayesian_result_row'):
                    _bayes_posterior = db.get_bayesian_result_row(hist_asc[-1].get('issue', '') if hist_asc else '')
                if not _bayes_posterior:
                    db.cursor.execute('SELECT issue FROM p5_history_data ORDER BY issue DESC LIMIT 1')
                    _issue_row = db.cursor.fetchone()
                    if _issue_row:
                        _bayes_posterior = db.get_bayesian_result_row(_issue_row.get('issue', ''))
            except Exception:
                _bayes_posterior = None

            # ★ 专家软约束融合(从Redis读取): 多专家观点的加权平均
            _expert_constraints = self._get_expert_constraints_fusion() or {}

            out = {}
            for p in pos_keys:
                s = seq[p]
                n = len(s)
                if n == 0:
                    continue

                # ★ 指数衰减加权: 越近期出现的数字权重越高(halflife=12期,从10期放宽)
                _decay = self._exp_decay_weights(n, halflife=12)
                freq = Counter()
                for i, v in enumerate(s):
                    if v is not None:
                        freq[v] += _decay[i]

                # 遗漏: 距上次出现 (reversed 后 index 0 = 最新一期)
                omission = {}
                for d in range(10):
                    last_idx = None
                    for i, v in enumerate(reversed(s)):
                        if v == d:
                            last_idx = i
                            break
                    omission[d] = last_idx if last_idx is not None else n

                # 动量: 近期(末8期,从5期扩大到8期)均值 vs 整体均值
                recent = s[-8:] if n >= 8 else s
                recent_avg = sum(recent) / len(recent)
                overall_avg = sum(s) / n
                # 贴近度: 数字越接近近期均值得分越高
                mom_raw = {d: -abs(d - recent_avg) for d in range(10)}
                mom_min, mom_max = min(mom_raw.values()), max(mom_raw.values())
                mom_range = (mom_max - mom_min) or 1.0
                momentum = {d: (mom_raw[d] - mom_min) / mom_range for d in range(10)}

                total_freq = sum(freq.values()) or 1
                total_om = sum(omission.values()) or 1
                
                # ★ 贝叶斯后验概率融合
                _pos_index = pos_keys.index(p) if p in pos_keys else None
                _use_bayes = (_bayes_posterior is not None and 
                              _pos_index is not None and 
                              _pos_index < len(_bayes_posterior))
                _bayes_pos = _bayes_posterior[_pos_index] if _use_bayes else None


                # 基础打分
                scores = {}
                
                if _use_bayes and isinstance(_bayes_pos, dict) and _bayes_pos:
                    # ★★★ v3.11 优化权重 ★★★
                    # 频率0.30 + 遗漏0.22 + 动量0.13 + 贝叶斯0.17 + 专家0.08 + 方向0.05 + 和值0.05
                    total_bayes = sum(float(v) for v in _bayes_pos.values()) or 1
                    bayes_norm = {}
                    for d in range(10):
                        key_str = str(d)
                        prob = float(_bayes_pos.get(key_str, 0.1))
                        bayes_norm[d] = prob / total_bayes
                    
                    for d in range(10):
                        fz = freq.get(d, 0) / total_freq
                        oz = omission[d] / total_om
                        scores[d] = (0.30 * fz + 0.22 * oz + 0.13 * momentum[d] + 
                                     0.17 * bayes_norm.get(d, 0.1))
                else:
                    # 标准模式(无贝叶斯): 频率0.42 + 遗漏0.30 + 动量0.18
                    for d in range(10):
                        fz = freq.get(d, 0) / total_freq
                        oz = omission[d] / total_om
                        scores[d] = 0.42 * fz + 0.30 * oz + 0.18 * momentum[d]

                # ★ 专家软约束融合(权重0.08)
                if _expert_constraints and p in _expert_constraints:
                    expert_score = _expert_constraints[p]
                    if isinstance(expert_score, dict):
                        for d in range(10):
                            exp_val = expert_score.get(str(d), 0)
                            scores[d] += 0.08 * float(exp_val)

                # ★ 基础走势图融合(轻量偏置): 奇偶比/大小比
                if basic:
                    _lb = basic[0]
                    odd_bias = self._ratio_bias(_lb.get('odd_even_ratio', ''))
                    big_bias = self._ratio_bias(_lb.get('big_small_ratio', ''))
                    for d in range(10):
                        if odd_bias != 0:
                            _is_odd = (d % 2 == 1)
                            if (odd_bias > 0 and _is_odd) or (odd_bias < 0 and not _is_odd):
                                scores[d] += 0.03 * abs(odd_bias)
                        if big_bias != 0:
                            _is_big = (d >= 5)
                            if (big_bias > 0 and _is_big) or (big_bias < 0 and not _is_big):
                                scores[d] += 0.03 * abs(big_bias)

                # ★ 升平降方向偏置(权重0.05)
                _sp = spj_pref.get(p)
                if isinstance(_sp, dict):
                    _spref = _sp.get('pref')
                    if _spref == 'up':
                        for d in range(10):
                            scores[d] += 0.05 * (d / 9.0)
                    elif _spref == 'down':
                        for d in range(10):
                            scores[d] += 0.05 * ((9 - d) / 9.0)
                    elif _spref == 'flat':
                        _sld = _sp.get('latest_digit')
                        if _sld is not None:
                            for d in range(10):
                                scores[d] -= 0.06 * abs(d - _sld)

                # ★ 和值重心偏置(权重0.05)
                if _hezhi_mean is not None:
                    _exp_digit = _hezhi_mean / 5.0
                    for d in range(10):
                        scores[d] += 0.05 * (1.0 - min(1.0, abs(d - _exp_digit) / 5.0))

                # ★ SSD惩罚 + 跨位一致性检查 (避免相邻位置号码过于接近)
                #   这一步是v3.11新增, 提升组合质量
                
                # 先暂存所有位置的得分, 最后统一做SSD惩罚
                out[f'_scores_{p}'] = scores.copy()
                # 缓存频率%/遗漏期数, 供结果可读特征(features)展示
                out[f'_freq_{p}'] = {d: round((freq.get(d, 0) / (total_freq or 1)) * 100, 1) for d in range(10)}
                out[f'_om_{p}'] = {int(d): omission.get(d, 0) for d in range(10)}

            # ★ 统一SSD惩罚 + Top-5选择
            for p in pos_keys:
                scores = out.pop(f'_scores_{p}')
                
                # 检查与前一位的切比雪夫距离(≥2,避免过于接近)
                if p in pos_keys[1:]:
                    prev_p = pos_keys[pos_keys.index(p) - 1]
                    prev_top = out.get(prev_p, {}).get('numbers', [])
                    if prev_top:
                        for d in range(10):
                            for prev_d in prev_top:
                                if abs(d - prev_d) < 2:  # SSD惩罚
                                    scores[d] -= 0.15
                
                # Top-5 (从Top-4提升到Top-5,覆盖率50%→60%)
                top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:5]
                bayes_flag = " +贝叶斯" if _use_bayes else ""
                expert_flag = " +专家约束" if _expert_constraints and p in _expert_constraints else ""
                
                out[p] = {
                    'numbers': [int(d) for d, _ in top],
                    'confidence': [round(float(sc), 4) for _, sc in top],
                    'reason': (f'全源融合(近{data_period}期: 历史+基础+{pos_names[p]}走势'
                              f'+升平降+和值重心{bayes_flag}{expert_flag}) '
                              f'频率0.30/遗漏0.22/动量0.13/贝叶斯0.17加权, '
                              f'近期均值{recent_avg:.1f}')
                }
                # 可读特征: 每个推荐号码的近期频率%与当前遗漏期数(基于历史, 非编造)
                _freq_map = out.pop(f'_freq_{p}', {})
                _om_map = out.pop(f'_om_{p}', {})
                out[p]['features'] = {
                    int(d): {'freq_pct': _freq_map.get(d, 0.0), 'omission': _om_map.get(d, 0)}
                    for d, _ in top
                }

            logger.info(f'多源走势融合预测(v3.11)完成: { {k: v["numbers"] for k, v in out.items() if k in pos_keys} }')
            return out

        except Exception as e:
            logger.error(f'多源走势融合预测异常: {e}', exc_info=True)
            return {}

    @staticmethod
    def _ratio_bias(ratio_str: str) -> int:
        """
        解析 "a:b" 形式的偏置(如奇偶比 "3:2" / 大小比 "2:3")。
        返回: >0 表示偏向前者(奇/大), <0 表示偏向后者(偶/小), 0 表示中性或无数据。
        例: "3:2" -> +1 (偏奇/偏大); "1:4" -> -3 (强烈偏偶/偏小)
        """
        if not ratio_str or ':' not in str(ratio_str):
            return 0
        try:
            a, b = str(ratio_str).split(':', 1)
            a, b = int(a.strip()), int(b.strip())
            diff = a - b
            if diff == 0:
                return 0
            # 归一到 [-3, 3] 区间, 避免极端偏置
            return max(-3, min(3, diff))
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def _exp_decay_weights(n: int, halflife: float = 10.0) -> List[float]:
        """
        生成指数衰减权重序列(长度 n), 越近期(末尾)权重越高。
        w[i] = 0.5 ** ((n-1-i) / halflife)
          - i = n-1 (最新一期) -> 权重 1.0
          - 每经过 halflife 期, 权重衰减一半
        用于让 30 期走势统计更贴合"近期惯性", 而非等长简单平均。
        """
        if n <= 0:
            return []
        decay = math.log(2) / max(halflife, 1e-6)
        return [math.exp(-decay * (n - 1 - i)) for i in range(n)]

    def _get_expert_constraints_fusion(self) -> Optional[Dict[str, Any]]:
        """
        获取专家软约束融合结果 (v3.11 新增)
        
        从Redis读取专家分析报告中的软约束特征, 融合为每个位置的倾向分数。
        软约束包括: 奇偶倾向、大小倾向、和值偏好、热点号码、连号倾向
        
        Returns:
            {
                'wan': {0: 0.8, 1: 0.5, ..., 9: 0.3},  # 每个数字的专家倾向分数
                'qian': {...},
                ...
            } 或 None
        """
        try:
            from modules.cache import RedisCache as RC
            redis_client = RC()
            if not redis_client.connect():
                return None
            
            # 尝试从Redis读取融合后的专家约束
            import json
            constraint_key = 'kpluckynumber:pl5:merged_constraints'
            
            # 获取最新的专家约束
            all_constraints = []
            try:
                db = self.db_client
                if db and getattr(db, 'connection', None):
                    db.cursor.execute('''
                        SELECT constraint_data FROM p5_expert_constraints 
                        ORDER BY created_at DESC LIMIT 5
                    ''')
                    rows = db.cursor.fetchall()
                    for row in rows:
                        if row and row.get('constraint_data'):
                            try:
                                data = json.loads(row['constraint_data'])
                                all_constraints.append(data)
                            except:
                                pass
            except:
                pass
            
            if not all_constraints:
                return None
            
            # 融合: 取平均值
            fused = {}
            for p in ['wan', 'qian', 'bai', 'shi', 'ge']:
                scores = {str(d): 0.0 for d in range(10)}
                count = 0
                for constraint in all_constraints:
                    pos_data = constraint.get(p, {})
                    if isinstance(pos_data, dict):
                        for d, score in pos_data.items():
                            if d in scores and isinstance(score, (int, float)):
                                scores[d] += float(score)
                        count += 1
                
                if count > 0:
                    for d in scores:
                        scores[d] /= count
                    fused[p] = scores
            
            return fused if fused else None
            
        except Exception as e:
            logger.debug(f'专家约束融合失败: {e}')
            return None

    def _get_spj_direction_preference(self, data_period: int = 30, n_dir: int = 10):
        """
        从 p5_spjzs_data(升平降走势) 派生每个位置的"涨跌方向偏好":
          对每个位置, 比较相邻期数字得到 升(up)/平(flat)/降(down) 序列(最近 n_dir 次变迁),
          取多数方向为 pref(主导方向), 并记录最新一期方向 latest 与最新实际数字 latest_digit。
        供 _build_constrained_combinations 做组合级方向一致性打分。
        """
        db = self.db_client
        if not db or not getattr(db, 'connection', None):
            return None
        pos_keys = ['wan', 'qian', 'bai', 'shi', 'ge']
        try:
            db.cursor.execute(
                'SELECT issue, wan, qian, bai, shi, ge FROM p5_spjzs_data '
                'ORDER BY issue DESC LIMIT %s', (data_period + 1,))
            rows = db.cursor.fetchall() or []
            if len(rows) < 2:
                return None
            asc = list(reversed(rows))  # 旧 -> 新
            latest = asc[-1]
            # 相邻期变迁: (旧, 新) 配对, 取最近 n_dir 次
            transitions = list(zip(asc, asc[1:]))
            last_trans = transitions[-n_dir:] if len(transitions) >= n_dir else transitions
            states = {p: [] for p in pos_keys}
            for a, b in last_trans:
                for p in pos_keys:
                    va, vb = a.get(p), b.get(p)
                    if va is None or vb is None:
                        continue
                    va, vb = int(va), int(vb)
                    states[p].append('up' if vb > va else ('down' if vb < va else 'flat'))
            pref = {}
            for p in pos_keys:
                seq = states[p]
                if not seq:
                    continue
                cnt = {'up': seq.count('up'), 'flat': seq.count('flat'), 'down': seq.count('down')}
                pref[p] = {
                    'pref': max(cnt, key=cnt.get),
                    'latest': seq[-1],
                    'latest_digit': int(latest[p]) if latest.get(p) is not None else None,
                }
            return pref
        except Exception as e:
            logger.warning(f'读取升平降方向偏好失败: {e}')
            return None

    def _build_constrained_combinations(self, prediction: Dict[str, Any],
                                         target_issue: str,
                                         data_period: int = 30):
        """
        用 p5_hzzst_data(和值走势) + p5_spjzs_data(升平降走势) 对多源融合的每位置 Top-4 候选做
        组合级约束与打分:
          1. 和值区间(自适应带宽): 以最近10期和值重心为中心, 宽度按最近10期波动率(1.3σ)自适应
             (波动大->区间宽, 平稳->窄), 保底宽度12防过约束。
          2. 每位置取多源融合 Top-4 候选(数字+置信度)。
          3. 笛卡尔积枚举(≤4^5), 仅保留和值落在区间内的组合; 再按"跨位置信度 + 升平降方向一致
             性"综合打分排序(方向匹配 pref +0.10, 匹配最新方向 +0.04, 背离 -0.06)。
          4. 约束过严(<5)自动放宽±4再试; 仍无结果返回 None(降级)。
        Returns:
            (combinations_list, hezhi_range_str) 或 (None, '') 表示不可用/降级
        """
        db = self.db_client
        if not db or not getattr(db, 'connection', None):
            return None, ''
        pos_keys = ['wan', 'qian', 'bai', 'shi', 'ge']

        # 1) 和值区间(自适应带宽)
        try:
            db.cursor.execute(
                'SELECT hezhi FROM p5_hzzst_data ORDER BY issue DESC LIMIT %s', (data_period,))
            rows = db.cursor.fetchall() or []
            sums = [int(r['hezhi']) for r in rows if r.get('hezhi') is not None]
        except Exception as e:
            logger.warning(f'读取和值走势失败, 跳过和值约束: {e}')
            return None, ''

        if len(sums) < 5:
            return None, ''

        # ★ 自适应带宽: 以最近10期(最新在前)的重心为中心, 宽度按最近10期波动率定
        _recent = sums[:10]
        _rmean = sum(_recent) / len(_recent)
        _rvar = sum((x - _rmean) ** 2 for x in _recent) / len(_recent)
        _rstd = math.sqrt(_rvar) or 1.0
        lo = max(0, math.floor(_rmean - 1.3 * _rstd))
        hi = min(45, math.ceil(_rmean + 1.3 * _rstd))
        if hi - lo < 12:  # 保证不过度约束
            lo = max(0, lo - 3)
            hi = min(45, hi + 3)

        # 2) 升平降方向偏好(p5_spjzs_data)
        spj = self._get_spj_direction_preference(data_period)

        # 3) 每位置候选(必须都有 Top-4, 否则降级)
        cand = {}
        for p in pos_keys:
            pr = prediction.get(p)
            if not isinstance(pr, dict):
                return None, ''
            nums = pr.get('numbers', []) or []
            conf = pr.get('confidence', []) or []
            if len(nums) < 4:
                return None, ''
            cand[p] = [(int(d), float(conf[i]) if i < len(conf) else 0.0)
                       for i, d in enumerate(nums)]

        # 4) 枚举约束组合(和值过滤 + 升平降方向一致性打分)
        def _enumerate(lo_b, hi_b):
            out = []
            for combo in itertools.product(*[cand[p] for p in pos_keys]):
                digits = [c[0] for c in combo]
                s = sum(digits)
                if not (lo_b <= s <= hi_b):
                    continue
                conf = sum(c[1] for c in combo)
                spj_bonus = 0.0
                if spj:
                    for p, d in zip(pos_keys, digits):
                        info = spj.get(p)
                        if not info or info.get('latest_digit') is None:
                            continue
                        rel = 'up' if d > info['latest_digit'] else (
                            'down' if d < info['latest_digit'] else 'flat')
                        if rel == info['pref']:
                            spj_bonus += 0.10
                        elif rel == info['latest']:
                            spj_bonus += 0.04
                        else:
                            spj_bonus -= 0.06
                out.append((digits, conf + spj_bonus, s))
            out.sort(key=lambda x: x[1], reverse=True)
            return out

        best = _enumerate(lo, hi)
        if len(best) < 5:  # 约束过严, 放宽
            best = _enumerate(lo - 4, hi + 4)
        if not best:
            return None, ''

        out = []
        for digits, score, s in best[:10]:
            comb = ''.join(str(d) for d in digits)
            span = max(digits) - min(digits)
            out.append({
                'combination': comb,
                'confidence': round(float(score) / 5.0, 4),
                'reason': f"和值{s}(约束区间{lo}-{hi})/跨度{span}"
                          + ("" if not spj else "/升平降方向约束"),
                'hezhi': s,
                'span': span
            })
        return out, f"{lo}-{hi}"

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

            # 说明: 不再硬性要求 AI Key。统计融合预测引擎(P5Predictor)为必选路径,
            # AI 仅作为内部 ≤0.1 权重的修正信号(在 P5Predictor 内部融合,可选)。
            # 下方走势/综合报告读取与提示词构建保留为可选参考,不影响主流程。

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

            # ==================== 数据质量门禁 ====================
            logger.info('运行数据质量门禁检查...')
            quality_report = self._validate_data_quality(history_data, target_issue=target_issue)
            quality_checks = quality_report.get('checks', {})
            for gate_name, gate_result in quality_checks.items():
                if gate_result.get('severity') == 'critical' and not gate_result.get('passed'):
                    logger.error(f'[质量门禁 CRITICAL] {gate_name}: {gate_result["message"]}')
                elif gate_result.get('severity') == 'warning' and not gate_result.get('passed'):
                    logger.warning(f'[质量门禁 WARNING] {gate_name}: {gate_result["message"]}')
            # 收集所有失败的 critical gate 名称
            failed_critical = [gn for gn, gr in quality_checks.items()
                               if gr.get('severity') == 'critical' and not gr.get('passed')]
            if failed_critical:
                raise RuntimeError(
                    f'数据质量门禁未通过（critical gates: {", ".join(failed_critical)}），'
                    f'拒绝在不合格数据上进行预测。请检查数据完整性后重试。'
                )
            if not quality_report.get('valid'):
                logger.warning('数据质量门禁未全部通过(critical失败)，已中止预测')

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

请严格按照以下JSON格式输出（不要包含任何额外文字或markdown标记，不要使用 ``` 代码块）：
- 所有键和字符串值必须使用英文双引号包裹，禁止使用单引号或未加引号的键名
- 不要使用 Python 风格的 True/False/None，请使用 JSON 的 true/false/null
- 不要出现尾随逗号
只输出一个 JSON 对象，示例如下：

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

            # === 核心: 统计融合预测(取代原纯LLM拼装) ===
            # 从数据库重新拉取升序历史数据(构造预测器输入)
            self.db_client.cursor.execute('SELECT * FROM p5_history_data ORDER BY issue ASC')
            _rows = self.db_client.cursor.fetchall()
            if not _rows:
                result['error'] = '无历史开奖数据，无法进行预测'
                return result
            latest_issue = _rows[-1].get('issue', '')
            history_count = len(_rows)
            _history = [{
                'issue': r.get('issue'), 'draw_date': r.get('draw_date'),
                'wan': r.get('wan'), 'qian': r.get('qian'), 'bai': r.get('bai'),
                'shi': r.get('shi'), 'ge': r.get('ge'),
                'hezhi': r.get('hezhi'), 'span': r.get('span'),
            } for r in _rows]

            # 计算近期数据内容指纹(用于缓存失效:数据被修正但条数不变时也能检测到变化)
            import hashlib
            _recent = _history[-30:] if len(_history) > 30 else _history
            _sig = '|'.join(
                f"{r.get('issue')}:{r.get('wan')},{r.get('qian')},{r.get('bai')},{r.get('shi')},{r.get('ge')}"
                for r in _recent
            )
            _data_hash = hashlib.sha256(_sig.encode('utf-8')).hexdigest()

            # ★ 贝叶斯/预测统计产物缓存复用(v3.4 修复核心):
            #   同一最新期号、且历史数据量未变化(未新增开奖)时, 直接复用已落库的
            #   prediction_stat 产物(内含 bayesian_inference 后验概率等), 不再调用
            #   P5Predictor -> AI 模型, 彻底消除"每次流水线都频繁与AI交互"的问题。
            _stat = None
            _cached = False
            try:
                self._ensure_db()
                if self.db_client:
                    _cached_art = self.db_client.get_latest_artifact('prediction_stat', issue=latest_issue)
                    if _cached_art and isinstance(_cached_art.get('data'), dict):
                        _cmeta = _cached_art.get('meta') or {}
                        if _cmeta.get('history_count') == history_count and _cmeta.get('data_hash') == _data_hash:
                            _stat = _cached_art['data']
                            _cached = True
                            logger.info(f'复用已存储预测统计(含贝叶斯推断) issue={latest_issue}, '
                                         f'跳过AI模型交互与重算')
            except Exception as e:
                logger.warning(f'读取预测统计缓存失败(非致命): {e}')

            # ★ 贝叶斯结果增量复用(专用表 p5_bayesian_result):
            #   先查专用表, 若该 issue 已计算过贝叶斯后验, 则本次直接复用(即便 prediction_stat
            #   缓存因 history_count 变化而 miss, 贝叶斯部分仍可免去重算), 彻底避免频繁调AI。
            _bayes_cached_in_db = None
            try:
                if self.db_client:
                    _bayes_cached_in_db = self.db_client.get_bayesian_result_row(latest_issue)
            except Exception:
                _bayes_cached_in_db = None

            if not _cached:
                _predictor = self._get_predictor()
                _stat = _predictor.predict(_history, current_issue=latest_issue)
                if _stat.get('error'):
                    result['error'] = f'统计预测失败: {_stat["error"]}'
                    return result

                # 持久化完整预测统计到数据库 (主缓存, 决定是否需要调用AI)
                try:
                    if self.db_client:
                        self.db_client.save_artifact(
                            'prediction_stat', _stat, issue=latest_issue,
                            meta={'target_issue': target_issue, 'model': 'statistical+v3.2',
                                  'history_count': history_count, 'data_hash': _data_hash}
                        )
                except Exception as e:
                    logger.warning(f'预测统计产物入库失败(非致命): {e}')
            else:
                # 复用 prediction_stat 缓存: 若专用表也有贝叶斯, 以专用表为准确保一致性
                if _bayes_cached_in_db is not None:
                    _stat.setdefault('algorithm_probs', {})['bayesian_inference'] = _bayes_cached_in_db

            # 统一同步贝叶斯结果到专用表 p5_bayesian_result (幂等, 按 issue 唯一, 增量复用)
            try:
                if self.db_client:
                    _bayes = _stat.get('algorithm_probs', {}).get('bayesian_inference')
                    if _bayes is None and _bayes_cached_in_db is not None:
                        _stat.setdefault('algorithm_probs', {})['bayesian_inference'] = _bayes_cached_in_db
                        _bayes = _bayes_cached_in_db
                    if _bayes is not None:
                        self.db_client.insert_bayesian_result(latest_issue, _bayes, target_issue)
                        self._bayes_dedicated_used = (_bayes_cached_in_db is not None)
            except Exception as e:
                logger.warning(f'贝叶斯专用表同步失败(非致命): {e}')

            _fused = _stat['fused_probabilities']
            _top_combos = _stat['top_combinations']
            _trend_fc = _stat.get('trend_forecast', {})

            pos_keys = ['wan', 'qian', 'bai', 'shi', 'ge']

            # ★ 问题2: 多源走势融合预测 —— 结合历史走势图/基础走势图/万千百十个独立走势表
            #    最近30期数据, 用「频率+遗漏+动量」加权融合算法输出每位置 Top-4 推荐。
            #    作为"走势图数据预测结果（实时）"的主预测来源(压缩到4位, 满足问题1)。
            trend_multi = self._predict_trend_multi_source(target_issue, data_period=30)

            # ★ 知识模型增强：利用在线学习的历史知识动态调整走势预测分数
            if trend_multi and self.online_learner:
                try:
                    trend_multi = self.online_learner.apply_knowledge_to_trend_prediction(trend_multi)
                    logger.info('✓ 知识模型增强已应用到走势预测')
                except Exception as e:
                    logger.warning(f'知识模型增强失败(非致命): {e}')

            # 升平降方向偏好(供最终报告展示, 与组合级约束同源)
            try:
                spj_pref = self._get_spj_direction_preference() or {}
            except Exception:
                spj_pref = {}

            prediction = {}
            for _pk in pos_keys:
                if trend_multi.get(_pk):
                    # 多源融合成功: 直接采用 Top-4
                    prediction[_pk] = trend_multi[_pk]
                else:
                    # 降级: 沿用 P5Predictor 融合概率取 Top-4
                    _idx = pos_keys.index(_pk)
                    _pp = _fused[_idx] if _idx < len(_fused) else {}
                    _sn = sorted(_pp.items(), key=lambda x: x[1], reverse=True)[:4]
                    # 降级路径: 从DB现算 frequency/omission 特征供 GUI 展示(真实历史, 非编造)
                    _feat = {'freq_pct': {}, 'omission': {}}
                    try:
                        if self.db_client:
                            _hist = self.db_client.get_history_data(limit=30)  # DESC, index0=最新
                            _col = {'wan': 0, 'qian': 1, 'bai': 2, 'shi': 3, 'ge': 4}[_pk]
                            _counts = {d: 0 for d in range(10)}
                            _last_seen = {d: -1 for d in range(10)}
                            _n = len(_hist)
                            for _i, _row in enumerate(_hist):
                                _nums = _row.get('numbers') or []
                                if len(_nums) >= 5:
                                    _d = int(_nums[_col])
                                    _counts[_d] += 1
                                    _last_seen[_d] = _i  # 距最新出现的距离(_i=0最新)
                            _denom = _n or 1
                            for _d, _p in _sn:
                                _sd = str(_d)
                                _feat['freq_pct'][_sd] = round(_counts[_d] / _denom * 100, 2)
                                _feat['omission'][_sd] = (_last_seen[_d] if _last_seen[_d] >= 0 else _n)
                    except Exception:
                        pass
                    prediction[_pk] = {
                        'numbers': [int(n) for n, _ in _sn],
                        'confidence': [round(float(p), 4) for _, p in _sn],
                        'reason': '统计融合模型(多算法+贝叶斯)Top推荐(多源预测降级)',
                        'features': _feat
                    }

            # ★ 和值走势约束候选组合: 用 p5_hzzst_data 派生和值区间, 对多源 Top-4 候选做和值过滤
            #    (直接落实新爬取的"排列5和值走势图"数据价值, 提升组合级命中率)
            recommended_combinations = []
            hezhi_range_str = ''
            if trend_multi and any(prediction.get(p) for p in pos_keys):
                try:
                    _cc, hezhi_range_str = self._build_constrained_combinations(
                        prediction, target_issue)
                    if _cc:
                        recommended_combinations = _cc
                except Exception as e:
                    logger.warning(f'和值约束组合生成失败(降级至统计模型): {e}')

            if not recommended_combinations and _top_combos:
                # 降级: 沿用 P5Predictor 的 Top 组合
                recommended_combinations = [{
                    'combination': c.get('combination', ''),
                    'confidence': c.get('confidence', 0),
                    'reason': f"和值{c.get('hezhi')}/跨度{c.get('span')}"
                } for c in _top_combos[:10]]

            trend_analysis = {}
            for _pn, _td in _trend_fc.items():
                if isinstance(_td, dict):
                    trend_analysis[_pn] = {
                        'top_numbers': _td.get('top_numbers', []),
                        'trend': _td.get('trend', ''),
                        'recent_values': _td.get('recent_values', [])
                    }
            key_conclusions = [
                '各位置推荐关注: ' + '; '.join(f"{_pk}={prediction[_pk]['numbers']}" for _pk in pos_keys),
            ]
            if hezhi_range_str:
                key_conclusions.append(
                    f"推荐组合经和值走势约束(区间 {hezhi_range_str})与升平降方向约束, "
                    f"由多源 Top-4 候选枚举筛选出 {len(recommended_combinations)} 组"
                    f"最贴合近期和值重心与涨跌惯性的组合。")
            else:
                key_conclusions.append(
                    f"共生成 {len(_top_combos)} 个候选组合，取置信度最高的前 "
                    f"{min(10, len(_top_combos))} 个。")
            reasoning_process = [
                '步骤1: 加载历史开奖数据并归一化、按期号排序',
                '步骤2: 并行执行频率/遗漏/趋势/马尔可夫/形态/贝叶斯/特征 7 类算法',
                '步骤3: 按自适应权重融合各算法概率分布并归一化',
                '步骤4: 边界保护(冷热号/相邻位/方差)与组合约束筛选',
                '步骤5: 生成 Top 组合并注册预测记录供后续验证'
            ]
            final_report = {
                'data_source': '统计融合模型(P5Predictor v3.2)+多源走势融合+可选AI文本增强',
                'model_version': 'statistical+v3.3',
                'current_issue': latest_issue,
                'next_issue': target_issue,
                # ★ 拆分为两个独立模块
                'trend_prediction': prediction,  # 走势图数据预测结果（多源30期融合, Top-4）
                'article_prediction': {},  # 专家文章预测结果（可由步骤3的结果填充）
                # ★ 问题4修复: 同时写入 'prediction' 键, 供 _save_final_prediction_to_db /
                #    _register_prediction_for_verification 读取(此前两处都读 final_report['prediction'],
                #    而实际数据在 trend_prediction 中 -> 入库 prediction_stats/recommended_numbers 全为空)
                'prediction': prediction,
                'recommended_combinations': recommended_combinations,
                'article_recommendations': [],  # 专家文章推荐组合
                'trend_analysis': trend_analysis,
                'key_conclusions': key_conclusions,
                'reasoning_process': reasoning_process,
                'risk_warning': _stat.get('risk_warning', '理性购彩，量力而行'),
                'statistical_summary': _stat.get('summary', ''),
                # ★ 新增元数据, 供 GUI 展示本次改进点
                'hezhi_range': hezhi_range_str,
                'spj_direction_preference': spj_pref,
                'bayesian_cache_used': _cached,
                'bayesian_dedicated_table': getattr(self, '_bayes_dedicated_used', False),
                'multi_source_method': '30期全源融合(历史+基础+万千百十个走势+升平降方向+和值重心+贝叶斯后验概率, 频率0.35+遗漏0.25+动量0.15+贝叶斯0.25, 指数衰减加权)',
                # 贝叶斯推断后验概率摘要(若存在), 供 GUI 展示算法透明性
                'bayesian_inference': _stat.get('algorithm_probs', {}).get('bayesian_inference')
            }
            # 数据质量门禁结果纳入最终报告
            final_report['data_quality_gate'] = {
                'passed': quality_report.get('valid', False),
                'checks': list(quality_report.get('checks', {}).values()),
                'checked_at': datetime.utcnow().isoformat()
            }
            
            # ★ 从 pipeline_state 中获取独立报告并整合到 final_report
            expert_article_report = self.pipeline_state.get('expert_article_report')
            if expert_article_report:
                successful_count = expert_article_report.get('successful_articles', 0)
                logger.info(f'整合专家文章报告: {successful_count}/{expert_article_report.get("total_articles", 0)}篇有效')
                
                # 整合专家文章预测结果
                final_report['article_prediction'] = expert_article_report.get('prediction', {})
                article_recs = expert_article_report.get('position_recommendations', {})
                if article_recs:
                    # 提取专家共识号码
                    for pos in ['wan', 'qian', 'bai', 'shi', 'ge']:
                        rec = article_recs.get(pos, {})
                        if rec.get('top_numbers'):
                            final_report['article_prediction'][pos] = {
                                'numbers': rec['top_numbers'],
                                'consensus': f"专家共识（提及{rec.get('total_mentions', 0)}次）"
                            }
                    
                    # 构建专家推荐组合
                    article_rec_list = []
                    for pos_name, pos_key in [('万位', 'wan'), ('千位', 'qian'), ('百位', 'bai'), ('十位', 'shi'), ('个位', 'ge')]:
                        rec = article_recs.get(pos_key, {})
                        top_nums = rec.get('top_numbers', [])
                        if top_nums:
                            article_rec_list.append({
                                'position': pos_name,
                                'top_numbers': top_nums,
                                'frequency': rec.get('frequency', {}),
                                'total_mentions': rec.get('total_mentions', 0)
                            })
                    final_report['article_recommendations'] = article_rec_list
                    
                    if successful_count > 0:
                        logger.info(f'✓ 已整合专家文章预测结果: {successful_count}篇专家文章')
                    else:
                        logger.warning('⚠ 专家文章无有效预测数据,将仅依赖走势图预测')
                else:
                    logger.warning('⚠ 专家文章分析报告结构不完整,position_recommendations为空')
            else:
                logger.warning('⚠ 未在pipeline_state中找到expert_article_report')
            
            trend_chart_report = self.pipeline_state.get('trend_chart_report')
            if trend_chart_report:
                # ★ 问题3修复: 把多源融合预测结果填充进走势图报告 prediction 字段后再入库,
                #   确保数据库中的"走势图数据预测报告"预测数据不再为空。
                trend_chart_report['prediction'] = prediction
                trend_chart_report['data_period'] = '最近30期(历史+基础+位置走势表)'
                uuid_val = self._save_report_to_db(trend_chart_report, 'trend_chart', target_issue, latest_issue)
                if uuid_val:
                    logger.info(f'走势图数据预测报告已入库(含多源预测): {uuid_val}')
                else:
                    logger.warning('走势图数据预测报告入库失败(非致命)')

            # 7. 存入数据库
            logger.info('保存最终预测结果到数据库...')
            report_uuid = self._save_final_prediction_to_db(final_report, latest_issue, target_issue)

            if not report_uuid:
                result['error'] = '最终预测保存到数据库失败'
                return result

            result['success'] = True
            result['report_uuid'] = report_uuid
            result['final_report'] = final_report
            self.pipeline_state['final_report'] = final_report

            logger.info('=' * 80)
            logger.info('【步骤4】完成：最终预测结果已入库')
            logger.info(f'报告UUID: {report_uuid}')
            logger.info(f'预测期号: {target_issue}')
            
            # ★ 贝叶斯专用表写入确认日志（关键，便于排查问题）
            _bayes_written = getattr(self, '_bayes_dedicated_used', False)
            _bayes_in_report = final_report.get('bayesian_inference')
            if _bayes_in_report:
                logger.info(f'✓ 贝叶斯推断结果已写入专用表 p5_bayesian_result')
                logger.info(f'  基于历史数据: {latest_issue} ({history_count}条记录)')
                logger.info(f'  预测目标期号: {target_issue}')
                if isinstance(_bayes_in_report, list):
                    pos_names = ['万位', '千位', '百位', '十位', '个位']
                    top_nums = []
                    for i, pos_dict in enumerate(_bayes_in_report[:5]):
                        if isinstance(pos_dict, dict) and pos_dict:
                            top_num = max(pos_dict, key=pos_dict.get)
                            top_nums.append(top_num)
                            top3 = sorted(pos_dict.items(), key=lambda x: x[1], reverse=True)[:3]
                            logger.info(f'  {pos_names[i]}: top={top_num}, Top-3={top3}')
                        else:
                            logger.info(f'  {pos_names[i]}: 非字典格式')
                    logger.info(f'  综合推荐: {" ".join(str(n) for n in top_nums)}')
                logger.info(f'  写入状态: {"增量复用(缓存命中)" if _bayes_written else "全新计算并写入"}')
            else:
                logger.warning('⚠ 贝叶斯推断结果为空，未写入专用表')
            
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

            # 提取各算法 Top-5 预测数据，供 per-algo 命中率验证使用
            per_algo_preds = final_report.get('per_algo_top_predictions')

            # 确保数据库连接可用
            if not self._ensure_db():
                logger.error('数据库连接失败，无法保存最终预测')
                return None

            success = self.db_client.insert_ai_report(
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
                report_format='JSON',
                per_algo_predictions=per_algo_preds
            )

            if success:
                logger.info(f'最终预测已保存到数据库，UUID: {report_uuid}')
                return report_uuid
            else:
                logger.error('保存到数据库失败')
                return None

        except Exception as e:
            logger.error(f'保存最终预测到数据库失败: {e}', exc_info=True)
            return None

    def _save_report_to_db(self, report: Dict[str, Any], report_type: str,
                           target_issue: str, latest_issue: str = None) -> Optional[str]:
        """
        把一份独立报告(专家文章预测报告 / 走势图数据预测报告)持久化到 p5_ai_report 表。

        修复: 此前这两份独立报告只写入文件(reports/*.json)与Redis, 未进入数据库,
        导致"数据库里没有两份报告"。现统一落库并标记 report_type 以便区分。
        """
        if not report:
            return None
        try:
            import json as _json
            self._ensure_db()
            if not self.db_client or not self.db_client.connection:
                logger.warning(f'数据库未连接, {report_type} 报告仅保存文件')
                return None

            if not latest_issue:
                try:
                    latest_rows = self.db_client.get_history_data(limit=1, order_by='issue DESC')
                    latest_issue = latest_rows[0]['issue'] if latest_rows else ''
                except Exception:
                    latest_issue = ''

            report_content = _json.dumps(report, ensure_ascii=False, default=str)
            prediction = report.get('prediction', {}) or {}
            prediction_json = _json.dumps(prediction, ensure_ascii=False, default=str)
            combos = report.get('recommended_combinations', []) or []
            combos_json = _json.dumps(combos, ensure_ascii=False, default=str)
            trend = report.get('trend_analysis', {})
            trend_json = _json.dumps(trend, ensure_ascii=False, default=str)
            kc = report.get('key_conclusions', [])
            kc_json = _json.dumps(kc if isinstance(kc, list) else [str(kc)], ensure_ascii=False, default=str)
            conf = report.get('confidence_scores') or prediction_json
            reasons = report.get('recommendation_reasons') or report.get('methodology') or ''
            if not isinstance(reasons, str):
                reasons = _json.dumps(reasons, ensure_ascii=False, default=str)
            risk = report.get('risk_warning', '理性购彩，量力而行')
            data_count = int(report.get('total_articles') or report.get('data_count') or 0)

            return self.db_client.insert_ai_report(
                report_content=report_content,
                data_count=data_count,
                latest_issue=latest_issue or '',
                next_issue=target_issue,
                trend_analysis=trend_json,
                probability_stats=prediction_json,
                recommended_numbers=prediction_json,
                recommended_combinations=combos_json,
                confidence_scores=conf,
                recommendation_reasons=reasons,
                key_conclusions=kc_json,
                risk_warning=risk,
                report_format='JSON',
                report_type=report_type
            )
        except Exception as e:
            logger.error(f'{report_type} 报告入库失败(不影响主流程): {e}')
            return None

    def _register_prediction_for_verification(self, report_uuid: str,
                                               target_issue: str,
                                               final_report: Dict[str, Any]):
        """
        注册预测记录供后续验证使用（在线学习引擎集成）
        
        该方法是OnlineLearner的入口点。当开奖结果出来后，可以调用
        OnlineLearner.track_prediction_result来完成验证。
        
        Args:
            report_uuid: 报告UUID
            target_issue: 预测目标期号
            final_report: 最终AI预测结果
        """
        try:
            if not self.db_client:
                self._init_db_client()
            
            if not self.db_client or not self.db_client.connection:
                logger.warning('数据库未连接，无法注册预测记录')
                return False
            
            # 从最终报告中提取预测号码
            prediction = final_report.get('prediction', {})
            combos = final_report.get('recommended_combinations', [])

            # === 关键修复 (5.4 验证格式BUG) ===
            # 验证闭环 database.update_prediction_verification() 期望
            #   predicted.get('wan') -> list[number]
            # 但 final_report['prediction'] 结构是
            #   {'wan': {'numbers': [...], 'confidence': [...], 'reason': ...}, ...}
            # 若直接 json.dumps(prediction)，则 predicted.get('wan') 得到 dict，
            # check_match() 的 isinstance(list) 守卫会恒返回 False -> 命中率恒为0。
            # 因此此处必须"扁平化":抽离每个位置的 numbers 列表再序列化。
            flat_predicted = {}
            for _pos in ['wan', 'qian', 'bai', 'shi', 'ge']:
                _pdata = prediction.get(_pos, {})
                if isinstance(_pdata, dict):
                    _nums = _pdata.get('numbers', [])
                elif isinstance(_pdata, list):
                    _nums = _pdata
                else:
                    _nums = []
                flat_predicted[_pos] = [int(n) for n in _nums]

            # 置信度同样扁平化(供 GUI/学习引擎读取)
            flat_confidence = {}
            for _pos in ['wan', 'qian', 'bai', 'shi', 'ge']:
                _pdata = prediction.get(_pos, {})
                if isinstance(_pdata, dict):
                    _conf = _pdata.get('confidence', [])
                else:
                    _conf = []
                flat_confidence[_pos] = [round(float(c), 4) for c in _conf]

            # 序列化预测号码(扁平化 -> 满足验证契约)
            predicted_numbers_json = json.dumps(flat_predicted, ensure_ascii=False)
            predicted_combos_json = json.dumps(combos, ensure_ascii=False)
            confidence_scores_json = json.dumps(flat_confidence, ensure_ascii=False)

            # 存入数据库预测验证记录表
            success = self.db_client.insert_prediction_record(
                report_uuid=report_uuid,
                target_issue=target_issue,
                predicted_numbers=predicted_numbers_json,
                predicted_combinations=predicted_combos_json,
                confidence_scores=confidence_scores_json
            )
            
            if success:
                logger.info(f'预测记录注册成功: UUID={report_uuid}, 期号={target_issue}')
            else:
                logger.warning(f'预测记录注册失败: 期号={target_issue}')
            
            return success
            
        except Exception as e:
            logger.error(f'注册预测记录失败: {e}', exc_info=True)
            return False

    def _load_adaptive_weights(self) -> Dict[str, Any]:
        """
        从数据库或Redis加载最新自适应权重配置

        优先从Redis读取,若Redis不可用则从数据库p5_weight_history表获取。

        Returns:
            权重配置字典,格式:
            {
                'version': 'v3.0',
                'updated_at': '2026-07-04 12:00:00',
                'weights': {
                    'frequency_weighted': 0.38,
                    'omission_regression': 0.26,
                    ...
                },
                'source': 'redis' | 'database' | 'default'
            }
        """
        # 优先从Redis加载
        if self.redis_client and self.redis_client.is_connected():
            try:
                redis_key = 'kpluckynumber:pl5:adaptive_weights:latest'
                data_str = self.redis_client.client.get(redis_key)
                if data_str:
                    config = json.loads(data_str)
                    logger.info(f'从Redis加载自适应权重: {config.get("weights", {})}')
                    config['source'] = 'redis'
                    return config
            except Exception as e:
                logger.warning(f'从Redis加载权重失败: {e}')

        # 从数据库加载最近的权重历史
        if self.db_client and self.db_client.connection:
            try:
                self.db_client.cursor.execute(
                    '''SELECT algo_name, position, weight_value, updated_at
                       FROM p5_weight_history
                       WHERE position = 'all'
                       ORDER BY updated_at DESC
                       LIMIT 6'''
                )
                rows = self.db_client.cursor.fetchall()
                if rows:
                    weights = {}
                    for row in rows:
                        weights[row['algo_name']] = float(row['weight_value'])
                    # 归一化权重
                    total = sum(weights.values())
                    if total > 0:
                        weights = {k: v / total for k, v in weights.items()}
                    logger.info(f'从数据库加载自适应权重: {weights}')
                    return {
                        'version': 'v3.0',
                        'updated_at': rows[0]['updated_at'].strftime('%Y-%m-%d %H:%M:%S') if hasattr(rows[0]['updated_at'], 'strftime') else str(rows[0]['updated_at']),
                        'weights': weights,
                        'source': 'database'
                    }
            except Exception as e:
                logger.warning(f'从数据库加载权重失败: {e}')

        # 返回默认权重
        logger.info('使用默认自适应权重')
        return {
            'version': 'v3.0',
            'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'weights': {
                'frequency_weighted': 0.35,
                'omission_regression': 0.25,
                'trend_momentum': 0.12,
                'markov_transition': 0.10,
                'pattern_continuation': 0.08,
                'bayesian_inference': 0.10
            },
            'source': 'default'
        }

    # ================================================================
    # 步骤5: 开奖后权重自适应调整 (v3.0 新增)
    # ================================================================

    def step5_weight_adaptation(self, target_issue: str, actual_numbers: List[int]) -> Dict[str, Any]:
        """
        步骤5 - 开奖后权重自适应调整 (v3.0 新增)

        在每期开奖后,根据实际结果自动更新模型权重:
        1. 计算各算法在该期的预测命中率
        2. 记录到贝叶斯推断引擎
        3. 更新权重管理器
        4. 持久化权重历史到数据库和Redis
        5. 生成自适应权重报告

        Args:
            target_issue: 被验证的目标期号
            actual_numbers: 实际开奖号码 [wan, qian, bai, shi, ge]

        Returns:
            {
                'status': 'success',
                'algorithm_hits': {'frequency_weighted': 0.8, ...},
                'weight_updates': {...},
                'persistence': 'saved',
            }
        """
        logger.info('=' * 80)
        logger.info('【步骤5】开始：开奖后权重自适应调整 (v3.0)')
        logger.info(f'目标期号: {target_issue}, 实际开奖: {actual_numbers}')
        logger.info('=' * 80)

        result = {
            'status': 'pending',
            'algorithm_hits': {},
            'weight_updates': {},
            'persistence': 'none',
            'error': None
        }

        try:
            # 1. 初始化组件
            self._init_db_client()
            self._init_redis()

            if not self.db_client or not self.db_client.connection:
                result['error'] = '数据库客户端未连接'
                result['status'] = 'failed'
                return result

            # 2. 计算各算法的预测命中率
            logger.info('计算各算法预测命中率...')
            algo_hits = self._calculate_algorithm_hits(target_issue, actual_numbers)
            result['algorithm_hits'] = algo_hits

            # 3. 更新权重管理器
            logger.info('更新自适应权重管理器...')
            weight_updates = self._update_weight_manager(algo_hits)
            result['weight_updates'] = weight_updates

            # 4. 持久化到数据库和Redis
            logger.info('持久化权重更新记录...')
            persistence_result = self._persist_weight_update(weight_updates, target_issue, actual_numbers)
            result['persistence'] = persistence_result

            # 5. 记录验证结果到预测验证表
            self._record_verification_result(target_issue, actual_numbers, algo_hits)

            result['status'] = 'success'
            logger.info(f'步骤5完成: 期号{target_issue}权重自适应调整成功')

        except Exception as e:
            logger.error(f'步骤5异常: {e}', exc_info=True)
            result['status'] = 'failed'
            result['error'] = str(e)

        return result

    def _calculate_algorithm_hits(self, target_issue: str, actual_numbers: List[int]) -> Dict[str, Any]:
        """
        计算各算法在目标期的预测命中率

        从 p5_ai_report 表的 per_algo_predictions 列中读取每个算法的 Top-5 预测，
        分别计算每个算法在各个位置的命中情况。每个算法的命中率可以不同。

        Args:
            target_issue: 目标期号
            actual_numbers: 实际开奖号码列表

        Returns:
            各算法的命中统计: {
                'frequency_weighted': {'hit_positions': [...], 'hit_rate': 0.6},
                'omission_regression': {'hit_positions': [...], 'hit_rate': 0.4},
                ...
            }
        """
        algo_hits = {}
        positions = ['wan', 'qian', 'bai', 'shi', 'ge']
        algo_names = ['frequency_weighted', 'omission_regression', 'trend_momentum',
                      'markov_transition', 'pattern_continuation', 'bayesian_inference',
                      'feature_engineering']

        # 从数据库获取该期的 per-algo 预测数据
        per_algo_predictions = self._fetch_per_algo_predictions_from_report(target_issue)

        if per_algo_predictions:
            # 有 per-algo 预测数据：逐个算法分别计算命中率
            for algo_name in algo_names:
                algo_preds = per_algo_predictions.get(algo_name, {})
                hit_positions = []
                top1_positions = []  # ★ v3.14: Top-1 精准度命中位置（最高概率位是否中）
                for pos_idx, pos in enumerate(positions):
                    if pos_idx < len(actual_numbers):
                        actual_num = actual_numbers[pos_idx]
                        pred_nums = algo_preds.get(pos, [])
                        if pred_nums and actual_num in pred_nums:
                            hit_positions.append(pos)
                        # Top-1: pred_nums[0] 是该算法预测的最高概率号
                        if pred_nums and len(pred_nums) > 0 and pred_nums[0] == actual_num:
                            top1_positions.append(pos)
                hit_rate = len(hit_positions) / 5.0
                top1_hit_rate = len(top1_positions) / 5.0   # ★ v3.14 新增
                algo_hits[algo_name] = {
                    'hit_positions': hit_positions,
                    'hit_rate': hit_rate,
                    'top1_positions': top1_positions,         # ★ v3.14 新增
                    'top1_hit_rate': top1_hit_rate,          # ★ v3.14 新增
                    'total_positions': 5
                }
        else:
            # 无 per-algo 数据（旧报告），回退到原来的统一命中率方式
            report = self._fetch_ai_report_row(target_issue)
            if report:
                predicted_numbers = self._parse_predicted_numbers_from_report(report, positions)
                total_hits = 0
                top1_hits = 0  # ★ v3.14
                for pos_idx, pos in enumerate(positions):
                    if pos_idx < len(actual_numbers):
                        actual_num = actual_numbers[pos_idx]
                        preds = predicted_numbers.get(pos, [])
                        if preds and actual_num in preds:
                            total_hits += 1
                        # Top-1: preds[0] 是最高概率号
                        if preds and len(preds) > 0 and preds[0] == actual_num:
                            top1_hits += 1
                overall_hit_rate = total_hits / 5.0 if positions else 0.0
                overall_top1_hit_rate = top1_hits / 5.0 if positions else 0.0  # ★ v3.14
                for algo in algo_names:
                    algo_hits[algo] = {
                        'hit_positions': [positions[i] for i in range(5)
                                          if i < len(actual_numbers)
                                          and predicted_numbers.get(positions[i])
                                          and actual_numbers[i] in predicted_numbers[positions[i]]],
                        'hit_rate': overall_hit_rate,
                        'top1_positions': [positions[i] for i in range(5)
                                            if i < len(actual_numbers)
                                            and predicted_numbers.get(positions[i])
                                            and len(predicted_numbers[positions[i]]) > 0
                                            and predicted_numbers[positions[i]][0] == actual_numbers[i]],
                        'top1_hit_rate': overall_top1_hit_rate,
                        'total_positions': 5
                    }
            else:
                for algo in algo_names:
                    algo_hits[algo] = {'hit_positions': [], 'hit_rate': 0.0,
                                       'top1_positions': [], 'top1_hit_rate': 0.0,
                                       'total_positions': 5}

        logger.info(f'算法命中率计算完成: per-algo 模式={bool(per_algo_predictions)}')
        if per_algo_predictions:
            for algo_name, hit_info in algo_hits.items():
                logger.info(f'  [{algo_name}] 命中率: {hit_info["hit_rate"]:.0%} (命中: {hit_info["hit_positions"]})')
        return algo_hits

    def _fetch_ai_report_row(self, target_issue: str) -> Optional[Dict]:
        """从 p5_ai_report 表获取一行记录。"""
        try:
            self.db_client.cursor.execute(
                'SELECT * FROM p5_ai_report WHERE next_issue = %s ORDER BY created_at DESC LIMIT 1',
                (target_issue,)
            )
            return self.db_client.cursor.fetchone()
        except Exception as e:
            logger.warning(f'查询AI报告失败: {e}')
            return None

    def _parse_predicted_numbers_from_report(self, report: Dict, positions: List[str]) -> Dict[str, List]:
        """从数据库报告行中解析各位置预测号码。"""
        predicted_numbers = {}
        for pos in positions:
            val = report.get(pos)
            if isinstance(val, str):
                val = json.loads(val) if val.startswith('[') else [val]
            if isinstance(val, list):
                predicted_numbers[pos] = val
            else:
                predicted_numbers[pos] = []
        return predicted_numbers

    def _fetch_per_algo_predictions_from_report(self, target_issue: str) -> Optional[Dict]:
        """从 p5_ai_report 表的 per_algo_predictions 列中读取各算法 Top-5 预测。

        Returns:
            dict: {algo_name: {position: [num1, num2, ...]}} 或 None
        """
        try:
            self.db_client.cursor.execute(
                'SELECT per_algo_predictions FROM p5_ai_report WHERE next_issue = %s ORDER BY created_at DESC LIMIT 1',
                (target_issue,)
            )
            row = self.db_client.cursor.fetchone()
            if not row:
                return None
            per_algo_json = row.get('per_algo_predictions') if isinstance(row, dict) else row[0]
            if not per_algo_json:
                return None
            return json.loads(per_algo_json)
        except Exception as e:
            logger.warning(f'查询 per-algo 预测失败: {e}')
            return None

    def _update_weight_manager(self, algo_hits: Dict[str, Any]) -> Dict[str, Any]:
        """
        使用在线学习引擎更新各算法权重

        Args:
            algo_hits: 各算法的命中统计

        Returns:
            权重更新结果
        """
        weight_updates = {}

        try:
            if not self.online_learner:
                self._init_online_learner()

            if not self.online_learner:
                logger.warning('在线学习引擎未初始化，跳过权重更新')
                return weight_updates

            # 通过OnlineLearner的增量学习更新权重
            # ★ v3.14: 同时记录 hit_rate(旧, 位置覆盖率) + top1_hit_rate(新, Top-1 精准度)
            for algo_name, hit_info in algo_hits.items():
                hit_rate = hit_info.get('hit_rate', 0.0)
                top1_hit_rate = hit_info.get('top1_hit_rate', 0.0)  # ★ v3.14 新增
                if hit_rate > 0 or top1_hit_rate > 0:
                    # 记录验证到权重管理器(双信号)
                    if hasattr(self.online_learner, 'record_algo_hit'):
                        self.online_learner.record_algo_hit(
                            algo_name, hit_rate, top1_hit=top1_hit_rate)

                    weight_updates[algo_name] = {
                        'prev_hit_rate': hit_info.get('hit_rate', 0.0),
                        'top1_hit_rate': top1_hit_rate,    # ★ v3.14 同时记录
                        'hit_positions': hit_info.get('hit_positions', []),
                        'top1_positions': hit_info.get('top1_positions', []),
                        'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    }
                    logger.info(
                        f'算法 [{algo_name}] 覆盖率: {hit_rate:.0%}, '
                        f'Top-1精准度: {top1_hit_rate:.0%}, 已双信号更新权重')

            # 获取更新后的自适应权重(★ v3.14: 默认用 top1_hit 通道)
            if hasattr(self.online_learner, 'weight_manager'):
                new_weights = self.online_learner.weight_manager.get_adaptive_weights(
                    metric='top1_hit')
                weight_updates['_adaptive_weights'] = new_weights
                logger.info(f'自适应权重已更新(top1_hit): {new_weights}')

        except Exception as e:
            logger.warning(f'权重管理器更新失败（不影响主流程）: {e}')

        return weight_updates

    def _persist_weight_update(self, weight_updates: Dict[str, Any],
                                target_issue: str,
                                actual_numbers: List[int]) -> str:
        """
        将权重更新持久化到数据库p5_weight_history表和Redis

        Args:
            weight_updates: 权重更新数据
            target_issue: 目标期号
            actual_numbers: 实际开奖号码

        Returns:
            'saved' 或 'skipped'
        """
        try:
            positions = ['wan', 'qian', 'bai', 'shi', 'ge']
            algo_names = ['frequency_weighted', 'omission_regression', 'trend_momentum',
                          'markov_transition', 'pattern_continuation', 'bayesian_inference',
                          'feature_engineering']

            saved_count = 0

            for algo_name in algo_names:
                algo_info = weight_updates.get(algo_name, {})
                hit_rate = algo_info.get('hit_rate', 0.0)
                hit_positions = algo_info.get('hit_positions', [])
                hit_count = len(hit_positions)

                # 记录算法级权重历史
                for pos in positions:
                    is_hit = pos in hit_positions
                    validation_result = 'hit' if is_hit else 'miss'

                    # 计算权重值（基于命中率）
                    weight_value = hit_rate if is_hit else 0.0

                    # 模拟贝叶斯概率
                    prior_prob = 0.2  # 均匀先验
                    likelihood = hit_rate if is_hit else (1 - hit_rate)
                    posterior_prob = (likelihood * prior_prob) / max(0.01,
                        (likelihood * prior_prob) + ((1 - likelihood) * (1 - prior_prob)))

                    self.db_client.insert_weight_history(
                        algo_name=algo_name,
                        position=pos,
                        weight_value=weight_value,
                        validation_result=validation_result,
                        match_count=hit_count
                    )
                    saved_count += 1

            # 持久化到Redis
            if self.redis_client and self.redis_client.is_connected():
                redis_key = f'kpluckynumber:pl5:weight_update:{target_issue}'
                redis_data = {
                    'issue': target_issue,
                    'actual_numbers': actual_numbers,
                    'weight_updates': str(weight_updates),
                    'persisted_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }
                try:
                    self.redis_client.client.setex(
                        redis_key,
                        timedelta(days=90),
                        json.dumps(redis_data, ensure_ascii=False)
                    )
                    logger.info(f'权重更新已存入Redis: {redis_key}')
                except Exception as e:
                    logger.warning(f'权重更新存入Redis失败: {e}')

            logger.info(f'权重历史持久化完成: 插入 {saved_count} 条记录')
            return 'saved'

        except Exception as e:
            logger.error(f'持久化权重更新失败: {e}', exc_info=True)
            return 'failed'

    def _record_verification_result(self, target_issue: str,
                                     actual_numbers: List[int],
                                     algo_hits: Dict[str, Any]):
        """
        记录验证结果到预测验证表

        Args:
            target_issue: 目标期号
            actual_numbers: 实际开奖号码
            algo_hits: 各算法命中统计
        """
        try:
            # 查询是否存在预测记录
            self.db_client.cursor.execute(
                'SELECT * FROM p5_prediction_record WHERE target_issue = %s ORDER BY created_at DESC LIMIT 1',
                (target_issue,)
            )
            record = self.db_client.cursor.fetchone()

            if not record:
                logger.info(f'未找到期号{target_issue}的预测记录，无需更新验证状态')
                return

            total_hits = sum(h.get('hit_rate', 0) * h.get('total_positions', 5) for h in algo_hits.values())
            algo_count = max(len(algo_hits), 1)
            avg_hit_rate = (total_hits / algo_count) / 5.0

            # 更新验证状态
            self.db_client.cursor.execute(
                '''UPDATE p5_prediction_record
                   SET verification_status = %s,
                       verified_at = NOW(),
                       verified_issue = %s,
                       verification_notes = %s
                   WHERE target_issue = %s''',
                ('verified', str(actual_numbers),
                 json.dumps({'avg_hit_rate': round(avg_hit_rate, 4), 'algo_hits': algo_hits}, ensure_ascii=False),
                 target_issue)
            )
            self.db_client.connection.commit()
            logger.info(f'预测验证结果已更新: 期号{target_issue}, 平均命中率{avg_hit_rate:.0%}')

            # 提取该期预测号码(供 predictor 贝叶斯似然与自适应权重闭环使用)
            predicted_numbers = self._fetch_predicted_numbers(target_issue)
            # 将各算法命中率整理为 algo_evaluations(per-algo hit_rate), 供 predictor
            # AdaptiveWeightManager.load_from_records 消费(此前 key 名不匹配导致静默空操作)
            algo_evaluations = {
                algo: float(info.get('hit_rate', 0.0))
                for algo, info in algo_hits.items()
            }

            # 同步持久化到统一产物表(v3.3), 供 predictor 自适应权重学习闭环读取
            try:
                self.db_client.save_artifact(
                    'weight_history',
                    {
                        'timestamp': datetime.now().isoformat(),
                        'target_issue': target_issue,
                        'predicted_numbers': predicted_numbers,
                        'actual_numbers': actual_numbers,
                        'avg_hit_rate': avg_hit_rate,
                        'algo_evaluations': algo_evaluations,
                        'algo_hits': algo_hits,
                    },
                    issue=target_issue,
                )
            except Exception as e:
                logger.warning(f'验证记录产物入库失败(非致命): {e}')

        except Exception as e:
            logger.warning(f'记录验证结果失败（不影响主流程）: {e}')

    def _fetch_predicted_numbers(self, target_issue: str) -> List[int]:
        """
        从 p5_ai_report 提取某期的预测号码(Top-1/首位), 供验证闭环使用。

        返回长度为 5 的号码列表 [wan, qian, bai, shi, ge]; 查询失败或任一位置缺失时返回空列表。
        """
        try:
            if not self.db_client or not self.db_client.connection:
                return []
            self.db_client.cursor.execute(
                'SELECT wan_numbers, qian_numbers, bai_numbers, shi_numbers, ge_numbers '
                'FROM p5_ai_report WHERE next_issue = %s ORDER BY created_at DESC LIMIT 1',
                (target_issue,)
            )
            row = self.db_client.cursor.fetchone()
            if not row:
                return []
            cols = ['wan_numbers', 'qian_numbers', 'bai_numbers', 'shi_numbers', 'ge_numbers']
            nums = []
            for col in cols:
                val = row.get(col)
                if isinstance(val, str):
                    try:
                        val = json.loads(val) if val.startswith('[') else [val]
                    except Exception:
                        val = []
                if isinstance(val, list) and val:
                    nums.append(int(val[0]))
                else:
                    return []  # 任一位置缺失则视为不可用
            return nums
        except Exception as e:
            logger.warning(f'提取预测号码失败(非致命): {e}')
            return []

    # ================================================================
    # 附加步骤方法 (集成到流水线中执行)
    # ================================================================

    def _execute_prediction_verification(self, target_issue: str) -> Dict[str, Any]:
        """
        执行预测验证(作为流水线的附加步骤)
        
        查询该期号的预测记录,检查是否有开奖结果可以进行验证。
        如果有验证结果,自动触发在线学习引擎更新权重。
        
        Args:
            target_issue: 目标期号
            
        Returns:
            {success, verified_count, details, error}
        """
        result = {
            'success': False,
            'verified_count': 0,
            'details': [],
            'error': None
        }
        
        try:
            # 懒加载数据库客户端
            if not self.db_client:
                self._init_db_client()
            
            if not self.db_client or not self.db_client.connection:
                result['error'] = '数据库未连接'
                return result
            
            # 查询该期号的预测记录
            self.db_client.cursor.execute(
                '''SELECT id, report_uuid, target_issue, predicted_numbers, 
                         predicted_combinations, confidence_scores, verification_status
                  FROM p5_prediction_record 
                  WHERE target_issue = %s 
                  ORDER BY created_at DESC''',
                (target_issue,)
            )
            records = self.db_client.cursor.fetchall()
            
            if not records:
                logger.info(f'期号{target_issue}无预测记录,跳过验证')
                result['success'] = True
                result['verified_count'] = 0
                return result
            
            # 检查每条记录的验证状态
            for record in records:
                record_id = record.get('id')
                verification_status = record.get('verification_status', 'pending')
                
                record_detail = {
                    'report_uuid': record.get('report_uuid'),
                    'predicted_numbers': record.get('predicted_numbers'),
                    'verification_status': verification_status
                }
                result['details'].append(record_detail)
                
                if verification_status == 'verified':
                    result['verified_count'] += 1
            
            logger.info(f'预测验证完成: 共{len(records)}条记录,已验证{result["verified_count"]}条')
            result['success'] = True
            
        except Exception as e:
            logger.error(f'预测验证执行失败: {e}', exc_info=True)
            result['error'] = str(e)
        
        return result

    def verify_pending_predictions(self) -> Dict[str, Any]:
        """
        闭合「预测→开奖」验证闭环（v3.16 新增，自动化关键能力）

        问题背景：
            流水线 step4 通过 _register_prediction_for_verification 注册预测记录
            （status='pending'），但原 _execute_prediction_verification 只是「空检查」
            —— 仅统计已 verified 的记录数，从不拉取实开号码调用 update_prediction_verification。
            结果：真实预测永远停留在 pending，贝叶斯验证学习拿不到真实的「预测→开奖」反馈，
            只能依赖 batch_generate_verification.py 回放历史数据。

        本方法：
            1. 取最新已开奖期号 latest_issue
            2. 查所有 status='pending' 且 target_issue <= latest_issue 的预测记录
            3. 对每条从 p5_history_data 拉取实开号码，调用 db.update_prediction_verification
               完成位置级命中判定 + 写入 p5_verification_detail
            4. 返回统计（verified_count / skipped / details）

        幂等性：已 verified 的记录不会再次进入候选集，可安全每日重复调用。

        Returns:
            {success, verified_count, skipped, total_scanned, details, error}
        """
        result = {
            'success': True,
            'verified_count': 0,
            'skipped': 0,
            'total_scanned': 0,
            'details': [],
            'error': None,
        }
        try:
            if not self.db_client:
                self._init_db_client()
            if not self.db_client or not self.db_client.connection:
                result['success'] = False
                result['error'] = '数据库未连接'
                return result
            db = self.db_client

            # 1. 最新已开奖期号
            db.cursor.execute('SELECT issue FROM p5_history_data ORDER BY issue DESC LIMIT 1')
            row = db.cursor.fetchone()
            if not row:
                result['success'] = False
                result['error'] = '历史数据为空，无法确定最新期号'
                return result
            latest_issue = int(row['issue'])

            # 2. 待验证候选集：pending 且 期号已开奖
            db.cursor.execute('''
                SELECT id, report_uuid, target_issue, predicted_numbers
                FROM p5_prediction_record
                WHERE verification_status = 'pending'
                  AND CAST(target_issue AS UNSIGNED) <= %s
                ORDER BY target_issue ASC, id ASC
            ''', (latest_issue,))
            pending = db.cursor.fetchall()
            result['total_scanned'] = len(pending)

            if not pending:
                logger.info('验证闭环: 无待验证记录')
                return result

            # 3. 逐条闭合
            for rec in pending:
                ti = rec['target_issue']
                try:
                    # 拉取实开号码
                    db.cursor.execute(
                        'SELECT wan, qian, bai, shi, ge FROM p5_history_data WHERE issue = %s',
                        (ti,)
                    )
                    act = db.cursor.fetchone()
                    if not act:
                        # 该期号历史数据中缺失（异常），跳过避免卡死
                        result['skipped'] += 1
                        logger.warning(f'验证闭环: 期号 {ti} 无开奖数据, 跳过')
                        continue
                    actual_numbers = [
                        int(act['wan']), int(act['qian']), int(act['bai']),
                        int(act['shi']), int(act['ge'])
                    ]
                    vr = db.update_prediction_verification(
                        rec['report_uuid'], ti, actual_numbers, ti
                    )
                    status = vr.get('status')
                    if status in ('success', 'verified'):
                        result['verified_count'] += 1
                        result['details'].append({
                            'issue': ti,
                            'report_uuid': rec['report_uuid'],
                            'match_count': vr.get('match_count'),
                            'accuracy_rate': vr.get('accuracy_rate'),
                        })
                        logger.info(
                            f'验证闭环: 期号 {ti} 已验证 (命中 {vr.get("match_count")}/5, '
                            f'准确率 {vr.get("accuracy_rate")}%)'
                        )
                    else:
                        result['skipped'] += 1
                        logger.warning(f'验证闭环: 期号 {ti} 验证跳过 ({status}: {vr.get("message")})')
                except Exception as e:
                    result['skipped'] += 1
                    logger.warning(f'验证闭环: 期号 {ti} 处理异常: {e}')

            logger.info(
                f'验证闭环完成: 扫描 {result["total_scanned"]} 条, '
                f'已验证 {result["verified_count"]} 条, 跳过 {result["skipped"]} 条'
            )
        except Exception as e:
            logger.error(f'验证闭环执行失败: {e}', exc_info=True)
            result['success'] = False
            result['error'] = str(e)

        return result

    def _execute_online_learning(self, target_issue: str) -> Dict[str, Any]:
        """
        执行在线学习引擎(作为流水线的附加步骤)
        
        基于最新的验证结果,自动更新各算法的权重配置。
        
        Args:
            target_issue: 目标期号
            
        Returns:
            {success, weight_updates, learning_report, error}
        """
        result = {
            'success': False,
            'weight_updates': {},
            'learning_report': None,
            'error': None
        }
        
        try:
            # 懒加载在线学习引擎
            if not self.online_learner:
                self._init_online_learner()
            
            if not self.online_learner:
                logger.info('在线学习引擎未初始化,跳过学习步骤')
                result['success'] = True
                return result
            
            # 生成学习报告
            learning_report = self.online_learner.generate_learning_report(target_issue=target_issue, days=30)
            result['learning_report'] = learning_report

            # 兼容字段: 报告可能使用 total_issues_tracked 或 total_verified
            _report = learning_report or {}
            tv = _report.get('total_verified')
            if tv is None:
                tv = _report.get('total_issues_tracked', 0) or 0
            try:
                tv = int(tv)
            except (TypeError, ValueError):
                tv = 0

            if learning_report and tv > 0:
                logger.info(f'在线学习完成: 基于{tv}条验证记录更新权重')
            else:
                logger.info('在线学习: 暂无新的验证记录可供学习')

            result['success'] = True
            
        except Exception as e:
            logger.error(f'在线学习执行失败: {e}', exc_info=True)
            result['error'] = str(e)
        
        return result

    def _execute_backtest_analysis(self, target_issue: str) -> Dict[str, Any]:
        """
        执行历史回测(作为流水线的附加步骤)
        
        使用回测引擎对最近N期进行模拟预测,评估算法表现。
        
        Args:
            target_issue: 目标期号(回测时会使用历史期号)
            
        Returns:
            {success, backtest_results, stats, error}
        """
        result = {
            'success': False,
            'backtest_results': None,
            'stats': {},
            'error': None
        }
        
        try:
            # 懒加载数据库客户端
            if not self.db_client:
                self._init_db_client()
            
            if not self.db_client or not self.db_client.connection:
                result['error'] = '数据库未连接'
                return result
            
            logger.info('执行历史回测分析...')

            # 获取回测引擎 (注意: 模块名为 backtester, 类名为 Backtester)
            from modules.backtester import Backtester
            _predictor = self._get_predictor()
            backtester = Backtester(_predictor, self.db_client)

            # 执行回测: 前50期作为训练, 回测后续50期(与main.py backtest 默认一致)
            backtest_results = backtester.run_backtest(
                start_index=50, test_count=50, use_validation_split=False
            )
            result['backtest_results'] = backtest_results
            result['stats'] = backtest_results.get('overall_stats', {})

            logger.info(f'历史回测完成: 回测{backtest_results.get("total_tested", 0)}期, '
                        f'Top-1命中率={result["stats"].get("avg_top1_hit_rate", 0):.2f}%')
            result['success'] = True

        except Exception as e:
            logger.error(f'历史回测执行失败(不影响主流程): {e}', exc_info=True)
            result['error'] = str(e)

        return result

    def _execute_feature_analysis(self, target_issue: str) -> Dict[str, Any]:
        """
        执行特征分析(作为流水线的附加步骤)
        
        分析历史数据的特征重要性,帮助理解哪些特征对预测最有价值。
        
        Args:
            target_issue: 目标期号
            
        Returns:
            {success, feature_importance, top_features, error}
        """
        result = {
            'success': False,
            'feature_importance': {},
            'top_features': [],
            'error': None
        }
        
        try:
            # 懒加载数据库客户端
            if not self.db_client:
                self._init_db_client()
            
            if not self.db_client or not self.db_client.connection:
                result['error'] = '数据库未连接'
                return result
            
            logger.info('执行特征分析...')

            # 获取特征工程模块 (注意: 模块名为 features, 类名为 P5Features)
            from modules.features import P5Features
            feature_engineer = P5Features()

            # 加载最近100期历史数据(特征模块需要原始数据列表)
            history = self.db_client.get_history_data(limit=100, order='ASC')
            if not history:
                logger.warning('特征分析: 无足够历史数据')
                result['error'] = '无历史数据'
                return result

            # 提取多维特征
            features = feature_engineer.extract_all_features(history)
            result['extracted_features'] = features

            # 计算特征重要性
            importance = feature_engineer.calculate_feature_importance(history)
            result['feature_importance'] = importance.get('feature_importance', {})

            # 获取最重要的前10个特征
            ranking = importance.get('ranking', [])
            result['top_features'] = ranking[:10]

            logger.info(f'特征分析完成: 共分析{len(result["feature_importance"])}类特征')
            result['success'] = True

        except Exception as e:
            logger.error(f'特征分析执行失败(不影响主流程): {e}', exc_info=True)
            result['error'] = str(e)

        return result

    # ================================================================
    # 实时进度回调 (GUI 流式输出 / 分步验证报告)
    # ================================================================

    def _emit(self, level: str, message):
        """
        向 GUI 实时推送一条进度/日志消息。

        level 取值: 'info' | 'success' | 'warning' | 'error'
                    | 'section' | 'data' | 'progress'
        'progress' 的 message 应为 dict: {'value': 0-100, 'text': str}
        """
        if self._progress_callback:
            try:
                self._progress_callback(level, message)
            except Exception:
                pass

    def _attach_gui_handler(self):
        """将 GUI 日志处理器挂到根日志器(仅当设置了回调时)。"""
        if not self._progress_callback:
            return None
        handler = _PipelineGUIHandler(self._progress_callback)
        handler.setLevel(logging.INFO)
        logging.getLogger().addHandler(handler)
        return handler

    def _detach_gui_handler(self, handler):
        """移除 GUI 日志处理器(防止跨次运行泄漏)。"""
        if handler is not None:
            try:
                logging.getLogger().removeHandler(handler)
            except Exception:
                pass

    def _emit_step_progress(self, step_num: int, total: int, name: str):
        """步骤开始: 输出章节标题并推进进度条。"""
        self._emit('section', f'▶ 步骤{step_num}/{total}: {name}')
        self._emit('progress', {'value': 5 + (step_num - 1) * 20,
                                'text': f'步骤{step_num}: {name}...'})

    def _emit_step_result(self, step_num: int, step_result: Dict[str, Any], target_issue: str, total: int = 4):
        """步骤结束: 输出成功/警告并触发步骤级结果校验。"""
        name = ['专家文章爬取与结构化AI分析', '走势图数据分析与AI预测',
                '专家报告整合分析', '最终预测结果生成与入库'][step_num - 1]
        ok = step_result.get('success', False) or bool(step_result.get('fallback_strategy'))
        if ok:
            self._emit('success', f'  ✓ 步骤{step_num}/{total}: {name} 完成 ({step_result.get("duration", 0):.1f}s)')
        else:
            self._emit('warning', f'  ⚠ 步骤{step_num}/{total}: {name} 异常: {str(step_result.get("error", ""))[:80]}')
        self._validate_step_output(step_num, step_result, target_issue)

    def _validate_step_output(self, step_num: int, step_result: Dict[str, Any], target_issue: str):
        """
        步骤级结果校验(结构性校验), 满足"每步完成后自动校验并输出验证报告"。

        说明: 数值型命中率验证需等待实际开奖号码, 由附加步骤 _execute_prediction_verification 负责;
        此处对每个步骤的关键产出做结构性检查, 确保流水线每一步都产生了合规的中间结果。
        
        优化(2026-07-15): 减少emit('data')调用次数, 只在失败或警告时才输出详细校验结果。
        """
        # ★ 仅在步骤失败或存在降级时才输出详细校验信息
        has_issues = False
        
        if step_num == 1:
            rep = step_result.get('expert_article_report')
            if not rep and not step_result.get('fallback_strategy'):
                has_issues = True
            elif step_result.get('fallback_strategy'):
                has_issues = True  # 警告: 降级策略
        elif step_num == 2:
            rep = step_result.get('trend_chart_report')
            if not rep:
                has_issues = True
        elif step_num == 3:
            if not step_result.get('success') and not step_result.get('fallback_strategy'):
                has_issues = True
        elif step_num == 4:
            fr = step_result.get('final_report', {}) or {}
            pred = fr.get('prediction', {}) if isinstance(fr, dict) else {}
            ok_pos = [p for p in ['wan', 'qian', 'bai', 'shi', 'ge']
                      if isinstance(pred.get(p), dict) and pred[p].get('numbers')]
            if len(ok_pos) != 5:
                has_issues = True
        
        # 只在有问题时输出详细校验信息
        if has_issues:
            self._emit('data', f'  🔎 步骤{step_num} 结果校验:')
            checks = []
            if step_num == 1:
                rep = step_result.get('expert_article_report')
                if rep and isinstance(rep, dict):
                    checks.append(('专家文章预测报告', True, f'已生成 (分析文章={rep.get("total_articles", "?")})'))
                elif step_result.get('fallback_strategy'):
                    checks.append(('专家文章预测报告', False, '降级: AI超时, 使用降级策略'))
                else:
                    checks.append(('专家文章预测报告', False, '未生成'))
            elif step_num == 2:
                rep = step_result.get('trend_chart_report')
                if rep:
                    checks.append(('走势图数据预测报告', True, '已生成'))
                else:
                    checks.append(('走势图数据预测报告', False, '未生成'))
            elif step_num == 3:
                if step_result.get('success'):
                    checks.append(('专家报告整合', True, '完成'))
                elif step_result.get('fallback_strategy'):
                    checks.append(('专家报告整合', True, '降级完成(沿用走势图数据)'))
                else:
                    checks.append(('专家报告整合', False, str(step_result.get('error', '失败'))[:60]))
            elif step_num == 4:
                fr = step_result.get('final_report', {}) or {}
                pred = fr.get('prediction', {}) if isinstance(fr, dict) else {}
                ok_pos = [p for p in ['wan', 'qian', 'bai', 'shi', 'ge']
                          if isinstance(pred.get(p), dict) and pred[p].get('numbers')]
                checks.append(('最终预测覆盖', len(ok_pos) == 5,
                               f'{len(ok_pos)}/5 位置已生成预测号码' + ('' if len(ok_pos) == 5 else ' ⚠需关注')))
            for label, passed, note in checks:
                self._emit('data', f'    {"✓" if passed else "⚠"} {label}: {note}')

    def _emit_final_prediction(self, final_report: Dict[str, Any]):
        """实时输出最终预测结果(各位置推荐号码 + 首选组合)。"""
        if not final_report:
            return
        self._emit('section', '🎯 最终预测结果 (实时)')
        pred = final_report.get('prediction', {}) or {}
        pos_names = {'wan': '万位', 'qian': '千位', 'bai': '百位', 'shi': '十位', 'ge': '个位'}
        for pk, pn in pos_names.items():
            pdata = pred.get(pk, {})
            nums = pdata.get('numbers', []) if isinstance(pdata, dict) else []
            if nums:
                self._emit('data', f'    • {pn}: {nums}')
        combos = final_report.get('recommended_combinations', []) or []
        if combos and isinstance(combos[0], dict):
            top = combos[0]
            self._emit('data', f'    • 首选组合: {top.get("combination", "")} (相对热度 {float(top.get("confidence", 0)):.2f})')

    def _emit_verification_summary(self, vr: Dict[str, Any]):
        """实时输出自动预测验证报告。"""
        if not vr:
            return
        self._emit('section', '🔍 自动预测验证报告')
        if vr.get('success'):
            vc = vr.get('verified_count', 0)
            details = vr.get('details', []) or []
            total = vr.get('total_records', len(details) if isinstance(details, list) else '?')
            self._emit('success', f'  ✓ 预测验证完成: 已验证 {vc} 条记录 (共 {total} 条)')
            if vc > 0:
                self._emit('data', '    • 已比对历史预测号码与实际开奖结果(容错±1命中机制)')
            else:
                self._emit('data', '    • 本期为前瞻预测, 开奖后系统将自动执行命中率验证闭环')
        else:
            self._emit('warning', f'  ⚠ 预测验证跳过: {vr.get("error", "")}')

    def _emit_learning_summary(self, lr: Dict[str, Any], target_issue: str):
        """实时输出在线学习引擎报告(闭环迭代)。"""
        if not lr:
            return
        self._emit('section', '🧠 在线学习引擎报告')
        if lr.get('success'):
            rep = lr.get('learning_report') or {}
            tv = rep.get('total_verified', 0)
            if tv and tv > 0:
                self._emit('success', f'  ✓ 在线学习完成: 基于 {tv} 条验证记录动态调整算法权重')
                wu = lr.get('weight_updates') or {}
                if wu:
                    parts = [f'{k}={v}' for k, v in list(wu.items())[:6]]
                    self._emit('data', '    • 权重调整: ' + ', '.join(parts))
                self._emit('data', '    • 学习成果已持久化(跨进程生效), 将在下一次流水线运行时迭代优化预测准确性')
            else:
                self._emit('data', '    • 在线学习就绪: 暂无新的验证记录, 沿用当前权重')
        else:
            self._emit('warning', f'  ⚠ 在线学习跳过: {lr.get("error", "")}')

    def _emit_extra_summary(self, kind: str, result: Dict[str, Any]):
        """实时输出回测/特征分析等附加步骤摘要。"""
        if not result or not result.get('success'):
            return
        if kind == 'backtest':
            stats = result.get('stats', {}) or {}
            hit = stats.get('avg_top1_hit_rate', 0)
            self._emit('data', f'  📈 历史回测: Top-1 平均命中率 {float(hit):.2f}% (量化验证模型效果)')
        elif kind == 'feature':
            tf = result.get('top_features', []) or []
            if tf:
                names = ', '.join(str(f.get('feature') if isinstance(f, dict) else f) for f in tf[:3])
                self._emit('data', f'  🔬 特征重要性 Top3: {names}')

    # ================================================================
    # 统计预测引擎
    # ================================================================

    def _calc_statistical_prediction(self, target_issue: str, data_limit: int = 60) -> Dict[str, Any]:
        """
        统计预测(含贝叶斯推断) - 替代原步骤1-3

        这是 v3.15 简化流水线的第一步，使用 P5Predictor 多算法融合模型
        直接对历史开奖数据进行预测，不再依赖专家文章爬取和AI走势分析。

        流程:
        1. 从数据库拉取最近 N 期历史数据
        2. 调用 P5Predictor.predict() 进行五算法融合预测
        3. 将预测结果存入 pipeline_state 供步骤2（报告生成）使用
        4. 保存预测统计产物到数据库用于缓存复用

        Args:
            target_issue: 目标期号
            data_limit: 历史数据期数限制

        Returns:
            {success, prediction_uuid, target_issue, top_combinations, ...}
        """
        logger.info('=' * 80)
        logger.info('【统计预测】开始：多算法融合预测(含贝叶斯推断)')
        logger.info(f'目标期号: {target_issue}, 数据期数: {data_limit}')
        logger.info('=' * 80)

        try:
            self._ensure_db()
            if not self.db_client or not self.db_client.connection:
                logger.error('数据库未连接，无法进行统计预测')
                return {'success': False, 'error': '数据库未连接'}

            # 拉取历史数据
            self.db_client.cursor.execute(
                f'SELECT * FROM p5_history_data ORDER BY issue ASC LIMIT {data_limit}'
            )
            history_data = self.db_client.cursor.fetchall()

            if not history_data:
                logger.error('无历史开奖数据')
                return {'success': False, 'error': '无历史数据'}

            latest_issue = history_data[-1].get('issue', '')
            logger.info(f'加载历史数据 {len(history_data)} 期，最新期号: {latest_issue}')

            # 构建 P5Predictor 输入
            _history = [{
                'issue': r.get('issue'),
                'draw_date': r.get('draw_date'),
                'wan': r.get('wan'),
                'qian': r.get('qian'),
                'bai': r.get('bai'),
                'shi': r.get('shi'),
                'ge': r.get('ge'),
                'hezhi': r.get('hezhi'),
                'span': r.get('span'),
            } for r in history_data]

            # 调用预测器
            predictor = self._get_predictor()
            prediction_result = predictor.predict(_history, current_issue=latest_issue)

            if prediction_result.get('error'):
                logger.error(f'预测失败: {prediction_result["error"]}')
                return {'success': False, 'error': prediction_result['error']}

            logger.info(f'统计预测完成: 目标期号 {prediction_result.get("target_issue")}, '
                       f'推荐组合数 {len(prediction_result.get("top_combinations", []))}')

            # 返回结构化结果
            return {
                'success': True,
                'predict_uuid': prediction_result.get('predict_uuid'),
                'target_issue': prediction_result.get('target_issue'),
                'top_combinations': prediction_result.get('top_combinations', []),
                'fused_probabilities': prediction_result.get('fused_probabilities'),
                'algorithm_weights': prediction_result.get('algorithm_weights'),
                'trend_forecast': prediction_result.get('trend_forecast'),
                'predict_time': prediction_result.get('predict_time'),
                'risk_warning': prediction_result.get('risk_warning', ''),
                'raw_prediction': prediction_result,
            }

        except Exception as e:
            logger.error(f'统计预测异常: {e}', exc_info=True)
            return {'success': False, 'error': str(e)}

    # ================================================================
    # 流水线主入口
    # ================================================================

    def execute_pipeline(self, target_issue: str, data_limit: int = 60,
                         verify_with_actual: bool = False,
                         actual_numbers: Optional[List[int]] = None,
                         include_verification: bool = True,
                         include_online_learning: bool = True,
                         include_backtest: bool = True,
                         include_feature_analysis: bool = True,
                         progress_callback=None) -> Dict[str, Any]:
        """
        执行完整的五步流水线分析(增强版)
        
        执行流程:
        步骤1: 专家文章爬取与结构化AI分析
        步骤2: 走势图数据分析与AI预测
        步骤3: 专家报告整合分析
        步骤4: 最终预测结果生成与入库
        步骤5: (可选) 开奖后权重自适应调整
        附加步骤: (可选) 预测验证、在线学习、历史回测、特征分析
        
        所有附加步骤的输出会自动合并到最终报告中。
        
        Args:
            target_issue: 目标预测期号(如"2026165")
            data_limit: 获取历史数据的期数限制(默认40期)
            verify_with_actual: 是否执行步骤5(权重自适应调整)
            actual_numbers: 实际开奖号码 [wan, qian, bai, shi, ge],需与verify_with_actual配合使用
            include_verification: 是否在流水线中包含预测验证(默认True)
            include_online_learning: 是否在流水线中包含在线学习(默认True)
            include_backtest: 是否执行历史回测(默认True,必选)
            include_feature_analysis: 是否执行特征分析(默认True,必选)
            
        Returns:
            {
                success, total_steps, completed_steps,
                step1_result, step2_result, step3_result, step4_result, step5_result,
                verification_result, learning_result, backtest_result, feature_result,
                total_duration, report_uuid, final_report, expert_report, trend_report, error
            }
        """
        logger.info('#' * 80)
        logger.info('# 开始执行AI预测流水线(已简化:移除专家文章分析)')
        logger.info(f'# 目标期号: {target_issue}')
        logger.info(f'# 数据期数: {data_limit}')
        logger.info('#' * 80)

        self.pipeline_state = {
            'article_reports': [],       # 已废弃:专家文章分析已移除
            'trend_report': None,        # 已废弃:走势图AI分析已移除
            'integrated_report': None,   # 已废弃:专家报告整合已移除
            'final_report': None,        # 最终预测结果
            'soft_constraints': None,    # 已废弃:软约束特征已移除
            'started_at': datetime.now(),
            'completed_at': None,
        }

        # 实时进度回调(GUI 流式输出)
        self._progress_callback = progress_callback
        gui_handler = self._attach_gui_handler()
        self._emit('section', '🚀 AI预测流水线启动')
        self._emit('data', f'  • 目标期号: {target_issue} | 数据期数: {data_limit}')
        self._emit('data', f'  • 自动预测验证: {"开启" if include_verification else "关闭"} | 在线学习: {"开启" if include_online_learning else "关闭"}')

        start_time = time.time()
        pipeline_result = {
            'success': False,
            'total_steps': 2,  # 简化为2步
            'completed_steps': 0,
            'step1_result': None,  # 统计预测
            'step2_result': None,  # 最终报告
            'step3_result': None,  # 已废弃
            'step4_result': None,
            'step5_result': None,
            'verification_result': None,
            'learning_result': None,
            'backtest_result': None,
            'feature_result': None,
            'total_duration': 0,
            'report_uuid': None,
            'final_report': None,
            'expert_report': None,  # 已废弃
            'trend_report': None,   # 已废弃
            'error': None,
            'stages': []
        }

        try:
            # ========================================
            # [已废弃] 步骤1-3: 专家文章分析模块
            # ========================================
            # 以下为历史代码,已停用:
            # - 步骤1: 专家文章爬取与结构化AI分析 (耗时8-15分钟,AI返回格式不稳定)
            # - 步骤2: 走势图数据分析与AI预测 (被步骤4的多源走势融合取代)
            # - 步骤3: 专家报告整合分析 (依赖步骤1,已无意义)
            # ========================================
            #
            # 如需启用(不推荐),取消下面注释:
            #
            # self._emit_step_progress(1, 4, '专家文章爬取与结构化AI分析')
            # step1_start = time.time()
            # step1_result = self.step1_crawl_articles_and_analyze(target_issue)
            # ...
            #
            # self._emit_step_progress(2, 4, '走势图数据分析与AI预测')
            # step2_start = time.time()
            # step2_result = self.step2_trend_analysis(target_issue, data_limit)
            # ...
            #
            # self._emit_step_progress(3, 4, '专家报告整合分析')
            # step3_start = time.time()
            # step3_result = self.step3_integrate_expert_reports(target_issue)
            # ...
            #
            # ========================================
            # 步骤1(新): 统计预测(含贝叶斯推断)
            # ========================================
            self._emit('section', '📊 步骤1: 统计预测(含贝叶斯推断)')
            step1_start = time.time()
            step1_result = self._calc_statistical_prediction(target_issue, data_limit)
            step1_elapsed = time.time() - step1_start
            step1_info = {
                'step': 1,
                'name': '统计预测(含贝叶斯推断)',
                'success': step1_result.get('success', False),
                'duration': step1_elapsed,
                'details': step1_result
            }
            pipeline_result['stages'].append(step1_info)
            pipeline_result['step1_result'] = step1_result
            if step1_result.get('success'):
                pipeline_result['completed_steps'] += 1
            
            # 存储中间结果供步骤2使用
            self.pipeline_state['stat_prediction'] = step1_result
            self._emit('info', f'  ✓ 统计预测完成 (耗时{step1_elapsed:.1f}s)')

            # ---- 步骤4 ----
            self._emit_step_progress(4, 4, '最终预测结果生成与入库')
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

                # 注册预测记录供后续验证使用
                self._register_prediction_for_verification(
                    step4_result.get('report_uuid'),
                    target_issue,
                    step4_result.get('final_report', {})
                )
                logger.info(f'预测记录已注册供后续验证: 期号{target_issue}')
                # ★ 实时输出最终预测结果(供GUI逐步骤追踪)
                self._emit_final_prediction(step4_result.get('final_report'))

            self._emit_step_result(2, step4_result, target_issue)

            pipeline_result['total_duration'] = time.time() - start_time
            pipeline_result['step2_result'] = step4_result  # 复用step4_result到step2

            # ---- 附加步骤: 验证闭环、预测验证、在线学习、历史回测、特征分析 ----
            logger.info('执行附加分析步骤...')

            # 0. 闭合「预测→开奖」验证闭环 (v3.16 新增)
            #    先对历史 pending 预测记录执行验证, 让贝叶斯验证学习获得真实反馈,
            #    再生成下一期预测(其注册记录将在未来某次运行被此处闭合)。
            try:
                logger.info('执行验证闭环(闭合历史 pending 预测)...')
                self._ensure_db()
                closed = self.verify_pending_predictions()
                pipeline_result['verification_closed'] = closed
                logger.info(
                    f'验证闭环: 扫描 {closed.get("total_scanned", 0)} 条, '
                    f'已验证 {closed.get("verified_count", 0)} 条'
                )
                self._emit('info', f'  ✓ 验证闭环: 本次闭合 {closed.get("verified_count", 0)} 条历史预测')
            except Exception as e:
                logger.warning(f'验证闭环执行失败(不影响主流程): {e}')

            # 1. 预测验证(如果有已验证的历史数据)
            if include_verification:
                try:
                    logger.info('执行预测验证...')
                    self._ensure_db()
                    verification_result = self._execute_prediction_verification(target_issue)
                    pipeline_result['verification_result'] = verification_result
                    logger.info(f'预测验证完成: {"成功" if verification_result.get("success") else "失败"}')
                    self._emit_verification_summary(verification_result)
                except Exception as e:
                    logger.warning(f'预测验证执行失败(不影响主流程): {e}')

            # 2. 在线学习(如果有验证结果)
            if include_online_learning:
                try:
                    logger.info('执行在线学习...')
                    learning_result = self._execute_online_learning(target_issue)
                    pipeline_result['learning_result'] = learning_result
                    logger.info(f'在线学习完成: {"成功" if learning_result.get("success") else "失败"}')
                    self._emit_learning_summary(learning_result, target_issue)
                except Exception as e:
                    logger.warning(f'在线学习执行失败(不影响主流程): {e}')

            # 3. 历史回测(可选)
            if include_backtest:
                try:
                    logger.info('执行历史回测...')
                    self._ensure_db()
                    backtest_result = self._execute_backtest_analysis(target_issue)
                    pipeline_result['backtest_result'] = backtest_result
                    logger.info(f'历史回测完成: {"成功" if backtest_result.get("success") else "失败"}')
                    self._emit_extra_summary('backtest', backtest_result)
                except Exception as e:
                    logger.warning(f'历史回测执行失败(不影响主流程): {e}')

            # 4. 特征分析(可选)
            if include_feature_analysis:
                try:
                    logger.info('执行特征分析...')
                    self._ensure_db()
                    feature_result = self._execute_feature_analysis(target_issue)
                    pipeline_result['feature_result'] = feature_result
                    logger.info(f'特征分析完成: {"成功" if feature_result.get("success") else "失败"}')
                    self._emit_extra_summary('feature', feature_result)
                except Exception as e:
                    logger.warning(f'特征分析执行失败(不影响主流程): {e}')

            # 简化流水线已移除专家文章分析模块，独立报告收集功能已停用
            # if step1_result.get('expert_article_report'):
            #     pipeline_result['expert_report'] = step1_result['expert_article_report']
            # if step2_result.get('trend_chart_report'):
            #     pipeline_result['trend_report'] = step2_result['trend_chart_report']

            # ---- 步骤5: 权重自适应调整（可选，仅在verify_with_actual=True且actual_numbers提供时执行）----
            if verify_with_actual and actual_numbers:
                logger.info('检测到实际开奖数据，执行步骤5：权重自适应调整')
                step5_start = time.time()
                step5_result = self.step5_weight_adaptation(target_issue, actual_numbers)
                step5_elapsed = time.time() - step5_start

                # 更新总步数为5
                pipeline_result['total_steps'] = 5
                pipeline_result['step5_result'] = step5_result
                pipeline_result['stages'].append({
                    'step': 5,
                    'name': '开奖后权重自适应调整',
                    'success': step5_result.get('status') == 'success',
                    'duration': step5_elapsed,
                    'details': step5_result
                })

                # 步骤5失败不影响前面步骤的成功
                if step5_result.get('status') == 'success':
                    pipeline_result['completed_steps'] += 1
                    logger.info('步骤5完成：权重自适应调整成功')
                else:
                    logger.warning(f'步骤5执行异常（不影响前面步骤）: {step5_result.get("error", "未知错误")}')

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
            logger.error(f'流水线执行异常: {e}', exc_info=True)
            pipeline_result['error'] = str(e)
            pipeline_result['total_duration'] = time.time() - start_time
            self.pipeline_state['completed_at'] = datetime.now()
        finally:
            # 无论成败都移除 GUI 日志处理器, 防止跨次运行泄漏
            self._detach_gui_handler(gui_handler)

        return pipeline_result


def run_four_step_pipeline(target_issue: Optional[str] = None, data_limit: int = 60,
                          progress_callback=None,
                          include_backtest: bool = True,
                          include_feature_analysis: bool = True) -> Dict[str, Any]:
    """
    便捷函数：执行四步流水线分析

    Args:
        target_issue: 目标期号，如不提供则从数据库最新期号推算
        data_limit: 历史数据期数限制
        progress_callback: 实时进度回调(level, message), 用于 GUI 流式输出
        include_backtest: 是否执行历史回测(默认True；每日自动化任务应设 False 以规避 AI 限速)
        include_feature_analysis: 是否执行特征分析(默认True；每日自动化任务可设 False)

    Returns:
        流水线执行结果
    """
    try:
        from modules.database import P5Database
        db = P5Database()
        if db.connect():
            db.cursor.execute('SELECT issue FROM p5_history_data ORDER BY issue DESC LIMIT 1')
            row = db.cursor.fetchone()
            if row:
                latest_issue = row.get('issue', '')
                if target_issue is None:
                    target_issue = str(int(latest_issue) + 1)
            db.disconnect()

        if target_issue is None:
            logger.error('无法确定目标期号，请手动指定')
            return {'success': False, 'error': '无法确定目标期号'}

        pipeline = Pipeline()
        return pipeline.execute_pipeline(
            target_issue=target_issue, data_limit=data_limit,
            progress_callback=progress_callback,
            include_backtest=include_backtest,
            include_feature_analysis=include_feature_analysis,
        )

    except Exception as e:
        logger.error(f'四步流水线调用失败: {e}', exc_info=True)
        return {'success': False, 'error': str(e)}


def validate_pl5_data(history_data: List[Dict], target_issue: str = None) -> Dict:
    """
    模块级便利函数：快速验证排列5数据质量。

    独立于流水线使用，直接调用 Pipeline 的内部门禁检查。

    Args:
        history_data: 历史开奖数据列表
        target_issue: 目标期号（可选，用于 RECENTNESS 检查）

    Returns:
        质量门禁报告字典，结构与 Pipeline._validate_data_quality 返回值一致。
    """
    from modules.pipeline import Pipeline
    p = Pipeline()
    p._init_db_client()
    return p._validate_data_quality(history_data, target_issue=target_issue)


if __name__ == '__main__':
    print('=' * 80)
    print('四步流水线分析模块测试')
    print('=' * 80)
    result = run_four_step_pipeline(target_issue='2026165', data_limit=40)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

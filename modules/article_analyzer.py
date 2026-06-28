"""
文章内容AI分析模块（完整版双阶段AI分析）

实现完整的6步文章分析工作流：
1. 爬取文章内容（YDNiuSpider，支持分页和期号过滤）
2. 第一次AI分析：结构化整理（生成JSON结构化的文章分析）
3. 存储到Redis（文章原始数据+AI分析结果，7天过期）
4. 从Redis加载数据（按期号索引）
5. 第二次AI分析：整合多源数据并生成综合预测报告
6. 存储最终报告到数据库（MySQL，含UUID追踪）

调用路径：
    main.py → run_article_analysis() / run_save_articles_to_redis()
           → ArticleAnalyzer.analyze_article_workflow() / save_all_articles_bulk_to_redis()

与 article_processor.py 的区别：
    - ArticleAnalyzer: 完整6步流程，含双阶段AI分析+数据库存储
    - ArticleProcessor: 简化4步流程，仅单次AI分析+Redis存储
    - 两者共享: 爬虫(YDNiuSpider)、Redis、AI客户端

懒加载模式：
    所有外部组件（爬虫/Redis/AI/数据库）采用懒加载模式，
    仅在首次使用时通过 _init_*() 方法初始化，
    避免导入时因环境依赖缺失而失败。

已知冗余（待后续重构）:
    - _extract_issue_from_article() 与 article_processor.py::_extract_issue_from_content() 功能重复
    - save_to_redis() / save_all_articles_to_redis() 与 article_processor.py::save_report_to_redis() 逻辑类似
    - 第二次AI分析回退逻辑（second_ai_analysis中）较为复杂，可考虑提取为独立方法
"""

import logging
import os
import json
import re
from datetime import datetime
from typing import Dict, List, Any, Optional

os.makedirs('logs', exist_ok=True)

# 说明：文章分析链路涉及爬虫、AI、Redis与数据库等外部依赖。
# 为提高模块可导入性，本项目采用延迟/懒加载（在函数内部导入依赖模块）的模式，
# 在修改本文件时请保持该风格以避免导入时出现环境依赖错误。

logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler = logging.FileHandler('logs/article_analyzer.log', encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


class ArticleAnalyzer:
    """
    文章内容AI分析器（完整版双阶段分析）

    整合爬虫、AI分析、Redis缓存、数据库存储的完整分析工作流。

    核心工作流 (analyze_article_workflow):
    步骤1: 爬取文章 (crawl_all_articles → YDNiuSpider)
    步骤2: 第一次AI分析 (first_ai_analysis → ERNIEAIAnalyzer)
    步骤3: Redis存储 (save_all_articles_to_redis)
    步骤4: 从Redis加载 (load_from_redis)
    步骤5: 获取历史数据 (_fetch_data_from_database → ERNIEAIAnalyzer)
    步骤6: 第二次AI分析 (second_ai_analysis → 整合多源数据+回退方案)
    步骤7: 数据库存储 (save_to_database → P5Database)

    批量保存工作流 (save_all_articles_bulk_to_redis):
    步骤1: 爬取文章
    步骤2: 初始化Redis
    步骤3: (可选)初始化PredictionExtractor
    步骤4: 批量处理（HTML清洗+AI分析+Redis存储）

    组件初始化（全部懒加载）:
    - _init_spider(): YDNiuSpider 爬虫
    - _init_redis(): RedisClient 缓存
    - _init_ai_client(): ERNIEAIAnalyzer AI客户端
    - _init_db_client(): P5Database MySQL数据库

    调用方:
    - main.py: run_article_analysis(), run_save_articles_to_redis()
    - gui.py: _execute_optimized_p5_ai()
    """

    def __init__(self):
        """初始化分析器（所有组件采用懒加载，首次使用时才初始化）"""
        self.spider = None
        self.redis_client = None
        self.ai_client = None
        self.db_client = None

    def _init_spider(self):
        """
        懒加载初始化爬虫模块 (YDNiuSpider)

        仅在首次调用爬取相关方法时初始化，
        避免导入时因网络/依赖问题导致整个模块加载失败。
        """
        try:
            from modules.ydniu_spider import YDNiuSpider
            self.spider = YDNiuSpider()
            logger.info('爬虫模块初始化成功')
        except ImportError:
            logger.error('无法导入爬虫模块')

    def _init_redis(self):
        """
        懒加载初始化Redis客户端 (RedisClient)

        初始化后立即检查连接状态，连接失败记录warning但不阻塞后续流程。
        """
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
        """
        懒加载初始化AI客户端 (ERNIEAIAnalyzer)

        ERNIEAIAnalyzer封装了百度Qianfan API的调用和响应解析，
        本模块通过其 _call_ai_model() 和 _parse_ai_response() 进行AI交互。
        """
        try:
            from modules.ernie_ai_analyzer import ERNIEAIAnalyzer
            self.ai_client = ERNIEAIAnalyzer()
            logger.info('AI客户端初始化成功')
        except ImportError:
            logger.error('无法导入AI客户端模块')

    def _init_db_client(self):
        """
        懒加载初始化数据库客户端 (P5Database)

        初始化后立即尝试连接MySQL，
        连接失败记录warning但不阻塞后续流程（允许Redis缓存降级）。
        """
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
        构建第一次AI分析提示词：要求输出结构化JSON

        输出JSON包含字段:
        - data_source: 固定"亿点牛文章分析"
        - article_info: {title, author, publish_time, url}
        - analysis_time: 分析时间
        - issue_number: 提取的期号
        - forecast_numbers: {wan, qian, bai, shi, ge} 各位置号码列表
        - recommended_combinations: 推荐组合
        - key_points: 关键分析点
        - trend_analysis: 趋势分析总结文本
        - confidence_level: 置信度（高/中/低）
        - risk_warning: 风险提示
        - summary: 文章内容总结

        Args:
            article_data: 文章数据字典（含title/author/publish_time/url/content等字段）

        Returns:
            完整的提示词文本
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
        "wan": [],
        "qian": [],
        "bai": [],
        "shi": [],
        "ge": []
    },
    "recommended_combinations": [],
    "key_points": [],
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
        构建第二次AI分析提示词：整合5大数据源进行深度预测

        数据源:
        一、文章内容分析数据（Redis）：文章AI分析结果、推荐号码、关键点、趋势分析
        二、历史开奖数据（MySQL）：最近30期记录，含和值/跨度/奇偶比/大小比
        三、基础走势图数据（MySQL）：最近20期走势（含日期、和值、奇偶、大小）
        四、各位置走势统计（MySQL）：万/千/百/十/个位独立走势，含热号/冷号/最大遗漏

        输出要求: 严格JSON格式，要求分析5个维度（文章内容+历史+走势+奇偶大小+和值跨度）

        Args:
            redis_data: Redis中保存的文章分析数据（含articles和ai_analysis）
            first_ai_result: 第一次AI的结构化分析结果
            db_history: 数据库历史数据（含history_data和trend_data和各位置走势）

        Returns:
            完整的提示词文本（通常2000+字符）
        """
        prompt_parts = []

        prompt_parts.append("""
你是一位专业的排列5彩票数据深度分析专家。请基于以下多源数据进行综合分析和预测下一期号码。

【彩种规则】
- 排列5：5位数字，每位0-9，每天开奖
- 号码位置：万位、千位、百位、十位、个位
- 和值范围：0-45
- 跨度范围：0-9

【数据来源说明】
本次分析整合了以下三类数据：
1. 文章内容分析：从亿点牛网站爬取并经过AI结构化整理的文章内容
2. 历史开奖数据：最近30期历史开奖记录及各位置走势统计
3. 各位置走势图数据：万位、千位、百位、十位的独立走势数据
4. 初步分析结果：AI模型对文章内容的初步分析

请综合以上所有数据，进行深度分析，生成下一期的号码预测报告。
""")

        prompt_parts.append("\n" + "=" * 60)
        prompt_parts.append("一、文章内容分析数据")
        prompt_parts.append("=" * 60)

        if redis_data:
            ai_analysis = redis_data.get('ai_analysis', {})
            prompt_parts.append(f"数据来源：{ai_analysis.get('data_source', '未知')}")
            prompt_parts.append(f"分析时间：{ai_analysis.get('analysis_time', '未知')}")
            prompt_parts.append(f"期号：{ai_analysis.get('issue_number', '未知')}")

            if ai_analysis.get('forecast_numbers'):
                nums = ai_analysis['forecast_numbers']
                prompt_parts.append("\n【文章推荐号码】")
                for pos_name, pos_key in zip(['万位', '千位', '百位', '十位', '个位'],
                                             ['wan', 'qian', 'bai', 'shi', 'ge']):
                    if nums.get(pos_key):
                        prompt_parts.append(f"  {pos_name}：{nums[pos_key]}")

            if ai_analysis.get('key_points'):
                prompt_parts.append("\n【关键点】")
                for point in ai_analysis['key_points']:
                    prompt_parts.append(f"  - {point}")

            if ai_analysis.get('trend_analysis'):
                prompt_parts.append(f"\n【趋势分析】\n{ai_analysis['trend_analysis']}")

            if ai_analysis.get('summary'):
                prompt_parts.append(f"\n【文章总结】\n{ai_analysis['summary']}")

        prompt_parts.append("\n" + "=" * 60)
        prompt_parts.append("二、历史开奖数据（最近30期）")
        prompt_parts.append("=" * 60)

        if db_history:
            prompt_parts.append(f"数据条数：{db_history.get('data_count', 0)}")
            prompt_parts.append(f"最新期号：{db_history.get('latest_issue', '未知')}")

            if db_history.get('history_data'):
                recent = db_history['history_data'][:20]
                prompt_parts.append("\n最近20期开奖记录：")
                for item in recent:
                    issue = item.get('issue', '')
                    wan = item.get('wan', 0)
                    qian = item.get('qian', 0)
                    bai = item.get('bai', 0)
                    shi = item.get('shi', 0)
                    ge = item.get('ge', 0)
                    hezhi = item.get('hezhi', '')
                    span = item.get('span', '')
                    odd_even = item.get('odd_even_ratio', '')
                    big_small = item.get('big_small_ratio', '')
                    prompt_parts.append(f"  {issue}: {wan}{qian}{bai}{shi}{ge} 和值:{hezhi} 跨度:{span} 奇偶:{odd_even} 大小:{big_small}")

        prompt_parts.append("\n" + "=" * 60)
        prompt_parts.append("三、基础走势图数据（最近30期）")
        prompt_parts.append("=" * 60)

        if db_history and db_history.get('trend_data'):
            trend_data = db_history['trend_data'][:20]
            prompt_parts.append("\n【走势图数据】")
            for item in trend_data:
                issue = item.get('issue', '')
                wan = item.get('wan', 0)
                qian = item.get('qian', 0)
                bai = item.get('bai', 0)
                shi = item.get('shi', 0)
                ge = item.get('ge', 0)
                draw_date = item.get('draw_date', '')
                hezhi = item.get('hezhi', '')
                odd_even = item.get('odd_even_ratio', '')
                big_small = item.get('big_small_ratio', '')
                prompt_parts.append(f"  {issue} [{draw_date}]: {wan}{qian}{bai}{shi}{ge} 和值:{hezhi} 奇偶:{odd_even} 大小:{big_small}")

        prompt_parts.append("\n" + "=" * 60)
        prompt_parts.append("四、各位置走势统计数据")
        prompt_parts.append("=" * 60)

        position_info = [
            ('万位', 'wan_trend_data', 'wan_number'),
            ('千位', 'qian_trend_data', 'qian_number'),
            ('百位', 'bai_trend_data', 'bai_number'),
            ('十位', 'shi_trend_data', 'shi_number'),
            ('个位', 'ge_trend_data', 'ge_number'),
        ]

        for pos_name, pos_key, num_key in position_info:
            if db_history and db_history.get(pos_key):
                pos_data = db_history[pos_key][:15]
                prompt_parts.append(f"\n【{pos_name}走势数据（最近15期）】")

                recent_values = []
                hot_nums = {}
                cold_nums = {}
                max_omission = 0

                for item in pos_data:
                    num = item.get(num_key, 0)
                    recent_values.append(num)
                    hot_nums[num] = hot_nums.get(num, 0) + 1
                    omission = item.get('omission', 0)
                    max_omission = max(max_omission, omission)

                sorted_nums = sorted(hot_nums.items(), key=lambda x: x[1], reverse=True)
                hot = [n for n, _ in sorted_nums[:3]]
                cold = [n for n, _ in sorted_nums[-3:]]

                prompt_parts.append(f"  近期走势: {recent_values}")
                prompt_parts.append(f"  热号(高频): {hot}")
                prompt_parts.append(f"  冷号(低频): {cold}")
                prompt_parts.append(f"  最大遗漏: {max_omission}")

        prompt_parts.append("\n" + "=" * 60)
        prompt_parts.append("五、分析要求")
        prompt_parts.append("=" * 60)

        prompt_parts.append("""
请基于以上多源数据，进行深度综合分析，输出JSON格式的预测报告。

【分析维度要求】
1. 综合文章内容和历史数据进行分析
2. 参考各位置的走势统计（热号、冷号、遗漏值）
3. 分析奇偶比、大小比的变化趋势
4. 考虑和值与跨度的合理范围
5. 结合文章中的专家观点和推荐

【输出格式要求】
请严格按照以下JSON格式输出，不要包含任何额外文字（不要有markdown标记）：

{
    "data_source": "亿点牛文章+历史数据综合AI分析",
    "analysis_time": "YYYY-MM-DD HH:MM:SS",
    "model_version": "模型版本号",
    "data_period": "分析数据周期描述",
    "current_issue": "当前最新期号",
    "next_issue": "预测目标期号",
    "prediction": {
        "wan": {
            "numbers": [1, 3, 5],
            "confidence": [0.85, 0.72, 0.65],
            "reason": "推荐理由"
        },
        "qian": {
            "numbers": [2, 4, 6],
            "confidence": [0.80, 0.75, 0.68],
            "reason": "推荐理由"
        },
        "bai": {
            "numbers": [3, 5, 7],
            "confidence": [0.82, 0.76, 0.70],
            "reason": "推荐理由"
        },
        "shi": {
            "numbers": [4, 6, 8],
            "confidence": [0.78, 0.74, 0.70],
            "reason": "推荐理由"
        },
        "ge": {
            "numbers": [5, 7, 9],
            "confidence": [0.88, 0.78, 0.72],
            "reason": "推荐理由"
        }
    },
    "trend_analysis": {
        "summary": "整体趋势分析总结",
        "wan": "万位趋势分析...",
        "qian": "千位趋势分析...",
        "bai": "百位趋势分析...",
        "shi": "十位趋势分析...",
        "ge": "个位趋势分析..."
    },
    "reasoning_process": [
        "第一步推理过程...",
        "第二步推理过程...",
        "第三步推理过程..."
    ],
    "recommended_combinations": [
        {"combination": "13524", "confidence": 0.75, "reason": "组合推荐理由"},
        {"combination": "35746", "confidence": 0.70, "reason": "组合推荐理由"},
        {"combination": "57968", "confidence": 0.65, "reason": "组合推荐理由"}
    ],
    "statistical_features": {
        "hezhi_range": "和值范围",
        "span_range": "跨度范围",
        "odd_even_ratio": "奇偶比偏好",
        "big_small_ratio": "大小比偏好",
        "hot_numbers": "热号描述",
        "cold_numbers": "冷号描述",
        "key_patterns": ["模式1", "模式2", "模式3"]
    },
    "key_conclusions": [
        "关键结论1...",
        "关键结论2...",
        "关键结论3..."
    ],
    "risk_warning": "风险提示文本"
}

注意事项：
1. prediction字段中每个位置必须包含numbers数组、confidence数组和reason字符串
2. recommended_combinations必须是对象数组，每个对象含combination(5位号码字符串)、confidence(0-1浮点数)、reason
3. reasoning_process必须是字符串数组，每条是完整推理过程
4. trend_analysis必须包含summary和各位置分析
5. key_conclusions必须是字符串数组
6. 严格按照以上字段名输出，不要省略任何字段
""")

        return "\n".join(prompt_parts)

    def first_ai_analysis(self, article_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        第一次AI分析：结构化整理文章内容为JSON

        流程:
        1. 构建分析提示词（_build_first_analysis_prompt）
        2. 调用AI模型（ERNIEAIAnalyzer._call_ai_model, max_tokens=4000, temperature=0.5）
        3. 解析AI响应JSON（ERNIEAIAnalyzer._parse_ai_response）

        Args:
            article_data: 文章数据（含title/content等字段）

        Returns:
            AI分析结果JSON字典（含issue_number/forecast_numbers/confidence_level等），
            失败返回None
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
        保存单篇文章数据到Redis（原始数据+AI分析结果）

        存储键:
        - 原始数据: RedisClient.save_raw_data(issue, article_data)
        - AI分析: kpluckynumber:pl5:ai:{issue} (7天过期，JSON格式)
          {issue, article_data, ai_analysis, save_time}

        Args:
            issue: 期号（作为键名的一部分）
            article_data: 文章原始数据
            ai_result: 第一次AI的结构化分析结果

        Returns:
            是否保存成功
        """
        logger.info('保存数据到Redis...')

        self._init_redis()

        if not self.redis_client:
            logger.error('Redis客户端未初始化')
            return False

        if not self.redis_client.is_connected():
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
            logger.error(f'保存到Redis失败: {e}', exc_info=True)
            return False

    def save_all_articles_to_redis(self, issue: str, articles: List[Dict[str, Any]], ai_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        批量保存所有爬取的文章到Redis

        流程:
        1. 遍历articles列表，为每篇生成article_id
        2. 逐篇调用redis_client.save_article_data()（7天过期）
        3. 最后保存AI分析结果到 kpluckynumber:pl5:ai:{issue}

        注意: 与 save_all_articles_bulk_to_redis() 不同，
             本方法先做第一次AI分析再保存，后者在保存过程中做AI分析。

        Args:
            issue: 期号
            articles: 文章数据列表
            ai_result: 第一次AI的结构化分析结果

        Returns:
            {success, saved_count, failed_count, total_count} 或 {success: False, error}
        """
        logger.info(f'保存所有文章数据到Redis（共{len(articles)}篇）...')

        self._init_redis()

        if not self.redis_client:
            logger.error('Redis客户端未初始化')
            return {'success': False, 'error': 'Redis客户端未初始化', 'saved_count': 0}

        if not self.redis_client.is_connected():
            logger.error('Redis客户端未连接')
            return {'success': False, 'error': 'Redis客户端未连接', 'saved_count': 0}

        try:
            saved_count = 0
            failed_count = 0

            # 保存每一篇文章
            for idx, article in enumerate(articles):
                try:
                    # 生成文章唯一ID
                    article_id = self.redis_client.generate_article_id(article.get('url', ''), idx)

                    # 保存文章数据
                    article_with_issue = article.copy()
                    article_with_issue['issue'] = issue
                    article_with_issue['article_index'] = idx

                    success = self.redis_client.save_article_data(article_id, article_with_issue, expire_days=7)

                    if success:
                        saved_count += 1
                        logger.info(f'文章{idx + 1}保存成功: {article.get("title", "未知")[:50]}')
                    else:
                        failed_count += 1
                        logger.warning(f'文章{idx + 1}保存失败: {article.get("title", "未知")[:50]}')

                except Exception as e:
                    failed_count += 1
                    logger.error(f'文章{idx + 1}保存异常: {e}')

            # 保存AI分析结果（使用统一键名）
            redis_data = {
                'issue': issue,
                'articles_count': len(articles),
                'articles': articles,
                'ai_analysis': ai_result,
                'save_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }

            key = self.redis_client.get_ai_analysis_key(issue)
            self.redis_client.client.setex(key, 86400 * 7, json.dumps(redis_data, ensure_ascii=False))

            logger.info(f'所有文章数据保存完成: 成功{saved_count}篇, 失败{failed_count}篇')

            return {
                'success': True,
                'saved_count': saved_count,
                'failed_count': failed_count,
                'total_count': len(articles)
            }

        except Exception as e:
            logger.error(f'保存所有文章到Redis失败: {e}', exc_info=True)
            return {'success': False, 'error': str(e), 'saved_count': 0}

    def load_from_redis(self, issue: str) -> Optional[Dict[str, Any]]:
        """
        从Redis按期号加载AI分析数据

        键名: kpluckynumber:pl5:ai:{issue}（通过redis_client.get_ai_analysis_key生成）

        Args:
            issue: 期号

        Returns:
            包含 articles/ai_analysis/save_time 的数据字典，未找到返回None
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
        第二次AI分析：整合多源数据进行综合预测

        流程:
        1. 提取第一次AI分析结果（redis_data['ai_analysis']）
        2. 构建二次分析提示词（_build_second_analysis_prompt）
        3. 调用AI模型（max_tokens=8000, temperature=0.7）
        4. 解析AI响应JSON

        回退方案（当第二次AI调用失败时）:
        - 从第一次AI分析中提取 forecast_numbers 作为预测号码
        - 置信度设为0.0（表示回退结果）
        - 尝试推算next_issue（latest_issue + 1）
        - 确保后续流程（保存DB、展示报告）可继续执行

        Args:
            redis_data: Redis中加载的数据（含articles和ai_analysis）
            db_history: 数据库历史数据（含history_data/trend_data/各位置走势）

        Returns:
            预测结果JSON（含prediction/trend_analysis/reasoning_process/
            recommended_combinations/risk_warning），失败时返回回退结果
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
        prompt = self._build_second_analysis_prompt(redis_data, first_ai_result, db_history)
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

        try:
            ai_response = self.ai_client._call_ai_model(messages=messages, max_tokens=8000, temperature=0.7)
            if not ai_response:
                logger.error('AI模型调用失败')
                raise RuntimeError('AI模型调用失败')

            # 解析AI响应
            ai_result = self.ai_client._parse_ai_response(ai_response)
            if not ai_result:
                logger.error('AI响应解析失败')
                raise RuntimeError('AI响应解析失败')

            logger.info('第二次AI分析完成')
            logger.info(f'预测期号: {ai_result.get("next_issue", "未知")}')

            return ai_result

        except Exception as e:
            # 发生错误时，记录日志并返回一个基于第一次AI分析与历史数据的回退报告，
            # 以便后续流程（保存到DB、展示报告）能够继续执行。
            logger.error(f'第二次AI分析异常，使用回退方案继续: {e}', exc_info=True)

            # 尝试从redis_data中提取第一次AI分析结果
            first_ai = redis_data.get('ai_analysis', {}) if isinstance(redis_data, dict) else {}

            # 构建回退预测：优先使用第一次AI分析中的 forecast_numbers 字段
            forecast = first_ai.get('forecast_numbers', {}) if isinstance(first_ai, dict) else {}

            prediction = {}
            for pos_key in ['wan', 'qian', 'bai', 'shi', 'ge']:
                nums = forecast.get(pos_key, []) if isinstance(forecast, dict) else []
                if not isinstance(nums, list):
                    nums = [nums] if nums is not None else []
                prediction[pos_key] = {
                    'numbers': nums,
                    'confidence': [0.0] * len(nums),
                    'reason': '二次AI分析失败，回退使用文章初步分析结果作为推荐（置信度占位0.0）'
                }

            # 组合回退结果
            latest_issue = ''
            try:
                latest_issue = db_history.get('latest_issue', '') if isinstance(db_history, dict) else ''
            except Exception:
                latest_issue = ''

            next_issue = ''
            try:
                if latest_issue and str(latest_issue).isdigit():
                    next_issue = str(int(latest_issue) + 1)
                else:
                    # 尝试从 first_ai 中取 issue_number
                    fi = first_ai.get('issue_number') if isinstance(first_ai, dict) else None
                    if fi and str(fi).isdigit():
                        next_issue = str(int(fi) + 1)
            except Exception:
                next_issue = ''

            fallback = {
                'data_source': '回退：文章初步分析+历史数据',
                'analysis_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'model_version': getattr(self.ai_client, 'model_name', 'unknown'),
                'current_issue': latest_issue,
                'next_issue': next_issue,
                'prediction': prediction,
                'trend_analysis': {},
                'reasoning_process': ['二次AI分析失败，回退使用初步文章分析结果'],
                'recommended_combinations': [],
                'risk_warning': '二次AI分析失败，结果基于回退逻辑，请谨慎使用'
            }

            return fallback

    def save_to_database(self, final_report: Dict[str, Any], db_history: Dict[str, Any]) -> Optional[str]:
        """
        保存最终AI分析报告到MySQL数据库

        存储字段映射:
        - report_content: 推理过程文本（从reasoning_process提取，支持list和str）
        - next_issue: 预测目标期号
        - trend_analysis: 趋势分析JSON
        - recommended_combinations: 推荐组合JSON
        - key_conclusions: 关键结论（优先取key_conclusions，回退key_features）
        - risk_warning: 风险提示

        通过 P5Database.insert_ai_report() 写入，
        自动生成UUID作为追踪标识。

        Args:
            final_report: 第二次AI分析的完整结果
            db_history: 数据库历史数据（用于data_count/latest_issue统计）

        Returns:
            报告UUID字符串，失败返回None
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

            # ---- 提取并规范化各字段 ----

            # next_issue: 优先从 final_report 取，回退从 db_history 推算
            next_issue = final_report.get('next_issue', '')
            if not next_issue:
                latest_issue = db_history.get('latest_issue', '')
                if latest_issue and str(latest_issue).isdigit():
                    next_issue = str(int(latest_issue) + 1)

            # report_content: reasoning_process 可能是list或str，统一转为可读文本
            reasoning = final_report.get('reasoning_process', '')
            if isinstance(reasoning, list):
                report_content = '\n'.join([f'{i + 1}. {r}' for i, r in enumerate(reasoning)])
            elif reasoning:
                report_content = str(reasoning)
            else:
                # 回退：从prediction各位置reason拼接
                prediction = final_report.get('prediction', {})
                reasons = []
                for pos in ['wan', 'qian', 'bai', 'shi', 'ge']:
                    pos_data = prediction.get(pos, {})
                    if isinstance(pos_data, dict) and pos_data.get('reason'):
                        pos_names = {'wan': '万位', 'qian': '千位', 'bai': '百位', 'shi': '十位', 'ge': '个位'}
                        reasons.append(f"{pos_names.get(pos, pos)}: {pos_data['reason']}")
                report_content = '；'.join(reasons) if reasons else '暂无推理过程'

            # trend_analysis: 可能是dict或str
            trend = final_report.get('trend_analysis', {})
            if isinstance(trend, dict):
                trend_analysis = json.dumps(trend, ensure_ascii=False)
            elif isinstance(trend, str) and trend:
                trend_analysis = trend
            else:
                trend_analysis = json.dumps({}, ensure_ascii=False)

            # key_conclusions: 优先key_conclusions，回退key_features
            conclusions = final_report.get('key_conclusions', None)
            if conclusions is None:
                conclusions = final_report.get('key_features', [])
            if isinstance(conclusions, list):
                key_conclusions = json.dumps(conclusions, ensure_ascii=False)
            elif conclusions:
                key_conclusions = str(conclusions)
            else:
                key_conclusions = json.dumps([], ensure_ascii=False)

            # recommended_combinations: list of dicts或list of strings
            combos = final_report.get('recommended_combinations', [])
            if combos:
                # 标准化格式
                formatted_combos = []
                for c in combos:
                    if isinstance(c, dict):
                        combo_str = c.get('combination', c.get('numbers', ''))
                        if isinstance(combo_str, list):
                            combo_str = ''.join(str(n) for n in combo_str)
                        formatted_combos.append({'combination': str(combo_str), 'confidence': c.get('confidence', 0), 'reason': c.get('reason', '')})
                    elif isinstance(c, (list, str)):
                        formatted_combos.append({'combination': ''.join(str(n) for n in c) if isinstance(c, list) else str(c)})
                recommended_combinations = json.dumps(formatted_combos, ensure_ascii=False)
            else:
                recommended_combinations = json.dumps([], ensure_ascii=False)

            # prediction数据（probability_stats, recommended_numbers, confidence_scores）
            prediction = final_report.get('prediction', {})
            probability_stats = json.dumps(prediction, ensure_ascii=False)
            recommended_numbers = json.dumps(prediction, ensure_ascii=False)
            confidence_scores = json.dumps(prediction, ensure_ascii=False)

            # 保存到数据库
            success = self.db_client.insert_ai_report(
                report_content=report_content,
                data_count=db_history.get('data_count', 0),
                latest_issue=db_history.get('latest_issue', ''),
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

            if success:
                logger.info(f'最终报告已保存到数据库，UUID: {report_uuid}, next_issue={next_issue}')
                return report_uuid
            else:
                logger.error('保存到数据库失败')
                return None

        except Exception as e:
            logger.error(f'保存到数据库失败: {e}', exc_info=True)
            return None

    # ============================================================
    # 新流水线方法 - 走势AI分析（步骤2）
    # ============================================================

    def _build_trend_analysis_prompt(self, trend_data: Dict[str, Any]) -> str:
        """
        构建走势图AI分析提示词

        将最近30期的走势图数据（基础走势+万/千/百/十/个位走势）
        格式化为AI可理解的文本，要求输出结构化的走势分析报告JSON。

        Args:
            trend_data: 包含以下键的字典:
                - basic_trend: 基础走势数据列表(最近30期)
                - wan_trend: 万位走势数据列表
                - qian_trend: 千位走势数据列表
                - bai_trend: 百位走势数据列表
                - shi_trend: 十位走势数据列表
                - ge_trend: 个位走势数据列表

        Returns:
            完整的提示词文本
        """
        prompt_parts = []

        prompt_parts.append("""
你是一位专业的排列5数据分析专家。请对以下最近30期的走势图数据进行深度分析。

【分析目标】
基于走势图数据分析各位置数字的走势规律、冷热状态、遗漏趋势等，
生成一份结构化的走势分析报告。

【报告要求】
请以严格的JSON格式输出，不要包含任何额外文字或markdown标记：

""")

        # ---- 基础走势数据 ----
        prompt_parts.append("=" * 60)
        prompt_parts.append("一、基础走势图数据（最近30期）")
        prompt_parts.append("=" * 60)

        basic = trend_data.get('basic_trend', [])
        if basic:
            prompt_parts.append("\n期号 | 日期 | 万 | 千 | 百 | 十 | 个 | 和值 | 奇偶比 | 大小比")
            prompt_parts.append("-" * 55)
            for item in basic[:30]:
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

        # ---- 各位置走势数据 ----
        position_configs = [
            ('万位', 'wan_trend', 'wan_number', '万'),
            ('千位', 'qian_trend', 'qian_number', '千'),
            ('百位', 'bai_trend', 'bai_number', '百'),
            ('十位', 'shi_trend', 'shi_number', '十'),
            ('个位', 'ge_trend', 'ge_number', '个'),
        ]

        for pos_name, key, num_key, abbr in position_configs:
            prompt_parts.append(f"\n{'=' * 60}")
            prompt_parts.append(f"二-{abbr}、{pos_name}走势数据（最近30期）")
            prompt_parts.append(f"{'=' * 60}")

            pos_data = trend_data.get(key, [])
            if pos_data:
                prompt_parts.append(f"\n期号 | 数字 | 奇偶 | 大小 | 质合 | 遗漏值 | 冷热等级 | 连续次数")
                prompt_parts.append("-" * 65)
                for item in pos_data[:30]:
                    issue = item.get('issue', '')
                    num = item.get(num_key, 0)
                    is_odd = '奇' if item.get('is_odd') else '偶'
                    is_big = '大' if item.get('is_big') else '小'
                    is_prime = '质' if item.get('is_prime') else '合'
                    omission = item.get('omission', 0)
                    hot_level = item.get('hot_level', '')
                    consecutive = item.get('consecutive_count', 0)
                    prompt_parts.append(f"{issue} | {num} | {is_odd} | {is_big} | {is_prime} | {omission} | {hot_level} | {consecutive}")

                # 统计摘要
                num_freq = {}
                for item in pos_data[:30]:
                    n = item.get(num_key, 0)
                    num_freq[n] = num_freq.get(n, 0) + 1
                sorted_nums = sorted(num_freq.items(), key=lambda x: x[1], reverse=True)
                hot = [n for n, _ in sorted_nums[:3]]
                cold = [n for n, _ in sorted_nums[-3:]]
                max_omission = max((item.get('omission', 0) for item in pos_data[:30]), default=0)
                prompt_parts.append(f"\n统计摘要: 热号{hot}, 冷号{cold}, 最大遗漏{max_omission}")

        # ---- 输出格式要求 ----
        prompt_parts.append(f"""
{'=' * 60}
输出格式要求
{'=' * 60}

请严格按照以下JSON格式输出走势分析报告：

{{
    "analysis_type": "走势图AI分析",
    "analysis_time": "YYYY-MM-DD HH:MM:SS",
    "data_period": "最近30期走势数据",
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
            "trend_direction": "走势方向描述（向上/向下/震荡）",
            "omission_analysis": "遗漏分析",
            "recommended_numbers": []
        }},
        "qian": {{
            "hot_numbers": [],
            "cold_numbers": [],
            "trend_direction": "走势方向描述",
            "omission_analysis": "遗漏分析",
            "recommended_numbers": []
        }},
        "bai": {{
            "hot_numbers": [],
            "cold_numbers": [],
            "trend_direction": "走势方向描述",
            "omission_analysis": "遗漏分析",
            "recommended_numbers": []
        }},
        "shi": {{
            "hot_numbers": [],
            "cold_numbers": [],
            "trend_direction": "走势方向描述",
            "omission_analysis": "遗漏分析",
            "recommended_numbers": []
        }},
        "ge": {{
            "hot_numbers": [],
            "cold_numbers": [],
            "trend_direction": "走势方向描述",
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
    "key_patterns": [
        "发现的规律模式1",
        "发现的规律模式2",
        "发现的规律模式3"
    ],
    "risk_factors": [
        "需要关注的风险因素1",
        "需要关注的风险因素2"
    ]
}}

注意事项：
1. 必须严格按JSON格式输出，不要有额外文字
2. 每个位置都有hot_numbers和cold_numbers（数字数组）
3. trend_direction描述走势方向
4. recommended_numbers为基于走势分析推荐的号码（数组）
""")

        return "\n".join(prompt_parts)

    def trend_analysis_with_ai(self, trend_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        走势AI分析：将最近30期走势图数据喂给AI，生成走势分析报告

        Args:
            trend_data: 包含basic_trend和各位置走势数据的字典

        Returns:
            AI分析结果JSON（统一格式），失败返回None
        """
        logger.info('=' * 80)
        logger.info('开始走势AI分析（最近30期走势图数据）')
        logger.info('=' * 80)

        self._init_ai_client()

        if not self.ai_client:
            logger.error('AI客户端未初始化')
            return None

        prompt = self._build_trend_analysis_prompt(trend_data)
        logger.info(f'走势分析提示词长度: {len(prompt)}')

        messages = [
            {
                "role": "system",
                "content": "你是一位专业的排列5走势数据分析专家，擅长分析走势图数据并发现规律。请严格按照要求输出JSON格式。"
            },
            {
                "role": "user",
                "content": prompt
            }
        ]

        try:
            ai_response = self.ai_client._call_ai_model(messages=messages, max_tokens=6000, temperature=0.5)
            if not ai_response:
                logger.error('走势AI分析 - AI模型调用失败')
                return None

            ai_result = self.ai_client._parse_ai_response(ai_response)
            if not ai_result:
                logger.error('走势AI分析 - AI响应解析失败')
                return None

            logger.info('走势AI分析完成')
            return ai_result

        except Exception as e:
            logger.error(f'走势AI分析异常: {e}', exc_info=True)
            return None

    def save_trend_analysis_to_redis(self, issue: str, trend_result: Dict[str, Any]) -> bool:
        """
        保存走势AI分析结果到Redis

        Args:
            issue: 期号
            trend_result: 走势AI分析结果

        Returns:
            是否保存成功
        """
        self._init_redis()

        if not self.redis_client or not self.redis_client.is_connected():
            logger.error('Redis客户端未连接，无法保存走势分析')
            return False

        try:
            return self.redis_client.save_trend_analysis(issue, trend_result)
        except Exception as e:
            logger.error(f'保存走势AI分析到Redis失败: {e}')
            return False

    def load_trend_analysis_from_redis(self, issue: str) -> Optional[Dict[str, Any]]:
        """
        从Redis加载走势AI分析结果

        Args:
            issue: 期号

        Returns:
            走势分析结果，失败返回None
        """
        self._init_redis()

        if not self.redis_client or not self.redis_client.is_connected():
            logger.error('Redis客户端未连接，无法加载走势分析')
            return None

        try:
            return self.redis_client.get_trend_analysis(issue)
        except Exception as e:
            logger.error(f'从Redis加载走势AI分析失败: {e}')
            return None

    # ============================================================
    # 新流水线方法 - 最终整合分析（步骤3）
    # ============================================================

    def _build_final_integrated_prompt(self,
                                       articles_analyses: List[Dict[str, Any]],
                                       trend_analysis: Optional[Dict[str, Any]],
                                       db_history: Dict[str, Any]) -> str:
        """
        构建最终整合分析提示词：整合文章AI分析+走势AI分析+历史数据

        将第一步的文章AI分析结果、第二步的走势AI分析报告、以及数据库历史数据
        整合到一个综合prompt中，要求AI输出最终的预测报告。

        Args:
            articles_analyses: 文章AI分析结果列表
            trend_analysis: 走势AI分析报告
            db_history: 数据库历史数据

        Returns:
            完整的提示词文本
        """
        prompt_parts = []

        prompt_parts.append("""
你是一位顶尖的排列5综合预测专家。请整合以下多源数据进行深度综合分析，给出最终的号码预测报告。

【任务说明】
你将收到三类数据：
1. 多篇专家文章的AI分析结果（各篇文章的结构化预测信息）
2. 最近30期的走势图AI分析报告（走势规律、冷热号、遗漏趋势等）
3. 最近30期的历史开奖数据
请综合所有信息，给出最终的预测号码和详细推理过程。

""")

        # ---- 第一部分：文章AI分析结果汇总 ----
        prompt_parts.append("=" * 60)
        prompt_parts.append("一、文章AI分析结果汇总")
        prompt_parts.append("=" * 60)

        if articles_analyses:
            prompt_parts.append(f"共 {len(articles_analyses)} 篇文章的分析结果：\n")
            for idx, analysis in enumerate(articles_analyses, 1):
                prompt_parts.append(f"--- 文章 {idx} ---")
                title = analysis.get('article_info', {}).get('title', analysis.get('title', '未知'))
                prompt_parts.append(f"标题: {title}")
                prompt_parts.append(f"期号: {analysis.get('issue_number', '未知')}")
                prompt_parts.append(f"置信度: {analysis.get('confidence_level', '未知')}")

                forecast = analysis.get('forecast_numbers', {})
                if forecast:
                    pos_map = {'wan': '万位', 'qian': '千位', 'bai': '百位', 'shi': '十位', 'ge': '个位'}
                    for pos_key, pos_name in pos_map.items():
                        nums = forecast.get(pos_key, [])
                        if nums:
                            prompt_parts.append(f"  {pos_name}推荐: {nums}")

                combos = analysis.get('recommended_combinations', [])
                if combos:
                    if isinstance(combos, list):
                        prompt_parts.append(f"  推荐组合: {combos[:5]}")

                key_points = analysis.get('key_points', [])
                if key_points:
                    for pt in key_points[:3]:
                        prompt_parts.append(f"  要点: {pt}")

                trend = analysis.get('trend_analysis', '')
                if trend:
                    prompt_parts.append(f"  趋势分析: {str(trend)[:100]}")

                summary = analysis.get('summary', '')
                if summary:
                    prompt_parts.append(f"  总结: {str(summary)[:150]}")

                prompt_parts.append("")

        # ---- 第二部分：走势AI分析报告 ----
        prompt_parts.append("=" * 60)
        prompt_parts.append("二、走势图AI分析报告（基于最近30期）")
        prompt_parts.append("=" * 60)

        if trend_analysis:
            trend_summary = trend_analysis.get('trend_summary', {})
            if trend_summary:
                prompt_parts.append(f"\n整体走势: {trend_summary.get('overall_trend', '')}")
                prompt_parts.append(f"热号总结: {trend_summary.get('hot_numbers_summary', '')}")
                prompt_parts.append(f"冷号总结: {trend_summary.get('cold_numbers_summary', '')}")
                prompt_parts.append(f"规律总结: {trend_summary.get('pattern_summary', '')}")

            pos_analysis = trend_analysis.get('position_analysis', {})
            pos_names = {'wan': '万位', 'qian': '千位', 'bai': '百位', 'shi': '十位', 'ge': '个位'}
            for pos_key, pos_name in pos_names.items():
                pa = pos_analysis.get(pos_key, {})
                if pa:
                    prompt_parts.append(f"\n{pos_name}:")
                    prompt_parts.append(f"  热号: {pa.get('hot_numbers', [])}")
                    prompt_parts.append(f"  冷号: {pa.get('cold_numbers', [])}")
                    prompt_parts.append(f"  走势方向: {pa.get('trend_direction', '')}")
                    prompt_parts.append(f"  遗漏分析: {pa.get('omission_analysis', '')}")
                    prompt_parts.append(f"  推荐号码: {pa.get('recommended_numbers', [])}")

            stats = trend_analysis.get('statistical_analysis', {})
            if stats:
                prompt_parts.append(f"\n和值分析: {stats.get('hezhi_analysis', '')}")
                prompt_parts.append(f"跨度分析: {stats.get('span_analysis', '')}")
                prompt_parts.append(f"奇偶分析: {stats.get('odd_even_analysis', '')}")
                prompt_parts.append(f"大小分析: {stats.get('big_small_analysis', '')}")

            patterns = trend_analysis.get('key_patterns', [])
            if patterns:
                prompt_parts.append(f"\n发现规律: {'; '.join(patterns)}")

            risks = trend_analysis.get('risk_factors', [])
            if risks:
                prompt_parts.append(f"\n风险因素: {'; '.join(risks)}")

        # ---- 第三部分：历史开奖数据 ----
        prompt_parts.append(f"\n{'=' * 60}")
        prompt_parts.append("三、历史开奖数据（最近30期）")
        prompt_parts.append("=" * 60)

        if db_history:
            prompt_parts.append(f"数据条数: {db_history.get('data_count', 0)}")
            prompt_parts.append(f"最新期号: {db_history.get('latest_issue', '未知')}")

            history = db_history.get('history_data', [])
            if history:
                prompt_parts.append("\n最近20期开奖记录：")
                for item in history[:20]:
                    issue = item.get('issue', '')
                    wan = item.get('wan', 0)
                    qian = item.get('qian', 0)
                    bai = item.get('bai', 0)
                    shi = item.get('shi', 0)
                    ge = item.get('ge', 0)
                    hezhi = item.get('hezhi', '')
                    odd_even = item.get('odd_even_ratio', '')
                    big_small = item.get('big_small_ratio', '')
                    prompt_parts.append(f"  {issue}: {wan}{qian}{bai}{shi}{ge} 和值:{hezhi} 奇偶:{odd_even} 大小:{big_small}")

        # ---- 输出格式要求 ----
        prompt_parts.append(f"""
{'=' * 60}
四、最终分析要求
{'=' * 60}

请综合以上三类数据（文章分析+走势报告+历史数据），进行深度推理，输出最终的预测报告。

请严格按照以下JSON格式输出（不要包含任何额外文字或markdown标记）：

{{
    "data_source": "文章AI分析+走势AI分析+历史数据综合",
    "analysis_time": "YYYY-MM-DD HH:MM:SS",
    "model_version": "综合预测模型v2.0",
    "current_issue": "当前最新期号",
    "next_issue": "预测目标期号",
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
    "statistical_features": {{
        "hezhi_range": "和值范围",
        "span_range": "跨度范围",
        "odd_even_ratio": "奇偶比偏好",
        "big_small_ratio": "大小比偏好",
        "hot_numbers": "热号",
        "cold_numbers": "冷号",
        "key_patterns": ["模式1", "模式2"]
    }},
    "key_conclusions": ["关键结论1", "关键结论2", "关键结论3"],
    "risk_warning": "风险提示文本"
}}

注意事项：
1. prediction中每个位置必须包含numbers(号码数组)、confidence(置信度数组)、reason(推荐理由)
2. numbers至少2个最多5个，confidence与numbers一一对应
3. recommended_combinations至少3个，每个含combination/confidence/reason
4. reasoning_process至少3步完整推理
5. 综合考量文章专家意见、走势规律和历史数据，给出最有可能的预测
""")

        return "\n".join(prompt_parts)

    def final_integrated_analysis(self,
                                  articles_analyses: List[Dict[str, Any]],
                                  trend_analysis: Optional[Dict[str, Any]],
                                  db_history: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        最终整合AI分析：合并文章分析+走势分析+历史数据，生成最终预测报告

        Args:
            articles_analyses: 各篇文章的AI分析结果列表
            trend_analysis: 走势AI分析报告
            db_history: 数据库历史数据

        Returns:
            最终预测报告JSON，失败返回None
        """
        logger.info('=' * 80)
        logger.info('开始最终整合AI分析（文章+走势+历史数据）')
        logger.info('=' * 80)

        self._init_ai_client()

        if not self.ai_client:
            logger.error('AI客户端未初始化')
            return None

        prompt = self._build_final_integrated_prompt(articles_analyses, trend_analysis, db_history)
        logger.info(f'最终整合提示词长度: {len(prompt)}')

        messages = [
            {
                "role": "system",
                "content": "你是一位顶尖的排列5综合预测专家，擅长整合多元数据进行深度分析和精准预测。请严格按照要求输出JSON格式。"
            },
            {
                "role": "user",
                "content": prompt
            }
        ]

        try:
            ai_response = self.ai_client._call_ai_model(messages=messages, max_tokens=8000, temperature=0.6)
            if not ai_response:
                logger.error('最终整合AI分析 - AI模型调用失败')
                return None

            ai_result = self.ai_client._parse_ai_response(ai_response)
            if not ai_result:
                logger.error('最终整合AI分析 - AI响应解析失败')
                return None

            logger.info('最终整合AI分析完成')
            logger.info(f'预测期号: {ai_result.get("next_issue", "未知")}')

            return ai_result

        except Exception as e:
            logger.error(f'最终整合AI分析异常: {e}', exc_info=True)
            # 回退：基于文章分析和走势分析构建基本结果
            return self._build_fallback_integrated_result(articles_analyses, trend_analysis, db_history)

    def _build_fallback_integrated_result(self,
                                          articles_analyses: List[Dict[str, Any]],
                                          trend_analysis: Optional[Dict[str, Any]],
                                          db_history: Dict[str, Any]) -> Dict[str, Any]:
        """
        当最终AI整合分析失败时，构建回退结果

        整合文章分析中的forecast_numbers和走势分析中的recommended_numbers
        """
        prediction = {}
        for pos_key in ['wan', 'qian', 'bai', 'shi', 'ge']:
            # 从文章分析收集号码
            article_nums = []
            for analysis in articles_analyses:
                forecast = analysis.get('forecast_numbers', {})
                if isinstance(forecast, dict):
                    nums = forecast.get(pos_key, [])
                    if isinstance(nums, list):
                        article_nums.extend(nums)

            # 从走势分析收集推荐号码
            trend_nums = []
            if trend_analysis:
                pa = trend_analysis.get('position_analysis', {})
                pos_data = pa.get(pos_key, {})
                if isinstance(pos_data, dict):
                    rec = pos_data.get('recommended_numbers', [])
                    if isinstance(rec, list):
                        trend_nums.extend(rec)

            # 合并去重
            all_nums = list(dict.fromkeys(article_nums + trend_nums))[:5]
            if not all_nums:
                all_nums = list(range(10))[:5]

            prediction[pos_key] = {
                'numbers': all_nums,
                'confidence': [0.5] * len(all_nums),
                'reason': '回退结果：综合文章分析与走势分析推荐'
            }

        latest_issue = db_history.get('latest_issue', '')
        next_issue = str(int(latest_issue) + 1) if latest_issue and str(latest_issue).isdigit() else ''

        return {
            'data_source': '回退：文章+走势+历史综合',
            'analysis_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'model_version': 'fallback',
            'current_issue': latest_issue,
            'next_issue': next_issue,
            'prediction': prediction,
            'trend_analysis': {'summary': 'AI分析失败，使用回退结果'},
            'reasoning_process': ['最终AI整合分析失败，使用回退整合方案'],
            'recommended_combinations': [],
            'statistical_features': {},
            'key_conclusions': ['基于文章分析和走势分析的回退结果，请谨慎参考'],
            'risk_warning': '最终AI分析失败，结果基于回退逻辑，仅供参考'
        }

    # ============================================================
    # TXT报告生成
    # ============================================================

    def generate_txt_report(self, final_report: Dict[str, Any], output_path: str) -> str:
        """
        生成TXT格式的最终分析报告文本

        Args:
            final_report: 最终AI分析结果
            output_path: 输出文件路径

        Returns:
            报告文本内容
        """
        lines = []

        lines.append("=" * 70)
        lines.append("  排列5 AI智能分析系统 - 综合预测报告")
        lines.append("=" * 70)
        lines.append("")

        lines.append(f"【基本信息】")
        lines.append(f"  数据来源: {final_report.get('data_source', '未知')}")
        lines.append(f"  分析时间: {final_report.get('analysis_time', '未知')}")
        lines.append(f"  当前期号: {final_report.get('current_issue', '未知')}")
        lines.append(f"  预测期号: {final_report.get('next_issue', '未知')}")
        lines.append("")

        # 各位置预测
        lines.append("【各位置预测】")
        pos_names = {'wan': '万位', 'qian': '千位', 'bai': '百位', 'shi': '十位', 'ge': '个位'}
        prediction = final_report.get('prediction', {})
        for pos_key, pos_name in pos_names.items():
            pos_data = prediction.get(pos_key, {})
            numbers = pos_data.get('numbers', []) if isinstance(pos_data, dict) else []
            confidence = pos_data.get('confidence', []) if isinstance(pos_data, dict) else []
            reason = pos_data.get('reason', '') if isinstance(pos_data, dict) else ''

            lines.append(f"\n  {pos_name}:")
            if numbers:
                for i, (num, conf) in enumerate(zip(numbers, confidence), 1):
                    lines.append(f"    {i}. 号码 {num} (置信度: {conf:.2%})")
            if reason:
                lines.append(f"    理由: {reason}")
        lines.append("")

        # 推荐组合
        lines.append("【推荐组合】")
        combinations = final_report.get('recommended_combinations', [])
        for i, combo in enumerate(combinations, 1):
            if isinstance(combo, dict):
                combo_str = combo.get('combination', combo.get('numbers', ''))
                if isinstance(combo_str, list):
                    combo_str = ''.join(str(n) for n in combo_str)
                conf = combo.get('confidence', '')
                reason = combo.get('reason', '')
                lines.append(f"  {i}. {combo_str}")
                if conf:
                    lines.append(f"     置信度: {conf:.2%}" if isinstance(conf, float) else f"     置信度: {conf}")
                if reason:
                    lines.append(f"     理由: {str(reason)[:80]}")
            elif isinstance(combo, list):
                lines.append(f"  {i}. {''.join(str(n) for n in combo)}")
            else:
                lines.append(f"  {i}. {combo}")
        lines.append("")

        # 趋势分析
        trend = final_report.get('trend_analysis', {})
        if trend:
            lines.append("【趋势分析】")
            if isinstance(trend, dict):
                summary = trend.get('summary', '')
                if summary:
                    lines.append(f"  综合分析: {summary}")
                for pos_key, pos_name in pos_names.items():
                    pos_analysis = trend.get(pos_key, '')
                    if pos_analysis:
                        lines.append(f"  {pos_name}: {pos_analysis}")
            elif isinstance(trend, str):
                lines.append(f"  {trend}")
        lines.append("")

        # 统计特征
        lines.append("【关键统计特征】")
        stats = final_report.get('statistical_features', {})
        if stats:
            lines.append(f"  和值范围: {stats.get('hezhi_range', '无')}")
            lines.append(f"  跨度范围: {stats.get('span_range', '无')}")
            lines.append(f"  奇偶比: {stats.get('odd_even_ratio', '无')}")
            lines.append(f"  大小比: {stats.get('big_small_ratio', '无')}")
            lines.append(f"  热号: {stats.get('hot_numbers', '无')}")
            lines.append(f"  冷号: {stats.get('cold_numbers', '无')}")
            patterns = stats.get('key_patterns', [])
            for i, p in enumerate(patterns, 1):
                lines.append(f"  模式{i}: {p}")
        lines.append("")

        # 推理过程
        lines.append("【推理过程】")
        reasoning = final_report.get('reasoning_process', [])
        if isinstance(reasoning, list):
            for i, step in enumerate(reasoning, 1):
                lines.append(f"  步骤{i}: {step}")
        elif reasoning:
            lines.append(f"  {reasoning}")
        else:
            lines.append("  暂无推理过程")
        lines.append("")

        # 关键结论
        lines.append("【关键结论】")
        conclusions = final_report.get('key_conclusions', [])
        if isinstance(conclusions, list):
            for i, c in enumerate(conclusions, 1):
                lines.append(f"  {i}. {c}")
        elif conclusions:
            lines.append(f"  {conclusions}")
        lines.append("")

        # 风险提示
        lines.append("【风险提示】")
        lines.append(f"  {final_report.get('risk_warning', '本分析基于历史数据统计，不保证中奖，请理性购彩。')}")
        lines.append("")

        lines.append("=" * 70)
        lines.append(f"  报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"  系统版本: 排列5 AI智能分析系统 v2.0")
        lines.append("=" * 70)

        report_text = "\n".join(lines)

        # 写入文件
        try:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(report_text)
            logger.info(f'TXT报告已生成: {output_path}')
        except Exception as e:
            logger.error(f'生成TXT报告失败: {e}')

        return report_text

    def analyze_article_workflow(self, target_issue: Optional[str] = None, data_limit: int = 30) -> Dict[str, Any]:
        """
        完整的6步文章分析工作流（主入口方法）

        步骤:
        步骤1: 爬取文章 (YDNiuSpider.crawl_all_articles, max_articles=30)
        步骤2: 第一次AI分析 (first_ai_analysis → 结构化整理文章内容)
        步骤3: Redis存储 (save_all_articles_to_redis → 7天过期)
        步骤4: Redis加载 (load_from_redis → 按期号索引)
        步骤5: 获取历史数据 (ERNIEAIAnalyzer._fetch_data_from_database)
        步骤6: 第二次AI分析 (second_ai_analysis → 整合多源数据预测)
        步骤7: 数据库存储 (save_to_database → MySQL, 含UUID)

        调用方:
        - main.py: run_article_analysis()
        - gui.py: _execute_optimized_p5_ai()
        - CLI: python main.py article --issue 2026165 --limit 30

        Args:
            target_issue: 目标期号（如"2026165"），None则从文章提取
            data_limit: 历史数据获取期数（默认30）

        Returns:
            {success, step1_crawl, step2_first_ai, step3_redis_save,
             step4_redis_load, step5_second_ai, step6_db_save,
             error, report_uuid, final_report}
        """
        logger.info('=' * 80)
        logger.info('开始完整文章分析工作流')
        logger.info('=' * 80)

        result = {
            'success': False,
            'step1_crawl': False,
            'step2.first_ai': False,
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

            crawl_result = self.spider.crawl_all_articles(target_issue=target_issue, max_articles=30)

            if not crawl_result.get('articles'):
                result['error'] = '未爬取到文章内容'
                return result

            articles = crawl_result['articles']
            logger.info(f'成功爬取 {len(articles)} 篇文章')

            article_data = articles[0]
            result['step1_crawl'] = True
            logger.info(f'使用第一篇文章: {article_data.get("title", "未知")})')

            # 提取期号（委托 _extract_issue_from_article，复用了标题/URL/link_title 的多源提取逻辑）
            issue = self._extract_issue_from_article(article_data, target_issue)

            if not issue or not re.match(r'^\d{6,8}$', str(issue)):
                result['error'] = '无法确定期号'
                return result

            logger.info(f'提取到期号: {issue}')

            # 步骤2：第一次AI分析
            logger.info('步骤2：第一次AI分析（结构化整理）...')
            first_ai_result = self.first_ai_analysis(article_data)

            if not first_ai_result:
                result['error'] = '第一次AI分析失败'
                return result

            result['step2_first_ai'] = True

            # 步骤3：保存所有文章到Redis
            logger.info('步骤3：保存所有文章到Redis...')
            redis_save_result = self.save_all_articles_to_redis(issue, articles, first_ai_result)

            if not redis_save_result.get('success'):
                result['error'] = f'保存到Redis失败: {redis_save_result.get("error", "未知错误")}'
                return result

            result['step3_redis_save'] = True
            result['redis_saved_count'] = redis_save_result.get('saved_count', 0)
            result['redis_failed_count'] = redis_save_result.get('failed_count', 0)
            logger.info(f'Redis保存完成: 成功{redis_save_result.get("saved_count", 0)}篇, 失败{redis_save_result.get("failed_count", 0)}篇')

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

    def save_all_articles_bulk_to_redis(self, target_issue: Optional[str] = None,
                                        max_articles: int = 100,
                                        extract_predictions: bool = True) -> Dict[str, Any]:
        """
        批量爬取文章并保存到Redis（含可选预测数据提取）

        流程:
        步骤1: 爬取文章 (YDNiuSpider.crawl_all_articles → 按target_issue过滤)
        步骤2: 初始化Redis客户端
        步骤3: (可选)初始化PredictionExtractor（仅当extract_predictions=True且AI可用）
        步骤4: 批量处理每篇文章:
            4.1: 提取期号（_extract_issue_from_article）
            4.2: 生成article_id（RedisClient.generate_article_id）
            4.3: HTML清洗为纯文本（HTMLTextCleaner）
            4.4: 第一次AI分析（first_ai_analysis）
            4.5: 将AI结果和元数据保存到Redis（按article_id，7天过期）

        调用方:
        - main.py: run_save_articles_to_redis()
        - CLI: python main.py save-articles --max 100

        Args:
            target_issue: 目标期号，None则爬取全部文章
            max_articles: 最大处理文章数（默认100）
            extract_predictions: 是否提取预测数据（默认True，通过PredictionExtractor）

        Returns:
            {success, total_articles, saved_articles, failed_articles,
             extracted_predictions, high_quality_predictions, articles, predictions, error}
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
            logger.info('步骤4：批量处理文章（HTML清洗+内容理解+预测提取+Redis存储）...')
            for i, article_data in enumerate(articles, 1):
                try:
                    # 提取期号
                    issue = self._extract_issue_from_article(article_data, target_issue)

                    # 生成文章唯一ID
                    url = article_data.get('url', article_data.get('link_url', f'article_{i}'))
                    article_id = self.redis_client.generate_article_id(url, i)

                    # 构建用于AI分析的最小化文章数据（先清洗HTML）
                    logger.info(f'  步骤4.1：清洗HTML为纯文本并进行AI分析...')
                    try:
                        from modules.html_cleaner import HTMLTextCleaner
                        html_cleaner = HTMLTextCleaner()
                        raw_content = article_data.get('content', '')
                        clean_text = html_cleaner.clean_html(raw_content)
                    except Exception as e:
                        logger.warning(f'  HTML清洗失败，使用原始内容: {e}')
                        clean_text = article_data.get('content', '')

                    # 构造传给AI的文章数据，只包含必要字段
                    article_for_ai = {
                        'title': article_data.get('title', ''),
                        'author': article_data.get('author', ''),
                        'publish_time': article_data.get('publish_time', ''),
                        'url': article_data.get('url', ''),
                        'content': clean_text,
                        'crawl_time': article_data.get('crawl_time', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                    }

                    # 调用一次AI分析（使用第一次结构化分析接口）
                    try:
                        ai_analysis = self.first_ai_analysis(article_for_ai)
                    except Exception as e:
                        logger.error(f'  AI分析失败: {e}', exc_info=True)
                        ai_analysis = None

                    # 仅将AI分析结果和必要元数据保存到Redis（不保存原始HTML或全文）
                    redis_store = {
                        'issue': issue,
                        'article_id': article_id,
                        'title': article_data.get('title', '')[:200],
                        'url': article_data.get('url', ''),
                        'ai_analysis': ai_analysis,
                        'save_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    }

                    save_success = self.redis_client.save_article_data(article_id, redis_store, expire_days=7)

                    if save_success:
                        result['saved_articles'] += 1
                        result['articles'].append({
                            'article_id': article_id,
                            'issue': issue,
                            'title': article_data.get('title', '未知')[:50],
                            'has_ai_analysis': ai_analysis is not None
                        })
                        logger.info(f'文章 {i}/{len(articles)} AI分析并保存完成: {article_id} (期号: {issue})')
                    else:
                        result['failed_articles'] += 1
                        logger.warning(f'文章 {i}/{len(articles)} 保存失败')

                except Exception as e:
                    result['failed_articles'] += 1
                    logger.error(f'处理文章 {i} 时出错: {e}')
                    continue

            result['success'] = result['saved_articles'] > 0

            # 步骤5：将所有文章AI分析汇总保存到统一键（供二次AI分析按期号加载）
            if result['saved_articles'] > 0:
                try:
                    self._save_aggregated_ai_to_redis(target_issue, articles, result)
                except Exception as e:
                    logger.warning(f'汇总AI分析保存到统一键失败（不影响主流程）: {e}')

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

    def _save_aggregated_ai_to_redis(self, target_issue: Optional[str],
                                     articles: List[Dict[str, Any]],
                                     batch_result: Dict[str, Any]) -> None:
        """
        将批量文章处理的AI分析结果汇总保存到统一键 kpluckynumber:pl5:ai:{issue}

        从各篇文章的Redis存储中加载AI分析结果，汇总后保存到统一键，
        确保后续二次AI分析（second_ai_analysis/load_from_redis）能按期号加载。

        Args:
            target_issue: 目标期号
            articles: 原始文章数据列表
            batch_result: 批量处理结果（含saved_articles列表）
        """
        if not target_issue:
            # 尝试从文章中提取期号
            for article_data in articles[:3]:
                issue = self._extract_issue_from_article(article_data, None)
                if issue and re.match(r'^\d{6,8}$', str(issue)):
                    target_issue = issue
                    break
            if not target_issue:
                target_issue = datetime.now().strftime('%Y%m%d')

        self._init_redis()
        if not self.redis_client or not self.redis_client.is_connected():
            logger.warning('Redis未连接，跳过汇总AI分析保存')
            return

        # 收集所有已保存文章的AI分析结果
        collected_analyses = []
        saved_article_ids = []
        for saved_info in batch_result.get('articles', []):
            article_id = saved_info.get('article_id', '')
            if article_id:
                saved_article_ids.append(article_id)
                # 从Redis加载已保存的文章AI分析
                try:
                    article_data = self.redis_client.get_article_data(article_id)
                    if article_data and article_data.get('ai_analysis'):
                        collected_analyses.append({
                            'article_id': article_id,
                            'title': article_data.get('title', ''),
                            'url': article_data.get('url', ''),
                            'issue': article_data.get('issue', ''),
                            'ai_analysis': article_data['ai_analysis']
                        })
                except Exception as e:
                    logger.debug(f'加载文章{article_id}的AI分析失败: {e}')

        # 构建汇总的Redis数据
        aggregated = {
            'issue': target_issue,
            'articles_count': len(articles),
            'saved_count': batch_result.get('saved_articles', 0),
            'collected_analyses_count': len(collected_analyses),
            'articles': articles,  # 原始文章列表（用于二次AI分析）
            'ai_analysis': collected_analyses[0].get('ai_analysis') if collected_analyses else {},
            'all_ai_analyses': collected_analyses,  # 所有文章的AI分析
            'save_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }

        # 保存到统一键
        key = self.redis_client.get_ai_analysis_key(target_issue)
        self.redis_client.client.setex(key, 86400 * 7, json.dumps(aggregated, ensure_ascii=False))
        logger.info(f'汇总AI分析已保存到统一键: {key} (含{len(collected_analyses)}篇AI分析)')

    def _extract_issue_from_article(self, article_data: Dict[str, Any],
                                    target_issue: Optional[str] = None) -> str:
        """
        从文章数据中提取期号（多源策略，按优先级）

        优先级:
        1. target_issue参数 - 若提供且符合r'^\\d{6,8}$'格式，直接使用
        2. 文章标题 - 匹配 r'(\\d{6,8})期' 模式
        3. link_title字段 - 同上模式
        4. 文章URL - 匹配 r'(\\d{6,8})' 模式
        5. 默认值 - 当前日期 YYYYMMDD

        注意: 与 article_processor.py::_extract_issue_from_content() 功能重复，
             本版本多了link_title和URL的提取支持。

        Args:
            article_data: 文章数据（含title/url/link_title等字段）
            target_issue: 指定的目标期号（可选，最高优先级）

        Returns:
            提取的期号字符串
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


# ============================================================
# 便捷函数：供外部模块直接调用的简化入口
# ============================================================

def run_article_analysis(target_issue: Optional[str] = None, data_limit: int = 30) -> Dict[str, Any]:
    """
    便捷函数：执行完整文章分析工作流

    使用示例:
        result = run_article_analysis(target_issue='2026165', data_limit=30)
        if result['success']:
            print(f"报告UUID: {result['report_uuid']}")

    Args:
        target_issue: 目标期号
        data_limit: 历史数据期数限制

    Returns:
        ArticleAnalyzer.analyze_article_workflow() 的完整返回结果
    """
    analyzer = ArticleAnalyzer()
    return analyzer.analyze_article_workflow(target_issue=target_issue, data_limit=data_limit)


# ============================================================
# 独立测试入口：可运行 python -m modules.article_analyzer
# ============================================================
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

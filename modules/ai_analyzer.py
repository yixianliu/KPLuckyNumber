"""
排列5 AI分析模块

基于 AGNES AI 大语言模型，整合多数据源进行深度分析，
生成结构化AI分析报告并存储到数据库。

核心功能：
1. 数据源整合 - 读取30期走势图、万位/千位/百位/十位走势图数据
2. AI模型调用 - 使用 AGNES API 进行深度分析
3. 报告生成 - 生成包含预测结果、置信度、趋势分析的结构化报告
4. 数据库存储 - 将报告完整存入p5_ai_report表

参考接口规范：
- API端点：https://apihub.agnes-ai.com/v1/chat/completions
- 模型：agnes-2.0-flash
- 认证方式：Bearer Token
"""

import logging
import os
import json
import uuid
from datetime import datetime
from typing import Dict, List, Any, Optional

import requests

os.makedirs('logs', exist_ok=True)

# 日志目录保证：遵循项目约定，将日志写入 logs/ 供集中查看。

logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler = logging.FileHandler('logs/ernie_ai_analyzer.log', encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

# 说明：本模块负责调用 AI 并生成结构化报告。AI 配置从 config.py 的 AGNES_API_CONFIG 加载。


class AIAnalyzer:
    """
    排列5 AI分析器

    整合多数据源，调用 AGNES AI 大语言模型进行深度分析，
    生成结构化分析报告并存储到数据库。
    """

    def __init__(self):
        self._init_ai_config()
        self.position_names = ['万位', '千位', '百位', '十位', '个位']
        self.position_keys = ['wan', 'qian', 'bai', 'shi', 'ge']

    def _init_ai_config(self):
        """初始化AI模型配置（从config.py读取AGNES配置）"""
        try:
            # 尝试从config.py加载配置（使用模块导入以避免在except路径中出现未定义名警告）
            import config as cfg
            self.api_config = getattr(cfg, 'AGNES_API_CONFIG', {}) or {}
            self.api_url = self.api_config.get('api_url', "https://apihub.agnes-ai.com/v1/chat/completions")
            self.api_key = self.api_config.get('api_key', '')
            self.model_name = self.api_config.get('model_name', 'agnes-2.0-flash')
            self.ai_available = bool(self.api_key)

            if self.ai_available:
                self.headers = {
                    'Content-Type': 'application/json',
                    'Authorization': f'Bearer {self.api_key}'
                }
                logger.info(f'从config.py加载API配置: {self.model_name}')
            else:
                logger.warning('config.py中未配置API密钥')
        except Exception:
            # 如果无法导入config模块，使用空配置继续
            self.api_config = {}
            self.api_url = "https://apihub.agnes-ai.com/v1/chat/completions"
            self.api_key = ''
            self.model_name = 'agnes-2.0-flash'
            self.ai_available = False
            logger.info('未能从config.py加载配置')

    def _call_ai_model(self, messages: List[Dict[str, Any]],
                       max_tokens: int = 8000,
                       temperature: float = 0.7) -> Optional[str]:
        """
        调用 AGNES AI 模型

        参考接口规范：
        - POST https://apihub.agnes-ai.com/v1/chat/completions
        - Content-Type: application/json
        - Authorization: Bearer <token>

        Args:
            messages: 消息列表，包含system、user角色
            max_tokens: 最大输出token数
            temperature: 温度参数

        Returns:
            AI模型返回的内容，失败返回None
        """
        if not self.ai_available:
            logger.warning('AI模型不可用（未配置API密钥）')
            return None

        logger.info(f'=== 开始调用AI模型: {self.model_name} ===')

        # 构建请求payload，参考function call接口规范
        # 注意：payload 尽量保持简洁，response 可能包含非严格 JSON 的文本，因此解析需增加容错。
        payload = {
            "model": self.model_name,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False
        }

        try:
            response = requests.request(
                "POST",
                self.api_url,
                headers=self.headers,
                data=json.dumps(payload)
            )
            response.raise_for_status()

            result = response.json()

            if 'choices' in result and len(result['choices']) > 0:
                content = result['choices'][0]['message']['content']
                logger.info(f'AI模型调用成功，返回长度: {len(content)}')
                return content

            logger.error(f'AI模型返回格式异常: {result}')
            return None

        except requests.exceptions.RequestException as e:
            logger.error(f'AI模型调用失败: {e}')
            return None
        except json.JSONDecodeError as e:
            logger.error(f'AI响应JSON解析失败: {e}')
            return None
        except Exception as e:
            logger.error(f'AI模型调用异常: {e}')
            return None

    def _parse_ai_response(self, response_text: str) -> Dict[str, Any]:
        """解析AI响应为JSON格式"""
        try:
            start_idx = response_text.find('{')
            end_idx = response_text.rfind('}') + 1

            if start_idx == -1 or end_idx == 0:
                logger.error('无法找到JSON起始或结束位置')
                return {}

            json_str = response_text[start_idx:end_idx]
            # 按照仓库约定：从第一个 '{' 到最后一个 '}' 提取第一个 JSON 对象并解析
            # 该策略同样在 optimized_p5_predictor.py 中采用，确保两个模块解析逻辑一致
            return json.loads(json_str)

        except json.JSONDecodeError as e:
            logger.error(f'JSON解析失败: {e}')
            return {}
        except Exception as e:
            logger.error(f'解析AI响应失败: {e}')
            return {}

    def _fetch_data_from_database(self, limit: int = 30) -> Dict[str, Any]:
        """
        从数据库获取所有必要的数据源

        Args:
            limit: 获取最近多少期数据

        Returns:
            包含所有数据源的字典
        """
        try:
            from modules.database import P5Database
            db = P5Database()
            if not db.connect():
                logger.error('数据库连接失败，无法加载数据')
                return {'error': '数据库连接失败'}

            history_data = db.get_history_data(limit=limit, order_by='issue DESC')
            trend_data = db.get_trend_data(limit=limit)
            wan_trend_data = db.get_wan_trend_data(limit=limit)
            qian_trend_data = db.get_qian_trend_data(limit=limit)
            bai_trend_data = db.get_bai_trend_data(limit=limit)
            shi_trend_data = db.get_shi_trend_data(limit=limit)
            ge_trend_data = db.get_ge_trend_data(limit=limit)

            db.disconnect()

            latest_issue = history_data[0]['issue'] if history_data else ''

            logger.info(f'数据库数据加载完成: 历史数据{len(history_data)}条, 走势数据{len(trend_data)}条')
            logger.info(f'各位置走势数据: 万位{len(wan_trend_data)}条, 千位{len(qian_trend_data)}条, 百位{len(bai_trend_data)}条, 十位{len(shi_trend_data)}条, 个位{len(ge_trend_data)}条')

            return {
                'history_data': history_data,
                'trend_data': trend_data,
                'wan_trend_data': wan_trend_data,
                'qian_trend_data': qian_trend_data,
                'bai_trend_data': bai_trend_data,
                'shi_trend_data': shi_trend_data,
                'ge_trend_data': ge_trend_data,
                'latest_issue': latest_issue,
                'data_count': len(history_data),
                'error': None
            }

        except Exception as e:
            logger.error(f'从数据库加载数据失败: {e}')
            return {'error': str(e)}

    def _generate_position_stats(self, position_data: List[Dict[str, Any]], position_name: str) -> str:
        """
        生成单个位置的统计信息

        Args:
            position_data: 位置走势数据
            position_name: 位置名称

        Returns:
            统计信息字符串
        """
        if not position_data:
            return f'{position_name}: 暂无数据'

        lines = []
        lines.append(f'【{position_name}统计】')

        num_counts = {}
        omissions = {}
        odd_count = 0
        big_count = 0

        for item in position_data:
            if position_name == '万位':
                num = item.get('wan_number', 0)
            elif position_name == '千位':
                num = item.get('qian_number', 0)
            elif position_name == '百位':
                num = item.get('bai_number', 0)
            elif position_name == '十位':
                num = item.get('shi_number', 0)
            else:
                continue

            num_counts[num] = num_counts.get(num, 0) + 1

            if item.get('is_odd'):
                odd_count += 1
            if item.get('is_big'):
                big_count += 1

            omission = item.get('omission', 0)
            omissions[num] = max(omissions.get(num, 0), omission)

        sorted_nums = sorted(num_counts.items(), key=lambda x: x[1], reverse=True)
        hot_nums = [n for n, _ in sorted_nums[:3]]
        cold_nums = [n for n, _ in sorted_nums[-3:]]

        high_omission = sorted(omissions.items(), key=lambda x: x[1], reverse=True)[:3]

        lines.append(f'  热号: {hot_nums}')
        lines.append(f'  冷号: {cold_nums}')
        lines.append(f'  高遗漏号码: {[(n, o) for n, o in high_omission]}')
        lines.append(f'  奇数比例: {odd_count}/{len(position_data)}')
        lines.append(f'  大数比例: {big_count}/{len(position_data)}')

        recent_values = []
        for item in position_data[:10]:
            if position_name == '万位':
                recent_values.append(item.get('wan_number', 0))
            elif position_name == '千位':
                recent_values.append(item.get('qian_number', 0))
            elif position_name == '百位':
                recent_values.append(item.get('bai_number', 0))
            elif position_name == '十位':
                recent_values.append(item.get('shi_number', 0))

        lines.append(f'  近期走势(最近10期): {recent_values}')

        return '\n'.join(lines)

    def _generate_trend_data_summary(self, trend_data: List[Dict[str, Any]]) -> str:
        """
        生成走势图数据摘要

        Args:
            trend_data: 走势图数据

        Returns:
            摘要字符串
        """
        if not trend_data:
            return '走势图数据：暂无数据'

        lines = []
        lines.append('【走势图数据摘要】')

        for item in trend_data[:20]:
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

            lines.append(f'期号:{issue} 日期:{draw_date} 号码:{wan}{qian}{bai}{shi}{ge} 和值:{hezhi} 奇偶比:{odd_even} 大小比:{big_small}')

        return '\n'.join(lines)

    def _build_ai_prompt(self, data: Dict[str, Any]) -> str:
        """
        构建AI分析提示词，整合所有数据源

        Args:
            data: 包含所有数据源的字典

        Returns:
            完整的提示词字符串
        """
        prompt = f"""你是一位专业的排列5彩票数据分析专家。请基于以下提供的详细历史数据和走势数据，进行深度分析并预测下一期各位置号码。

【彩种规则】
- 排列5：5位数字，每位0-9，每天开奖
- 号码位置：万位、千位、百位、十位、个位
- 和值范围：0-45
- 跨度范围：0-9

【数据来源说明】
- 历史开奖数据：最近{data['data_count']}期
- 走势图数据：最近{len(data['trend_data'])}期
- 万位走势：最近{len(data['wan_trend_data'])}期
- 千位走势：最近{len(data['qian_trend_data'])}期
- 百位走势：最近{len(data['bai_trend_data'])}期
- 十位走势：最近{len(data['shi_trend_data'])}期
- 最新期号：{data['latest_issue']}
- 分析时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

【近期开奖数据（最近20期）】
"""

        for item in data['history_data'][:20]:
            issue = item.get('issue', '')
            draw_date = item.get('draw_date', '')
            wan = item.get('wan', 0)
            qian = item.get('qian', 0)
            bai = item.get('bai', 0)
            shi = item.get('shi', 0)
            ge = item.get('ge', 0)
            hezhi = item.get('hezhi', '')
            span = item.get('span', '')
            odd_even_ratio = item.get('odd_even_ratio', '')
            big_small_ratio = item.get('big_small_ratio', '')

            prompt += f'期号:{issue} 日期:{draw_date} 号码:{wan}{qian}{bai}{shi}{ge} 和值:{hezhi} 跨度:{span} 奇偶比:{odd_even_ratio} 大小比:{big_small_ratio}\n'

        prompt += """
【各位置走势统计】
"""

        prompt += self._generate_position_stats(data['wan_trend_data'], '万位') + '\n\n'
        prompt += self._generate_position_stats(data['qian_trend_data'], '千位') + '\n\n'
        prompt += self._generate_position_stats(data['bai_trend_data'], '百位') + '\n\n'
        prompt += self._generate_position_stats(data['shi_trend_data'], '十位') + '\n\n'

        prompt += """
【分析要求】
1. 数据来源与预处理说明：说明使用的数据来源、数据周期、数据质量评估
2. 各位置号码预测：基于统计规律和AI深度分析，预测万位、千位、百位、十位号码
3. 置信度评估：为每个推荐号码提供置信度分数（0-1），并解释评估依据
4. 趋势分析：分析各位置号码近期走势、冷热号变化趋势、关键特征提取
5. 预测依据与模型推理过程：详细说明推理逻辑、使用的分析方法、数据支撑
6. 风险提示：明确说明所有分析仅基于历史数据统计，不保证中奖，请理性购彩

【输出格式要求】
请严格按照以下JSON格式输出，不要包含任何额外文字：

{
    "data_source": {
        "description": "数据来源与预处理说明",
        "data_period": "数据周期描述",
        "data_count": 30,
        "latest_issue": "最新期号",
        "analysis_time": "分析时间"
    },
    "predictions": {
        "wan": [
            {"number": 5, "confidence": 0.85, "reason": "近期热号，频次统计排名第一"},
            {"number": 3, "confidence": 0.78, "reason": "遗漏值即将到期"},
            {"number": 8, "confidence": 0.72, "reason": "趋势分析显示上升"}
        ],
        "qian": [
            {"number": 2, "confidence": 0.82, "reason": "频次统计排名第一"},
            {"number": 6, "confidence": 0.76, "reason": "奇偶模式转换"},
            {"number": 9, "confidence": 0.70, "reason": "近期走势明显"}
        ],
        "bai": [
            {"number": 7, "confidence": 0.80, "reason": "遗漏值回归"},
            {"number": 1, "confidence": 0.75, "reason": "冷热号交替"},
            {"number": 4, "confidence": 0.68, "reason": "大小模式转换"}
        ],
        "shi": [
            {"number": 4, "confidence": 0.83, "reason": "频次统计排名第一"},
            {"number": 0, "confidence": 0.77, "reason": "遗漏值即将到期"},
            {"number": 5, "confidence": 0.71, "reason": "趋势分析显示下降"}
        ]
    },
    "trend_analysis": {
        "wan": "万位近期走势分析：...",
        "qian": "千位近期走势分析：...",
        "bai": "百位近期走势分析：...",
        "shi": "十位近期走势分析：..."
    },
    "key_features": [
        "特征1描述",
        "特征2描述",
        "特征3描述"
    ],
    "reasoning_process": [
        "推理步骤1说明",
        "推理步骤2说明",
        "推理步骤3说明"
    ],
    "recommended_combinations": [
        {"numbers": [5, 2, 7, 4], "confidence": 0.72, "reason": "综合各位置最优推荐"},
        {"numbers": [5, 2, 7, 0], "confidence": 0.68, "reason": "十位备选方案"}
    ],
    "risk_warning": "本分析基于历史数据统计，不保证中奖，请理性购彩。"
}
"""
        return prompt

    def _generate_structured_report(self, ai_result: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
        """
        生成结构化AI分析报告

        Args:
            ai_result: AI模型返回的分析结果
            data: 原始数据源

        Returns:
            完整的结构化报告字典
        """
        report_uuid = str(uuid.uuid4())
        report_date = datetime.now().strftime('%Y-%m-%d')

        data_source = ai_result.get('data_source', {})
        predictions = ai_result.get('predictions', {})
        trend_analysis = ai_result.get('trend_analysis', {})
        key_features = ai_result.get('key_features', [])
        reasoning_process = ai_result.get('reasoning_process', [])
        recommended_combinations = ai_result.get('recommended_combinations', [])
        risk_warning = ai_result.get('risk_warning', '')

        recommended_numbers = {}
        confidence_scores = {}

        for pos_key, pos_name in zip(self.position_keys[:4], self.position_names[:4]):
            rec_list = predictions.get(pos_key, [])
            recommended_numbers[pos_key] = [r.get('number') for r in rec_list if isinstance(r, dict)]
            confidence_scores[pos_key] = [r.get('confidence', 0) for r in rec_list if isinstance(r, dict)]

        report_content = self._format_report_content(
            data_source, predictions, trend_analysis, key_features,
            reasoning_process, recommended_combinations, risk_warning
        )

        report = {
            'report_uuid': report_uuid,
            'report_date': report_date,
            'data_count': data.get('data_count', 0),
            'latest_issue': data.get('latest_issue', ''),
            'next_issue': self._infer_next_issue(data.get('latest_issue', '')),
            'data_source': data_source,
            'predictions': predictions,
            'trend_analysis': trend_analysis,
            'key_features': key_features,
            'reasoning_process': reasoning_process,
            'recommended_numbers': recommended_numbers,
            'confidence_scores': confidence_scores,
            'recommended_combinations': recommended_combinations,
            'risk_warning': risk_warning,
            'report_content': report_content,
            'model_version': self.model_name,
            'analysis_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'created_at': datetime.now().isoformat()
        }

        return report

    def _format_report_content(self, data_source: Dict, predictions: Dict,
                              trend_analysis: Dict, key_features: List,
                              reasoning_process: List, combinations: List,
                              risk_warning: str) -> str:
        """
        格式化报告内容为可读文本

        Args:
            data_source: 数据源信息
            predictions: 预测结果
            trend_analysis: 趋势分析
            key_features: 关键特征
            reasoning_process: 推理过程
            combinations: 推荐组合
            risk_warning: 风险提示

        Returns:
            格式化的报告内容字符串
        """
        lines = []
        lines.append('=' * 80)
        lines.append('排列5 AI分析报告')
        lines.append('=' * 80)
        lines.append(f'生成时间: {data_source.get("analysis_time", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))}')
        lines.append(f'最新期号: {data_source.get("latest_issue", "")}')
        lines.append(f'数据周期: {data_source.get("data_period", "")}')
        lines.append(f'数据条数: {data_source.get("data_count", 0)}')
        lines.append('')

        lines.append('一、数据来源与预处理说明')
        lines.append('-' * 50)
        lines.append(data_source.get('description', '未提供'))
        lines.append('')

        lines.append('二、各位置号码预测结果')
        lines.append('-' * 50)

        pos_mapping = {'wan': '万位', 'qian': '千位', 'bai': '百位', 'shi': '十位'}
        for pos_key, pos_name in pos_mapping.items():
            lines.append(f'\n【{pos_name}】')
            rec_list = predictions.get(pos_key, [])
            for i, rec in enumerate(rec_list, 1):
                if isinstance(rec, dict):
                    lines.append(f'  {i}. 号码{rec.get("number", "?")} (置信度: {rec.get("confidence", 0):.2%}) - {rec.get("reason", "")}')

        lines.append('')
        lines.append('三、趋势分析与关键特征提取')
        lines.append('-' * 50)

        for pos_key, pos_name in pos_mapping.items():
            lines.append(f'\n【{pos_name}走势分析】')
            lines.append(trend_analysis.get(pos_key, '未提供'))

        lines.append('')
        lines.append('【关键特征】')
        for i, feature in enumerate(key_features, 1):
            lines.append(f'  {i}. {feature}')

        lines.append('')
        lines.append('四、预测依据与模型推理过程')
        lines.append('-' * 50)
        for i, step in enumerate(reasoning_process, 1):
            lines.append(f'  {i}. {step}')

        lines.append('')
        lines.append('五、推荐组合')
        lines.append('-' * 50)
        for i, combo in enumerate(combinations, 1):
            if isinstance(combo, dict):
                nums = combo.get('numbers', [])
                lines.append(f'  {i}. {"".join(map(str, nums))} (置信度: {combo.get("confidence", 0):.2%}) - {combo.get("reason", "")}')

        lines.append('')
        lines.append('=' * 80)
        lines.append('风险提示')
        lines.append('=' * 80)
        lines.append(risk_warning)
        lines.append('')
        lines.append('本报告仅基于历史数据统计分析，无法保证开奖结果，请理性购彩。')
        lines.append('=' * 80)

        return '\n'.join(lines)

    def _infer_next_issue(self, current_issue: str) -> str:
        """推导下一期期号"""
        if current_issue and current_issue.isdigit():
            next_num = int(current_issue) + 1
            return str(next_num)
        return '未知'

    def _save_report_to_database(self, report: Dict[str, Any]) -> Optional[str]:
        """
        将分析报告保存到数据库

        Args:
            report: 结构化报告字典

        Returns:
            报告UUID，失败返回None
        """
        try:
            from modules.database import P5Database
            db = P5Database()
            if not db.connect():
                logger.error('数据库连接失败，无法保存报告')
                return None

            trend_analysis_json = json.dumps(report.get('trend_analysis', {}), ensure_ascii=False)
            probability_stats_json = json.dumps({
                'key_features': report.get('key_features', []),
                'reasoning_process': report.get('reasoning_process', []),
                'model_version': report.get('model_version', '')
            }, ensure_ascii=False)
            recommended_numbers_json = json.dumps(report.get('recommended_numbers', {}), ensure_ascii=False)
            recommended_combinations_json = json.dumps(report.get('recommended_combinations', []), ensure_ascii=False)
            confidence_scores_json = json.dumps(report.get('confidence_scores', {}), ensure_ascii=False)

            report_uuid = db.insert_ai_report(
                report_content=report.get('report_content', ''),
                data_count=report.get('data_count', 0),
                latest_issue=report.get('latest_issue', ''),
                next_issue=report.get('next_issue', ''),
                trend_analysis=trend_analysis_json,
                probability_stats=probability_stats_json,
                recommended_numbers=recommended_numbers_json,
                recommended_combinations=recommended_combinations_json,
                confidence_scores=confidence_scores_json,
                recommendation_reasons='AI深度分析推荐',
                key_conclusions=json.dumps(report.get('key_features', []), ensure_ascii=False),
                risk_warning=report.get('risk_warning', ''),
                report_format='JSON'
            )

            db.disconnect()

            if report_uuid:
                logger.info(f'AI分析报告保存成功，UUID: {report_uuid}')
                return report_uuid
            else:
                logger.error('AI分析报告保存失败')
                return None

        except Exception as e:
            logger.error(f'保存报告到数据库失败: {e}')
            return None

    def analyze(self, data_limit: int = 30) -> Dict[str, Any]:
        """
        执行完整的AI分析流程

        Args:
            data_limit: 获取历史数据的期数限制

        Returns:
            分析结果字典，包含报告内容和数据库存储状态
        """
        logger.info('=' * 80)
        logger.info('开始执行AI分析')
        logger.info('=' * 80)

        # 1. 获取数据源
        logger.info('步骤1：获取数据源...')
        data = self._fetch_data_from_database(limit=data_limit)
        if data.get('error'):
            return {
                'success': False,
                'error': data['error'],
                'report': None
            }

        if data['data_count'] == 0:
            return {
                'success': False,
                'error': '数据库中没有历史数据',
                'report': None
            }

        # 2. 构建提示词
        logger.info('步骤2：构建AI分析提示词...')
        prompt = self._build_ai_prompt(data)
        logger.info(f'提示词长度: {len(prompt)}')

        # 3. 调用AI模型
        logger.info('步骤3：调用AI模型...')
        messages = [
            {
                "role": "system",
                "content": "你是一位专业的排列5彩票数据分析专家，擅长基于历史数据进行深度分析和预测。请严格按照要求输出JSON格式。"
            },
            {
                "role": "user",
                "content": prompt
            }
        ]

        ai_response = self._call_ai_model(
            messages=messages,
            max_tokens=8000,
            temperature=0.7
        )

        if not ai_response:
            return {
                'success': False,
                'error': 'AI模型调用失败',
                'report': None
            }

        # 4. 解析AI响应
        logger.info('步骤4：解析AI响应...')
        ai_result = self._parse_ai_response(ai_response)
        if not ai_result:
            return {
                'success': False,
                'error': 'AI响应解析失败',
                'report': None
            }

        # 5. 生成结构化报告
        logger.info('步骤5：生成结构化报告...')
        report = self._generate_structured_report(ai_result, data)

        # 6. 保存报告到数据库
        logger.info('步骤6：保存报告到数据库...')
        report_uuid = self._save_report_to_database(report)

        # 7. 保存报告到文件
        logger.info('步骤7：保存报告到文件...')
        report_file = self._save_report_to_file(report)

        result = {
            'success': True,
            'report': report,
            'report_uuid': report_uuid,
            'report_file': report_file,
            'model_version': self.model_name,
            'data_count': data['data_count'],
            'latest_issue': data['latest_issue'],
            'next_issue': report['next_issue'],
            'risk_warning': report['risk_warning']
        }

        logger.info('=' * 80)
        logger.info('AI分析完成')
        logger.info(f'报告UUID: {report_uuid}')
        logger.info(f'报告文件: {report_file}')
        logger.info('=' * 80)

        return result

    def _save_report_to_file(self, report: Dict[str, Any]) -> str:
        """保存报告到本地文件"""
        os.makedirs('reports', exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'reports/p5_ernie_ai_report_{timestamp}.json'

        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            logger.info(f'报告已保存到文件: {filename}')
            return filename
        except Exception as e:
            logger.error(f'保存报告文件失败: {e}')
            return ''

    def get_latest_report(self) -> Optional[Dict[str, Any]]:
        """获取最新的AI分析报告"""
        try:
            from modules.database import P5Database
            db = P5Database()
            if not db.connect():
                return None

            report = db.get_latest_ai_report()
            db.disconnect()

            if report:
                if report.get('trend_analysis'):
                    report['trend_analysis'] = json.loads(report['trend_analysis'])
                if report.get('probability_stats'):
                    report['probability_stats'] = json.loads(report['probability_stats'])
                if report.get('recommended_numbers'):
                    report['recommended_numbers'] = json.loads(report['recommended_numbers'])
                if report.get('recommended_combinations'):
                    report['recommended_combinations'] = json.loads(report['recommended_combinations'])
                if report.get('confidence_scores'):
                    report['confidence_scores'] = json.loads(report['confidence_scores'])

            return report

        except Exception as e:
            logger.error(f'获取最新报告失败: {e}')
            return None

    def list_reports(self, limit: int = 10) -> List[Dict[str, Any]]:
        """获取AI分析报告列表"""
        try:
            from modules.database import P5Database
            db = P5Database()
            if not db.connect():
                return []

            reports = db.get_all_ai_reports(limit=limit)
            db.disconnect()

            for report in reports:
                if report.get('trend_analysis'):
                    report['trend_analysis'] = json.loads(report['trend_analysis'])
                if report.get('recommended_numbers'):
                    report['recommended_numbers'] = json.loads(report['recommended_numbers'])

            return reports

        except Exception as e:
            logger.error(f'获取报告列表失败: {e}')
            return []


def run_ernie_ai_analysis(data_limit: int = 30) -> Dict[str, Any]:
    """
    便捷函数：执行AI分析

    Args:
        data_limit: 获取历史数据的期数限制

    Returns:
        分析结果字典
    """
    analyzer = AIAnalyzer()
    return analyzer.analyze(data_limit=data_limit)


class ComprehensiveAnalyzer(AIAnalyzer):
    """
    综合分析器

    整合多源数据进行二次深度分析：
    - Redis中的原始数据
    - 网络大神分析结果
    - AI模型初步分析数据
    """

    def __init__(self):
        super().__init__()
        self.redis_client = None
        # 专家分析器模块已整合到pipeline.py中,此处保留兼容性但不初始化
        self.expert_analyzer = None

    def _init_redis(self):
        """初始化Redis客户端"""
        try:
            from modules.cache import CacheClient
            self.redis_client = CacheClient()
            if self.redis_client.is_connected():
                logger.info('Redis客户端初始化成功')
            else:
                logger.warning('Redis客户端连接失败')
        except ImportError:
            logger.warning('无法导入Redis模块')

    # NOTE: _init_expert_analyzer() 已被移除。
    # 原方法尝试导入 modules.expert_analyzer 但该文件不存在。
    # 专家分析功能已迁移到 pipeline.py 的 step1_crawl_articles_and_analyze() 中。

    def _build_comprehensive_prompt(self, 
                                    raw_data: Dict[str, Any],
                                    expert_data: Dict[str, Any],
                                    ai_preliminary: Dict[str, Any],
                                    db_data: Dict[str, Any]) -> str:
        """
        构建综合分析提示词

        Args:
            raw_data: Redis中的原始数据
            expert_data: 网络专家分析数据
            ai_preliminary: AI初步分析数据
            db_data: 数据库中的历史数据

        Returns:
            综合提示词
        """
        prompt_parts = []

        prompt_parts.append("""
你是一位专业的排列5彩票数据深度分析专家。请基于以下多源数据进行综合分析和预测：

【数据来源说明】
本次分析整合了以下三类数据：
1. 原始开奖数据：最近30期历史开奖记录及各位置走势统计
2. 网络专家分析：多位彩票分析专家的推荐意见和预测
3. AI初步分析：AI模型基于历史数据的初步分析结果

请综合以上所有数据，进行深度分析，生成包含关键指标、趋势判断和预测结果的综合报告。
""")

        prompt_parts.append("\n" + "=" * 60)
        prompt_parts.append("一、原始开奖数据")
        prompt_parts.append("=" * 60)
        
        if raw_data:
            prompt_parts.append(f"数据来源: {raw_data.get('source_url', '未知')}")
            prompt_parts.append(f"爬取时间: {raw_data.get('crawl_time', '未知')}")
            prompt_parts.append(f"当前期号: {raw_data.get('current_issue', '未知')}")
            
            if raw_data.get('trend_statistics'):
                stats = raw_data['trend_statistics']
                for pos_name, pos_key in zip(['万位', '千位', '百位', '十位'], ['wanwei', 'qianwei', 'baiwei', 'shiwei']):
                    if stats.get(pos_key):
                        prompt_parts.append(f"\n【{pos_name}走势统计】")
                        prompt_parts.append(f"数据条数: {len(stats[pos_key])}")

        if db_data:
            prompt_parts.append(f"\n【数据库历史数据】")
            prompt_parts.append(f"数据条数: {db_data.get('data_count', 0)}")
            prompt_parts.append(f"最新期号: {db_data.get('latest_issue', '未知')}")
            
            if db_data.get('recent_data'):
                recent = db_data['recent_data'][:10]
                prompt_parts.append("最近10期开奖记录:")
                for item in recent:
                    prompt_parts.append(f"  {item.get('issue', '')}: {item.get('numbers_display', '')}")

        prompt_parts.append("\n" + "=" * 60)
        prompt_parts.append("二、网络专家分析")
        prompt_parts.append("=" * 60)
        
        if expert_data:
            prompt_parts.append(f"专家数量: {expert_data.get('expert_count', 0)}")
            prompt_parts.append(f"获取时间: {expert_data.get('fetch_time', '未知')}")
            
            if expert_data.get('aggregated'):
                aggregated = expert_data['aggregated']
                for pos_name, pos_key in zip(['万位', '千位', '百位', '十位'], ['wan', 'qian', 'bai', 'shi']):
                    if aggregated.get(pos_key):
                        pos_data = aggregated[pos_key]
                        prompt_parts.append(f"\n【{pos_name}专家推荐】")
                        prompt_parts.append(f"热门号码: {', '.join(pos_data.get('top_digits', []))}")
                        prompt_parts.append(f"参与专家数: {pos_data.get('total_experts', 0)}")
            
            if expert_data.get('raw_experts'):
                experts = expert_data['raw_experts'][:5]
                prompt_parts.append("\n【部分专家推荐详情】")
                for expert in experts:
                    prompt_parts.append(f"  专家: {expert.get('expert_name', '')}")
                    prompt_parts.append(f"    推荐号码: {', '.join(expert.get('forecast_numbers', []))}")
                    prompt_parts.append(f"    准确率: {expert.get('accuracy_rate', '')}")

        prompt_parts.append("\n" + "=" * 60)
        prompt_parts.append("三、AI初步分析")
        prompt_parts.append("=" * 60)
        
        if ai_preliminary:
            prompt_parts.append(f"模型版本: {ai_preliminary.get('model_version', '未知')}")
            
            if ai_preliminary.get('recommended_numbers'):
                nums = ai_preliminary['recommended_numbers']
                prompt_parts.append("\n【AI初步推荐号码】")
                for pos_name, pos_key in zip(['万位', '千位', '百位', '十位'], ['wan', 'qian', 'bai', 'shi']):
                    if nums.get(pos_key):
                        prompt_parts.append(f"  {pos_name}: {nums[pos_key]}")
            
            if ai_preliminary.get('confidence_scores'):
                scores = ai_preliminary['confidence_scores']
                prompt_parts.append("\n【置信度分数】")
                for pos_name, pos_key in zip(['万位', '千位', '百位', '十位'], ['wan', 'qian', 'bai', 'shi']):
                    if scores.get(pos_key):
                        prompt_parts.append(f"  {pos_name}: {scores[pos_key]}")

            if ai_preliminary.get('key_conclusions'):
                prompt_parts.append("\n【AI关键结论】")
                prompt_parts.append(ai_preliminary['key_conclusions'][:500])

        prompt_parts.append("\n" + "=" * 60)
        prompt_parts.append("四、分析要求")
        prompt_parts.append("=" * 60)
        prompt_parts.append("""
请基于以上多源数据，进行深度综合分析，输出JSON格式的分析报告。

输出格式要求（必须严格按照JSON格式输出）：
{
    "data_source": "综合分析（原始数据+专家分析+AI初步分析）",
    "analysis_time": "YYYY-MM-DD HH:MM:SS",
    "model_version": "agnes-2.0-flash",
    "data_period": "最近30期",
    "next_issue": "预测期号",
    
    "prediction": {
        "wan": {"numbers": [数字列表], "confidence": [置信度列表], "reason": "分析理由"},
        "qian": {"numbers": [数字列表], "confidence": [置信度列表], "reason": "分析理由"},
        "bai": {"numbers": [数字列表], "confidence": [置信度列表], "reason": "分析理由"},
        "shi": {"numbers": [数字列表], "confidence": [置信度列表], "reason": "分析理由"}
    },
    
    "trend_analysis": {
        "summary": "总体趋势总结",
        "wan": "万位趋势分析",
        "qian": "千位趋势分析",
        "bai": "百位趋势分析",
        "shi": "十位趋势分析"
    },
    
    "key_features": [
        "特征1描述",
        "特征2描述",
        "特征3描述"
    ],
    
    "reasoning_process": "详细的推理过程说明",
    
    "recommended_combinations": ["推荐组合1", "推荐组合2"],
    
    "data_source_summary": {
        "raw_data_count": 原始数据条数,
        "expert_count": 专家数量,
        "ai_preliminary_version": "AI模型版本"
    },
    
    "risk_warning": "本分析基于历史数据统计，不保证中奖，请理性购彩。"
}

请确保：
1. 推荐号码每个位置最多3个
2. 置信度为0-1之间的小数
3. 分析过程要综合考虑所有数据源
4. 推理过程要清晰、有条理
""")

        return "\n".join(prompt_parts)

    def analyze_with_multi_sources(self, data_limit: int = 30) -> Dict[str, Any]:
        """
        执行综合分析（二次深度分析）

        Args:
            data_limit: 获取历史数据的期数限制

        Returns:
            综合分析结果
        """
        logger.info('=' * 80)
        logger.info('开始执行综合分析（二次深度分析）')
        logger.info('=' * 80)

        self._init_redis()
        # expert_analyzer已移除,专家分析功能在pipeline.py中

        # 1. 获取数据库历史数据
        logger.info('步骤1：获取数据库历史数据...')
        db_data = self._fetch_data_from_database(limit=data_limit)
        if db_data.get('error'):
            return {'success': False, 'error': db_data['error'], 'report': None}

        # 2. 获取Redis原始数据
        logger.info('步骤2：获取Redis原始数据...')
        raw_data = {}
        if self.redis_client and self.redis_client.is_connected():
            latest_issue = db_data.get('latest_issue', '')
            if latest_issue:
                raw_data = self.redis_client.get_raw_data(latest_issue) or {}
            if not raw_data:
                raw_data_list = self.redis_client.get_all_raw_data(limit=1)
                if raw_data_list:
                    raw_data = raw_data_list[0]

        # 3. 获取网络专家分析数据（已迁移到pipeline.py）
        logger.info('步骤3：获取网络专家分析数据...')
        expert_data = {}
        # 专家分析已整合到pipeline的step1中,此处保留空字典兼容

        # 4. 获取AI初步分析数据
        logger.info('步骤4：获取AI初步分析数据...')
        ai_preliminary = {}
        latest_report = self.get_latest_report()
        if latest_report:
            ai_preliminary = {
                'source': 'AI模型初步分析',
                'model_version': latest_report.get('probability_stats', {}).get('model_version', '未知'),
                'analysis_time': latest_report.get('report_date', ''),
                'recommended_numbers': latest_report.get('recommended_numbers', {}),
                'confidence_scores': latest_report.get('confidence_scores', {}),
                'trend_analysis': latest_report.get('trend_analysis', ''),
                'key_conclusions': latest_report.get('key_conclusions', ''),
                'summary': latest_report.get('report_content', '')[:500]
            }

        # 5. 构建综合提示词
        logger.info('步骤5：构建综合分析提示词...')
        prompt = self._build_comprehensive_prompt(raw_data, expert_data, ai_preliminary, db_data)
        logger.info(f'提示词长度: {len(prompt)}')

        # 6. 调用AI模型进行二次分析
        logger.info('步骤6：调用AI模型进行二次深度分析...')
        messages = [
            {
                "role": "system",
                "content": "你是一位专业的排列5彩票数据深度分析专家，擅长整合多源数据进行综合分析。请严格按照要求输出JSON格式。"
            },
            {
                "role": "user",
                "content": prompt
            }
        ]

        ai_response = self._call_ai_model(messages=messages, max_tokens=8000, temperature=0.7)
        if not ai_response:
            return {'success': False, 'error': 'AI模型调用失败', 'report': None}

        # 7. 解析AI响应
        logger.info('步骤7：解析AI响应...')
        ai_result = self._parse_ai_response(ai_response)
        if not ai_result:
            return {'success': False, 'error': 'AI响应解析失败', 'report': None}

        # 8. 生成结构化报告
        logger.info('步骤8：生成结构化综合报告...')
        report = self._generate_structured_report(ai_result, db_data)

        # 9. 保存到数据库
        logger.info('步骤9：保存报告到数据库...')
        # _save_report_to_database 接受单个 report 参数（与父类实现一致），
        # 传入多余参数会导致调用错误，故仅传入 report。
        report_uuid = self._save_report_to_database(report)

        # 10. 保存到文件
        logger.info('步骤10：保存报告到文件...')
        report_file = self._save_report_to_file(report)

        # 11. 保存到Redis
        if self.redis_client and self.redis_client.is_connected():
            next_issue = report.get('next_issue', '')
            if next_issue:
                self.redis_client.save_combined_analysis(next_issue, report)

        result = {
            'success': True,
            'report_uuid': report_uuid,
            'latest_issue': db_data.get('latest_issue'),
            'next_issue': report.get('next_issue'),
            'data_count': db_data.get('data_count', 0),
            'model_version': self.model_name,
            'report_file': report_file,
            'report': report,
            'data_sources': {
                'redis_raw_data': bool(raw_data),
                'expert_data': expert_data.get('expert_count', 0),
                'ai_preliminary': bool(ai_preliminary)
            }
        }

        logger.info('=' * 80)
        logger.info('综合分析完成')
        logger.info(f'报告UUID: {report_uuid}')
        logger.info(f'报告文件: {report_file}')
        logger.info(f'数据源: Redis原始数据={bool(raw_data)}, 专家数据={expert_data.get("expert_count", 0)}条, AI初步分析={bool(ai_preliminary)}')
        logger.info('=' * 80)

        return result


def run_comprehensive_analysis(data_limit: int = 30) -> Dict[str, Any]:
    """
    便捷函数：执行综合分析（二次深度分析）

    Args:
        data_limit: 获取历史数据的期数限制

    Returns:
        综合分析结果字典
    """
    analyzer = ComprehensiveAnalyzer()
    return analyzer.analyze_with_multi_sources(data_limit=data_limit)


if __name__ == '__main__':
    print('=' * 80)
    print('排列5 AI分析模块测试')
    print('=' * 80)

    analyzer = AIAnalyzer()
    result = analyzer.analyze(data_limit=30)

    if result['success']:
        print('\n分析成功！')
        print(f'报告UUID: {result["report_uuid"]}')
        print(f'最新期号: {result["latest_issue"]}')
        print(f'预测期号: {result["next_issue"]}')
        print(f'数据条数: {result["data_count"]}')
        print(f'模型版本: {result["model_version"]}')
        print(f'报告文件: {result["report_file"]}')
        print('\n' + '=' * 80)
        print('报告内容预览:')
        print('=' * 80)
        print(result['report']['report_content'][:2000])
    else:
        print(f'\n分析失败: {result["error"]}')
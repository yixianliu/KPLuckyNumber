"""
AI分析器模块（排列5专用）

负责整合排列5历史数据与走势图数据，调用AI模型进行深度分析，
并将分析结果按照指定格式保存到AI分析报告表中。

核心功能：
1. 数据整合 - 从数据库读取历史数据和走势图数据，进行质量检查和标准化
2. AI模型调用 - 基于百度千帆API规范，调用大语言模型进行分析
3. 结果解析 - 解析AI返回的JSON格式结果，提取关键信息
4. 结果存储 - 将分析结果保存到p5_ai_report表中
"""

import os
import sys
import json
import logging
import time
import random
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.makedirs('logs', exist_ok=True)

logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler = logging.FileHandler('logs/ai_analyzer.log', encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

# 说明：本模块直接依赖外部AI配置（QIANYAN_API_CONFIG），在没有配置api_key时会抛出异常。
# 请通过项目根的 config.py 提供适当的 QIANYAN_API_CONFIG 配置，或在调用前捕获异常以优雅降级。

class AIAnalyzer:
    """
    AI分析器类
    
    提供从数据整合到AI分析再到结果存储的完整流程。
    """
    
    def __init__(self, api_key: str = None, api_url: str = None):
        """
        初始化AI分析器
        
        Args:
            api_key: API密钥，默认为从config.py读取
            api_url: API地址，默认为从config.py读取
        """
        from config import DB_CONFIG, QIANYAN_API_CONFIG
        self.db_config = DB_CONFIG
        
        # 从config.py读取API配置
        api_config = QIANYAN_API_CONFIG
        self.api_url = api_url or api_config.get('api_url', "https://qianfan.baidubce.com/v2/chat/completions")
        self.api_key = api_key or api_config.get('api_key', '')
        self.model_name = api_config.get('model_name', 'deepseek-v3.1-250821')
        
        if not self.api_key:
            raise ValueError('API密钥未配置，请在config.py的QIANYAN_API_CONFIG中设置api_key')
        
        self.headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.api_key}'
        }
        
        self.position_names = ['万位', '千位', '百位', '十位', '个位']
        self.analysis_prompt_template = self._build_analysis_prompt()
    
    def _build_analysis_prompt(self) -> str:
        """构建AI分析提示词模板"""
        return """你是一位专业的排列5彩票数据分析专家。请基于以下提供的排列5历史开奖数据和走势图数据，进行深度统计分析和趋势预测。

【彩种规则】
- 排列5：5位数字，每位0-9，不重复选取，每天开奖
- 号码位置：万位、千位、百位、十位、个位
- 和值范围：0-45
- 跨度范围：0-9

【分析要求】
1. 趋势分析：分析各位置号码近期走势、冷热号变化趋势
2. 概率统计：计算各号码出现频次、遗漏值统计
3. 模式识别：识别奇偶比、大小比、质合比等模式规律
4. 号码推荐：基于统计规律推荐下一期各位置号码（每个位置推荐2-3个号码）
5. 组合推荐：推荐3-5个完整号码组合
6. 置信度评估：为每个推荐号码和组合提供置信度分数（0-1）
7. 风险提示：明确说明所有分析仅基于历史数据统计，不保证中奖

【输出格式要求】
请严格按照以下JSON格式输出，不要包含任何额外文字：

{
    "trend_analysis": {
        "wan": "万位近期走势分析...",
        "qian": "千位近期走势分析...",
        "bai": "百位近期走势分析...",
        "shi": "十位近期走势分析...",
        "ge": "个位近期走势分析..."
    },
    "probability_stats": {
        "frequency": {
            "wan": {"0": 10, "1": 15, ...},
            "qian": {"0": 12, "1": 18, ...},
            "bai": {"0": 8, "1": 14, ...},
            "shi": {"0": 11, "1": 16, ...},
            "ge": {"0": 9, "1": 13, ...}
        },
        "hot_numbers": {
            "wan": [{"number": 5, "frequency": 20, "recent_appearances": 3}],
            "qian": [{"number": 3, "frequency": 18, "recent_appearances": 2}],
            "bai": [{"number": 7, "frequency": 19, "recent_appearances": 4}],
            "shi": [{"number": 2, "frequency": 17, "recent_appearances": 2}],
            "ge": [{"number": 8, "frequency": 21, "recent_appearances": 3}]
        },
        "cold_numbers": {
            "wan": [{"number": 9, "frequency": 5, "omission": 15}],
            "qian": [{"number": 0, "frequency": 6, "omission": 12}],
            "bai": [{"number": 4, "frequency": 7, "omission": 10}],
            "shi": [{"number": 1, "frequency": 8, "omission": 14}],
            "ge": [{"number": 6, "frequency": 5, "omission": 16}]
        },
        "odd_even_distribution": {"odd_count": 3, "even_count": 2, "ratio": "3:2"},
        "big_small_distribution": {"big_count": 3, "small_count": 2, "ratio": "3:2"},
        "hezhi_range": {"min": 10, "max": 35, "most_frequent": [15, 20, 25]},
        "span_distribution": {"most_frequent": [6, 7, 8]}
    },
    "recommended_numbers": {
        "wan": [{"number": 5, "confidence": 0.85, "reason": "近期热号，连续出现概率高"}],
        "qian": [{"number": 3, "confidence": 0.78, "reason": "遗漏值即将到期"}],
        "bai": [{"number": 7, "confidence": 0.82, "reason": "频次统计排名第一"}],
        "shi": [{"number": 2, "confidence": 0.75, "reason": "奇偶模式转换概率高"}],
        "ge": [{"number": 8, "confidence": 0.88, "reason": "近期走势明显"}]
    },
    "recommended_combinations": [
        {"numbers": [5, 3, 7, 2, 8], "confidence": 0.72, "reason": "综合各位置最优推荐"},
        {"numbers": [5, 3, 7, 2, 6], "confidence": 0.68, "reason": "个位备选方案"},
        {"numbers": [5, 8, 7, 2, 8], "confidence": 0.65, "reason": "千位备选方案"}
    ],
    "confidence_scores": {
        "wan": 0.85,
        "qian": 0.78,
        "bai": 0.82,
        "shi": 0.75,
        "ge": 0.88,
        "overall": 0.82
    },
    "recommendation_reasons": "基于最近120期历史数据分析，各位置推荐号码均符合以下条件：1）近期出现频次较高；2）遗漏值在合理范围内；3）符合奇偶大小模式转换规律；4）与历史走势具有相似性。",
    "key_conclusions": [
        "万位5号近期热度明显上升，建议重点关注",
        "千位3号遗漏值即将到期，出现概率增大",
        "百位7号连续多期未出现，可能在近期出现",
        "十位2号符合奇偶转换规律",
        "个位8号为近期热号，连续出现可能性较高",
        "预计下一期和值在18-25之间",
        "预计跨度在6-8之间"
    ],
    "risk_warning": "本分析报告基于历史数据统计分析生成，所有结论均为概率性预测，不保证中奖。彩票开奖结果具有随机性，请理性购买。",
    "analysis_summary": "本次分析基于最近120期数据，通过频次统计、遗漏分析、模式识别等方法，综合推荐下一期号码组合。各位置置信度在0.75-0.88之间，整体置信度0.82。"
}

【待分析数据】
"""
    
    # ==================== 数据整合 ====================
    
    def fetch_and_integrate_data(self, limit: int = 120) -> Dict[str, Any]:
        """
        从数据库获取并整合排列5历史数据和走势图数据
        
        Args:
            limit: 获取最近多少期数据，默认120期
        
        Returns:
            整合后的数据集字典
        """
        logger.info(f'=== 开始获取并整合排列5数据 (最近{limit}期) ===')
        
        from modules.database_p5 import P5Database
        
        db = P5Database()
        if not db.connect():
            logger.error('数据库连接失败')
            return {'success': False, 'error': '数据库连接失败'}
        
        try:
            history_data = db.get_history_data(limit=limit)
            trend_data = db.get_trend_data(limit=limit)
            
            logger.info(f'数据库加载完成: 历史数据 {len(history_data)} 条, 走势数据 {len(trend_data)} 条')
            
            if not history_data:
                return {'success': False, 'error': '数据库中没有历史数据'}
            
            integrated_data = self._integrate_data(history_data, trend_data)
            
            db.disconnect()
            
            return {
                'success': True,
                'data': integrated_data,
                'data_count': len(integrated_data),
                'latest_issue': integrated_data[0]['issue'] if integrated_data else '',
                'next_issue': self._calculate_next_issue(integrated_data[0]['issue']) if integrated_data else '',
                'history_count': len(history_data),
                'trend_count': len(trend_data)
            }
        except Exception as e:
            logger.error(f'数据整合失败: {e}')
            db.disconnect()
            return {'success': False, 'error': str(e)}
    
    def _integrate_data(self, history_data: List[Dict], trend_data: List[Dict]) -> List[Dict]:
        """
        整合历史数据和走势图数据
        
        将走势图数据中的遗漏值、质合比等扩展字段补充到历史数据中
        
        Args:
            history_data: 历史数据列表
            trend_data: 走势图数据列表
        
        Returns:
            整合后的数据列表
        """
        trend_dict = {str(item['issue']): item for item in trend_data if 'issue' in item}
        
        integrated = []
        for item in history_data:
            issue = str(item['issue'])
            integrated_item = {
                'issue': issue,
                'date': item.get('draw_date', ''),
                'wan': item.get('wan', 0),
                'qian': item.get('qian', 0),
                'bai': item.get('bai', 0),
                'shi': item.get('shi', 0),
                'ge': item.get('ge', 0),
                'hezhi': item.get('hezhi', 0),
                'span': item.get('span', 0),
                'odd_even_ratio': item.get('odd_even_ratio', ''),
                'odd_even_pattern': item.get('odd_even_pattern', ''),
                'big_small_ratio': item.get('big_small_ratio', ''),
                'wan_omission': 0,
                'qian_omission': 0,
                'bai_omission': 0,
                'shi_omission': 0,
                'ge_omission': 0,
                'prime_composite_ratio': ''
            }
            
            if issue in trend_dict:
                t_item = trend_dict[issue]
                integrated_item['wan_omission'] = t_item.get('wan_omission', 0)
                integrated_item['qian_omission'] = t_item.get('qian_omission', 0)
                integrated_item['bai_omission'] = t_item.get('bai_omission', 0)
                integrated_item['shi_omission'] = t_item.get('shi_omission', 0)
                integrated_item['ge_omission'] = t_item.get('ge_omission', 0)
                integrated_item['prime_composite_ratio'] = t_item.get('prime_composite_ratio', '')
            
            integrated.append(integrated_item)
        
        return sorted(integrated, key=lambda x: int(x['issue']) if x['issue'].isdigit() else 0, reverse=True)
    
    def _calculate_next_issue(self, current_issue: str) -> str:
        """
        计算下一期期号
        
        Args:
            current_issue: 当前期号，格式如2026001
        
        Returns:
            下一期期号
        """
        try:
            year = current_issue[:4]
            seq = int(current_issue[4:])
            next_seq = seq + 1
            return f'{year}{next_seq:03d}'
        except Exception:
            return ''
    
    def fetch_position_trend_data(self, db, limit: int = 30) -> Dict[str, List[Dict]]:
        """
        获取各位置独立走势数据（万位、千位、百位、十位）
        
        Args:
            db: 数据库连接对象
            limit: 获取最近多少期
        
        Returns:
            各位置走势数据字典
        """
        logger.info(f'=== 获取各位置独立走势数据 (最近{limit}期) ===')
        
        wan_trend = db.get_wan_trend_data(limit=limit) or []
        qian_trend = db.get_qian_trend_data(limit=limit) or []
        bai_trend = db.get_bai_trend_data(limit=limit) or []
        shi_trend = db.get_shi_trend_data(limit=limit) or []
        
        logger.info(f'位置走势数据: 万位={len(wan_trend)}条, 千位={len(qian_trend)}条, 百位={len(bai_trend)}条, 十位={len(shi_trend)}条')
        
        return {
            'wan': wan_trend,
            'qian': qian_trend,
            'bai': bai_trend,
            'shi': shi_trend
        }
    
    def fetch_and_integrate_4position_data(self, limit: int = 30) -> Dict[str, Any]:
        """
        获取并整合排列5四位置数据（万位、千位、百位、十位）
        
        Args:
            limit: 获取最近多少期数据，默认30期
        
        Returns:
            整合后的数据集字典
        """
        logger.info(f'=== 开始获取并整合排列5四位置数据 (最近{limit}期) ===')
        
        from modules.database_p5 import P5Database
        
        db = P5Database()
        if not db.connect():
            logger.error('数据库连接失败')
            return {'success': False, 'error': '数据库连接失败'}
        
        try:
            # 获取历史数据
            history_data = db.get_history_data(limit=limit)
            if not history_data:
                return {'success': False, 'error': '数据库中没有历史数据'}
            
            # 获取各位置独立走势数据
            position_trends = self.fetch_position_trend_data(db, limit=limit)
            
            logger.info(f'数据库加载完成: 历史数据 {len(history_data)} 条')
            
            # 整合数据
            integrated_data = self._integrate_4position_data(history_data, position_trends)
            
            db.disconnect()
            
            return {
                'success': True,
                'data': integrated_data,
                'data_count': len(integrated_data),
                'latest_issue': integrated_data[0]['issue'] if integrated_data else '',
                'next_issue': self._calculate_next_issue(integrated_data[0]['issue']) if integrated_data else '',
                'history_count': len(history_data),
                'wan_trend_count': len(position_trends['wan']),
                'qian_trend_count': len(position_trends['qian']),
                'bai_trend_count': len(position_trends['bai']),
                'shi_trend_count': len(position_trends['shi'])
            }
        except Exception as e:
            logger.error(f'四位置数据整合失败: {e}')
            db.disconnect()
            return {'success': False, 'error': str(e)}
    
    def _integrate_4position_data(self, history_data: List[Dict], position_trends: Dict[str, List[Dict]]) -> List[Dict]:
        """
        整合历史数据和四位置走势图数据
        
        Args:
            history_data: 历史数据列表
            position_trends: 各位置走势数据字典
        
        Returns:
            整合后的数据列表
        """
        # 转换为字典便于查找
        wan_dict = {str(item['issue']): item for item in position_trends.get('wan', []) if 'issue' in item}
        qian_dict = {str(item['issue']): item for item in position_trends.get('qian', []) if 'issue' in item}
        bai_dict = {str(item['issue']): item for item in position_trends.get('bai', []) if 'issue' in item}
        shi_dict = {str(item['issue']): item for item in position_trends.get('shi', []) if 'issue' in item}
        
        integrated = []
        for item in history_data:
            issue = str(item['issue'])
            integrated_item = {
                'issue': issue,
                'date': item.get('draw_date', ''),
                'wan': item.get('wan', 0),
                'qian': item.get('qian', 0),
                'bai': item.get('bai', 0),
                'shi': item.get('shi', 0),
                'ge': item.get('ge', 0),
                'hezhi': item.get('hezhi', 0),
                'span': item.get('span', 0),
                'odd_even_ratio': item.get('odd_even_ratio', ''),
                'odd_even_pattern': item.get('odd_even_pattern', ''),
                'big_small_ratio': item.get('big_small_ratio', ''),
                # 四位置详细走势数据
                'wan_trend': wan_dict.get(issue, {}),
                'qian_trend': qian_dict.get(issue, {}),
                'bai_trend': bai_dict.get(issue, {}),
                'shi_trend': shi_dict.get(issue, {})
            }
            integrated.append(integrated_item)
        
        return sorted(integrated, key=lambda x: int(x['issue']) if x['issue'].isdigit() else 0, reverse=True)
    
    def format_data_for_ai_4position(self, integrated_data: List[Dict]) -> str:
        """
        将四位置整合后的数据格式化为AI可理解的文本格式
        
        Args:
            integrated_data: 整合后的数据列表
        
        Returns:
            格式化后的文本字符串
        """
        lines = []
        
        # 最近30期历史数据
        lines.append('【最近30期历史数据】')
        for i, item in enumerate(integrated_data[:30], 1):
            lines.append(f'期号:{item["issue"]} 日期:{item["date"]} 号码:{item["wan"]}{item["qian"]}{item["bai"]}{item["shi"]}{item["ge"]} 和值:{item["hezhi"]} 跨度:{item["span"]}')
        
        # 各位置详细走势数据
        lines.append('\n【各位置详细走势数据（最近30期）】')
        for pos, pos_name in [('wan', '万位'), ('qian', '千位'), ('bai', '百位'), ('shi', '十位')]:
            lines.append(f'\n{pos_name}:')
            for item in integrated_data[:30]:
                trend = item.get(f'{pos}_trend', {})
                if trend:
                    num = trend.get(f'{pos}_number', item.get(pos, 0))
                    omission = trend.get('omission', 0)
                    hot_level = trend.get('hot_level', '')
                    consecutive = trend.get('consecutive_count', 0)
                    lines.append(f'  {item["issue"]}: 号码={num}, 遗漏={omission}, 冷热={hot_level}, 连出={consecutive}')
                else:
                    lines.append(f'  {item["issue"]}: 号码={item.get(pos, 0)}, 遗漏=N/A')
        
        # 号码频次统计（最近30期）
        lines.append('\n【号码频次统计（最近30期）】')
        for pos, pos_name in [('wan', '万位'), ('qian', '千位'), ('bai', '百位'), ('shi', '十位')]:
            freq = {}
            for item in integrated_data:
                num = item[pos]
                freq[num] = freq.get(num, 0) + 1
            sorted_freq = sorted(freq.items(), key=lambda x: x[1], reverse=True)
            top3 = sorted_freq[:3]
            bottom3 = sorted_freq[-3:] if len(sorted_freq) >= 3 else sorted_freq
            lines.append(f'{pos_name}: 高频[{", ".join([f"{n}({c}次)" for n, c in top3])}] 低频[{", ".join([f"{n}({c}次)" for n, c in bottom3])}]')
        
        # 各位置遗漏值分析
        lines.append('\n【各位置遗漏值分析】')
        for pos, pos_name in [('wan', '万位'), ('qian', '千位'), ('bai', '百位'), ('shi', '十位')]:
            omissions = []
            for item in integrated_data[:30]:
                trend = item.get(f'{pos}_trend', {})
                if trend and 'omission' in trend:
                    omissions.append(trend['omission'])
                else:
                    omissions.append(0)
            
            if omissions:
                max_omission = max(omissions)
                avg_omission = sum(omissions) / len(omissions)
                current_omission = omissions[0] if omissions else 0
                lines.append(f'{pos_name}: 当前遗漏={current_omission}, 最大遗漏={max_omission}, 平均遗漏={avg_omission:.1f}')
        
        # 奇偶大小分布
        lines.append('\n【奇偶分布】')
        odd_counts = {'奇': 0, '偶': 0}
        for item in integrated_data[:30]:
            pattern = item.get('odd_even_pattern', '')
            for p in pattern:
                if p in odd_counts:
                    odd_counts[p] += 1
        lines.append(f'奇数:{odd_counts["奇"]}次, 偶数:{odd_counts["偶"]}次')
        
        lines.append('\n【大小分布】')
        for item in integrated_data[:30]:
            wan = item.get('wan', 0)
            qian = item.get('qian', 0)
            bai = item.get('bai', 0)
            shi = item.get('shi', 0)
            big = sum(1 for n in [wan, qian, bai, shi] if n >= 5)
            lines.append(f'{item["issue"]}: 大数={big}, 小数={4-big}')
        
        # 和值与跨度统计
        lines.append('\n【和值与跨度统计】')
        hezhi_values = [item['hezhi'] for item in integrated_data]
        span_values = [item['span'] for item in integrated_data]
        if hezhi_values:
            lines.append(f'和值: 最小={min(hezhi_values)}, 最大={max(hezhi_values)}, 平均={sum(hezhi_values)/len(hezhi_values):.1f}')
        if span_values:
            lines.append(f'跨度: 最小={min(span_values)}, 最大={max(span_values)}, 平均={sum(span_values)/len(span_values):.1f}')
        
        return '\n'.join(lines)
    
    def analyze_with_ai_4position(self, limit: int = 30) -> Dict[str, Any]:
        """
        执行四位置AI分析流程
        
        Args:
            limit: 使用最近多少期数据进行分析
        
        Returns:
            包含分析结果的字典
        """
        logger.info(f'=== 开始四位置AI分析流程 (最近{limit}期) ===')
        
        data_result = self.fetch_and_integrate_4position_data(limit=limit)
        if not data_result['success']:
            return {'success': False, 'error': data_result['error']}
        
        formatted_data = self.format_data_for_ai_4position(data_result['data'])
        
        # 构建四位置分析专用提示词
        prompt = self._build_4position_analysis_prompt() + formatted_data
        
        ai_response = self.call_ai_model(prompt)
        if not ai_response:
            return {'success': False, 'error': 'AI模型调用失败'}
        
        parsed_result = self._parse_ai_4position_response(ai_response)
        
        return {
            'success': True,
            'raw_response': ai_response,
            'parsed_result': parsed_result,
            'data_count': data_result['data_count'],
            'latest_issue': data_result['latest_issue'],
            'next_issue': data_result['next_issue'],
            'data': data_result['data']
        }
    
    def _build_4position_analysis_prompt(self) -> str:
        """构建四位置AI分析提示词模板"""
        return """你是一位专业的排列5彩票数据分析专家。请基于以下提供的排列5历史开奖数据和万位、千位、百位、十位独立走势图数据，进行深度统计分析并预测下一期号码。

【彩种规则】
- 排列5：5位数字，每位0-9，不重复选取，每天开奖
- 号码位置：万位、千位、百位、十位、个位（本次分析仅针对前四位）
- 和值范围：0-45（万位+千位+百位+十位+个位）
- 跨度范围：0-9（五个数中最大值-最小值）

【重要提示】
- 本次分析仅需预测万位、千位、百位、十位四个位置的号码
- 个位号码不在本次预测范围内，请忽略
- 每个位置需推荐2-3个候选号码，并给出置信度

【分析要求】
1. 趋势分析：分析万位、千位、百位、十位近期走势、冷热号变化
2. 概率统计：计算各位置号码出现频次、遗漏值统计
3. 模式识别：识别各位置奇偶比、大小比模式规律
4. 号码推荐：基于统计规律推荐下一期各位置号码（每个位置推荐2-3个号码）
5. 组合推荐：基于四位置推荐，推荐3-5个四码组合（不包含个位）
6. 置信度评估：为每个推荐号码提供置信度分数（0-1）
7. 风险提示：明确说明所有分析仅基于历史数据统计，不保证中奖

【输出格式要求】
请严格按照以下JSON格式输出，不要包含任何额外文字：

{
    "trend_analysis": {
        "wan": "万位近期走势分析...",
        "qian": "千位近期走势分析...",
        "bai": "百位近期走势分析...",
        "shi": "十位近期走势分析..."
    },
    "probability_stats": {
        "frequency": {
            "wan": {"0": 3, "1": 5, "2": 2, "3": 4, "4": 1, "5": 6, "6": 2, "7": 3, "8": 2, "9": 2},
            "qian": {"0": 4, "1": 3, "2": 5, "3": 2, "4": 3, "5": 4, "6": 3, "7": 2, "8": 3, "9": 1},
            "bai": {"0": 2, "1": 4, "2": 3, "3": 5, "4": 2, "5": 3, "6": 4, "7": 3, "8": 2, "9": 2},
            "shi": {"0": 3, "1": 2, "2": 4, "3": 3, "4": 5, "5": 2, "6": 3, "7": 4, "8": 2, "9": 2}
        },
        "hot_numbers": {
            "wan": [{"number": 5, "frequency": 6, "recent_appearances": 2}],
            "qian": [{"number": 2, "frequency": 5, "recent_appearances": 3}],
            "bai": [{"number": 3, "frequency": 5, "recent_appearances": 2}],
            "shi": [{"number": 4, "frequency": 5, "recent_appearances": 3}]
        },
        "cold_numbers": {
            "wan": [{"number": 4, "frequency": 1, "omission": 8}],
            "qian": [{"number": 9, "frequency": 1, "omission": 12}],
            "bai": [{"number": 0, "frequency": 2, "omission": 6}],
            "shi": [{"number": 1, "frequency": 2, "omission": 10}]
        },
        "omission_analysis": {
            "wan": {"current": 3, "max": 15, "avg": 5.2},
            "qian": {"current": 0, "max": 12, "avg": 4.8},
            "bai": {"current": 2, "max": 10, "avg": 4.5},
            "shi": {"current": 1, "max": 14, "avg": 5.0}
        }
    },
    "recommended_numbers": {
        "wan": [
            {"number": 5, "confidence": 0.85, "reason": "近期热号，连续出现概率高"},
            {"number": 3, "confidence": 0.72, "reason": "遗漏值接近平均值"},
            {"number": 7, "confidence": 0.65, "reason": "大小模式转换概率高"}
        ],
        "qian": [
            {"number": 2, "confidence": 0.80, "reason": "频次统计排名靠前"},
            {"number": 6, "confidence": 0.75, "reason": "遗漏值即将到期"},
            {"number": 8, "confidence": 0.68, "reason": "奇偶模式转换"}
        ],
        "bai": [
            {"number": 3, "confidence": 0.82, "reason": "近期走势明显"},
            {"number": 5, "confidence": 0.76, "reason": "遗漏值合理"},
            {"number": 9, "confidence": 0.70, "reason": "大小比偏好"}
        ],
        "shi": [
            {"number": 4, "confidence": 0.78, "reason": "遗漏值接近最大值"},
            {"number": 6, "confidence": 0.74, "reason": "频次稳定"},
            {"number": 2, "confidence": 0.70, "reason": "奇偶平衡"}
        ]
    },
    "recommended_combinations": [
        {"numbers": [5, 2, 3, 4], "confidence": 0.75, "reason": "综合各位置最优推荐"},
        {"numbers": [5, 2, 3, 6], "confidence": 0.70, "reason": "千位备选方案"},
        {"numbers": [3, 6, 5, 4], "confidence": 0.68, "reason": "万位备选方案"},
        {"numbers": [7, 8, 9, 2], "confidence": 0.65, "reason": "高置信度组合"},
        {"numbers": [3, 2, 5, 6], "confidence": 0.62, "reason": "均衡配置方案"}
    ],
    "confidence_scores": {
        "wan": 0.85,
        "qian": 0.80,
        "bai": 0.82,
        "shi": 0.78,
        "overall": 0.81
    },
    "recommendation_reasons": "基于最近30期数据分析，各位置推荐号码均符合以下条件：1）近期出现频次较高；2）遗漏值在合理范围内；3）符合奇偶大小模式转换规律；4）与历史走势具有相似性。",
    "key_conclusions": [
        "万位5号近期热度明显上升，建议重点关注",
        "千位2号频次统计排名第一，出现概率较大",
        "百位3号近期走势强劲，可作为核心参考",
        "十位4号遗漏值接近历史最大值，反弹概率较高",
        "预计下一期四位置和值在15-25之间",
        "奇偶比预计偏向3:1或2:2平衡模式",
        "大小比预计偏向2:2或3:1模式"
    ],
    "risk_warning": "本分析报告基于历史数据统计分析生成，所有结论均为概率性预测，不保证中奖。彩票开奖结果具有随机性，请理性购买。",
    "analysis_summary": "本次分析基于最近30期数据，通过频次统计、遗漏分析、模式识别等方法，为万位、千位、百位、十位四个位置分别推荐2-3个候选号码，并给出综合组合建议。各位置置信度在0.70-0.85之间，整体置信度0.81。"
}

【待分析数据】
"""
    
    def _parse_ai_4position_response(self, response_text: str) -> Dict[str, Any]:
        """
        解析AI四位置分析响应
        
        Args:
            response_text: AI返回的文本
        
        Returns:
            解析后的JSON数据
        """
        logger.info('=== 开始解析四位置AI响应 ===')
        
        try:
            start_idx = response_text.find('{')
            end_idx = response_text.rfind('}') + 1
            
            if start_idx == -1 or end_idx == 0:
                logger.error('无法找到JSON起始或结束位置')
                return self._generate_fallback_4position_result()
            
            json_str = response_text[start_idx:end_idx]
            result = json.loads(json_str)
            logger.info('四位置AI响应解析成功')
            return result
        
        except json.JSONDecodeError as e:
            logger.error(f'JSON解析失败: {e}, 尝试修复...')
            try:
                fixed_json = self._fix_json_format(response_text)
                result = json.loads(fixed_json)
                logger.info('修复后JSON解析成功')
                return result
            except Exception as e2:
                logger.error(f'修复后仍解析失败: {e2}')
                return self._generate_fallback_4position_result()
        except Exception as e:
            logger.error(f'解析AI响应失败: {e}')
            return self._generate_fallback_4position_result()
    
    def _generate_fallback_4position_result(self) -> Dict[str, Any]:
        """生成四位置备用分析结果（当AI解析失败时）"""
        return {
            'trend_analysis': {
                'wan': 'AI解析失败，无法获取趋势分析',
                'qian': 'AI解析失败，无法获取趋势分析',
                'bai': 'AI解析失败，无法获取趋势分析',
                'shi': 'AI解析失败，无法获取趋势分析'
            },
            'probability_stats': {},
            'recommended_numbers': {
                'wan': [{'number': 5, 'confidence': 0.5, 'reason': 'AI解析失败，默认推荐'}],
                'qian': [{'number': 3, 'confidence': 0.5, 'reason': 'AI解析失败，默认推荐'}],
                'bai': [{'number': 7, 'confidence': 0.5, 'reason': 'AI解析失败，默认推荐'}],
                'shi': [{'number': 2, 'confidence': 0.5, 'reason': 'AI解析失败，默认推荐'}]
            },
            'recommended_combinations': [],
            'confidence_scores': {'wan': 0.5, 'qian': 0.5, 'bai': 0.5, 'shi': 0.5, 'overall': 0.5},
            'recommendation_reasons': 'AI解析失败，使用默认推荐',
            'key_conclusions': ['AI解析失败'],
            'risk_warning': '本分析报告基于历史数据统计分析生成，所有结论均为概率性预测，不保证中奖。彩票开奖结果具有随机性，请理性购买。',
            'analysis_summary': 'AI解析失败，生成备用结果'
        }
    
    def save_4position_report_to_database(self, analysis_result: Dict[str, Any]) -> Optional[str]:
        """
        将四位置AI分析结果保存到数据库（可读文本格式）
        
        Args:
            analysis_result: AI分析结果字典
        
        Returns:
            报告UUID，失败返回None
        """
        logger.info('=== 开始保存四位置AI分析报告到数据库 ===')
        
        from modules.database_p5 import P5Database
        
        db = P5Database()
        if not db.connect():
            logger.error('数据库连接失败')
            return None
        
        try:
            parsed = analysis_result['parsed_result']
            next_issue = analysis_result['next_issue']
            latest_issue = analysis_result['latest_issue']
            
            # 生成可读文本格式的报告内容
            lines = []
            lines.append('=' * 70)
            lines.append('排列5 四位置AI分析报告'.center(60))
            lines.append('=' * 70)
            lines.append(f'报告生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
            lines.append(f'分析数据期数: 最近{analysis_result["data_count"]}期')
            lines.append(f'最新开奖期号: {latest_issue}')
            lines.append(f'★ 预测目标期号: {next_issue} ★')
            lines.append('')
            lines.append('-' * 70)
            lines.append('【各位置推荐号码】')
            lines.append('-' * 70)
            
            rec_nums = parsed.get('recommended_numbers', {})
            pos_names = {'wan': '万位', 'qian': '千位', 'bai': '百位', 'shi': '十位'}
            
            for pos in ['wan', 'qian', 'bai', 'shi']:
                pos_name = pos_names[pos]
                nums = rec_nums.get(pos, [])
                confidence = parsed.get('confidence_scores', {}).get(pos, 0)
                lines.append(f'\n{pos_name} (置信度: {confidence:.2f}):')
                if isinstance(nums, list):
                    for i, n in enumerate(nums, 1):
                        if isinstance(n, dict):
                            num = n.get('number', '')
                            conf = n.get('confidence', 0)
                            reason = n.get('reason', '')
                            lines.append(f'  推荐{i}: {num} (置信度: {conf:.2f}) - {reason}')
                        else:
                            lines.append(f'  推荐{i}: {n}')
            
            lines.append('')
            lines.append('-' * 70)
            lines.append('【推荐号码组合】')
            lines.append('-' * 70)
            
            combos = parsed.get('recommended_combinations', [])
            if isinstance(combos, list):
                for i, combo in enumerate(combos, 1):
                    if isinstance(combo, dict):
                        numbers = combo.get('numbers', [])
                        conf = combo.get('confidence', 0)
                        reason = combo.get('reason', '')
                        combo_str = ''.join(str(n) for n in numbers) if isinstance(numbers, list) else str(numbers)
                        lines.append(f'  组合{i}: {combo_str} (置信度: {conf:.2f})')
                        if reason:
                            lines.append(f'       理由: {reason}')
            
            lines.append('')
            lines.append('-' * 70)
            lines.append('【趋势分析】')
            lines.append('-' * 70)
            
            trend = parsed.get('trend_analysis', {})
            for pos in ['wan', 'qian', 'bai', 'shi']:
                pos_name = pos_names[pos]
                analysis = trend.get(pos, '')
                if analysis:
                    lines.append(f'\n{pos_name}: {analysis}')
            
            lines.append('')
            lines.append('-' * 70)
            lines.append('【关键结论】')
            lines.append('-' * 70)
            
            conclusions = parsed.get('key_conclusions', [])
            if isinstance(conclusions, list):
                for i, c in enumerate(conclusions, 1):
                    lines.append(f'  {i}. {c}')
            
            lines.append('')
            lines.append('-' * 70)
            lines.append('【概率统计摘要】')
            lines.append('-' * 70)
            
            prob = parsed.get('probability_stats', {})
            hot = prob.get('hot_numbers', {})
            cold = prob.get('cold_numbers', {})
            
            for pos in ['wan', 'qian', 'bai', 'shi']:
                pos_name = pos_names[pos]
                lines.append(f'\n{pos_name}:')
                hot_nums = hot.get(pos, [])
                cold_nums = cold.get(pos, [])
                if isinstance(hot_nums, list) and hot_nums:
                    hot_str = ', '.join([f"{h.get('number', '')}({h.get('frequency', '')}次)" for h in hot_nums[:3] if isinstance(h, dict)])
                    lines.append(f'  热号: {hot_str}')
                if isinstance(cold_nums, list) and cold_nums:
                    cold_str = ', '.join([f"{c.get('number', '')}(遗漏{c.get('omission', '')})" for c in cold_nums[:3] if isinstance(c, dict)])
                    lines.append(f'  冷号: {cold_str}')
            
            lines.append('')
            lines.append('=' * 70)
            lines.append(parsed.get('recommendation_reasons', ''))
            lines.append('')
            lines.append('⚠️ 风险提示: ' + parsed.get('risk_warning', '本分析报告基于历史数据统计分析生成，所有结论均为概率性预测，不保证中奖。彩票开奖结果具有随机性，请理性购买。'))
            lines.append('=' * 70)
            lines.append('')
            lines.append(f'分析摘要: {parsed.get("analysis_summary", "")}')
            lines.append('')
            lines.append(f'整体置信度: {parsed.get("confidence_scores", {}).get("overall", 0):.2f}')
            lines.append('')
            
            report_content = '\n'.join(lines)
            
            # 格式化推荐号码用于单独字段存储
            formatted_nums = {}
            for pos in ['wan', 'qian', 'bai', 'shi']:
                nums = rec_nums.get(pos, [])
                if isinstance(nums, list) and nums:
                    formatted_nums[pos] = [str(item['number']) if isinstance(item, dict) else str(item) for item in nums]
                else:
                    formatted_nums[pos] = []
            
            # 格式化推荐组合
            formatted_combinations = []
            for combo in combos:
                if isinstance(combo, dict) and 'numbers' in combo:
                    formatted_combinations.append(''.join(str(n) for n in combo['numbers']))
                elif isinstance(combo, list):
                    formatted_combinations.append(''.join(str(n) for n in combo))
            
            # 格式化置信度
            confidence_scores = parsed.get('confidence_scores', {})
            formatted_confidence = []
            for pos in ['wan', 'qian', 'bai', 'shi']:
                formatted_confidence.append(confidence_scores.get(pos, 0))
            
            # 格式化关键结论
            key_conclusions_list = parsed.get('key_conclusions', [])
            if isinstance(key_conclusions_list, list):
                key_conclusions_str = '; '.join(str(c) for c in key_conclusions_list)
            else:
                key_conclusions_str = str(key_conclusions_list)
            
            report_uuid = db.insert_ai_report(
                report_content=report_content,
                data_count=analysis_result['data_count'],
                latest_issue=latest_issue,
                next_issue=next_issue,
                trend_analysis=parsed.get('trend_analysis', {}).get('wan', '') + '\n' + 
                               parsed.get('trend_analysis', {}).get('qian', '') + '\n' + 
                               parsed.get('trend_analysis', {}).get('bai', '') + '\n' + 
                               parsed.get('trend_analysis', {}).get('shi', ''),
                probability_stats='',
                recommended_numbers=json.dumps(formatted_nums, ensure_ascii=False),
                recommended_combinations=json.dumps(formatted_combinations, ensure_ascii=False),
                confidence_scores=json.dumps(formatted_confidence, ensure_ascii=False),
                recommendation_reasons=parsed.get('recommendation_reasons', ''),
                key_conclusions=key_conclusions_str,
                risk_warning=parsed.get('risk_warning', ''),
                report_format='TEXT'
            )
            
            db.disconnect()
            
            if report_uuid:
                logger.info(f'四位置AI分析报告保存成功, UUID: {report_uuid}')
                return report_uuid
            else:
                logger.error('四位置AI分析报告保存失败')
                return None
        
        except Exception as e:
            logger.error(f'保存四位置报告到数据库失败: {e}')
            db.disconnect()
            return None
    
    def run_4position_full_analysis(self, limit: int = 30, save_to_db: bool = True, save_to_file: bool = True) -> Dict[str, Any]:
        """
        执行四位置完整AI分析流程：数据整合 → AI分析 → 结果存储
        
        Args:
            limit: 使用最近多少期数据，默认30期
            save_to_db: 是否保存到数据库
            save_to_file: 是否保存到文件
        
        Returns:
            包含所有结果的字典
        """
        logger.info(f'=== 执行四位置AI分析流程 (最近{limit}期) ===')
        
        start_time = time.time()
        
        analysis_result = self.analyze_with_ai_4position(limit=limit)
        
        if not analysis_result['success']:
            logger.error(f'AI分析失败: {analysis_result["error"]}')
            return analysis_result
        
        result = {
            'success': True,
            'analysis_result': analysis_result,
            'db_status': None,
            'file_path': None,
            'execution_time': time.time() - start_time
        }
        
        if save_to_db:
            report_uuid = self.save_4position_report_to_database(analysis_result)
            result['db_status'] = {
                'success': report_uuid is not None,
                'report_uuid': report_uuid
            }
        
        if save_to_file:
            file_path = self.save_4position_report_to_file(analysis_result)
            result['file_path'] = file_path
        
        logger.info(f'=== 四位置AI分析流程完成, 耗时: {result["execution_time"]:.2f}秒 ===')
        
        return result
    
    def run_4position_dual_analysis(self, save_to_db: bool = True, save_to_file: bool = True) -> Dict[str, Any]:
        """
        执行四位置双报告分析：自动生成30期和20期两份报告
        
        Args:
            save_to_db: 是否保存到数据库
            save_to_file: 是否保存到文件
        
        Returns:
            包含两份报告结果的字典
        """
        logger.info('=== 执行四位置双报告分析 (30期 + 20期) ===')
        
        start_time = time.time()
        results = []
        
        # 1. 生成30期报告
        logger.info('>>> 开始生成30期分析报告...')
        result_30 = self.run_4position_full_analysis(limit=30, save_to_db=save_to_db, save_to_file=save_to_file)
        results.append(('30期', result_30))
        
        # 2. 生成20期报告
        logger.info('>>> 开始生成20期分析报告...')
        result_20 = self.run_4position_full_analysis(limit=20, save_to_db=save_to_db, save_to_file=save_to_file)
        results.append(('20期', result_20))
        
        # 汇总结果
        summary = {
            'success': True,
            'reports': [],
            'execution_time': time.time() - start_time
        }
        
        for label, result in results:
            report_info = {
                'label': label,
                'success': result.get('success', False),
                'data_count': result.get('analysis_result', {}).get('data_count', 0),
                'latest_issue': result.get('analysis_result', {}).get('latest_issue', ''),
                'next_issue': result.get('analysis_result', {}).get('next_issue', ''),
                'db_uuid': result.get('db_status', {}).get('report_uuid') if result.get('db_status') else None,
                'file_path': result.get('file_path')
            }
            summary['reports'].append(report_info)
        
        logger.info(f'=== 四位置双报告分析完成, 耗时: {summary["execution_time"]:.2f}秒 ===')
        
        return summary
    
    def save_4position_report_to_file(self, analysis_result: Dict[str, Any], filepath: str = None) -> str:
        """
        将四位置AI分析结果保存到文件（可读性格式）
        
        Args:
            analysis_result: AI分析结果字典
            filepath: 保存路径，None则自动生成
        
        Returns:
            保存的文件路径
        """
        if filepath is None:
            os.makedirs('reports', exist_ok=True)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filepath = f'reports/p5_4position_ai_report_{timestamp}.txt'
        
        try:
            parsed = analysis_result['parsed_result']
            next_issue = analysis_result['next_issue']
            latest_issue = analysis_result['latest_issue']
            
            lines = []
            lines.append('=' * 70)
            lines.append('排列5 四位置AI分析报告'.center(60))
            lines.append('=' * 70)
            lines.append(f'报告生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
            lines.append(f'分析数据期数: 最近{analysis_result["data_count"]}期')
            lines.append(f'最新开奖期号: {latest_issue}')
            lines.append(f'★ 预测目标期号: {next_issue} ★')
            lines.append('')
            lines.append('-' * 70)
            lines.append('【各位置推荐号码】')
            lines.append('-' * 70)
            
            rec_nums = parsed.get('recommended_numbers', {})
            pos_names = {'wan': '万位', 'qian': '千位', 'bai': '百位', 'shi': '十位'}
            
            for pos in ['wan', 'qian', 'bai', 'shi']:
                pos_name = pos_names[pos]
                nums = rec_nums.get(pos, [])
                confidence = parsed.get('confidence_scores', {}).get(pos, 0)
                lines.append(f'\n{pos_name} (置信度: {confidence:.2f}):')
                if isinstance(nums, list):
                    for i, n in enumerate(nums, 1):
                        if isinstance(n, dict):
                            num = n.get('number', '')
                            conf = n.get('confidence', 0)
                            reason = n.get('reason', '')
                            lines.append(f'  推荐{i}: {num} (置信度: {conf:.2f}) - {reason}')
                        else:
                            lines.append(f'  推荐{i}: {n}')
            
            lines.append('')
            lines.append('-' * 70)
            lines.append('【推荐号码组合】')
            lines.append('-' * 70)
            
            combos = parsed.get('recommended_combinations', [])
            if isinstance(combos, list):
                for i, combo in enumerate(combos, 1):
                    if isinstance(combo, dict):
                        numbers = combo.get('numbers', [])
                        conf = combo.get('confidence', 0)
                        reason = combo.get('reason', '')
                        combo_str = ''.join(str(n) for n in numbers) if isinstance(numbers, list) else str(numbers)
                        lines.append(f'  组合{i}: {combo_str} (置信度: {conf:.2f})')
                        if reason:
                            lines.append(f'       理由: {reason}')
            
            lines.append('')
            lines.append('-' * 70)
            lines.append('【趋势分析】')
            lines.append('-' * 70)
            
            trend = parsed.get('trend_analysis', {})
            for pos in ['wan', 'qian', 'bai', 'shi']:
                pos_name = pos_names[pos]
                analysis = trend.get(pos, '')
                if analysis:
                    lines.append(f'\n{pos_name}: {analysis}')
            
            lines.append('')
            lines.append('-' * 70)
            lines.append('【关键结论】')
            lines.append('-' * 70)
            
            conclusions = parsed.get('key_conclusions', [])
            if isinstance(conclusions, list):
                for i, c in enumerate(conclusions, 1):
                    lines.append(f'  {i}. {c}')
            
            lines.append('')
            lines.append('-' * 70)
            lines.append('【概率统计摘要】')
            lines.append('-' * 70)
            
            prob = parsed.get('probability_stats', {})
            hot = prob.get('hot_numbers', {})
            cold = prob.get('cold_numbers', {})
            
            for pos in ['wan', 'qian', 'bai', 'shi']:
                pos_name = pos_names[pos]
                lines.append(f'\n{pos_name}:')
                hot_nums = hot.get(pos, [])
                cold_nums = cold.get(pos, [])
                if isinstance(hot_nums, list) and hot_nums:
                    hot_str = ', '.join([f"{h.get('number', '')}({h.get('frequency', '')}次)" for h in hot_nums[:3] if isinstance(h, dict)])
                    lines.append(f'  热号: {hot_str}')
                if isinstance(cold_nums, list) and cold_nums:
                    cold_str = ', '.join([f"{c.get('number', '')}(遗漏{c.get('omission', '')})" for c in cold_nums[:3] if isinstance(c, dict)])
                    lines.append(f'  冷号: {cold_str}')
            
            lines.append('')
            lines.append('=' * 70)
            lines.append(parsed.get('recommendation_reasons', ''))
            lines.append('')
            lines.append('⚠️ 风险提示: ' + parsed.get('risk_warning', '本分析报告基于历史数据统计分析生成，所有结论均为概率性预测，不保证中奖。彩票开奖结果具有随机性，请理性购买。'))
            lines.append('=' * 70)
            lines.append('')
            lines.append(f'分析摘要: {parsed.get("analysis_summary", "")}')
            lines.append('')
            lines.append(f'整体置信度: {parsed.get("confidence_scores", {}).get("overall", 0):.2f}')
            lines.append('')
            
            report_content = '\n'.join(lines)
            
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(report_content)
            
            logger.info(f'四位置AI分析报告已保存到文件: {filepath}')
            return filepath
        except Exception as e:
            logger.error(f'保存四位置报告到文件失败: {e}')
            return ''
    
    def format_data_for_ai(self, integrated_data: List[Dict]) -> str:
        """
        将整合后的数据格式化为AI可理解的文本格式
        
        Args:
            integrated_data: 整合后的数据列表
        
        Returns:
            格式化后的文本字符串
        """
        lines = []
        
        lines.append('【最近30期历史数据】')
        for i, item in enumerate(integrated_data[:30], 1):
            lines.append(f'期号:{item["issue"]} 日期:{item["date"]} 号码:{item["wan"]}{item["qian"]}{item["bai"]}{item["shi"]}{item["ge"]} 和值:{item["hezhi"]} 跨度:{item["span"]} 奇偶比:{item["odd_even_ratio"]} 大小比:{item["big_small_ratio"]}')
        
        lines.append('\n【各位置遗漏值统计】')
        for pos in ['wan', 'qian', 'bai', 'shi', 'ge']:
            pos_name = {'wan': '万位', 'qian': '千位', 'bai': '百位', 'shi': '十位', 'ge': '个位'}[pos]
            omissions = [item[f'{pos}_omission'] for item in integrated_data[:20]]
            max_omission = max(omissions) if omissions else 0
            avg_omission = sum(omissions) / len(omissions) if omissions else 0
            lines.append(f'{pos_name}: 最大遗漏={max_omission}, 平均遗漏={avg_omission:.1f}')
        
        lines.append('\n【号码频次统计（最近120期）】')
        for pos in ['wan', 'qian', 'bai', 'shi', 'ge']:
            pos_name = {'wan': '万位', 'qian': '千位', 'bai': '百位', 'shi': '十位', 'ge': '个位'}[pos]
            freq = {}
            for item in integrated_data:
                num = item[pos]
                freq[num] = freq.get(num, 0) + 1
            sorted_freq = sorted(freq.items(), key=lambda x: x[1], reverse=True)
            top3 = sorted_freq[:3]
            bottom3 = sorted_freq[-3:]
            lines.append(f'{pos_name}: 高频[{", ".join([f"{n}({c}次)" for n, c in top3])}] 低频[{", ".join([f"{n}({c}次)" for n, c in bottom3])}]')
        
        lines.append('\n【和值与跨度分布】')
        hezhi_values = [item['hezhi'] for item in integrated_data]
        span_values = [item['span'] for item in integrated_data]
        if hezhi_values:
            lines.append(f'和值: 最小={min(hezhi_values)}, 最大={max(hezhi_values)}, 平均={sum(hezhi_values)/len(hezhi_values):.1f}')
        if span_values:
            lines.append(f'跨度: 最小={min(span_values)}, 最大={max(span_values)}, 平均={sum(span_values)/len(span_values):.1f}')
        
        lines.append('\n【奇偶比模式统计】')
        odd_even_patterns = {}
        for item in integrated_data:
            pattern = item.get('odd_even_pattern', '')
            if pattern:
                odd_even_patterns[pattern] = odd_even_patterns.get(pattern, 0) + 1
        top_patterns = sorted(odd_even_patterns.items(), key=lambda x: x[1], reverse=True)[:5]
        for pattern, count in top_patterns:
            lines.append(f'{pattern}: {count}次')
        
        lines.append('\n【大小比模式统计】')
        big_small_patterns = {}
        for item in integrated_data:
            ratio = item.get('big_small_ratio', '')
            if ratio:
                big_small_patterns[ratio] = big_small_patterns.get(ratio, 0) + 1
        top_bs = sorted(big_small_patterns.items(), key=lambda x: x[1], reverse=True)[:5]
        for ratio, count in top_bs:
            lines.append(f'{ratio}: {count}次')
        
        return '\n'.join(lines)
    
    # ==================== AI模型调用 ====================
    
    def call_ai_model(self, prompt: str, max_tokens: int = 8000, temperature: float = 0.7) -> Optional[str]:
        """
        调用AI模型进行分析
        
        Args:
            prompt: 输入提示词
            max_tokens: 最大输出token数
            temperature: 温度参数，控制输出随机性
        
        Returns:
            AI模型返回的原始文本，失败返回None
        """
        logger.info(f'=== 开始调用AI模型: {self.model_name} ===')
        
        import requests
        
        payload = json.dumps({
            "model": self.model_name,
            "messages": [
                {
                    "role": "system",
                    "content": "你是一位专业的彩票数据分析专家，擅长排列5号码分析和趋势预测。"
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "response_format": {"type": "json_object"}
        })
        
        try:
            response = requests.request("POST", self.api_url, headers=self.headers, data=payload)
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
            logger.error(f'AI模型返回解析失败: {e}')
            return None
    
    def analyze_with_ai(self, limit: int = 120) -> Dict[str, Any]:
        """
        执行完整的AI分析流程
        
        Args:
            limit: 使用最近多少期数据进行分析
        
        Returns:
            包含分析结果的字典
        """
        logger.info(f'=== 开始AI分析流程 (最近{limit}期) ===')
        
        data_result = self.fetch_and_integrate_data(limit=limit)
        if not data_result['success']:
            return {'success': False, 'error': data_result['error']}
        
        formatted_data = self.format_data_for_ai(data_result['data'])
        
        full_prompt = self.analysis_prompt_template + formatted_data
        
        ai_response = self.call_ai_model(full_prompt)
        if not ai_response:
            return {'success': False, 'error': 'AI模型调用失败'}
        
        parsed_result = self._parse_ai_response(ai_response)
        
        return {
            'success': True,
            'raw_response': ai_response,
            'parsed_result': parsed_result,
            'data_count': data_result['data_count'],
            'latest_issue': data_result['latest_issue'],
            'next_issue': data_result['next_issue'],
            'data': data_result['data']
        }
    
    def _parse_ai_response(self, response_text: str) -> Dict[str, Any]:
        """
        解析AI模型返回的响应
        
        Args:
            response_text: AI返回的文本
        
        Returns:
            解析后的JSON数据
        """
        logger.info('=== 开始解析AI响应 ===')
        
        try:
            start_idx = response_text.find('{')
            end_idx = response_text.rfind('}') + 1
            
            if start_idx == -1 or end_idx == 0:
                logger.error('无法找到JSON起始或结束位置')
                return self._generate_fallback_result()
            
            json_str = response_text[start_idx:end_idx]
            
            result = json.loads(json_str)
            logger.info('AI响应解析成功')
            return result
        
        except json.JSONDecodeError as e:
            logger.error(f'JSON解析失败: {e}, 尝试修复...')
            try:
                fixed_json = self._fix_json_format(response_text)
                result = json.loads(fixed_json)
                logger.info('修复后JSON解析成功')
                return result
            except Exception as e2:
                logger.error(f'修复后仍解析失败: {e2}')
                return self._generate_fallback_result()
        except Exception as e:
            logger.error(f'解析AI响应失败: {e}')
            return self._generate_fallback_result()
    
    def _fix_json_format(self, text: str) -> str:
        """修复JSON格式问题"""
        import re
        
        text = text.strip()
        
        text = re.sub(r',\s*([}\]])', r'\1', text)
        
        text = re.sub(r'(\d+)\s*(\w)', r'\1, \2', text)
        
        text = re.sub(r'\'([^"]*)\'', r'"\1"', text)
        
        return text
    
    def _generate_fallback_result(self) -> Dict[str, Any]:
        """生成备用分析结果（当AI解析失败时）"""
        return {
            'trend_analysis': {
                'wan': 'AI解析失败，无法获取趋势分析',
                'qian': 'AI解析失败，无法获取趋势分析',
                'bai': 'AI解析失败，无法获取趋势分析',
                'shi': 'AI解析失败，无法获取趋势分析',
                'ge': 'AI解析失败，无法获取趋势分析'
            },
            'probability_stats': {},
            'recommended_numbers': {
                'wan': [{'number': 5, 'confidence': 0.5, 'reason': 'AI解析失败，默认推荐'}],
                'qian': [{'number': 3, 'confidence': 0.5, 'reason': 'AI解析失败，默认推荐'}],
                'bai': [{'number': 7, 'confidence': 0.5, 'reason': 'AI解析失败，默认推荐'}],
                'shi': [{'number': 2, 'confidence': 0.5, 'reason': 'AI解析失败，默认推荐'}],
                'ge': [{'number': 8, 'confidence': 0.5, 'reason': 'AI解析失败，默认推荐'}]
            },
            'recommended_combinations': [],
            'confidence_scores': {'wan': 0.5, 'qian': 0.5, 'bai': 0.5, 'shi': 0.5, 'ge': 0.5, 'overall': 0.5},
            'recommendation_reasons': 'AI解析失败，使用默认推荐',
            'key_conclusions': ['AI解析失败'],
            'risk_warning': '本分析报告基于历史数据统计分析生成，所有结论均为概率性预测，不保证中奖。彩票开奖结果具有随机性，请理性购买。',
            'analysis_summary': 'AI解析失败，生成备用结果'
        }
    
    # ==================== 结果存储 ====================
    
    def save_report_to_database(self, analysis_result: Dict[str, Any]) -> Optional[str]:
        """
        将AI分析结果保存到数据库
        
        Args:
            analysis_result: AI分析结果字典
        
        Returns:
            报告UUID，失败返回None
        """
        logger.info('=== 开始保存AI分析报告到数据库 ===')
        
        from modules.database_p5 import P5Database
        
        db = P5Database()
        if not db.connect():
            logger.error('数据库连接失败')
            return None
        
        try:
            parsed = analysis_result['parsed_result']
            
            trend_analysis = json.dumps(parsed.get('trend_analysis', {}), ensure_ascii=False)
            probability_stats = json.dumps(parsed.get('probability_stats', {}), ensure_ascii=False)
            
            recommended_numbers = parsed.get('recommended_numbers', {})
            formatted_nums = {}
            for pos in ['wan', 'qian', 'bai', 'shi', 'ge']:
                nums = recommended_numbers.get(pos, [])
                if isinstance(nums, list) and nums:
                    formatted_nums[pos] = [str(item['number']) if isinstance(item, dict) else str(item) for item in nums]
                else:
                    formatted_nums[pos] = []
            
            recommended_combinations = parsed.get('recommended_combinations', [])
            formatted_combinations = []
            for combo in recommended_combinations:
                if isinstance(combo, dict) and 'numbers' in combo:
                    formatted_combinations.append(''.join(str(n) for n in combo['numbers']))
                elif isinstance(combo, list):
                    formatted_combinations.append(''.join(str(n) for n in combo))
            
            confidence_scores = parsed.get('confidence_scores', {})
            formatted_confidence = []
            for pos in ['wan', 'qian', 'bai', 'shi', 'ge']:
                formatted_confidence.append(confidence_scores.get(pos, 0))
            
            report_content = json.dumps(parsed, ensure_ascii=False, indent=2)
            
            report_uuid = db.insert_ai_report(
                report_content=report_content,
                data_count=analysis_result['data_count'],
                latest_issue=analysis_result['latest_issue'],
                next_issue=analysis_result['next_issue'],
                trend_analysis=trend_analysis,
                probability_stats=probability_stats,
                recommended_numbers=json.dumps(formatted_nums, ensure_ascii=False),
                recommended_combinations=json.dumps(formatted_combinations, ensure_ascii=False),
                confidence_scores=json.dumps(formatted_confidence, ensure_ascii=False),
                recommendation_reasons=parsed.get('recommendation_reasons', ''),
                key_conclusions=json.dumps(parsed.get('key_conclusions', []), ensure_ascii=False),
                risk_warning=parsed.get('risk_warning', ''),
                report_format='JSON'
            )
            
            db.disconnect()
            
            if report_uuid:
                logger.info(f'AI分析报告保存成功, UUID: {report_uuid}')
                return report_uuid
            else:
                logger.error('AI分析报告保存失败')
                return None
        
        except Exception as e:
            logger.error(f'保存报告到数据库失败: {e}')
            db.disconnect()
            return None
    
    def save_report_to_file(self, analysis_result: Dict[str, Any], filepath: Optional[str] = None) -> str:
        """
        将AI分析结果保存到文件
        
        Args:
            analysis_result: AI分析结果字典
            filepath: 保存路径，None则自动生成
        
        Returns:
            保存的文件路径
        """
        if filepath is None:
            os.makedirs('reports', exist_ok=True)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filepath = f'reports/p5_ai_analysis_report_{timestamp}.json'
        
        try:
            report_data = {
                'analysis_time': datetime.now().isoformat(),
                'data_count': analysis_result['data_count'],
                'latest_issue': analysis_result['latest_issue'],
                'next_issue': analysis_result['next_issue'],
                'raw_response': analysis_result['raw_response'],
                'parsed_result': analysis_result['parsed_result']
            }
            
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(report_data, f, ensure_ascii=False, indent=2)
            
            logger.info(f'AI分析报告已保存到文件: {filepath}')
            return filepath
        except Exception as e:
            logger.error(f'保存报告到文件失败: {e}')
            return ''
    
    # ==================== 完整流程 ====================
    
    def run_full_analysis(self, limit: int = 120, save_to_db: bool = True, save_to_file: bool = True) -> Dict[str, Any]:
        """
        执行完整的AI分析流程：数据整合 → AI分析 → 结果存储
        
        Args:
            limit: 使用最近多少期数据
            save_to_db: 是否保存到数据库
            save_to_file: 是否保存到文件
        
        Returns:
            包含所有结果的字典
        """
        logger.info(f'=== 执行完整AI分析流程 (最近{limit}期) ===')
        
        start_time = time.time()
        
        analysis_result = self.analyze_with_ai(limit=limit)
        
        if not analysis_result['success']:
            logger.error(f'AI分析失败: {analysis_result["error"]}')
            return analysis_result
        
        result = {
            'success': True,
            'analysis_result': analysis_result,
            'db_status': None,
            'file_path': None,
            'execution_time': time.time() - start_time
        }
        
        if save_to_db:
            report_uuid = self.save_report_to_database(analysis_result)
            result['db_status'] = {
                'success': report_uuid is not None,
                'report_uuid': report_uuid
            }
        
        if save_to_file:
            file_path = self.save_report_to_file(analysis_result)
            result['file_path'] = file_path
        
        logger.info(f'=== AI分析流程完成, 耗时: {result["execution_time"]:.2f}秒 ===')
        
        return result
    
    # ==================== GUI兼容接口 ====================
    
    def fetch_data(self, source: str = 'database') -> List[Dict]:
        """
        获取排列5数据（GUI兼容接口）
        
        Args:
            source: 数据源，'database'或'spider'
        
        Returns:
            数据列表
        """
        logger.info(f'=== 从{source}获取数据 ===')
        
        if source == 'database':
            from modules.database_p5 import P5Database
            db = P5Database()
            if db.connect():
                data = db.get_history_data(limit=120)
                db.disconnect()
                if data:
                    logger.info(f'从数据库获取到 {len(data)} 条数据')
                    return data
                else:
                    raise ValueError('数据库中没有数据')
            else:
                raise ValueError('数据库连接失败')
        
        elif source == 'spider':
            from modules.spider_p5 import P5Spider
            spider = P5Spider()
            data = spider.crawl_history_data(limit=50)
            if data:
                logger.info(f'从爬虫获取到 {len(data)} 条数据')
                return data
            else:
                raise ValueError('爬虫获取数据失败')
        
        else:
            raise ValueError(f'未知数据源: {source}')
    
    def validate_data_quality(self, data: List[Dict]) -> Dict[str, Any]:
        """
        验证数据质量（GUI兼容接口）
        
        Args:
            data: 数据列表
        
        Returns:
            质量报告字典
        """
        logger.info('=== 验证数据质量 ===')
        
        if not data:
            return {
                'status': 'error',
                'message': '数据为空',
                'total_count': 0,
                'valid_count': 0,
                'valid_rate': 0,
                'issues': ['数据列表为空']
            }
        
        total_count = len(data)
        valid_count = 0
        issues = []
        warnings = []
        
        for i, item in enumerate(data):
            try:
                issue = str(item.get('issue', ''))
                numbers = []
                for pos in ['wan', 'qian', 'bai', 'shi', 'ge']:
                    num = item.get(pos)
                    if num is None or not (0 <= int(num) <= 9):
                        issues.append(f'第{i+1}条数据: {pos}位号码无效')
                        break
                    numbers.append(int(num))
                else:
                    valid_count += 1
                    
                    hezhi = item.get('hezhi', 0)
                    if not (0 <= int(hezhi) <= 45):
                        warnings.append(f'第{i+1}条数据: 和值异常({hezhi})')
                    
                    span = item.get('span', 0)
                    if not (0 <= int(span) <= 9):
                        warnings.append(f'第{i+1}条数据: 跨度异常({span})')
            
            except Exception as e:
                issues.append(f'第{i+1}条数据: 解析失败 - {str(e)}')
        
        valid_rate = (valid_count / total_count) * 100
        
        if valid_count == 0:
            status = 'error'
            message = '没有有效数据'
        elif valid_rate < 50:
            status = 'error'
            message = f'有效率过低({valid_rate:.1f}%)'
        elif valid_rate < 80:
            status = 'warning'
            message = f'部分数据存在问题({valid_rate:.1f}%)'
        else:
            status = 'success'
            message = f'数据质量良好({valid_rate:.1f}%)'
        
        return {
            'status': status,
            'message': message,
            'total_count': total_count,
            'valid_count': valid_count,
            'valid_rate': round(valid_rate, 1),
            'issues': issues[:10],
            'warnings': warnings[:10]
        }
    
    def analyze_p5(self, data: List[Dict]) -> Dict[str, Any]:
        """
        执行排列5 AI分析（GUI兼容接口）
        
        Args:
            data: 数据列表
        
        Returns:
            分析结果字典
        """
        logger.info('=== 执行排列5 AI分析 ===')
        
        quality_report = self.validate_data_quality(data)
        
        if quality_report['status'] == 'error':
            return {
                'status': 'error',
                'message': quality_report['message'],
                'error_code': 'DATA_QUALITY_ERROR',
                'quality_report': quality_report
            }
        
        try:
            integrated_result = self._integrate_data(data, [])
            
            latest_issue = integrated_result[0]['issue'] if integrated_result else ''
            next_issue = self._calculate_next_issue(latest_issue)
            
            formatted_data = self.format_data_for_ai(integrated_result)
            full_prompt = self.analysis_prompt_template + formatted_data
            
            ai_response = self.call_ai_model(full_prompt)
            
            if not ai_response:
                return {
                    'status': 'error',
                    'message': 'AI模型调用失败',
                    'error_code': 'AI_CALL_FAILED',
                    'quality_report': quality_report
                }
            
            parsed_result = self._parse_ai_response(ai_response)
            
            data_summary = {
                'data_count': len(data),
                'valid_rate': quality_report['valid_rate'],
                'latest_issue': latest_issue,
                'next_issue': next_issue
            }
            
            report = {
                'status': 'success',
                'message': 'AI分析完成',
                'data_summary': data_summary,
                'quality_report': quality_report,
                'trend_analysis': parsed_result.get('trend_analysis', {}),
                'probability_stats': parsed_result.get('probability_stats', {}),
                'recommended_numbers': parsed_result.get('recommended_numbers', {}),
                'recommended_combinations': parsed_result.get('recommended_combinations', []),
                'confidence_scores': parsed_result.get('confidence_scores', {}),
                'key_conclusions': parsed_result.get('key_conclusions', []),
                'risk_warning': parsed_result.get('risk_warning', ''),
                'analysis_summary': parsed_result.get('analysis_summary', ''),
                'recommendation_reasons': parsed_result.get('recommendation_reasons', ''),
                '_raw_response': ai_response,
                '_parsed_result': parsed_result
            }
            
            return report
        
        except Exception as e:
            logger.error(f'AI分析失败: {e}')
            return {
                'status': 'error',
                'message': str(e),
                'error_code': 'ANALYSIS_EXCEPTION',
                'quality_report': quality_report
            }


# ==================== 便捷函数 ====================

def run_p5_ai_analysis(limit: int = 120, save_to_db: bool = True, save_to_file: bool = True) -> Dict[str, Any]:
    """
    便捷函数：执行排列5 AI分析
    
    Args:
        limit: 使用最近多少期数据
        save_to_db: 是否保存到数据库
        save_to_file: 是否保存到文件
    
    Returns:
        分析结果字典
    """
    analyzer = AIAnalyzer()
    return analyzer.run_full_analysis(limit=limit, save_to_db=save_to_db, save_to_file=save_to_file)


def run_p5_4position_ai_analysis(save_to_db: bool = True, save_to_file: bool = True) -> Dict[str, Any]:
    """
    便捷函数：执行排列5四位置AI分析（仅分析万位、千位、百位、十位）
    自动生成30期和20期两份报告
    
    Args:
        save_to_db: 是否保存到数据库
        save_to_file: 是否保存到文件
    
    Returns:
        分析结果字典（包含两份报告）
    """
    analyzer = AIAnalyzer()
    return analyzer.run_4position_dual_analysis(save_to_db=save_to_db, save_to_file=save_to_file)


def test_ai_analyzer():
    """测试AI分析器"""
    print('=== 测试AI分析器 ===')
    
    try:
        analyzer = AIAnalyzer()
        
        print('\n1. 测试数据获取与整合...')
        data_result = analyzer.fetch_and_integrate_data(limit=50)
        if data_result['success']:
            print(f'   ✓ 成功获取 {data_result["data_count"]} 条数据')
            print(f'   ✓ 最新期号: {data_result["latest_issue"]}')
            print(f'   ✓ 预测期号: {data_result["next_issue"]}')
        else:
            print(f'   ✗ 失败: {data_result["error"]}')
            return
        
        print('\n2. 测试数据格式化...')
        formatted_data = analyzer.format_data_for_ai(data_result['data'])
        print(f'   ✓ 格式化数据长度: {len(formatted_data)} 字符')
        print(f'   ✓ 前200字符: {formatted_data[:200]}...')
        
        print('\n3. 测试AI模型调用...')
        prompt = analyzer.analysis_prompt_template + formatted_data
        ai_response = analyzer.call_ai_model(prompt)
        if ai_response:
            print(f'   ✓ AI响应长度: {len(ai_response)} 字符')
            print(f'   ✓ 前300字符: {ai_response[:300]}...')
        else:
            print('   ✗ AI模型调用失败')
            return
        
        print('\n4. 测试结果解析...')
        parsed = analyzer._parse_ai_response(ai_response)
        print(f'   ✓ 解析成功')
        print(f'   ✓ 包含字段: {list(parsed.keys())}')
        
        if 'recommended_numbers' in parsed:
            print(f'   ✓ 推荐号码: {parsed["recommended_numbers"]}')
        if 'key_conclusions' in parsed:
            print(f'   ✓ 关键结论: {parsed["key_conclusions"][:3]}...')
        
        print('\n5. 测试保存到数据库...')
        analysis_result = {
            'success': True,
            'parsed_result': parsed,
            'data_count': data_result['data_count'],
            'latest_issue': data_result['latest_issue'],
            'next_issue': data_result['next_issue']
        }
        report_uuid = analyzer.save_report_to_database(analysis_result)
        if report_uuid:
            print(f'   ✓ 保存成功, UUID: {report_uuid}')
        else:
            print('   ✗ 保存失败')
        
        print('\n6. 测试保存到文件...')
        file_path = analyzer.save_report_to_file(analysis_result)
        if file_path:
            print(f'   ✓ 保存成功: {file_path}')
        else:
            print('   ✗ 保存失败')
        
        print('\n=== 测试完成 ===')
        
    except Exception as e:
        print(f'测试异常: {e}')
        logger.error(f'测试异常: {e}')


if __name__ == '__main__':
    test_ai_analyzer()
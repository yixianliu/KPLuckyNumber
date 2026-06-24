"""
排列5预测数据提取模块

专门用于从文章内容中精准提取预测相关数据，
过滤冗余内容，建立质量检查机制。

核心功能：
1. HTML清洗 - 去除所有HTML标签和样式，保留纯文本
2. 内容理解 - AI模型生成对文章内容的理解和结构化分析
3. 预测提取 - 从清洗后的内容中精准识别预测数据
4. 质量检查 - 验证提取结果的完整性和准确性
5. Redis存储 - 完整保存文章内容、理解结果和预测数据
"""

import logging
import os
import json
import re
import time
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple

import requests

os.makedirs('logs', exist_ok=True)

logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler = logging.FileHandler('logs/prediction_extractor.log', encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


class PredictionExtractor:
    """
    排列5预测数据提取器
    
    完整流程：
    1. HTML清洗 - 去除标签、样式、脚本，保留纯文本
    2. 内容理解 - AI模型生成对文章内容的深度理解
    3. 预测提取 - 从理解后的内容中提取预测号码
    4. 质量检查 - 验证数据完整性
    5. Redis存储 - 完整保存所有数据
    """
    
    def __init__(self):
        self._init_ai_config()
        self.position_names = ['万位', '千位', '百位', '十位', '个位']
        self.position_keys = ['wan', 'qian', 'bai', 'shi', 'ge']
        
        # 质量检查阈值
        self.quality_thresholds = {
            'min_positions': 3,  # 最少需要提取的位置数
            'min_numbers_per_position': 1,  # 每个位置最少号码数
            'max_numbers_per_position': 5,  # 每个位置最多号码数
            'min_confidence': 0.3,  # 最低置信度
            'max_confidence': 1.0,  # 最高置信度
            'required_fields': ['issue', 'prediction', 'extract_time']  # 必需字段
        }
    
    def _init_ai_config(self):
        """初始化AI模型配置"""
        try:
            from config import QIANYAN_API_CONFIG
            self.api_config = QIANYAN_API_CONFIG
            self.api_url = self.api_config.get('api_url', "https://qianfan.baidubce.com/v2/chat/completions")
            self.api_key = self.api_config.get('api_key', '')
            self.model_name = self.api_config.get('model_name', 'deepseek-v3.1-250821')
            self.ai_available = bool(self.api_key)
            
            if self.ai_available:
                self.headers = {
                    'Content-Type': 'application/json',
                    'Authorization': f'Bearer {self.api_key}'
                }
                logger.info(f'AI配置加载成功: {self.model_name}')
            else:
                logger.warning('未配置API密钥')
        except ImportError:
            self.api_config = {}
            self.ai_available = False
            logger.warning('config.py不存在，AI功能不可用')
    
    def _build_extraction_prompt(self, article_content: str, article_title: str) -> str:
        """
        构建预测数据提取专用Prompt
        
        Args:
            article_content: 文章内容
            article_title: 文章标题
            
        Returns:
            Prompt文本
        """
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # 分段构建Prompt，避免f-string中方括号被误解析
        prompt_parts = []
        
        prompt_parts.append("""你是一个专业的彩票预测数据提取专家。请从以下文章中精准提取预测相关数据，并严格过滤所有冗余内容。

【文章标题】""")
        prompt_parts.append(article_title)
        
        prompt_parts.append("""
【文章内容】""")
        prompt_parts.append(article_content)
        
        prompt_parts.append("""
【提取任务】
请执行以下步骤：
1. 精准识别文章中的预测号码（包括定位推荐、单选推荐、复式推荐等）
2. 提取每个位置的预测号码及其置信度（如有说明）
3. 过滤并去除所有非预测性内容：
   - 背景介绍、历史回顾
   - 广告信息、推广内容
   - 无关评论、格式性文本
   - 专家个人介绍、网站信息
   - 与预测无关的统计数据描述
4. 仅保留纯净的预测数据部分

【输出格式要求】
请严格按照以下JSON格式输出，不要输出任何其他内容：

```json
{
    "issue": "从标题或内容提取的期号（如2026165）",
    "expert_name": "专家名称（如有）",
    "extract_time": """)
        prompt_parts.append(current_time)
        prompt_parts.append("""",
    "prediction_type": "预测类型（定位/单选/复式/综合）",
    
    "prediction": {
        "wan": {
            "numbers": [],
            "confidence": [],
            "source_text": "原文中该位置预测的描述"
        },
        "qian": {
            "numbers": [],
            "confidence": [],
            "source_text": "原文中该位置预测的描述"
        },
        "bai": {
            "numbers": [],
            "confidence": [],
            "source_text": "原文中该位置预测的描述"
        },
        "shi": {
            "numbers": [],
            "confidence": [],
            "source_text": "原文中该位置预测的描述"
        },
        "ge": {
            "numbers": [],
            "confidence": [],
            "source_text": "原文中该位置预测的描述"
        }
    },
    
    "single_selection": {
        "recommended": [],
        "source_text": "原文中单选推荐的描述"
    },
    
    "combined_recommendation": {
        "combinations": [],
        "source_text": "原文中复式推荐的描述"
    },
    
    "key_prediction_points": [],
    
    "data_quality": {
        "has_prediction": true,
        "prediction_count": 0,
        "positions_covered": 0,
        "confidence_level": "中"
    },
    
    "filtered_content_summary": "过滤后保留的核心预测内容摘要（不超过100字）",
    
    "extraction_notes": "提取过程中的特殊说明（如有）"
}
```

【重要规则】
1. numbers数组中填写预测号码（0-9之间的整数），最多5个
2. confidence数组中填写对应置信度（0.3-1.0之间的小数），默认0.5
3. 如果文章中没有明确的预测号码，has_prediction设为false
4. source_text必须引用原文中的具体描述，便于验证
5. 如果某个位置没有预测数据，该位置numbers和confidence保持空数组
6. 期号格式必须为6-8位数字
7. 不要编造或推测任何预测数据，仅提取原文明确给出的内容

请开始提取，仅输出JSON格式数据：""")
        
        return ''.join(prompt_parts)
    
    def _build_content_understanding_prompt(self, clean_text: str, article_title: str) -> str:
        """
        构建内容理解Prompt - AI模型生成对文章的深度理解
        
        Args:
            clean_text: 清洗后的纯文本
            article_title: 文章标题
            
        Returns:
            Prompt文本
        """
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        prompt_parts = []
        
        prompt_parts.append("""你是一位专业的排列5彩票预测文章分析师。请仔细阅读以下文章内容，生成对文章内容的深度理解和结构化分析。

【文章标题】""")
        prompt_parts.append(article_title)
        
        prompt_parts.append("""
【文章内容（已去除HTML格式，纯文本）】""")
        prompt_parts.append(clean_text)
        
        prompt_parts.append(f"""
【分析任务】
请对文章进行深度理解和结构化分析，包括：

1. **文章类型判断**
   - 是预测分析类文章还是其他类型
   - 作者/专家身份
   - 发布时间和相关性

2. **核心内容理解**
   - 文章主要讨论什么
   - 预测的依据是什么
   - 提到了哪些关键指标或方法

3. **预测号码识别**
   - 明确识别出所有预测号码
   - 区分定位预测和综合推荐
   - 标注号码来源（原文直接给出 vs 分析推断）

4. **关键信息提取**
   - 遗漏值、出现频率等统计数据
   - 趋势分析描述
   - 专家选号思路

5. **内容质量评估**
   - 文章信息量（高/中/低）
   - 预测数据完整性
   - 可信度评估

【输出格式要求】
请严格按照以下JSON格式输出：

```json
{{
    "article_title": "文章标题",
    "analyze_time": """)
        prompt_parts.append(current_time)
        prompt_parts.append("""",
    "article_type": "文章类型（预测分析/数据统计/综合资讯/其他）",
    "expert_name": "专家名称（如有）",
    
    "content_summary": {{
        "main_topic": "文章主要讨论内容",
        "prediction_basis": "预测依据",
        "key_methods": ["关键方法1", "关键方法2"],
        "key_indicators": ["关键指标1", "关键指标2"]
    }},
    
    "prediction_understanding": {{
        "has_prediction": true/false,
        "prediction_type": "预测类型（定位/单选/复式/综合/无）",
        "prediction_positions": {{
            "万位": "万位预测描述",
            "千位": "千位预测描述",
            "百位": "百位预测描述",
            "十位": "十位预测描述",
            "个位": "个位预测描述"
        }},
        "single_recommendation": "单选推荐描述",
        "combined_recommendation": "复式/组合推荐描述"
    }},
    
    "key_information": [
        "关键信息1",
        "关键信息2",
        "关键信息3"
    ],
    
    "quality_assessment": {{
        "information_density": "高/中/低",
        "prediction_completeness": "完整/部分/无",
        "credibility": "高/中/低",
        "overall_score": 0-100的评分
    }},
    
    "raw_text_length": 原始纯文本长度,
    "paragraphs_count": 段落数量
}}
```

【重要规则】
1. 内容理解要基于原文，不要添加原文没有的信息
2. prediction_understanding中的描述要引用原文的具体表述
3. 如果没有预测内容，has_prediction设为false
4. quality_assessment要客观评估文章质量
5. raw_text_length和paragraphs_count填写实际数值

请开始分析，仅输出JSON格式数据：""")
        
        return ''.join(prompt_parts)
    
    def clean_html_to_plain_text(self, html_content: str) -> str:
        """
        清洗HTML内容为纯文本
        
        Args:
            html_content: 原始HTML内容
            
        Returns:
            清洗后的纯文本
        """
        try:
            from modules.html_cleaner import HTMLTextCleaner
            cleaner = HTMLTextCleaner()
            return cleaner.clean_html(html_content)
        except ImportError:
            # 回退方案：使用简单正则清洗
            logger.warning('HTMLCleaner导入失败，使用简单清洗方案')
            return self._simple_html_clean(html_content)
    
    def _simple_html_clean(self, html_content: str) -> str:
        """
        简单HTML清洗（回退方案）
        
        Args:
            html_content: 原始HTML
            
        Returns:
            清洗后的文本
        """
        import html as html_module
        
        if not html_content:
            return ""
        
        # 移除script和style
        text = re.sub(r'<script[^>]*>.*?</script>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        
        # 移除所有HTML标签
        text = re.sub(r'<[^>]+>', '', text)
        
        # 转换HTML实体
        text = html_module.unescape(text)
        
        # 规范化空白
        text = re.sub(r'\s+', ' ', text)
        text = text.strip()
        
        return text
    
    def generate_content_understanding(self, article_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        AI模型生成内容理解
        
        Args:
            article_data: 包含HTML内容的文章数据
            
        Returns:
            内容理解结果
        """
        logger.info('开始生成内容理解...')
        
        # 获取原始HTML内容
        raw_content = article_data.get('content', '')
        title = article_data.get('title', article_data.get('article_data', {}).get('title', ''))
        
        if not raw_content:
            logger.warning('文章内容为空，无法生成理解')
            return None
        
        # 步骤1：清洗HTML为纯文本
        logger.info('步骤1：清洗HTML为纯文本...')
        clean_text = self.clean_html_to_plain_text(raw_content)
        
        if not clean_text or len(clean_text) < 50:
            logger.warning(f'清洗后文本过短({len(clean_text)}字符)，可能解析失败')
            return None
        
        logger.info(f'HTML清洗完成：原始{len(raw_content)}字符 → 纯文本{len(clean_text)}字符')
        
        # 步骤2：构建内容理解Prompt
        logger.info('步骤2：构建内容理解Prompt...')
        prompt = self._build_content_understanding_prompt(clean_text, title)
        
        # 步骤3：调用AI模型
        logger.info('步骤3：调用AI模型生成内容理解...')
        ai_response = self.call_ai_model(prompt)
        
        if not ai_response:
            logger.error('AI模型调用失败')
            return None
        
        # 步骤4：解析响应
        logger.info('步骤4：解析AI响应...')
        understanding = self.parse_ai_response(ai_response)
        
        if not understanding:
            logger.error('AI响应解析失败')
            return None
        
        # 添加纯文本信息
        understanding['clean_text_length'] = len(clean_text)
        understanding['clean_text_preview'] = clean_text[:500] + '...' if len(clean_text) > 500 else clean_text
        understanding['paragraphs'] = [p for p in clean_text.split('\n') if p.strip()]
        
        logger.info(f'内容理解生成成功：类型={understanding.get("article_type")}, 质量评分={understanding.get("quality_assessment", {}).get("overall_score")}')
        
        return understanding
    
    def call_ai_model(self, prompt: str) -> Optional[str]:
        """
        调用AI模型
        
        Args:
            prompt: Prompt文本
            
        Returns:
            AI响应文本
        """
        if not self.ai_available:
            logger.error('AI模型不可用')
            return None
        
        try:
            payload = {
                'model': self.model_name,
                'messages': [
                    {'role': 'user', 'content': prompt}
                ],
                'temperature': 0.1,  # 低温度确保稳定输出
                'max_tokens': 2000
            }
            
            logger.info(f'调用AI模型: {self.model_name}')
            
            response = requests.post(
                self.api_url,
                headers=self.headers,
                json=payload,
                timeout=60
            )
            
            if response.status_code == 200:
                result = response.json()
                content = result.get('choices', [{}])[0].get('message', {}).get('content', '')
                logger.info(f'AI响应成功，长度: {len(content)}')
                return content
            else:
                logger.error(f'AI调用失败: {response.status_code} - {response.text}')
                return None
                
        except Exception as e:
            logger.error(f'AI调用异常: {e}', exc_info=True)
            return None
    
    def parse_ai_response(self, response_text: str) -> Optional[Dict[str, Any]]:
        """
        解析AI响应，提取JSON数据
        
        Args:
            response_text: AI响应文本
            
        Returns:
            解析后的字典数据
        """
        try:
            # 尝试直接解析JSON
            if response_text.strip().startswith('{'):
                return json.loads(response_text.strip())
            
            # 从Markdown代码块中提取JSON
            json_pattern = r'```json\s*([\s\S]*?)\s*```'
            match = re.search(json_pattern, response_text)
            if match:
                json_str = match.group(1)
                return json.loads(json_str)
            
            # 从普通代码块中提取
            code_pattern = r'```\s*([\s\S]*?)\s*```'
            match = re.search(code_pattern, response_text)
            if match:
                json_str = match.group(1)
                if json_str.strip().startswith('{'):
                    return json.loads(json_str.strip())
            
            # 將整个响应作为JSON尝试解析
            return json.loads(response_text)
            
        except json.JSONDecodeError as e:
            logger.error(f'JSON解析失败: {e}')
            logger.debug(f'响应内容: {response_text[:500]}')
            return None
    
    def validate_prediction_data(self, data: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """
        验证预测数据的完整性和准确性
        
        Args:
            data: 提取的预测数据
            
        Returns:
            (是否通过验证, 错误信息列表)
        """
        errors = []
        
        # 检查必需字段
        for field in self.quality_thresholds['required_fields']:
            if field not in data:
                errors.append(f'缺少必需字段: {field}')
        
        # 检查期号格式
        if 'issue' in data:
            issue = data['issue']
            if not re.match(r'^\d{6,8}$', str(issue)):
                errors.append(f'期号格式不正确: {issue}')
        
        # 检查预测数据
        prediction = data.get('prediction', {})
        if prediction:
            positions_covered = 0
            total_numbers = 0
            
            for pos_key in self.position_keys:
                pos_data = prediction.get(pos_key)
                if pos_data and isinstance(pos_data, dict):
                    numbers = pos_data.get('numbers', [])
                    confidence = pos_data.get('confidence', [])
                    
                    if numbers:
                        positions_covered += 1
                        total_numbers += len(numbers)
                        
                        # 验证号码范围
                        for num in numbers:
                            if not isinstance(num, int) or num < 0 or num > 9:
                                errors.append(f'{pos_key}位置号码无效: {num}')
                        
                        # 验证置信度
                        for conf in confidence:
                            if not isinstance(conf, (int, float)):
                                errors.append(f'{pos_key}位置置信度类型无效: {conf}')
                            elif conf < self.quality_thresholds['min_confidence'] or conf > self.quality_thresholds['max_confidence']:
                                errors.append(f'{pos_key}位置置信度范围无效: {conf}')
                        
                        # 验证号码数量
                        if len(numbers) < self.quality_thresholds['min_numbers_per_position']:
                            errors.append(f'{pos_key}位置号码数量不足: {len(numbers)}')
                        elif len(numbers) > self.quality_thresholds['max_numbers_per_position']:
                            errors.append(f'{pos_key}位置号码数量过多: {len(numbers)}')
            
            # 检查覆盖位置数
            if positions_covered < self.quality_thresholds['min_positions']:
                errors.append(f'预测位置覆盖不足: {positions_covered} < {self.quality_thresholds["min_positions"]}')
            
            # 更新数据质量统计
            if 'data_quality' in data:
                data['data_quality']['prediction_count'] = total_numbers
                data['data_quality']['positions_covered'] = positions_covered
        
        # 检查是否有预测数据
        has_prediction = data.get('data_quality', {}).get('has_prediction', False)
        if not has_prediction and not prediction:
            errors.append('文章未包含有效预测数据')
        
        is_valid = len(errors) == 0
        
        if is_valid:
            logger.info(f'数据验证通过: 期号={data.get("issue")}, 预测位置={data.get("data_quality", {}).get("positions_covered", 0)}')
        else:
            logger.warning(f'数据验证失败: {errors}')
        
        return is_valid, errors
    
    def clean_prediction_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        清洗预测数据，去除无效内容
        
        Args:
            data: 原始提取数据
            
        Returns:
            清洗后的数据
        """
        cleaned = {
            'issue': data.get('issue', ''),
            'expert_name': data.get('expert_name', ''),
            'extract_time': data.get('extract_time', datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
            'prediction_type': data.get('prediction_type', ''),
            'prediction': {},
            'single_selection': data.get('single_selection', {}),
            'combined_recommendation': data.get('combined_recommendation', {}),
            'key_prediction_points': data.get('key_prediction_points', []),
            'data_quality': data.get('data_quality', {}),
            'filtered_content_summary': data.get('filtered_content_summary', ''),
            'extraction_notes': data.get('extraction_notes', ''),
            'validation_status': 'pending'
        }
        
        # 清洗每个位置的预测数据
        prediction = data.get('prediction', {})
        for pos_key in self.position_keys:
            pos_data = prediction.get(pos_key)
            if pos_data and isinstance(pos_data, dict):
                numbers = pos_data.get('numbers', [])
                confidence = pos_data.get('confidence', [])
                source_text = pos_data.get('source_text', '')
                
                # 过滤无效号码
                valid_numbers = [n for n in numbers if isinstance(n, int) and 0 <= n <= 9]
                
                # 补充置信度
                if len(confidence) < len(valid_numbers):
                    confidence = confidence + [0.5] * (len(valid_numbers) - len(confidence))
                elif len(confidence) > len(valid_numbers):
                    confidence = confidence[:len(valid_numbers)]
                
                # 限制置信度范围
                confidence = [max(0.3, min(1.0, c)) for c in confidence]
                
                if valid_numbers:
                    cleaned['prediction'][pos_key] = {
                        'numbers': valid_numbers,
                        'confidence': confidence,
                        'source_text': source_text[:200]  # 限制长度
                    }
        
        return cleaned
    
    def extract_prediction_from_article(self, article_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        从文章中提取预测数据（完整流程）
        
        Args:
            article_data: 文章数据（包含title, content等）
            
        Returns:
            提取结果
        """
        result = {
            'success': False,
            'article_id': article_data.get('article_id', ''),
            'issue': '',
            'prediction_data': None,
            'validation_errors': [],
            'quality_score': 0,
            'extract_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        try:
            title = article_data.get('title', article_data.get('article_data', {}).get('title', ''))
            content = article_data.get('content', article_data.get('article_data', {}).get('content', ''))
            
            if not content:
                result['validation_errors'] = ['文章内容为空']
                return result
            
            logger.info(f'开始提取预测数据: {title[:50]}...')
            
            # 步骤1：构建Prompt
            prompt = self._build_extraction_prompt(content, title)
            
            # 步骤2：调用AI模型
            ai_response = self.call_ai_model(prompt)
            
            if not ai_response:
                result['validation_errors'] = ['AI模型调用失败']
                return result
            
            # 步骤3：解析响应
            parsed_data = self.parse_ai_response(ai_response)
            
            if not parsed_data:
                result['validation_errors'] = ['AI响应解析失败']
                return result
            
            # 步骤4：清洗数据
            cleaned_data = self.clean_prediction_data(parsed_data)
            
            # 步骤5：验证数据
            is_valid, errors = self.validate_prediction_data(cleaned_data)
            
            cleaned_data['validation_status'] = 'passed' if is_valid else 'failed'
            cleaned_data['validation_errors'] = errors
            
            # 计算质量评分
            quality_score = self._calculate_quality_score(cleaned_data)
            cleaned_data['quality_score'] = quality_score
            
            result['success'] = True
            result['issue'] = cleaned_data.get('issue', '')
            result['prediction_data'] = cleaned_data
            result['validation_errors'] = errors
            result['quality_score'] = quality_score
            
            logger.info(f'预测数据提取完成: 期号={result["issue"]}, 质量评分={quality_score}, 验证={is_valid}')
            
        except Exception as e:
            logger.error(f'预测数据提取失败: {e}', exc_info=True)
            result['validation_errors'] = [str(e)]
        
        return result
    
    def _calculate_quality_score(self, data: Dict[str, Any]) -> float:
        """
        计算预测数据质量评分
        
        Args:
            data: 预测数据
            
        Returns:
            质量评分（0-1）
        """
        score = 0.0
        
        # 期号有效性（20%）
        if re.match(r'^\d{6,8}$', str(data.get('issue', ''))):
            score += 0.2
        
        # 预测覆盖度（40%）
        prediction = data.get('prediction', {})
        positions_covered = len([k for k in self.position_keys if prediction.get(k)])
        score += (positions_covered / 5) * 0.4
        
        # 置信度合理性（20%）
        if prediction:
            valid_conf_count = 0
            total_conf_count = 0
            for pos_key in self.position_keys:
                pos_data = prediction.get(pos_key)
                if pos_data:
                    confs = pos_data.get('confidence', [])
                    total_conf_count += len(confs)
                    valid_conf_count += len([c for c in confs if 0.3 <= c <= 1.0])
            
            if total_conf_count > 0:
                score += (valid_conf_count / total_conf_count) * 0.2
        
        # 数据完整性（20%）
        required_fields = ['issue', 'prediction', 'extract_time', 'data_quality']
        fields_present = len([f for f in required_fields if data.get(f)])
        score += (fields_present / len(required_fields)) * 0.2
        
        return round(score, 2)
    
    def batch_extract_predictions(self, articles: List[Dict[str, Any]], 
                                   max_articles: int = 100) -> Dict[str, Any]:
        """
        批量提取多篇文章的预测数据
        
        Args:
            articles: 文章列表
            max_articles: 最大处理数量
            
        Returns:
            批量处理结果
        """
        result = {
            'total_articles': len(articles[:max_articles]),
            'processed_articles': 0,
            'successful_extracts': 0,
            'failed_extracts': 0,
            'high_quality_count': 0,
            'predictions': [],
            'errors': []
        }
        
        articles_to_process = articles[:max_articles]
        
        logger.info(f'开始批量提取预测数据，共{len(articles_to_process)}篇文章')
        
        for i, article in enumerate(articles_to_process, 1):
            try:
                logger.info(f'处理文章 {i}/{len(articles_to_process)}')
                
                extract_result = self.extract_prediction_from_article(article)
                
                result['processed_articles'] += 1
                
                if extract_result['success']:
                    result['successful_extracts'] += 1
                    
                    if extract_result['quality_score'] >= 0.7:
                        result['high_quality_count'] += 1
                    
                    result['predictions'].append({
                        'article_id': extract_result['article_id'],
                        'issue': extract_result['issue'],
                        'quality_score': extract_result['quality_score'],
                        'validation_status': extract_result['prediction_data'].get('validation_status'),
                        'prediction_summary': self._summarize_prediction(extract_result['prediction_data'])
                    })
                else:
                    result['failed_extracts'] += 1
                    result['errors'].append({
                        'article_id': extract_result['article_id'],
                        'errors': extract_result['validation_errors']
                    })
                
                # 控制请求频率
                time.sleep(1)
                
            except Exception as e:
                result['failed_extracts'] += 1
                result['errors'].append({
                    'article_id': article.get('article_id', f'article_{i}'),
                    'errors': [str(e)]
                })
                logger.error(f'处理文章{i}时出错: {e}')
        
        logger.info(f'批量提取完成: 成功{result["successful_extracts"]}, 失败{result["failed_extracts"]}, 高质量{result["high_quality_count"]}')
        
        return result
    
    def _summarize_prediction(self, prediction_data: Dict[str, Any]) -> str:
        """
        生成预测数据摘要
        
        Args:
            prediction_data: 预测数据
            
        Returns:
            摘要文本
        """
        if not prediction_data:
            return '无预测数据'
        
        prediction = prediction_data.get('prediction', {})
        summary_parts = []
        
        for pos_key, pos_name in zip(self.position_keys, self.position_names):
            pos_data = prediction.get(pos_key)
            if pos_data:
                numbers = pos_data.get('numbers', [])
                if numbers:
                    summary_parts.append(f'{pos_name}:{numbers}')
        
        return ' | '.join(summary_parts) if summary_parts else '无有效预测'


def run_prediction_extraction(article_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    便捷函数：执行单篇文章预测数据提取
    
    Args:
        article_data: 文章数据
        
    Returns:
        提取结果
    """
    extractor = PredictionExtractor()
    return extractor.extract_prediction_from_article(article_data)


if __name__ == '__main__':
    print('=' * 80)
    print('预测数据提取模块测试')
    print('=' * 80)
    
    # 测试示例
    test_article = {
        'title': '2026165期[大肚子]排列五复式+精选推荐',
        'content': '''
        本期推荐如下：
        
        万位关注：0、2、4
        千位重点：6、7
        百位推荐：5、8
        十位看好：4、6
        个位主推：5、7
        
        单选一注：0 6 5 4 5
        复式推荐：024/67/58/46/57
        
        置信度分析：
        万位0号置信度较高，约70%
        千位6号置信度中等，约60%
        
        本期趋势分析仅供参考，不保证中奖。
        '''
    }
    
    extractor = PredictionExtractor()
    result = extractor.extract_prediction_from_article(test_article)
    
    if result['success']:
        print('\n提取成功！')
        print(f'期号: {result["issue"]}')
        print(f'质量评分: {result["quality_score"]}')
        print(f'验证状态: {result["prediction_data"].get("validation_status")}')
        print('\n预测数据:')
        print(json.dumps(result['prediction_data'], indent=2, ensure_ascii=False))
    else:
        print('\n提取失败！')
        print(f'错误: {result["validation_errors"]}')
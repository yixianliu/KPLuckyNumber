"""
网络大神分析提取模块

从网络资源获取专家分析结果，并整合AI模型初步分析数据
"""

import logging
import os
import re
import json
import time
from datetime import datetime
from typing import Dict, List, Any, Optional

import requests
from bs4 import BeautifulSoup

os.makedirs('logs', exist_ok=True)

logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler = logging.FileHandler('logs/expert_analyzer.log', encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


class ExpertAnalyzer:
    """
    专家分析数据提取器

    从多个网络资源获取专家分析结果
    """

    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                          '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'
        }
        self.request_interval = 3
        self.max_retries = 2

        self.expert_sources = [
            {
                'name': '亿点牛专家推荐',
                'url': 'https://www.ydniu.com/info/pl5/zjtj/',
                'parser': 'parse_ydniu_expert'
            },
            {
                'name': '排列5专家预测',
                'url': 'https://www.ydniu.com/info/pl5/forecast/',
                'parser': 'parse_general_expert'
            }
        ]

    def _make_request(self, url: str) -> Optional[str]:
        """
        发起HTTP请求

        Args:
            url: 请求URL

        Returns:
            响应内容，失败返回None
        """
        for attempt in range(self.max_retries):
            try:
                response = requests.get(url, headers=self.headers, timeout=30)
                response.raise_for_status()
                
                time.sleep(self.request_interval)
                return response.text
                
            except requests.exceptions.RequestException as e:
                logger.error(f'请求{url}失败: {e}')
                if attempt < self.max_retries - 1:
                    time.sleep(2)
        
        return None

    def parse_ydniu_expert(self, html: str) -> List[Dict[str, Any]]:
        """
        解析亿点牛专家推荐数据

        Args:
            html: 网页HTML内容

        Returns:
            专家分析列表
        """
        experts = []
        
        try:
            soup = BeautifulSoup(html, 'html.parser')
            
            expert_boxes = soup.find_all('div', class_=re.compile(r'expert-box|expert-item|zjtj-item'))
            
            for box in expert_boxes:
                try:
                    name_tag = box.find('span', class_=re.compile(r'name|expert-name|author')) or \
                               box.find('div', class_=re.compile(r'name|expert-name|author'))
                    expert_name = name_tag.get_text(strip=True) if name_tag else '未知专家'
                    
                    number_tags = box.find_all('span', class_=re.compile(r'ball|number|num|digit'))
                    numbers = []
                    for tag in number_tags:
                        text = tag.get_text(strip=True)
                        if re.match(r'^\d+$', text):
                            numbers.append(text)
                    
                    rate_tag = box.find('span', class_=re.compile(r'rate|准确率|hit-rate'))
                    accuracy_rate = rate_tag.get_text(strip=True) if rate_tag else '未知'
                    
                    analysis_tag = box.find('p', class_=re.compile(r'analysis|content|desc')) or \
                                   box.find('div', class_=re.compile(r'analysis|content|desc'))
                    analysis = analysis_tag.get_text(strip=True) if analysis_tag else ''
                    
                    if numbers:
                        experts.append({
                            'expert_name': expert_name,
                            'forecast_numbers': numbers,
                            'accuracy_rate': accuracy_rate,
                            'analysis': analysis,
                            'source': '亿点牛',
                            'fetch_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        })
                        
                except Exception as e:
                    logger.warning(f'解析专家数据失败: {e}')
                    continue
            
            if not experts:
                tables = soup.find_all('table')
                for table in tables:
                    try:
                        rows = table.find_all('tr')
                        for row in rows[1:]:
                            cols = row.find_all('td')
                            if len(cols) >= 2:
                                expert_name = cols[0].get_text(strip=True) if cols[0] else '未知专家'
                                forecast = cols[1].get_text(strip=True) if cols[1] else ''
                                numbers = re.findall(r'\d{1,5}', forecast)
                                
                                if numbers:
                                    experts.append({
                                        'expert_name': expert_name,
                                        'forecast_numbers': numbers,
                                        'accuracy_rate': '未知',
                                        'analysis': '',
                                        'source': '亿点牛',
                                        'fetch_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                                    })
                    except Exception as e:
                        continue
            
            logger.info(f'从亿点牛解析到 {len(experts)} 条专家分析')
            
        except Exception as e:
            logger.error(f'解析亿点牛专家数据失败: {e}')
        
        return experts

    def parse_general_expert(self, html: str) -> List[Dict[str, Any]]:
        """
        通用专家分析解析器

        Args:
            html: 网页HTML内容

        Returns:
            专家分析列表
        """
        experts = []
        
        try:
            soup = BeautifulSoup(html, 'html.parser')
            
            content_divs = soup.find_all('div', class_=re.compile(r'content|article|post'))
            
            for div in content_divs:
                try:
                    text = div.get_text(strip=True)
                    
                    patterns = [
                        r'(\d{5,8})期\s*推荐\s*(\d{5})',
                        r'预测\s*(\d{5})',
                        r'精选\s*(\d{5})',
                        r'杀号\s*[：:]\s*([\d,，]+)',
                        r'胆码\s*[：:]\s*([\d,，]+)'
                    ]
                    
                    for pattern in patterns:
                        matches = re.findall(pattern, text)
                        for match in matches:
                            if isinstance(match, tuple):
                                issue = match[0]
                                numbers = match[1]
                            else:
                                issue = ''
                                numbers = match
                            
                            if len(numbers) <= 5:
                                experts.append({
                                    'expert_name': '网络分析',
                                    'forecast_numbers': [numbers],
                                    'accuracy_rate': '未知',
                                    'analysis': text[:200],
                                    'source': '通用来源',
                                    'fetch_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                    'pattern_type': pattern[:10]
                                })
                                
                except Exception as e:
                    continue
            
            logger.info(f'从通用来源解析到 {len(experts)} 条分析')
            
        except Exception as e:
            logger.error(f'解析通用专家数据失败: {e}')
        
        return experts

    def fetch_all_expert_data(self) -> List[Dict[str, Any]]:
        """
        获取所有专家分析数据

        Returns:
            专家分析列表
        """
        logger.info('=' * 60)
        logger.info('开始获取网络专家分析数据')
        logger.info('=' * 60)
        
        all_experts = []
        
        for source in self.expert_sources:
            logger.info(f'正在获取: {source["name"]}')
            
            html = self._make_request(source['url'])
            if not html:
                logger.warning(f'无法获取 {source["name"]} 的数据')
                continue
            
            parser_method = getattr(self, source['parser'], None)
            if parser_method:
                experts = parser_method(html)
                all_experts.extend(experts)
        
        logger.info('=' * 60)
        logger.info(f'专家分析数据获取完成，共 {len(all_experts)} 条')
        logger.info('=' * 60)
        
        return all_experts

    def extract_ai_preliminary_analysis(self, ai_report: Dict[str, Any]) -> Dict[str, Any]:
        """
        提取AI模型初步分析数据

        Args:
            ai_report: AI分析报告

        Returns:
            提取的初步分析数据
        """
        preliminary = {
            'source': 'AI模型初步分析',
            'model_version': ai_report.get('probability_stats', {}).get('model_version', '未知'),
            'analysis_time': ai_report.get('report_date', ''),
            'recommended_numbers': ai_report.get('recommended_numbers', {}),
            'confidence_scores': ai_report.get('confidence_scores', {}),
            'trend_analysis': ai_report.get('trend_analysis', ''),
            'key_conclusions': ai_report.get('key_conclusions', ''),
            'summary': ai_report.get('report_content', '')[:500]
        }
        
        return preliminary

    def aggregate_expert_opinions(self, experts: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        聚合专家意见，统计各位置号码出现频次

        Args:
            experts: 专家分析列表

        Returns:
            聚合结果
        """
        position_counts = {
            'wan': {},
            'qian': {},
            'bai': {},
            'shi': {},
            'ge': {}
        }
        
        for expert in experts:
            numbers = expert.get('forecast_numbers', [])
            for num_str in numbers:
                if len(num_str) == 5:
                    for i, pos in enumerate(['wan', 'qian', 'bai', 'shi', 'ge']):
                        digit = num_str[i]
                        position_counts[pos][digit] = position_counts[pos].get(digit, 0) + 1
        
        aggregated = {}
        for pos, counts in position_counts.items():
            sorted_digits = sorted(counts.items(), key=lambda x: x[1], reverse=True)
            aggregated[pos] = {
                'top_digits': [d[0] for d in sorted_digits[:3]],
                'frequency': dict(sorted_digits),
                'total_experts': len(experts)
            }
        
        return aggregated

    def get_combined_expert_analysis(self) -> Dict[str, Any]:
        """
        获取综合专家分析结果

        Returns:
            综合分析结果
        """
        experts = self.fetch_all_expert_data()
        
        result = {
            'fetch_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'expert_count': len(experts),
            'raw_experts': experts,
            'aggregated': self.aggregate_expert_opinions(experts)
        }
        
        return result


if __name__ == '__main__':
    analyzer = ExpertAnalyzer()
    result = analyzer.get_combined_expert_analysis()
    print(f'专家数量: {result["expert_count"]}')
    print(f'聚合结果: {json.dumps(result["aggregated"], ensure_ascii=False, indent=2)}')
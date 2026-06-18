"""
排列5增强版数据爬虫模块

负责从指定网站爬取历史开奖数据和走势图数据
支持自动重试、请求间隔控制、异常处理等功能
增强版：正确解析走势图中的遗漏值数据
"""

import requests
from bs4 import BeautifulSoup
import logging
import random
import time
import os
from datetime import datetime

os.makedirs('logs', exist_ok=True)

logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler = logging.FileHandler('logs/spider_p5_enhanced.log', encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


class P5SpiderEnhanced:
    """
    排列5增强版数据爬虫类
    
    负责从指定网站爬取历史开奖数据和走势图数据
    增强版特性：
    1. 正确解析走势图表格结构，提取遗漏值
    2. 支持多种走势图格式解析
    3. 更 robust 的数据提取逻辑
    """
    
    def __init__(self):
        """初始化爬虫配置"""
        self.base_url = 'https://www.55128.cn/kjh/tcp5-history-120.htm'
        self.trend_url = 'https://www.55128.cn/zs/3_32.htm'
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Referer': 'https://www.55128.cn/',
            'Connection': 'keep-alive',
            'Cache-Control': 'max-age=0'
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        self.session.mount('https://', requests.adapters.HTTPAdapter(max_retries=3))
    
    def _get_page(self, url, max_retries=3):
        """
        获取网页内容
        
        Args:
            url: 目标网页URL
            max_retries: 最大重试次数
        
        Returns:
            网页HTML内容，失败返回None
        """
        for attempt in range(max_retries):
            try:
                # 随机延迟，避免请求过于频繁
                delay = random.uniform(2, 5)
                time.sleep(delay)
                
                response = self.session.get(url, timeout=30)
                response.encoding = 'utf-8'
                
                if response.status_code == 200:
                    logger.debug(f'成功获取页面: {url}')
                    return response.text
                else:
                    logger.warning(f'请求失败，状态码: {response.status_code}, 尝试: {attempt + 1}/{max_retries}')
                    
            except requests.exceptions.RequestException as e:
                logger.error(f'请求异常: {e}, 尝试: {attempt + 1}/{max_retries}')
                if attempt < max_retries - 1:
                    # 指数退避重试
                    backoff_delay = random.uniform(3, 6) * (attempt + 1)
                    time.sleep(backoff_delay)
        
        logger.error(f'多次请求失败，已达到最大重试次数: {url}')
        return None
    
    def _parse_history_page(self, html):
        """
        解析历史开奖数据页面
        
        Args:
            html: 网页HTML内容
        
        Returns:
            解析后的开奖数据列表
        """
        data = []
        try:
            soup = BeautifulSoup(html, 'html.parser')
            
            # 尝试多种表格定位方式
            table = soup.find('table', class_='table table-bordered table-striped')
            
            if not table:
                table = soup.find('table', {'id': 'kjhTable'})
            
            if not table:
                table = soup.find('table')
            
            if not table:
                logger.warning('未找到数据表格')
                return data
            
            tbody = table.find('tbody')
            if not tbody:
                # 如果没有tbody，直接从table获取行
                rows = table.find_all('tr')
            else:
                rows = tbody.find_all('tr')
            
            for row in rows:
                try:
                    cells = row.find_all('td')
                    if len(cells) >= 3:
                        draw_date = cells[0].get_text(strip=True)
                        issue = cells[1].get_text(strip=True)
                        
                        # 提取号码 - 支持多种HTML结构
                        numbers_cell = cells[2]
                        numbers = []
                        
                        # 尝试通过span标签提取
                        number_spans = numbers_cell.find_all('span', class_=lambda x: x and ('ball' in x.lower() or 'number' in x.lower()))
                        if not number_spans:
                            number_spans = numbers_cell.find_all('span')
                        
                        if number_spans:
                            numbers = [span.get_text(strip=True) for span in number_spans if span.get_text(strip=True).isdigit()]
                        else:
                            # 如果没有span标签，直接提取文本
                            text_content = numbers_cell.get_text(strip=True)
                            # 按空格分割号码
                            numbers = text_content.split()
                            numbers = [n for n in numbers if n.isdigit()]
                        
                        # 确保提取到5个号码
                        if len(numbers) != 5:
                            logger.warning(f'期号 {issue} 号码数量异常: {len(numbers)}, 内容: {numbers}')
                            continue
                        
                        # 提取扩展字段
                        hezhi_feature = cells[3].get_text(strip=True) if len(cells) > 3 else ''
                        odd_even_ratio = cells[4].get_text(strip=True) if len(cells) > 4 else ''
                        odd_even_pattern = cells[5].get_text(strip=True) if len(cells) > 5 else ''
                        span = cells[6].get_text(strip=True) if len(cells) > 6 else ''
                        
                        # 计算和值
                        numbers_int = [int(n) for n in numbers]
                        hezhi = sum(numbers_int)
                        
                        # 计算跨度
                        span_calc = max(numbers_int) - min(numbers_int)
                        
                        item = {
                            'issue': issue,
                            'date': draw_date,
                            'numbers': numbers_int,
                            'hezhi': hezhi,
                            'hezhi_feature': hezhi_feature,
                            'odd_even_ratio': odd_even_ratio,
                            'odd_even_pattern': odd_even_pattern,
                            'span': span_calc,
                            'span_original': span
                        }
                        data.append(item)
                        logger.debug(f'解析数据: {issue} - {numbers_int}')
                        
                except Exception as e:
                    logger.error(f'解析行数据失败: {e}')
            
            logger.info(f'成功解析 {len(data)} 条历史数据')
            
        except Exception as e:
            logger.error(f'解析页面失败: {e}')
        
        return data
    
    def _parse_trend_page_enhanced(self, html):
        """
        增强版走势图解析 - 正确提取遗漏值和开奖号码
        
        走势图表格结构（每行61个单元格）：
        - 索引0: 期号
        - 索引1: 分隔列
        - 索引2-11: 万位（0-9，其中一个是开奖号ball-yred，其他是遗漏值miss-yred）
        - 索引12: 分隔列
        - 索引13-22: 千位（0-9）
        - 索引23: 分隔列
        - 索引24-33: 百位（0-9）
        - 索引34: 分隔列
        - 索引35-44: 十位（0-9）
        - 索引45: 分隔列
        - 索引46-55: 个位（0-9）
        - 索引56: 分隔列
        - 索引57: 和值
        - 索引58: 奇偶比
        - 索引59: 大小比
        - 索引60: 质合比
        
        Args:
            html: 网页HTML内容
        
        Returns:
            解析后的走势数据列表，包含遗漏值信息
        """
        data = []
        try:
            soup = BeautifulSoup(html, 'html.parser')
            
            # 查找包含走势数据的表格（通常是第二个大表格）
            tables = soup.find_all('table')
            
            for table in tables:
                try:
                    rows = table.find_all('tr')
                    
                    # 跳过表头行，查找数据行
                    for row in rows:
                        try:
                            cells = row.find_all('td')
                            if len(cells) < 50:  # 走势图应该有至少50个单元格
                                continue
                            
                            # 提取期号
                            issue = cells[0].get_text(strip=True)
                            if not issue or not issue.isdigit() or len(issue) < 6:
                                continue
                            
                            # 定义位置映射
                            positions = [
                                {'name': 'wan', 'cn_name': '万位', 'start': 2, 'end': 12},
                                {'name': 'qian', 'cn_name': '千位', 'start': 13, 'end': 23},
                                {'name': 'bai', 'cn_name': '百位', 'start': 24, 'end': 34},
                                {'name': 'shi', 'cn_name': '十位', 'start': 35, 'end': 45},
                                {'name': 'ge', 'cn_name': '个位', 'start': 46, 'end': 56}
                            ]
                            
                            # 提取每个位置的数据
                            numbers = []
                            omissions = {}  # 遗漏值数据
                            
                            for pos_idx, pos_info in enumerate(positions):
                                pos_cells = cells[pos_info['start']:pos_info['end']]
                                if len(pos_cells) < 10:
                                    continue
                                
                                pos_omissions = {}
                                drawn_number = None
                                
                                for num in range(10):
                                    if num < len(pos_cells):
                                        cell = pos_cells[num]
                                        text = cell.get_text(strip=True)
                                        class_name = cell.get('class', [])
                                        class_str = ' '.join(class_name) if isinstance(class_name, list) else str(class_name)
                                        
                                        if 'ball' in class_str.lower() or 'yred' in class_str.lower() or 'gblue' in class_str.lower():
                                            # 这是开奖号码
                                            drawn_number = num
                                            pos_omissions[num] = 0  # 当前遗漏为0
                                        else:
                                            # 这是遗漏值
                                            try:
                                                omission_val = int(text) if text else 0
                                                pos_omissions[num] = omission_val
                                            except ValueError:
                                                pos_omissions[num] = 0
                                
                                if drawn_number is not None:
                                    numbers.append(drawn_number)
                                
                                omissions[pos_info['name']] = pos_omissions
                            
                            # 确保提取到5个号码
                            if len(numbers) != 5:
                                logger.warning(f'期号 {issue} 走势数据号码数量异常: {len(numbers)}')
                                continue
                            
                            # 提取和值、奇偶比、大小比、质合比
                            hezhi = cells[57].get_text(strip=True) if len(cells) > 57 else ''
                            odd_even_ratio = cells[58].get_text(strip=True) if len(cells) > 58 else ''
                            big_small_ratio = cells[59].get_text(strip=True) if len(cells) > 59 else ''
                            prime_composite_ratio = cells[60].get_text(strip=True) if len(cells) > 60 else ''
                            
                            item = {
                                'issue': issue,
                                'numbers': numbers,
                                'trend': {
                                    'wan': numbers[0],
                                    'qian': numbers[1],
                                    'bai': numbers[2],
                                    'shi': numbers[3],
                                    'ge': numbers[4]
                                },
                                'omissions': omissions,  # 各位置各号码的当前遗漏值
                                'hezhi': hezhi,
                                'odd_even_ratio': odd_even_ratio,
                                'big_small_ratio': big_small_ratio,
                                'prime_composite_ratio': prime_composite_ratio
                            }
                            data.append(item)
                            
                        except Exception as e:
                            logger.debug(f'解析走势行数据失败: {e}')
                            continue
                            
                except Exception as e:
                    logger.debug(f'解析走势表格失败: {e}')
                    continue
            
            # 去重处理
            seen = set()
            unique_data = []
            for item in data:
                if item['issue'] not in seen:
                    seen.add(item['issue'])
                    unique_data.append(item)
            
            data = unique_data
            logger.info(f'成功解析 {len(data)} 条走势数据（含遗漏值）')
            
        except Exception as e:
            logger.error(f'解析走势图页面失败: {e}')
        
        return data
    
    def crawl_history_data(self):
        """
        爬取历史开奖数据
        
        Returns:
            历史开奖数据列表
        """
        logger.info('开始爬取排列5历史开奖数据')
        logger.info(f'正在请求: {self.base_url}')
        
        html = self._get_page(self.base_url)
        if html:
            data = self._parse_history_page(html)
            logger.info(f'爬取完成，共获取 {len(data)} 条历史数据')
            return data
        else:
            logger.warning('未获取到历史数据')
            return []
    
    def crawl_trend_data(self, record=120):
        """
        爬取走势图数据（增强版）
        
        Args:
            record: 获取的记录数量
        
        Returns:
            走势数据列表，包含遗漏值
        """
        url = f'{self.trend_url}?record={record}'
        logger.info(f'开始爬取排列5走势图数据（增强版）')
        logger.info(f'正在请求: {url}')
        
        html = self._get_page(url)
        if html:
            data = self._parse_trend_page_enhanced(html)
            logger.info(f'爬取完成，共获取 {len(data)} 条走势数据')
            return data
        else:
            logger.warning('未获取到走势图数据')
            return []
    
    def crawl_all_data(self, trend_record=120):
        """
        爬取所有数据（历史数据+走势图数据）
        
        Args:
            trend_record: 走势图记录数量
        
        Returns:
            包含历史数据和走势数据的字典
        """
        logger.info('开始爬取所有数据')
        
        history_data = self.crawl_history_data()
        trend_data = self.crawl_trend_data(record=trend_record)
        
        return {
            'history_data': history_data,
            'trend_data': trend_data
        }
    
    def crawl_incremental_data(self, last_issue=None):
        """
        增量爬取最新数据
        
        Args:
            last_issue: 已有的最新期号
        
        Returns:
            新增的数据列表
        """
        logger.info(f'开始增量爬取，已有最新期号: {last_issue}')
        
        all_data = self.crawl_history_data()
        
        if not last_issue:
            return all_data
        
        # 筛选出比last_issue更新的数据
        new_data = []
        for item in all_data:
            if item['issue'] > last_issue:
                new_data.append(item)
        
        logger.info(f'增量爬取完成，新增 {len(new_data)} 条数据')
        return new_data


def test_spider():
    """测试爬虫功能"""
    spider = P5SpiderEnhanced()
    
    # 测试历史数据爬取
    print('=== 测试排列5历史数据爬取 ===')
    history_data = spider.crawl_history_data()
    print(f'获取到 {len(history_data)} 条历史数据')
    if history_data:
        print('前5条数据:')
        for item in history_data[:5]:
            print(f'期号: {item["issue"]}, 日期: {item["date"]}, 号码: {item["numbers"]}')
            print(f'  和值: {item["hezhi"]}, 奇偶比: {item["odd_even_ratio"]}, 跨度: {item["span"]}')
    
    # 测试走势图数据爬取（增强版）
    print('\n=== 测试排列5走势图数据爬取（增强版）===')
    trend_data = spider.crawl_trend_data(record=50)
    print(f'获取到 {len(trend_data)} 条走势图数据')
    if trend_data:
        print(f'第一条走势图数据:')
        print(f'期号: {trend_data[0]["issue"]}')
        print(f'号码: {trend_data[0]["numbers"]}')
        print(f'遗漏值:')
        for pos_name, pos_omissions in trend_data[0]['omissions'].items():
            print(f'  {pos_name}: {dict(list(pos_omissions.items())[:5])}...')
    
    return history_data, trend_data


if __name__ == '__main__':
    test_spider()

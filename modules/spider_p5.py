"""
排列5数据爬虫模块

负责从指定网站爬取排列5历史开奖数据和走势图数据
支持自动重试、请求间隔控制、异常处理等功能
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
    file_handler = logging.FileHandler('logs/spider_p5.log', encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


class P5Spider:
    """
    排列5数据爬虫类
    
    负责从指定网站爬取历史开奖数据和走势图数据
    支持自动重试、请求间隔控制等功能
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
    
    def _parse_trend_page(self, html):
        """
        解析走势图页面
        
        Args:
            html: 网页HTML内容
        
        Returns:
            解析后的走势数据列表
        """
        data = []
        try:
            soup = BeautifulSoup(html, 'html.parser')
            
            # 查找包含走势数据的表格
            tables = soup.find_all('table')
            
            for table in tables:
                try:
                    tbody = table.find('tbody')
                    if tbody:
                        rows = tbody.find_all('tr')
                    else:
                        rows = table.find_all('tr')
                    
                    for row in rows:
                        try:
                            cells = row.find_all('td')
                            if len(cells) >= 2:
                                # 尝试提取期号和走势数据
                                first_cell = cells[0].get_text(strip=True)
                                # 期号通常为6-8位数字
                                if first_cell.isdigit() and 6 <= len(first_cell) <= 8:
                                    issue = first_cell
                                    trend_values = []
                                    
                                    # 提取5个位置的号码
                                    # 排列5: 万位、千位、百位、十位、个位
                                    positions = ['万位', '千位', '百位', '十位', '个位']
                                    position_data = {}
                                    
                                    # 解析每个位置的遗漏值
                                    # 表格结构：期号 | 万位(0-9) | 千位(0-9) | 百位(0-9) | 十位(0-9) | 个位(0-9) | 和值 | 奇偶比 | 大小比 | 质合比
                                    
                                    # 提取万位号码（第2-11个单元格，对应0-9）
                                    wan_position = self._extract_position_number(cells[1:12] if len(cells) > 11 else cells[1:min(12, len(cells))])
                                    # 提取千位号码
                                    qian_position = self._extract_position_number(cells[12:23] if len(cells) > 22 else cells[12:min(23, len(cells))]) if len(cells) > 12 else None
                                    # 提取百位号码
                                    bai_position = self._extract_position_number(cells[23:34] if len(cells) > 33 else cells[23:min(34, len(cells))]) if len(cells) > 23 else None
                                    # 提取十位号码
                                    shi_position = self._extract_position_number(cells[34:45] if len(cells) > 44 else cells[34:min(45, len(cells))]) if len(cells) > 34 else None
                                    # 提取个位号码
                                    ge_position = self._extract_position_number(cells[45:56] if len(cells) > 55 else cells[45:min(56, len(cells))]) if len(cells) > 45 else None
                                    
                                    # 提取和值、奇偶比、大小比、质合比
                                    hezhi = cells[-4].get_text(strip=True) if len(cells) > 4 else ''
                                    odd_even_ratio = cells[-3].get_text(strip=True) if len(cells) > 3 else ''
                                    big_small_ratio = cells[-2].get_text(strip=True) if len(cells) > 2 else ''
                                    prime_composite_ratio = cells[-1].get_text(strip=True) if len(cells) > 1 else ''
                                    
                                    # 构建号码列表
                                    numbers = []
                                    for pos_num in [wan_position, qian_position, bai_position, shi_position, ge_position]:
                                        if pos_num is not None:
                                            numbers.append(pos_num)
                                    
                                    if len(numbers) == 5:
                                        item = {
                                            'issue': issue,
                                            'numbers': numbers,
                                            'trend': {
                                                'wan': wan_position,
                                                'qian': qian_position,
                                                'bai': bai_position,
                                                'shi': shi_position,
                                                'ge': ge_position
                                            },
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
            logger.info(f'成功解析 {len(data)} 条走势数据')
            
        except Exception as e:
            logger.error(f'解析走势图页面失败: {e}')
        
        return data
    
    def _extract_position_number(self, cells):
        """
        从走势图单元格中提取该位置的号码
        
        Args:
            cells: 该位置对应的10个单元格（0-9）
        
        Returns:
            该位置的开奖号码
        """
        try:
            for idx, cell in enumerate(cells):
                text = cell.get_text(strip=True)
                # 如果单元格内容是数字且不是遗漏值（遗漏值通常较大）
                # 开奖号码通常会有特殊样式或为空/小数字
                class_attr = cell.get('class', [])
                
                # 检查是否有选中样式
                if any('selected' in str(c).lower() or 'active' in str(c).lower() for c in class_attr):
                    return idx
                
                # 检查文本内容
                if text and text.isdigit():
                    val = int(text)
                    # 如果是0-9之间的数字，可能是开奖号码
                    if 0 <= val <= 9:
                        return val
                
                # 检查是否有特殊标记（如背景色、粗体等）
                style = cell.get('style', '')
                if 'background' in style or 'font-weight' in style:
                    return idx
            
            # 如果无法确定，返回None
            return None
            
        except Exception as e:
            logger.debug(f'提取位置号码失败: {e}')
            return None
    
    def _parse_trend_page_simple(self, html):
        """
        简化版走势图解析 - 直接从页面文本提取
        
        Args:
            html: 网页HTML内容
        
        Returns:
            解析后的走势数据列表
        """
        data = []
        try:
            soup = BeautifulSoup(html, 'html.parser')
            
            # 查找所有表格行
            tables = soup.find_all('table')
            
            for table in tables:
                rows = table.find_all('tr')
                
                for row in rows:
                    try:
                        # 获取行内所有文本
                        text = row.get_text(separator='|', strip=True)
                        parts = text.split('|')
                        
                        # 查找期号（7位数字）
                        issue = None
                        for part in parts:
                            part = part.strip()
                            if part.isdigit() and len(part) == 7:
                                issue = part
                                break
                        
                        if not issue:
                            continue
                        
                        # 提取号码 - 查找连续的5个数字
                        numbers = []
                        for part in parts:
                            part = part.strip()
                            if part.isdigit() and len(part) == 1:
                                numbers.append(int(part))
                                if len(numbers) == 5:
                                    break
                        
                        if len(numbers) == 5:
                            # 计算和值
                            hezhi = sum(numbers)
                            
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
                                'hezhi': str(hezhi)
                            }
                            data.append(item)
                    
                    except Exception as e:
                        continue
            
            # 去重
            seen = set()
            unique_data = []
            for item in data:
                if item['issue'] not in seen:
                    seen.add(item['issue'])
                    unique_data.append(item)
            
            logger.info(f'简化解析成功获取 {len(unique_data)} 条走势数据')
            return unique_data
            
        except Exception as e:
            logger.error(f'简化解析走势图失败: {e}')
            return []
    
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
        爬取走势图数据
        
        Args:
            record: 获取的记录数量
        
        Returns:
            走势数据列表
        """
        url = f'{self.trend_url}?record={record}'
        logger.info(f'开始爬取排列5走势图数据')
        logger.info(f'正在请求: {url}')
        
        html = self._get_page(url)
        if html:
            # 先尝试标准解析
            data = self._parse_trend_page(html)
            
            # 如果标准解析失败，尝试简化解析
            if not data:
                logger.info('标准解析未获取到数据，尝试简化解析')
                data = self._parse_trend_page_simple(html)
            
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
    spider = P5Spider()
    
    # 测试历史数据爬取
    print('=== 测试排列5历史数据爬取 ===')
    history_data = spider.crawl_history_data()
    print(f'获取到 {len(history_data)} 条历史数据')
    if history_data:
        print('前5条数据:')
        for item in history_data[:5]:
            print(f'期号: {item["issue"]}, 日期: {item["date"]}, 号码: {item["numbers"]}')
            print(f'  和值: {item["hezhi"]}, 奇偶比: {item["odd_even_ratio"]}, 跨度: {item["span"]}')
    
    # 测试走势图数据爬取
    print('\n=== 测试排列5走势图数据爬取 ===')
    trend_data = spider.crawl_trend_data(record=120)
    print(f'获取到 {len(trend_data)} 条走势图数据')
    if trend_data:
        print(f'第一条走势图数据:')
        print(f'期号: {trend_data[0]["issue"]}')
        print(f'号码: {trend_data[0]["numbers"]}')
        print(f'走势数据: {trend_data[0]["trend"]}')
    
    return history_data, trend_data


if __name__ == '__main__':
    test_spider()

"""
七星彩增强版数据爬虫模块

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
    file_handler = logging.FileHandler('logs/spider_enhanced.log', encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


class QXCSpiderEnhanced:
    """
    七星彩增强版数据爬虫类
    
    负责从指定网站爬取历史开奖数据和走势图数据
    增强版特性：
    1. 正确解析走势图表格结构，提取遗漏值
    2. 支持多种走势图格式解析
    3. 更 robust 的数据提取逻辑
    """
    
    def __init__(self):
        """初始化爬虫配置"""
        self.base_url = 'https://www.55128.cn/kjh/tcqxc-history-120.htm'
        self.trend_url = 'https://www.55128.cn/zs/19_156.htm'
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
                        
                        # 确保提取到7个号码
                        if len(numbers) != 7:
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
        
        七星彩走势图表格结构：
        - 7个位置（第1位到第7位，第7位是特别号0-14）
        - 每个位置有对应的0-9（或0-14）号码列
        - 开奖号用特殊样式标记，其他单元格显示遗漏值
        
        Args:
            html: 网页HTML内容
        
        Returns:
            解析后的走势数据列表，包含遗漏值信息
        """
        data = []
        try:
            soup = BeautifulSoup(html, 'html.parser')
            
            # 查找包含走势数据的表格
            tables = soup.find_all('table')
            
            for table in tables:
                try:
                    rows = table.find_all('tr')
                    
                    # 跳过表头行，查找数据行
                    for row in rows:
                        try:
                            cells = row.find_all('td')
                            if len(cells) < 50:  # 走势图应该有较多单元格
                                continue
                            
                            # 提取期号
                            issue = cells[0].get_text(strip=True)
                            if not issue or not issue.isdigit() or len(issue) < 6:
                                continue
                            
                            # 七星彩有7个位置，前6位是0-9，第7位是特别号0-14
                            # 需要根据实际HTML结构调整位置映射
                            # 这里使用动态检测方式
                            
                            numbers = []
                            omissions = {}
                            
                            # 尝试从class名检测位置结构
                            # 查找所有带ball或miss类的单元格
                            pos_idx = 0
                            current_pos = 0
                            pos_omissions = {}
                            
                            for cell_idx, cell in enumerate(cells[1:], 1):  # 跳过期号列
                                class_name = cell.get('class', [])
                                class_str = ' '.join(class_name) if isinstance(class_name, list) else str(class_name)
                                text = cell.get_text(strip=True)
                                
                                # 跳过分隔列
                                if 'split' in class_str.lower():
                                    if pos_omissions:
                                        # 保存当前位置的数据
                                        pos_name = f'pos{current_pos + 1}'
                                        omissions[pos_name] = pos_omissions.copy()
                                        pos_omissions = {}
                                        current_pos += 1
                                    continue
                                
                                # 检测是否为开奖号码
                                if 'ball' in class_str.lower() or 'yred' in class_str.lower() or 'gblue' in class_str.lower():
                                    # 这是开奖号码，从文本中提取
                                    try:
                                        drawn_num = int(text)
                                        numbers.append(drawn_num)
                                        pos_omissions[drawn_num] = 0
                                    except ValueError:
                                        pass
                                elif text.isdigit():
                                    # 这是遗漏值，但需要知道对应哪个号码
                                    # 通过单元格在位置中的索引来推断
                                    try:
                                        omission_val = int(text)
                                        # 号码推断：在当前位置中，按顺序分配
                                        num_in_pos = len(pos_omissions)
                                        pos_omissions[num_in_pos] = omission_val
                                    except ValueError:
                                        pass
                            
                            # 保存最后一个位置的数据
                            if pos_omissions and current_pos < 7:
                                pos_name = f'pos{current_pos + 1}'
                                omissions[pos_name] = pos_omissions.copy()
                            
                            # 确保提取到7个号码
                            if len(numbers) != 7:
                                # 尝试备用解析方式
                                numbers, omissions = self._parse_trend_page_fallback(row)
                            
                            if len(numbers) == 7:
                                item = {
                                    'issue': issue,
                                    'numbers': numbers,
                                    'trend': {
                                        'pos1': numbers[0],
                                        'pos2': numbers[1],
                                        'pos3': numbers[2],
                                        'pos4': numbers[3],
                                        'pos5': numbers[4],
                                        'pos6': numbers[5],
                                        'pos7': numbers[6]  # 特别号
                                    },
                                    'omissions': omissions
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
    
    def _parse_trend_page_fallback(self, row):
        """
        备用解析方式 - 当主解析失败时使用
        
        Args:
            row: HTML表格行
        
        Returns:
            (numbers, omissions) 元组
        """
        numbers = []
        omissions = {}
        
        try:
            cells = row.find_all('td')
            if len(cells) < 8:
                return numbers, omissions
            
            # 简单方式：查找所有带ball类的单元格作为开奖号
            pos_idx = 0
            for cell in cells:
                class_name = cell.get('class', [])
                class_str = ' '.join(class_name) if isinstance(class_name, list) else str(class_name)
                text = cell.get_text(strip=True)
                
                if 'ball' in class_str.lower() or 'yred' in class_str.lower():
                    try:
                        num = int(text)
                        numbers.append(num)
                        pos_name = f'pos{pos_idx + 1}'
                        if pos_name not in omissions:
                            omissions[pos_name] = {}
                        omissions[pos_name][num] = 0
                        pos_idx += 1
                    except ValueError:
                        pass
            
        except Exception as e:
            logger.debug(f'备用解析失败: {e}')
        
        return numbers, omissions
    
    def crawl_history_data(self):
        """
        爬取历史开奖数据
        
        Returns:
            历史开奖数据列表
        """
        logger.info('开始爬取七星彩历史开奖数据')
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
        logger.info(f'开始爬取七星彩走势图数据（增强版）')
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
    spider = QXCSpiderEnhanced()
    
    # 测试历史数据爬取
    print('=== 测试七星彩历史数据爬取 ===')
    history_data = spider.crawl_history_data()
    print(f'获取到 {len(history_data)} 条历史数据')
    if history_data:
        print('前5条数据:')
        for item in history_data[:5]:
            print(f'期号: {item["issue"]}, 日期: {item["date"]}, 号码: {item["numbers"]}')
            print(f'  和值: {item["hezhi"]}, 奇偶比: {item["odd_even_ratio"]}, 跨度: {item["span"]}')
    
    # 测试走势图数据爬取（增强版）
    print('\n=== 测试七星彩走势图数据爬取（增强版）===')
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

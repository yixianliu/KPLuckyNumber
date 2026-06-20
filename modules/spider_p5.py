"""
排列5数据爬虫模块（完整版）

负责从多个数据源爬取排列5历史开奖数据和走势图数据
支持多源备份、自动重试、请求间隔控制、增量爬取、数据校验等功能
"""

import requests
from bs4 import BeautifulSoup
import logging
import random
import time
import os
import re
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple

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
    排列5数据爬虫类（完整版）
    
    负责从多个数据源爬取历史开奖数据和走势图数据
    支持多源备份、自动重试、请求间隔控制、增量爬取等功能
    """
    
    def __init__(self):
        """初始化爬虫配置"""
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Firefox/121.0',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/120.0.0.0',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        ]
        
        self.history_sources = [
            {
                'name': '55128_120',
                'url': 'https://www.55128.cn/kjh/tcp5-history-120.htm',
                'parser': 'parse_55128_history'
            },
            {
                'name': '55128_main',
                'url': 'https://www.55128.cn/kjh/tcp5.htm',
                'parser': 'parse_55128_history'
            }
        ]
        
        self.trend_sources = [
            {
                'name': '55128_trend',
                'url': 'https://www.55128.cn/zs/3_32.htm',
                'parser': 'parse_55128_trend'
            }
        ]
        
        self.headers = {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Connection': 'keep-alive',
            'Cache-Control': 'max-age=0'
        }
        
        self.session = requests.Session()
        self.session.mount('https://', requests.adapters.HTTPAdapter(max_retries=3))
        self.session.mount('http://', requests.adapters.HTTPAdapter(max_retries=3))
    
    def _get_random_headers(self):
        """获取随机请求头"""
        headers = self.headers.copy()
        headers['User-Agent'] = random.choice(self.user_agents)
        headers['Referer'] = random.choice([
            'https://www.55128.cn/',
            'https://www.cpzhixun.com/',
            'https://www.sporttery.cn/'
        ])
        return headers
    
    def _get_page(self, url, max_retries=3, delay_range=(2, 5)):
        """
        获取网页内容
        
        Args:
            url: 目标网页URL
            max_retries: 最大重试次数
            delay_range: 请求间隔范围（秒）
        
        Returns:
            网页HTML内容，失败返回None
        """
        for attempt in range(max_retries):
            try:
                delay = random.uniform(*delay_range)
                time.sleep(delay)
                
                self.session.headers.update(self._get_random_headers())
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
                    backoff_delay = random.uniform(3, 6) * (attempt + 1)
                    time.sleep(backoff_delay)
        
        logger.error(f'多次请求失败，已达到最大重试次数: {url}')
        return None
    
    def validate_data_item(self, item: Dict[str, Any]) -> Tuple[bool, str]:
        """
        数据完整性校验
        
        Args:
            item: 数据项
        
        Returns:
            (是否有效, 错误信息)
        """
        # 期号校验
        issue = str(item.get('issue', ''))
        if not issue:
            return False, '期号为空'
        
        # 期号格式校验（排列5期号通常为7位数字，如2024001）
        if not issue.isdigit():
            return False, f'期号格式错误: {issue}'
        
        # 号码校验
        numbers = item.get('numbers', [])
        if not isinstance(numbers, list) or len(numbers) != 5:
            return False, f'号码数量异常: {len(numbers) if isinstance(numbers, list) else type(numbers)}'
        
        for i, n in enumerate(numbers):
            try:
                num = int(n)
                if not (0 <= num <= 9):
                    return False, f'第{i+1}位号码超出范围: {num}'
            except (ValueError, TypeError):
                return False, f'第{i+1}位号码格式错误: {n}'
        
        # 和值校验
        hezhi = sum(int(n) for n in numbers)
        if not (0 <= hezhi <= 45):
            return False, f'和值异常: {hezhi}'
        
        # 跨度校验
        span = max(int(n) for n in numbers) - min(int(n) for n in numbers)
        if not (0 <= span <= 9):
            return False, f'跨度异常: {span}'
        
        return True, '数据有效'
    
    def parse_55128_history(self, html):
        """解析55128网站历史数据"""
        data = []
        try:
            soup = BeautifulSoup(html, 'html.parser')
            
            table = soup.find('table', class_='table table-bordered table-striped')
            if not table:
                table = soup.find('table', {'id': 'kjhTable'})
            if not table:
                table = soup.find('table')
            
            if not table:
                logger.warning('未找到数据表格')
                return data
            
            tbody = table.find('tbody')
            rows = tbody.find_all('tr') if tbody else table.find_all('tr')
            
            for row in rows:
                try:
                    cells = row.find_all('td')
                    if len(cells) >= 3:
                        draw_date = cells[0].get_text(strip=True)
                        issue = cells[1].get_text(strip=True)
                        
                        numbers_cell = cells[2]
                        numbers = []
                        
                        number_spans = numbers_cell.find_all('span', class_=lambda x: x and ('ball' in x.lower() or 'number' in x.lower()))
                        if not number_spans:
                            number_spans = numbers_cell.find_all('span')
                        
                        if number_spans:
                            numbers = [span.get_text(strip=True) for span in number_spans if span.get_text(strip=True).isdigit()]
                        else:
                            text_content = numbers_cell.get_text(strip=True)
                            numbers = text_content.split()
                            numbers = [n for n in numbers if n.isdigit()]
                        
                        if len(numbers) != 5:
                            continue
                        
                        numbers_int = [int(n) for n in numbers]
                        hezhi = sum(numbers_int)
                        span_calc = max(numbers_int) - min(numbers_int)
                        
                        odd_even_ratio = ''
                        odd_even_pattern = ''
                        if len(cells) > 4:
                            odd_even_ratio = cells[4].get_text(strip=True)
                        if len(cells) > 5:
                            odd_even_pattern = cells[5].get_text(strip=True)
                        
                        item = {
                            'issue': issue,
                            'date': draw_date,
                            'numbers': numbers_int,
                            'hezhi': hezhi,
                            'span': span_calc,
                            'odd_even_ratio': odd_even_ratio,
                            'odd_even_pattern': odd_even_pattern,
                            'source': '55128'
                        }
                        
                        # 数据校验
                        valid, msg = self.validate_data_item(item)
                        if valid:
                            data.append(item)
                        else:
                            logger.warning(f'数据校验失败 [{issue}]: {msg}')
                        
                except Exception as e:
                    logger.debug(f'解析行数据失败: {e}')
            
            logger.info(f'成功解析 {len(data)} 条历史数据')
            
        except Exception as e:
            logger.error(f'解析页面失败: {e}')
        
        return data
    
    def parse_cpzhixun_history(self, html):
        """解析cpzhixun网站历史数据"""
        data = []
        try:
            soup = BeautifulSoup(html, 'html.parser')
            
            table = soup.find('table', class_='kjh-table')
            if not table:
                table = soup.find('table')
            
            if not table:
                logger.warning('未找到数据表格')
                return data
            
            tbody = table.find('tbody')
            rows = tbody.find_all('tr') if tbody else table.find_all('tr')
            
            for row in rows:
                try:
                    cells = row.find_all('td')
                    if len(cells) >= 4:
                        issue = cells[0].get_text(strip=True)
                        draw_date = cells[1].get_text(strip=True)
                        
                        numbers_cell = cells[2]
                        numbers = []
                        
                        balls = numbers_cell.find_all(class_=lambda x: x and ('ball' in x.lower()))
                        if balls:
                            numbers = [b.get_text(strip=True) for b in balls if b.get_text(strip=True).isdigit()]
                        else:
                            text_content = numbers_cell.get_text(strip=True)
                            numbers = re.findall(r'\d', text_content)
                        
                        if len(numbers) != 5:
                            continue
                        
                        numbers_int = [int(n) for n in numbers]
                        hezhi = sum(numbers_int)
                        span_calc = max(numbers_int) - min(numbers_int)
                        
                        odd_count = sum(1 for n in numbers_int if n % 2 == 1)
                        even_count = 5 - odd_count
                        odd_even_ratio = f'{odd_count}:{even_count}'
                        
                        item = {
                            'issue': issue,
                            'date': draw_date,
                            'numbers': numbers_int,
                            'hezhi': hezhi,
                            'span': span_calc,
                            'odd_even_ratio': odd_even_ratio,
                            'odd_even_pattern': '',
                            'source': 'cpzhixun'
                        }
                        
                        valid, msg = self.validate_data_item(item)
                        if valid:
                            data.append(item)
                        else:
                            logger.warning(f'数据校验失败 [{issue}]: {msg}')
                        
                except Exception as e:
                    logger.debug(f'解析行数据失败: {e}')
            
            logger.info(f'成功解析 {len(data)} 条历史数据')
            
        except Exception as e:
            logger.error(f'解析页面失败: {e}')
        
        return data
    
    def parse_55128_trend(self, html):
        """
        解析55128网站走势图数据
        
        走势图页面表格结构复杂，包含期号、号码、和值、奇偶比例、大小比例、质合比例等
        表格列顺序：期号 | 万位 | 千位 | 百位 | 十位 | 个位 | 和值 | 奇偶比例 | 大小比例 | 质合比例 | 遗漏数据...
        """
        data = []
        try:
            soup = BeautifulSoup(html, 'html.parser')
            
            # 查找所有表格
            tables = soup.find_all('table')
            
            for table in tables:
                try:
                    tbody = table.find('tbody')
                    rows = tbody.find_all('tr') if tbody else table.find_all('tr')
                    
                    for row in rows:
                        try:
                            cells = row.find_all('td')
                            if len(cells) < 10:  # 至少需要期号+5位号码+和值+奇偶+大小+质合
                                continue
                            
                            # 第一列是期号
                            first_cell = cells[0].get_text(strip=True)
                            
                            # 跳过表头和特殊行
                            if not first_cell.isdigit():
                                continue
                            
                            # 期号应该是7位数字（如2026160）
                            if len(first_cell) != 7:
                                continue
                            
                            issue = first_cell
                            
                            # 解析号码（第2-6列是万、千、百、十、个位）
                            # 但走势图页面中，号码列可能包含遗漏数据，需要找到实际开奖号码
                            # 实际号码在带有特殊class的td中（如.ball-red或高亮显示）
                            numbers = []
                            
                            # 尝试从带class的元素中获取号码
                            for i in range(1, 6):
                                cell = cells[i]
                                # 查找高亮的号码（通常有特殊class）
                                highlighted = cell.find(class_=lambda x: x and ('red' in x.lower() or 'ball' in x.lower() or 'current' in x.lower()))
                                if highlighted:
                                    num_text = highlighted.get_text(strip=True)
                                    if num_text.isdigit() and len(num_text) == 1:
                                        numbers.append(int(num_text))
                            
                            # 如果没有找到高亮号码，尝试从文本中提取
                            if len(numbers) != 5:
                                numbers = []
                                for i in range(1, 6):
                                    cell_text = cells[i].get_text(strip=True)
                                    # 查找第一个数字
                                    for char in cell_text:
                                        if char.isdigit():
                                            numbers.append(int(char))
                                            break
                                    if len(numbers) == 5:
                                        break
                            
                            if len(numbers) != 5:
                                continue
                            
                            # 解析和值（第7列）
                            hezhi_text = cells[6].get_text(strip=True) if len(cells) > 6 else ''
                            hezhi = sum(numbers)  # 直接计算更准确
                            
                            # 解析奇偶比例（第8列，格式如"3:2"）
                            odd_even_ratio = cells[7].get_text(strip=True) if len(cells) > 7 else ''
                            
                            # 解析大小比例（第9列，格式如"4:1"）
                            big_small_ratio = cells[8].get_text(strip=True) if len(cells) > 8 else ''
                            
                            # 解析质合比例（第10列，格式如"2:3"）
                            prime_composite_ratio = cells[9].get_text(strip=True) if len(cells) > 9 else ''
                            
                            # 计算遗漏值（从后续列中提取）
                            omission_data = {}
                            for i in range(10, min(len(cells), 60)):
                                cell_text = cells[i].get_text(strip=True)
                                if cell_text.isdigit():
                                    omission_data[f'col_{i}'] = int(cell_text)
                            
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
                                'hezhi': str(hezhi),
                                'odd_even_ratio': odd_even_ratio,
                                'big_small_ratio': big_small_ratio,
                                'prime_composite_ratio': prime_composite_ratio,
                                'omission_data': omission_data
                            }
                            
                            valid, msg = self.validate_data_item(item)
                            if valid:
                                data.append(item)
                            else:
                                logger.debug(f'走势数据校验失败 [{issue}]: {msg}')
                        
                        except Exception as e:
                            logger.debug(f'解析走势行数据失败: {e}')
                            continue
                            
                except Exception as e:
                    logger.debug(f'解析走势表格失败: {e}')
                    continue
            
            # 去重
            seen = set()
            unique_data = []
            for item in data:
                if item['issue'] not in seen:
                    seen.add(item['issue'])
                    unique_data.append(item)
            
            logger.info(f'成功解析 {len(unique_data)} 条走势数据')
            return unique_data
            
        except Exception as e:
            logger.error(f'解析走势图页面失败: {e}')
            return []
    
    def parse_cpzhixun_trend(self, html):
        """解析cpzhixun网站走势图数据"""
        data = []
        try:
            soup = BeautifulSoup(html, 'html.parser')
            
            tables = soup.find_all('table')
            
            for table in tables:
                rows = table.find_all('tr')
                
                for row in rows:
                    try:
                        text = row.get_text(separator='|', strip=True)
                        parts = text.split('|')
                        
                        issue = None
                        for part in parts:
                            part = part.strip()
                            if part.isdigit() and len(part) == 7:
                                issue = part
                                break
                        
                        if not issue:
                            continue
                        
                        numbers = []
                        for part in parts:
                            part = part.strip()
                            if part.isdigit() and len(part) == 1:
                                numbers.append(int(part))
                                if len(numbers) == 5:
                                    break
                        
                        if len(numbers) == 5:
                            hezhi = sum(numbers)
                            odd_count = sum(1 for n in numbers if n % 2 == 1)
                            big_count = sum(1 for n in numbers if n >= 5)
                            primes = {1, 2, 3, 5, 7}
                            prime_count = sum(1 for n in numbers if n in primes)
                            
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
                                'hezhi': str(hezhi),
                                'odd_even_ratio': f'{odd_count}:{5-odd_count}',
                                'big_small_ratio': f'{big_count}:{5-big_count}',
                                'prime_composite_ratio': f'{prime_count}:{5-prime_count}'
                            }
                            
                            valid, msg = self.validate_data_item(item)
                            if valid:
                                data.append(item)
                    
                    except Exception as e:
                        continue
            
            seen = set()
            unique_data = []
            for item in data:
                if item['issue'] not in seen:
                    seen.add(item['issue'])
                    unique_data.append(item)
            
            logger.info(f'成功解析 {len(unique_data)} 条走势数据')
            return unique_data
            
        except Exception as e:
            logger.error(f'解析走势图页面失败: {e}')
            return []
    
    def crawl_history_data(self, max_records: int = 120) -> List[Dict[str, Any]]:
        """
        爬取历史开奖数据（多源备份）
        
        Args:
            max_records: 最大获取记录数，默认120条
        
        Returns:
            历史开奖数据列表
        """
        logger.info('开始爬取排列5历史开奖数据')
        
        all_data = []
        seen_issues = set()
        
        for source in self.history_sources:
            logger.info(f'尝试数据源: {source["name"]}')
            html = self._get_page(source['url'])
            
            if html:
                parser_method = getattr(self, source['parser'])
                data = parser_method(html)
                
                for item in data:
                    if item['issue'] not in seen_issues:
                        seen_issues.add(item['issue'])
                        all_data.append(item)
                
                if len(all_data) >= max_records:
                    break
            
            time.sleep(random.uniform(1, 3))
        
        all_data.sort(key=lambda x: x['issue'], reverse=True)
        
        logger.info(f'爬取完成，共获取 {len(all_data)} 条历史数据')
        return all_data[:max_records]
    
    def crawl_trend_data(self, record: int = 120) -> List[Dict[str, Any]]:
        """
        爬取走势图数据
        
        策略：直接从历史数据生成走势图数据，因为走势图页面结构复杂且不稳定
        
        Args:
            record: 获取的记录数量，默认120条
        
        Returns:
            走势数据列表
        """
        logger.info(f'开始获取排列5走势图数据（从历史数据生成）')
        
        # 直接从历史数据获取，然后生成走势图数据
        history_data = self.crawl_history_data(max_records=record)
        
        if not history_data:
            logger.warning('无法获取历史数据，走势图数据为空')
            return []
        
        trend_data = []
        for item in history_data:
            numbers = item.get('numbers', [])
            if len(numbers) != 5:
                continue
            
            # 计算各种统计指标
            hezhi = sum(numbers)
            odd_count = sum(1 for n in numbers if n % 2 == 1)
            even_count = 5 - odd_count
            big_count = sum(1 for n in numbers if n >= 5)
            small_count = 5 - big_count
            
            # 质数：1, 2, 3, 5, 7
            primes = {1, 2, 3, 5, 7}
            prime_count = sum(1 for n in numbers if n in primes)
            composite_count = 5 - prime_count
            
            # 跨度
            span = max(numbers) - min(numbers)
            
            trend_item = {
                'issue': item.get('issue', ''),
                'numbers': numbers,
                'trend': {
                    'wan': numbers[0],
                    'qian': numbers[1],
                    'bai': numbers[2],
                    'shi': numbers[3],
                    'ge': numbers[4]
                },
                'hezhi': str(hezhi),
                'span': str(span),
                'odd_even_ratio': f'{odd_count}:{even_count}',
                'big_small_ratio': f'{big_count}:{small_count}',
                'prime_composite_ratio': f'{prime_count}:{composite_count}',
                'source': 'generated_from_history'
            }
            
            trend_data.append(trend_item)
        
        logger.info(f'走势图数据生成完成，共 {len(trend_data)} 条')
        return trend_data[:record]
    
    def crawl_incremental_data(self, last_issue: Optional[str] = None) -> List[Dict[str, Any]]:
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
            logger.info(f'无历史数据，全量爬取 {len(all_data)} 条')
            return all_data
        
        new_data = []
        for item in all_data:
            if item['issue'] > last_issue:
                new_data.append(item)
        
        logger.info(f'增量爬取完成，新增 {len(new_data)} 条数据')
        return new_data
    
    def crawl_and_save_incremental(self) -> Tuple[int, int, int, int]:
        """
        增量爬取并保存到数据库
        
        Returns:
            (新增历史数据条数, 跳过历史数据条数, 新增走势数据条数, 跳过走势数据条数)
        """
        from modules.database_p5 import P5Database
        
        db = P5Database()
        if not db.connect():
            logger.error('数据库连接失败，无法保存数据')
            return 0, 0, 0, 0
        
        try:
            # 创建表
            db.create_tables()
            
            # 获取已有最新期号
            latest_issue = db.get_latest_history_issue()
            
            # 增量爬取历史数据
            new_history = self.crawl_incremental_data(latest_issue)
            history_success, history_skip = db.insert_history_data(new_history)
            
            # 爬取并保存走势数据
            trend_data = self.crawl_trend_data()
            trend_success, trend_skip = db.insert_trend_data(trend_data)
            
            logger.info(f'数据保存完成: 历史数据新增{history_success}条, 走势数据新增{trend_success}条')
            return history_success, history_skip, trend_success, trend_skip
            
        except Exception as e:
            logger.error(f'爬取并保存数据失败: {e}')
            return 0, 0, 0, 0
        finally:
            db.disconnect()
    
    def full_crawl_and_save(self, max_records: int = 120) -> Tuple[int, int, int, int]:
        """
        全量爬取并保存到数据库（首次运行或重建数据时使用）
        
        Args:
            max_records: 最大获取记录数，默认120条
        
        Returns:
            (新增历史数据条数, 跳过历史数据条数, 新增走势数据条数, 跳过走势数据条数)
        """
        from modules.database_p5 import P5Database
        
        db = P5Database()
        if not db.connect():
            logger.error('数据库连接失败，无法保存数据')
            return 0, 0, 0, 0
        
        try:
            db.create_tables()
            
            # 全量爬取历史数据
            history_data = self.crawl_history_data(max_records)
            history_success, history_skip = db.insert_history_data(history_data)
            
            # 爬取并保存走势数据
            trend_data = self.crawl_trend_data(min(max_records, 120))
            trend_success, trend_skip = db.insert_trend_data(trend_data)
            
            logger.info(f'全量爬取完成: 历史数据新增{history_success}条, 走势数据新增{trend_success}条')
            return history_success, history_skip, trend_success, trend_skip
            
        except Exception as e:
            logger.error(f'全量爬取失败: {e}')
            return 0, 0, 0, 0
        finally:
            db.disconnect()


def test_spider():
    """测试爬虫功能"""
    spider = P5Spider()
    
    print('=== 测试排列5历史数据爬取 ===')
    history_data = spider.crawl_history_data(max_records=50)
    print(f'获取到 {len(history_data)} 条历史数据')
    if history_data:
        print('前5条数据:')
        for item in history_data[:5]:
            print(f'期号: {item["issue"]}, 日期: {item["date"]}, 号码: {item["numbers"]}')
            print(f'  和值: {item["hezhi"]}, 跨度: {item["span"]}')
    
    print('\n=== 测试排列5走势图数据爬取 ===')
    trend_data = spider.crawl_trend_data(record=30)
    print(f'获取到 {len(trend_data)} 条走势图数据')
    if trend_data:
        print(f'第一条走势图数据:')
        print(f'期号: {trend_data[0]["issue"]}')
        print(f'号码: {trend_data[0]["numbers"]}')
    
    print('\n=== 测试增量爬取并保存 ===')
    result = spider.crawl_and_save_incremental()
    print(f'结果: 历史新增{result[0]}条, 跳过{result[1]}条, 走势新增{result[2]}条, 跳过{result[3]}条')
    
    return history_data, trend_data


if __name__ == '__main__':
    test_spider()
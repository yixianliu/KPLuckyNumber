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
    file_handler = logging.FileHandler('logs/data_fetcher.log', encoding='utf-8')
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
                'name': 'china_lottery_trend',
                'url': 'https://m.china-lottery.cn/zschart?frm=z_baidu&lotteryId=1004&ct=T_BASE&lotteryLength=1000',
                'parser': 'parse_china_lottery_trend'
            },
            {
                'name': '55128_trend',
                'url': 'https://www.55128.cn/zs/3_32.htm',
                'parser': 'parse_55128_trend'
            }
        ]
        
        self.wan_trend_sources = [
            {
                'name': 'china_lottery_wan_trend',
                'url': 'https://m.china-lottery.cn/zschart?frm=z_baidu&lotteryId=1004&ct=T_WAN&lotteryLength=1000',
                'parser': 'parse_china_lottery_wan_trend'
            }
        ]
        
        self.qian_trend_sources = [
            {
                'name': 'china_lottery_qian_trend',
                'url': 'https://m.china-lottery.cn/zschart?frm=z_baidu&lotteryId=1004&ct=T_QIAN&lotteryLength=1000',
                'parser': 'parse_china_lottery_qian_trend'
            }
        ]
        
        self.bai_trend_sources = [
            {
                'name': 'china_lottery_bai_trend',
                'url': 'https://m.china-lottery.cn/zschart?frm=z_baidu&lotteryId=1004&ct=T_BAI&lotteryLength=1000',
                'parser': 'parse_china_lottery_bai_trend'
            }
        ]
        
        self.shi_trend_sources = [
            {
                'name': 'china_lottery_shi_trend',
                'url': 'https://m.china-lottery.cn/zschart?frm=z_baidu&lotteryId=1004&ct=T_SHI&lotteryLength=1000',
                'parser': 'parse_china_lottery_shi_trend'
            }
        ]
        
        self.sum_end_trend_sources = [
            {
                'name': 'china_lottery_sum_end_trend',
                'url': 'https://m.china-lottery.cn/zschart?frm=z_baidu&lotteryId=1004&ct=T_SUM_END&lotteryLength=1000',
                'parser': 'parse_china_lottery_sum_end_trend'
            }
        ]
        
        self.back_three_trend_sources = [
            {
                'name': 'china_lottery_back_three_trend',
                'url': 'https://m.china-lottery.cn/zschart?frm=z_baidu&lotteryId=1004&ct=T_BACK_THREE&lotteryLength=1000',
                'parser': 'parse_china_lottery_back_three_trend'
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
    
    def parse_china_lottery_trend(self, html):
        """
        解析中华彩讯网站走势图数据
        数据存储在window.__NUXT__对象中，包含1000期完整数据
        注意：网站使用JavaScript变量混淆，号码值被替换为字母变量
        
        数据结构：
        - issueName: 期号（如"2023217"）
        - awArr: 开奖号码数组（5个字母变量）
        - zhr: 和值
        - span: 跨度
        - jor: 奇偶比
        - dxr: 大小比
        - jbm1-jbm5: 各位置遗漏数据
        """
        data = []
        try:
            import re
            
            nuxt_start = html.find('window.__NUXT__=')
            if nuxt_start == -1:
                logger.warning('未找到window.__NUXT__数据')
                return data
            
            nuxt_start += len('window.__NUXT__=')
            func_start = html.find('function(', nuxt_start)
            if func_start == -1:
                logger.warning('未找到function(部分')
                return data
            
            params_start = func_start + len('function(')
            params_end = html.find(')', params_start)
            if params_end == -1:
                logger.warning('未找到参数结束符)')
                return data
            
            params_str = html[params_start:params_end]
            params = params_str.split(',')
            params = [p.strip() for p in params if p.strip()]
            
            body_start = params_end + 1
            brace_count = 0
            body_end = -1
            for i in range(body_start, len(html)):
                if html[i] == '{':
                    brace_count += 1
                elif html[i] == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        body_end = i
                        break
            
            if body_end == -1:
                logger.info('未找到函数结束符，尝试直接在HTML中搜索数据')
                body_str = html[body_start:]
            else:
                body_str = html[body_start:body_end]
            
            value_map = {}
            for i, param in enumerate(params):
                if i < 10:
                    value_map[param] = i
            
            for key in ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j']:
                if key not in value_map:
                    idx = ord(key) - ord('a')
                    if 0 <= idx < 10:
                        value_map[key] = idx
            
            tm_match = re.search(r'tm:\s*\[([\s\S]*?)\]\s*\},', body_str)
            if not tm_match:
                tm_match = re.search(r'tm:\s*\[([\s\S]*?)\]\s*\]', body_str)
            if not tm_match:
                logger.warning('未找到tm数据')
                return data
            
            tm_str = tm_match.group(1)
            
            item_pattern = r'\{issueName:"([^"]+)",awArr:\[([^\]]+)\]'
            items = re.findall(item_pattern, tm_str)
            
            for issue, aw_arr_str in items:
                if len(issue) != 7:
                    continue
                
                aw_elements = aw_arr_str.split(',')
                aw_elements = [e.strip() for e in aw_elements if e.strip()]
                
                numbers = []
                for elem in aw_elements[:5]:
                    if elem in value_map:
                        numbers.append(value_map[elem])
                    elif elem.isdigit():
                        numbers.append(int(elem))
                
                if len(numbers) != 5:
                    continue
                
                for n in numbers:
                    if not (0 <= n <= 9):
                        continue
                
                hezhi = sum(numbers)
                span = max(numbers) - min(numbers)
                
                odd_count = sum(1 for n in numbers if n % 2 == 1)
                even_count = 5 - odd_count
                odd_even_ratio = f'{odd_count}:{even_count}'
                
                big_count = sum(1 for n in numbers if n >= 5)
                small_count = 5 - big_count
                big_small_ratio = f'{big_count}:{small_count}'
                
                primes = {1, 2, 3, 5, 7}
                prime_count = sum(1 for n in numbers if n in primes)
                composite_count = 5 - prime_count
                
                trend_item = {
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
                    'span': str(span),
                    'odd_even_ratio': odd_even_ratio,
                    'big_small_ratio': big_small_ratio,
                    'prime_composite_ratio': f'{prime_count}:{composite_count}',
                    'omission_data': {},
                    'source': 'china_lottery'
                }
                
                valid, msg = self.validate_data_item(trend_item)
                if valid:
                    data.append(trend_item)
            
            data.sort(key=lambda x: x['issue'], reverse=True)
            
            logger.info(f'成功解析中华彩讯 {len(data)} 条走势数据')
            return data
            
        except Exception as e:
            logger.error(f'解析中华彩讯走势图页面失败: {e}')
            return []
    
    def parse_china_lottery_wan_trend(self, html):
        """
        解析中华彩讯网站万位走势图数据
        URL: https://m.china-lottery.cn/zschart?frm=z_baidu&lotteryId=1004&ct=T_WAN&lotteryLength=1000
        
        数据结构：
        - issueName: 期号（如"2023217"）
        - awArr: 开奖号码数组（万位数字为第一个元素）
        - 网站使用JavaScript变量混淆，号码值被替换为字母变量
        """
        data = []
        try:
            import re
            
            nuxt_start = html.find('window.__NUXT__=')
            if nuxt_start == -1:
                logger.warning('万位走势-未找到window.__NUXT__数据')
                return data
            
            nuxt_start += len('window.__NUXT__=')
            func_start = html.find('function(', nuxt_start)
            if func_start == -1:
                logger.warning('万位走势-未找到function(部分')
                return data
            
            params_start = func_start + len('function(')
            params_end = html.find(')', params_start)
            if params_end == -1:
                logger.warning('万位走势-未找到参数结束符)')
                return data
            
            params_str = html[params_start:params_end]
            params = params_str.split(',')
            params = [p.strip() for p in params if p.strip()]
            
            value_map = {}
            for i, param in enumerate(params):
                if i < 10:
                    value_map[param] = i
            
            for key in ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j']:
                if key not in value_map:
                    idx = ord(key) - ord('a')
                    if 0 <= idx < 10:
                        value_map[key] = idx
            
            tm_match = re.search(r'tm:\s*\[([\s\S]*?)\]\s*\},', html[nuxt_start:nuxt_start+500000])
            if not tm_match:
                tm_match = re.search(r'tm:\s*\[([\s\S]*?)\]\s*\]', html[nuxt_start:nuxt_start+500000])
            if not tm_match:
                logger.warning('万位走势-未找到tm数据')
                return data
            
            tm_str = tm_match.group(1)
            
            item_pattern = r'issueName:"([^"]+)",[^}]*?awArr:\[([^\]]+)\]'
            items = re.findall(item_pattern, tm_str)
            
            primes = {1, 2, 3, 5, 7}
            number_counts = {}
            
            for issue, aw_arr_str in items:
                if len(issue) != 7:
                    continue
                
                aw_elements = aw_arr_str.split(',')
                aw_elements = [e.strip() for e in aw_elements if e.strip()]
                
                if not aw_elements:
                    continue
                
                first_elem = aw_elements[0]
                if first_elem in value_map:
                    wan_number = value_map[first_elem]
                elif first_elem.isdigit():
                    wan_number = int(first_elem)
                else:
                    continue
                
                if not (0 <= wan_number <= 9):
                    continue
                
                number_counts[wan_number] = number_counts.get(wan_number, 0) + 1
                
                is_odd = 1 if wan_number % 2 == 1 else 0
                is_big = 1 if wan_number >= 5 else 0
                is_prime = 1 if wan_number in primes else 0
                
                trend_item = {
                    'issue': issue,
                    'wan_number': wan_number,
                    'draw_date': '',
                    'is_odd': is_odd,
                    'is_big': is_big,
                    'is_prime': is_prime,
                    'omission': 0,
                    'hot_level': '',
                    'consecutive_count': 0,
                    'source': 'china_lottery'
                }
                
                data.append(trend_item)
            
            data.sort(key=lambda x: x['issue'], reverse=True)
            
            if data:
                omission_counts = {n: 0 for n in range(10)}
                for item in data:
                    wn = item['wan_number']
                    item['omission'] = omission_counts[wn]
                    for n in range(10):
                        omission_counts[n] += 1
                    omission_counts[wn] = 0
                
                avg_count = sum(number_counts.values()) / len(number_counts) if number_counts else 0
                for item in data:
                    wn = item['wan_number']
                    count = number_counts.get(wn, 0)
                    if count > avg_count * 1.2:
                        item['hot_level'] = 'hot'
                    elif count < avg_count * 0.8:
                        item['hot_level'] = 'cold'
                    else:
                        item['hot_level'] = 'warm'
            
            logger.info(f'成功解析中华彩讯万位走势 {len(data)} 条数据')
            return data
            
        except Exception as e:
            logger.error(f'解析中华彩讯万位走势图页面失败: {e}')
            return []
    
    def parse_china_lottery_qian_trend(self, html):
        """
        解析中华彩讯网站千位走势图数据
        URL: https://m.china-lottery.cn/zschart?frm=z_baidu&lotteryId=1004&ct=T_QIAN&lotteryLength=1000
        
        数据结构：
        - issueName: 期号（如"2023217"）
        - awArr: 开奖号码数组（千位数字为第二个元素）
        - 网站使用JavaScript变量混淆，号码值被替换为字母变量
        """
        data = []
        try:
            import re
            
            nuxt_start = html.find('window.__NUXT__=')
            if nuxt_start == -1:
                logger.warning('千位走势-未找到window.__NUXT__数据')
                return data
            
            nuxt_start += len('window.__NUXT__=')
            func_start = html.find('function(', nuxt_start)
            if func_start == -1:
                logger.warning('千位走势-未找到function(部分')
                return data
            
            params_start = func_start + len('function(')
            params_end = html.find(')', params_start)
            if params_end == -1:
                logger.warning('千位走势-未找到参数结束符)')
                return data
            
            params_str = html[params_start:params_end]
            params = params_str.split(',')
            params = [p.strip() for p in params if p.strip()]
            
            value_map = {}
            for i, param in enumerate(params):
                if i < 10:
                    value_map[param] = i
            
            for key in ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j']:
                if key not in value_map:
                    idx = ord(key) - ord('a')
                    if 0 <= idx < 10:
                        value_map[key] = idx
            
            tm_match = re.search(r'tm:\s*\[([\s\S]*?)\]\s*\},', html[nuxt_start:nuxt_start+500000])
            if not tm_match:
                tm_match = re.search(r'tm:\s*\[([\s\S]*?)\]\s*\]', html[nuxt_start:nuxt_start+500000])
            if not tm_match:
                logger.warning('千位走势-未找到tm数据')
                return data
            
            tm_str = tm_match.group(1)
            
            item_pattern = r'issueName:"([^"]+)",[^}]*?awArr:\[([^\]]+)\]'
            items = re.findall(item_pattern, tm_str)
            
            primes = {1, 2, 3, 5, 7}
            number_counts = {}
            
            for issue, aw_arr_str in items:
                if len(issue) != 7:
                    continue
                
                aw_elements = aw_arr_str.split(',')
                aw_elements = [e.strip() for e in aw_elements if e.strip()]
                
                if len(aw_elements) < 2:
                    continue
                
                second_elem = aw_elements[1]
                if second_elem in value_map:
                    qian_number = value_map[second_elem]
                elif second_elem.isdigit():
                    qian_number = int(second_elem)
                else:
                    continue
                
                if not (0 <= qian_number <= 9):
                    continue
                
                number_counts[qian_number] = number_counts.get(qian_number, 0) + 1
                
                is_odd = 1 if qian_number % 2 == 1 else 0
                is_big = 1 if qian_number >= 5 else 0
                is_prime = 1 if qian_number in primes else 0
                
                trend_item = {
                    'issue': issue,
                    'qian_number': qian_number,
                    'draw_date': '',
                    'is_odd': is_odd,
                    'is_big': is_big,
                    'is_prime': is_prime,
                    'omission': 0,
                    'hot_level': '',
                    'consecutive_count': 0,
                    'source': 'china_lottery'
                }
                
                data.append(trend_item)
            
            data.sort(key=lambda x: x['issue'], reverse=True)
            
            if data:
                omission_counts = {n: 0 for n in range(10)}
                for item in data:
                    qn = item['qian_number']
                    item['omission'] = omission_counts[qn]
                    for n in range(10):
                        omission_counts[n] += 1
                    omission_counts[qn] = 0
                
                avg_count = sum(number_counts.values()) / len(number_counts) if number_counts else 0
                for item in data:
                    qn = item['qian_number']
                    count = number_counts.get(qn, 0)
                    if count > avg_count * 1.2:
                        item['hot_level'] = 'hot'
                    elif count < avg_count * 0.8:
                        item['hot_level'] = 'cold'
                    else:
                        item['hot_level'] = 'warm'
            
            logger.info(f'成功解析中华彩讯千位走势 {len(data)} 条数据')
            return data
            
        except Exception as e:
            logger.error(f'解析中华彩讯千位走势图页面失败: {e}')
            return []
    
    def parse_china_lottery_bai_trend(self, html):
        """
        解析中华彩讯网站百位走势图数据
        URL: https://m.china-lottery.cn/zschart?frm=z_baidu&lotteryId=1004&ct=T_BAI&lotteryLength=1000
        
        数据结构：
        - issueName: 期号（如"2023217"）
        - awArr: 开奖号码数组（百位数字为第三个元素）
        - 网站使用JavaScript变量混淆，号码值被替换为字母变量
        """
        data = []
        try:
            import re
            
            nuxt_start = html.find('window.__NUXT__=')
            if nuxt_start == -1:
                logger.warning('百位走势-未找到window.__NUXT__数据')
                return data
            
            nuxt_start += len('window.__NUXT__=')
            func_start = html.find('function(', nuxt_start)
            if func_start == -1:
                logger.warning('百位走势-未找到function(部分')
                return data
            
            params_start = func_start + len('function(')
            params_end = html.find(')', params_start)
            if params_end == -1:
                logger.warning('百位走势-未找到参数结束符)')
                return data
            
            params_str = html[params_start:params_end]
            params = params_str.split(',')
            params = [p.strip() for p in params if p.strip()]
            
            value_map = {}
            for i, param in enumerate(params):
                if i < 10:
                    value_map[param] = i
            
            for key in ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j']:
                if key not in value_map:
                    idx = ord(key) - ord('a')
                    if 0 <= idx < 10:
                        value_map[key] = idx
            
            tm_match = re.search(r'tm:\s*\[([\s\S]*?)\]\s*\},', html[nuxt_start:nuxt_start+500000])
            if not tm_match:
                tm_match = re.search(r'tm:\s*\[([\s\S]*?)\]\s*\]', html[nuxt_start:nuxt_start+500000])
            if not tm_match:
                logger.warning('百位走势-未找到tm数据')
                return data
            
            tm_str = tm_match.group(1)
            
            item_pattern = r'issueName:"([^"]+)",[^}]*?awArr:\[([^\]]+)\]'
            items = re.findall(item_pattern, tm_str)
            
            primes = {1, 2, 3, 5, 7}
            number_counts = {}
            
            for issue, aw_arr_str in items:
                if len(issue) != 7:
                    continue
                
                aw_elements = aw_arr_str.split(',')
                aw_elements = [e.strip() for e in aw_elements if e.strip()]
                
                if len(aw_elements) < 3:
                    continue
                
                third_elem = aw_elements[2]
                if third_elem in value_map:
                    bai_number = value_map[third_elem]
                elif third_elem.isdigit():
                    bai_number = int(third_elem)
                else:
                    continue
                
                if not (0 <= bai_number <= 9):
                    continue
                
                number_counts[bai_number] = number_counts.get(bai_number, 0) + 1
                
                is_odd = 1 if bai_number % 2 == 1 else 0
                is_big = 1 if bai_number >= 5 else 0
                is_prime = 1 if bai_number in primes else 0
                
                trend_item = {
                    'issue': issue,
                    'bai_number': bai_number,
                    'draw_date': '',
                    'is_odd': is_odd,
                    'is_big': is_big,
                    'is_prime': is_prime,
                    'omission': 0,
                    'hot_level': '',
                    'consecutive_count': 0,
                    'source': 'china_lottery'
                }
                
                data.append(trend_item)
            
            data.sort(key=lambda x: x['issue'], reverse=True)
            
            if data:
                omission_counts = {n: 0 for n in range(10)}
                for item in data:
                    bn = item['bai_number']
                    item['omission'] = omission_counts[bn]
                    for n in range(10):
                        omission_counts[n] += 1
                    omission_counts[bn] = 0
                
                avg_count = sum(number_counts.values()) / len(number_counts) if number_counts else 0
                for item in data:
                    bn = item['bai_number']
                    count = number_counts.get(bn, 0)
                    if count > avg_count * 1.2:
                        item['hot_level'] = 'hot'
                    elif count < avg_count * 0.8:
                        item['hot_level'] = 'cold'
                    else:
                        item['hot_level'] = 'warm'
            
            logger.info(f'成功解析中华彩讯百位走势 {len(data)} 条数据')
            return data
            
        except Exception as e:
            logger.error(f'解析中华彩讯百位走势图页面失败: {e}')
            return []
    
    def parse_china_lottery_shi_trend(self, html):
        """
        解析中华彩讯网站十位走势图数据
        URL: https://m.china-lottery.cn/zschart?frm=z_baidu&lotteryId=1004&ct=T_SHI&lotteryLength=1000
        
        数据结构：
        - issueName: 期号（如"2023217"）
        - awArr: 开奖号码数组（十位数字为第四个元素）
        - 网站使用JavaScript变量混淆，号码值被替换为字母变量
        """
        data = []
        try:
            import re
            
            nuxt_start = html.find('window.__NUXT__=')
            if nuxt_start == -1:
                logger.warning('十位走势-未找到window.__NUXT__数据')
                return data
            
            nuxt_start += len('window.__NUXT__=')
            func_start = html.find('function(', nuxt_start)
            if func_start == -1:
                logger.warning('十位走势-未找到function(部分')
                return data
            
            params_start = func_start + len('function(')
            params_end = html.find(')', params_start)
            if params_end == -1:
                logger.warning('十位走势-未找到参数结束符)')
                return data
            
            params_str = html[params_start:params_end]
            params = params_str.split(',')
            params = [p.strip() for p in params if p.strip()]
            
            value_map = {}
            for i, param in enumerate(params):
                if i < 10:
                    value_map[param] = i
            
            for key in ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j']:
                if key not in value_map:
                    idx = ord(key) - ord('a')
                    if 0 <= idx < 10:
                        value_map[key] = idx
            
            tm_match = re.search(r'tm:\s*\[([\s\S]*?)\]\s*\},', html[nuxt_start:nuxt_start+500000])
            if not tm_match:
                tm_match = re.search(r'tm:\s*\[([\s\S]*?)\]\s*\]', html[nuxt_start:nuxt_start+500000])
            if not tm_match:
                logger.warning('十位走势-未找到tm数据')
                return data
            
            tm_str = tm_match.group(1)
            
            item_pattern = r'issueName:"([^"]+)",[^}]*?awArr:\[([^\]]+)\]'
            items = re.findall(item_pattern, tm_str)
            
            primes = {1, 2, 3, 5, 7}
            number_counts = {}
            
            for issue, aw_arr_str in items:
                if len(issue) != 7:
                    continue
                
                aw_elements = aw_arr_str.split(',')
                aw_elements = [e.strip() for e in aw_elements if e.strip()]
                
                if len(aw_elements) < 4:
                    continue
                
                fourth_elem = aw_elements[3]
                if fourth_elem in value_map:
                    shi_number = value_map[fourth_elem]
                elif fourth_elem.isdigit():
                    shi_number = int(fourth_elem)
                else:
                    continue
                
                if not (0 <= shi_number <= 9):
                    continue
                
                number_counts[shi_number] = number_counts.get(shi_number, 0) + 1
                
                is_odd = 1 if shi_number % 2 == 1 else 0
                is_big = 1 if shi_number >= 5 else 0
                is_prime = 1 if shi_number in primes else 0
                
                trend_item = {
                    'issue': issue,
                    'shi_number': shi_number,
                    'draw_date': '',
                    'is_odd': is_odd,
                    'is_big': is_big,
                    'is_prime': is_prime,
                    'omission': 0,
                    'hot_level': '',
                    'consecutive_count': 0,
                    'source': 'china_lottery'
                }
                
                data.append(trend_item)
            
            data.sort(key=lambda x: x['issue'], reverse=True)
            
            if data:
                omission_counts = {n: 0 for n in range(10)}
                for item in data:
                    sn = item['shi_number']
                    item['omission'] = omission_counts[sn]
                    for n in range(10):
                        omission_counts[n] += 1
                    omission_counts[sn] = 0
                
                avg_count = sum(number_counts.values()) / len(number_counts) if number_counts else 0
                for item in data:
                    sn = item['shi_number']
                    count = number_counts.get(sn, 0)
                    if count > avg_count * 1.2:
                        item['hot_level'] = 'hot'
                    elif count < avg_count * 0.8:
                        item['hot_level'] = 'cold'
                    else:
                        item['hot_level'] = 'warm'
            
            logger.info(f'成功解析中华彩讯十位走势 {len(data)} 条数据')
            return data
            
        except Exception as e:
            logger.error(f'解析中华彩讯十位走势图页面失败: {e}')
            return []
    
    def parse_china_lottery_sum_end_trend(self, html):
        """
        解析中华彩讯网站和尾走势图数据
        URL: https://m.china-lottery.cn/zschart?frm=z_baidu&lotteryId=1004&ct=T_SUM_END&lotteryLength=1000
        
        数据结构：
        - issueName: 期号（如"2023217"）
        - awArr: 开奖号码数组（5个数字）
        - sumEnd: 和尾值（字母变量形式，如X）
        - sum: 和值
        - 网站使用JavaScript变量混淆，号码值被替换为字母变量
        
        和尾 = 和值 % 10，通过计算5个号码的和得到
        """
        data = []
        try:
            import re
            
            nuxt_start = html.find('window.__NUXT__=')
            if nuxt_start == -1:
                logger.warning('和尾走势-未找到window.__NUXT__数据')
                return data
            
            nuxt_start += len('window.__NUXT__=')
            func_start = html.find('function(', nuxt_start)
            if func_start == -1:
                logger.warning('和尾走势-未找到function(部分')
                return data
            
            params_start = func_start + len('function(')
            params_end = html.find(')', params_start)
            if params_end == -1:
                logger.warning('和尾走势-未找到参数结束符)')
                return data
            
            params_str = html[params_start:params_end]
            params = params_str.split(',')
            params = [p.strip() for p in params if p.strip()]
            
            value_map = {}
            for i, param in enumerate(params):
                value_map[param] = i
            
            tm_pos = html.find('tm:', nuxt_start)
            if tm_pos == -1:
                logger.warning('和尾走势-未找到tm:数据')
                return data
            
            aw_pattern = r'issueName:"([^"]+)",[^}]*?awArr:\[([^\]]+)\]'
            items = re.findall(aw_pattern, html[tm_pos:])
            
            logger.info(f'和尾走势-awArr匹配数: {len(items)}')
            
            sum_end_counts = {}
            
            for issue, aw_arr_str in items:
                if len(issue) != 7:
                    continue
                
                aw_elements = aw_arr_str.split(',')
                aw_elements = [e.strip() for e in aw_elements if e.strip()]
                
                if len(aw_elements) < 5:
                    continue
                
                sum_value = 0
                valid_count = 0
                for elem in aw_elements[:5]:
                    if elem in value_map:
                        sum_value += value_map[elem]
                        valid_count += 1
                    elif elem.isdigit():
                        sum_value += int(elem)
                        valid_count += 1
                
                if valid_count < 5:
                    continue
                
                sum_end = sum_value % 10
                
                if not (0 <= sum_end <= 9):
                    continue
                
                sum_end_counts[sum_end] = sum_end_counts.get(sum_end, 0) + 1
                
                is_odd = 1 if sum_end % 2 == 1 else 0
                is_big = 1 if sum_end >= 5 else 0
                
                trend_item = {
                    'issue': issue,
                    'sum_end': sum_end,
                    'sum_value': sum_value,
                    'draw_date': '',
                    'is_odd': is_odd,
                    'is_big': is_big,
                    'omission': 0,
                    'hot_level': '',
                    'consecutive_count': 0,
                    'source': 'china_lottery'
                }
                
                data.append(trend_item)
            
            data.sort(key=lambda x: x['issue'], reverse=True)
            
            if data:
                omission_counts = {n: 0 for n in range(10)}
                for item in data:
                    se = item['sum_end']
                    item['omission'] = omission_counts[se]
                    for n in range(10):
                        omission_counts[n] += 1
                    omission_counts[se] = 0
                
                avg_count = sum(sum_end_counts.values()) / len(sum_end_counts) if sum_end_counts else 0
                for item in data:
                    se = item['sum_end']
                    count = sum_end_counts.get(se, 0)
                    if count > avg_count * 1.2:
                        item['hot_level'] = 'hot'
                    elif count < avg_count * 0.8:
                        item['hot_level'] = 'cold'
                    else:
                        item['hot_level'] = 'warm'
            
            logger.info(f'成功解析中华彩讯和尾走势 {len(data)} 条数据')
            return data
            
        except Exception as e:
            logger.error(f'解析中华彩讯和尾走势图页面失败: {e}')
            return []
    
    def parse_china_lottery_back_three_trend(self, html):
        """
        解析中华彩讯网站后三走势图数据
        URL: https://m.china-lottery.cn/zschart?frm=z_baidu&lotteryId=1004&ct=T_BACK_THREE&lotteryLength=1000
        
        数据结构：
        - issueName: 期号（如"2023217"）
        - awArr: 开奖号码数组（后三位为百位、十位、个位，索引2、3、4）
        - 网站使用JavaScript变量混淆，号码值被替换为字母变量
        
        后三 = 百位 + 十位 + 个位
        """
        data = []
        try:
            import re
            
            nuxt_start = html.find('window.__NUXT__=')
            if nuxt_start == -1:
                logger.warning('后三走势-未找到window.__NUXT__数据')
                return data
            
            nuxt_start += len('window.__NUXT__=')
            func_start = html.find('function(', nuxt_start)
            if func_start == -1:
                logger.warning('后三走势-未找到function(部分')
                return data
            
            params_start = func_start + len('function(')
            params_end = html.find(')', params_start)
            if params_end == -1:
                logger.warning('后三走势-未找到参数结束符)')
                return data
            
            params_str = html[params_start:params_end]
            params = params_str.split(',')
            params = [p.strip() for p in params if p.strip()]
            
            value_map = {}
            for i, param in enumerate(params):
                value_map[param] = i
            
            tm_pos = html.find('tm:', nuxt_start)
            if tm_pos == -1:
                logger.warning('后三走势-未找到tm:数据')
                return data
            
            aw_pattern = r'issueName:"([^"]+)",[^}]*?awArr:\[([^\]]+)\]'
            items = re.findall(aw_pattern, html[tm_pos:])
            
            logger.info(f'后三走势-awArr匹配数: {len(items)}')
            
            sum_end_counts = {}
            
            for issue, aw_arr_str in items:
                if len(issue) != 7:
                    continue
                
                aw_elements = aw_arr_str.split(',')
                aw_elements = [e.strip() for e in aw_elements if e.strip()]
                
                if len(aw_elements) < 5:
                    continue
                
                bai_num = None
                shi_num = None
                ge_num = None
                
                for i, elem in enumerate(aw_elements[:5]):
                    if elem in value_map:
                        if i == 2:
                            bai_num = value_map[elem]
                        elif i == 3:
                            shi_num = value_map[elem]
                        elif i == 4:
                            ge_num = value_map[elem]
                    elif elem.isdigit():
                        if i == 2:
                            bai_num = int(elem)
                        elif i == 3:
                            shi_num = int(elem)
                        elif i == 4:
                            ge_num = int(elem)
                
                if bai_num is None or shi_num is None or ge_num is None:
                    continue
                
                if not (0 <= bai_num <= 9 and 0 <= shi_num <= 9 and 0 <= ge_num <= 9):
                    continue
                
                sum_value = bai_num + shi_num + ge_num
                sum_end = sum_value % 10
                
                sum_end_counts[sum_end] = sum_end_counts.get(sum_end, 0) + 1
                
                back_three_value = f"{bai_num}{shi_num}{ge_num}"
                
                is_odd = 1 if sum_end % 2 == 1 else 0
                is_big = 1 if sum_end >= 5 else 0
                
                trend_item = {
                    'issue': issue,
                    'bai_number': bai_num,
                    'shi_number': shi_num,
                    'ge_number': ge_num,
                    'back_three_value': back_three_value,
                    'sum_value': sum_value,
                    'sum_end': sum_end,
                    'draw_date': '',
                    'is_odd': is_odd,
                    'is_big': is_big,
                    'omission': 0,
                    'hot_level': '',
                    'consecutive_count': 0,
                    'source': 'china_lottery'
                }
                
                data.append(trend_item)
            
            data.sort(key=lambda x: x['issue'], reverse=True)
            
            if data:
                omission_counts = {n: 0 for n in range(10)}
                for item in data:
                    se = item['sum_end']
                    item['omission'] = omission_counts[se]
                    for n in range(10):
                        omission_counts[n] += 1
                    omission_counts[se] = 0
                
                avg_count = sum(sum_end_counts.values()) / len(sum_end_counts) if sum_end_counts else 0
                for item in data:
                    se = item['sum_end']
                    count = sum_end_counts.get(se, 0)
                    if count > avg_count * 1.2:
                        item['hot_level'] = 'hot'
                    elif count < avg_count * 0.8:
                        item['hot_level'] = 'cold'
                    else:
                        item['hot_level'] = 'warm'
            
            logger.info(f'成功解析中华彩讯后三走势 {len(data)} 条数据')
            return data
            
        except Exception as e:
            logger.error(f'解析中华彩讯后三走势图页面失败: {e}')
            return []
    
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
    
    def crawl_wan_trend_data(self, record: int = 1000) -> List[Dict[str, Any]]:
        """
        爬取万位走势图数据
        
        策略：优先从中华彩讯网站获取（含1000期完整数据）
        
        Args:
            record: 获取的记录数量，默认1000条
        
        Returns:
            万位走势数据列表
        """
        logger.info('开始获取排列5万位走势图数据（优先从中华彩讯获取）')
        
        for source in self.wan_trend_sources:
            logger.info(f'尝试万位走势数据源: {source["name"]}')
            html = self._get_page(source['url'])
            
            if html:
                parser_method = getattr(self, source['parser'])
                data = parser_method(html)
                
                if data:
                    data.sort(key=lambda x: x['issue'], reverse=True)
                    logger.info(f'万位走势数据获取成功，共 {len(data)} 条')
                    return data[:record]
            
            time.sleep(random.uniform(1, 3))
        
        logger.warning('万位走势数据来源获取失败')
        return []
    
    def crawl_qian_trend_data(self, record: int = 1000) -> List[Dict[str, Any]]:
        """
        爬取千位走势图数据
        
        策略：优先从中华彩讯网站获取（含1000期完整数据）
        
        Args:
            record: 获取的记录数量，默认1000条
        
        Returns:
            千位走势数据列表
        """
        logger.info('开始获取排列5千位走势图数据（优先从中华彩讯获取）')
        
        for source in self.qian_trend_sources:
            logger.info(f'尝试千位走势数据源: {source["name"]}')
            html = self._get_page(source['url'])
            
            if html:
                parser_method = getattr(self, source['parser'])
                data = parser_method(html)
                
                if data:
                    data.sort(key=lambda x: x['issue'], reverse=True)
                    logger.info(f'千位走势数据获取成功，共 {len(data)} 条')
                    return data[:record]
            
            time.sleep(random.uniform(1, 3))
        
        logger.warning('千位走势数据来源获取失败')
        return []
    
    def crawl_bai_trend_data(self, record: int = 1000) -> List[Dict[str, Any]]:
        """
        爬取百位走势图数据
        
        策略：优先从中华彩讯网站获取（含1000期完整数据）
        
        Args:
            record: 获取的记录数量，默认1000条
        
        Returns:
            百位走势数据列表
        """
        logger.info('开始获取排列5百位走势图数据（优先从中华彩讯获取）')
        
        for source in self.bai_trend_sources:
            logger.info(f'尝试百位走势数据源: {source["name"]}')
            html = self._get_page(source['url'])
            
            if html:
                parser_method = getattr(self, source['parser'])
                data = parser_method(html)
                
                if data:
                    data.sort(key=lambda x: x['issue'], reverse=True)
                    logger.info(f'百位走势数据获取成功，共 {len(data)} 条')
                    return data[:record]
            
            time.sleep(random.uniform(1, 3))
        
        logger.warning('百位走势数据来源获取失败')
        return []
    
    def crawl_shi_trend_data(self, record: int = 1000) -> List[Dict[str, Any]]:
        """
        爬取十位走势图数据
        
        策略：优先从中华彩讯网站获取（含1000期完整数据）
        
        Args:
            record: 获取的记录数量，默认1000条
        
        Returns:
            十位走势数据列表
        """
        logger.info('开始获取排列5十位走势图数据（优先从中华彩讯获取）')
        
        for source in self.shi_trend_sources:
            logger.info(f'尝试十位走势数据源: {source["name"]}')
            html = self._get_page(source['url'])
            
            if html:
                parser_method = getattr(self, source['parser'])
                data = parser_method(html)
                
                if data:
                    data.sort(key=lambda x: x['issue'], reverse=True)
                    logger.info(f'十位走势数据获取成功，共 {len(data)} 条')
                    return data[:record]
            
            time.sleep(random.uniform(1, 3))
        
        logger.warning('十位走势数据来源获取失败')
        return []
    
    def crawl_sum_end_trend_data(self, record: int = 1000) -> List[Dict[str, Any]]:
        """
        爬取和尾走势图数据
        
        策略：优先从中华彩讯网站获取（含1000期完整数据）
        
        Args:
            record: 获取的记录数量，默认1000条
        
        Returns:
            和尾走势数据列表
        """
        logger.info('开始获取排列5和尾走势图数据（优先从中华彩讯获取）')
        
        for source in self.sum_end_trend_sources:
            logger.info(f'尝试和尾走势数据源: {source["name"]}')
            html = self._get_page(source['url'])
            
            if html:
                parser_method = getattr(self, source['parser'])
                data = parser_method(html)
                
                if data:
                    data.sort(key=lambda x: x['issue'], reverse=True)
                    logger.info(f'和尾走势数据获取成功，共 {len(data)} 条')
                    return data[:record]
            
            time.sleep(random.uniform(1, 3))
        
        logger.warning('和尾走势数据来源获取失败')
        return []
    
    def crawl_back_three_trend_data(self, record: int = 1000) -> List[Dict[str, Any]]:
        """
        爬取后三走势图数据
        
        策略：优先从中华彩讯网站获取（含1000期完整数据）
        
        Args:
            record: 获取的记录数量，默认1000条
        
        Returns:
            后三走势数据列表
        """
        logger.info('开始获取排列5后三走势图数据（优先从中华彩讯获取）')
        
        for source in self.back_three_trend_sources:
            logger.info(f'尝试后三走势数据源: {source["name"]}')
            html = self._get_page(source['url'])
            
            if html:
                parser_method = getattr(self, source['parser'])
                data = parser_method(html)
                
                if data:
                    data.sort(key=lambda x: x['issue'], reverse=True)
                    logger.info(f'后三走势数据获取成功，共 {len(data)} 条')
                    return data[:record]
            
            time.sleep(random.uniform(1, 3))
        
        logger.warning('后三走势数据来源获取失败')
        return []
    
    def crawl_trend_data(self, record: int = 120) -> List[Dict[str, Any]]:
        """
        爬取走势图数据
        
        策略：优先从中华彩讯网站获取（含1000期完整数据），失败时从历史数据生成
        
        Args:
            record: 获取的记录数量，默认120条
        
        Returns:
            走势数据列表
        """
        logger.info(f'开始获取排列5走势图数据（优先从中华彩讯获取）')
        
        # 优先从走势图数据源获取
        all_trend_data = []
        seen_issues = set()
        
        for source in self.trend_sources:
            logger.info(f'尝试走势图数据源: {source["name"]}')
            html = self._get_page(source['url'])
            
            if html:
                parser_method = getattr(self, source['parser'])
                data = parser_method(html)
                
                for item in data:
                    if item['issue'] not in seen_issues:
                        seen_issues.add(item['issue'])
                        all_trend_data.append(item)
                
                if len(all_trend_data) >= record:
                    break
            
            time.sleep(random.uniform(1, 3))
        
        # 如果成功获取到走势图数据
        if all_trend_data:
            all_trend_data.sort(key=lambda x: x['issue'], reverse=True)
            logger.info(f'走势图数据获取完成，共 {len(all_trend_data)} 条')
            return all_trend_data[:record]
        
        # 备用方案：从历史数据生成走势图数据
        logger.info('走势图数据源获取失败，从历史数据生成')
        history_data = self.crawl_history_data(max_records=record)
        
        if not history_data:
            logger.warning('无法获取历史数据，走势图数据为空')
            return []
        
        trend_data = []
        for item in history_data:
            numbers = item.get('numbers', [])
            if len(numbers) != 5:
                continue
            
            hezhi = sum(numbers)
            odd_count = sum(1 for n in numbers if n % 2 == 1)
            even_count = 5 - odd_count
            big_count = sum(1 for n in numbers if n >= 5)
            small_count = 5 - big_count
            
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
        from modules.database import P5Database
        
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
        from modules.database import P5Database
        
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
    
    def parse_expert_list(self, html):
        """
        解析专家列表页面，提取前3位专家信息
        URL: https://m.china-lottery.cn/expert/1004?frm=z_baidu&lotteryId=1004&ct=T_BACK_THREE&lotteryLength=1000
        
        数据结构：
        - aceExpertDTOList: 专家列表数组
        - 每个专家包含：userId, nickName, detailUrl, hitRatio, hitCount, serialHitCount, percent等
        
        页面使用混淆的JavaScript，格式为：
        window.__NUXT__=(function(a,b,c,d,e,f,g,h,i,j,k,l,m,n,o,p,q,r,s,t,u,v,w,x,y,z){return {...}})(0,1,false,null,"2026163","",9,7,8,3,2,5,6,4,...);
        需要先解析参数映射，然后替换body中的参数为实际值
        """
        experts = []
        try:
            nuxt_start = html.find('window.__NUXT__=')
            if nuxt_start == -1:
                logger.warning('专家列表-未找到window.__NUXT__数据')
                return experts
            
            nuxt_start += len('window.__NUXT__=')
            func_start = html.find('function(', nuxt_start)
            if func_start == -1:
                logger.warning('专家列表-未找到function(部分')
                return experts
            
            params_start = func_start + len('function(')
            params_end = html.find(')', params_start)
            if params_end == -1:
                logger.warning('专家列表-未找到参数结束符)')
                return experts
            
            params_str = html[params_start:params_end]
            param_names = [p.strip() for p in params_str.split(',') if p.strip()]
            
            body_start = params_end + 1
            brace_count = 0
            body_end = -1
            for i in range(body_start, len(html)):
                if html[i] == '{':
                    brace_count += 1
                elif html[i] == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        body_end = i
                        break
            
            if body_end == -1:
                logger.warning('专家列表-未找到函数结束符')
                return experts
            
            call_start = body_end + 2
            call_end = html.find(');', call_start)
            if call_end == -1:
                logger.warning('专家列表-未找到函数调用结束符');
                return experts
            
            call_str = html[call_start:call_end]
            call_values = self._parse_js_array(call_str)
            
            value_map = {}
            for i, name in enumerate(param_names):
                if i < len(call_values):
                    value_map[name] = call_values[i]
            
            body_str = html[body_start:body_end]
            
            ace_expert_pos = body_str.find('aceExpertDTOList:')
            if ace_expert_pos == -1:
                logger.warning('专家列表-未找到aceExpertDTOList数据')
                return experts
            
            array_start = body_str.find('[', ace_expert_pos)
            if array_start == -1:
                logger.warning('专家列表-未找到数组开始符[')
                return experts
            
            brace_count = 0
            bracket_count = 1
            i = array_start + 1
            array_end = -1
            in_string = False
            escape = False
            
            while i < len(body_str):
                char = body_str[i]
                
                if escape:
                    i += 1
                    escape = False
                    continue
                
                if char == '\\':
                    escape = True
                    i += 1
                    continue
                
                if char == '"':
                    in_string = not in_string
                    i += 1
                    continue
                
                if not in_string:
                    if char == '[':
                        bracket_count += 1
                    elif char == ']':
                        bracket_count -= 1
                        if bracket_count == 0:
                            array_end = i
                            break
                
                i += 1
            
            if array_end == -1:
                logger.warning('专家列表-未找到数组结束符]')
                return experts
            
            ace_expert_str = body_str[array_start + 1:array_end]
            
            expert_items = self._parse_expert_items(ace_expert_str)
            
            for item_data in expert_items[:3]:
                item_data = self._resolve_values(item_data, value_map)
                
                if 'userId' in item_data:
                    experts.append({
                        'user_id': item_data.get('userId', ''),
                        'nick_name': item_data.get('nickName', ''),
                        'head_url': item_data.get('headUrl', ''),
                        'detail_url': item_data.get('detailUrl', ''),
                        'hit_ratio': item_data.get('hitRatio', ''),
                        'hit_count': item_data.get('hitCount', 0),
                        'serial_hit_count': item_data.get('serialHitCount', 0),
                        'percent': item_data.get('percent', ''),
                        'hot': item_data.get('hot', 0),
                        'rank': item_data.get('rank', 0),
                        'watch_man': item_data.get('watchMan', 0)
                    })
            
            logger.info(f'成功解析专家列表 {len(experts)} 条数据')
            return experts
            
        except Exception as e:
            logger.error(f'解析专家列表页面失败: {e}')
            return []
    
    def _parse_expert_items(self, text):
        """解析专家列表项"""
        items = []
        i = 0
        text_len = len(text)
        
        while i < text_len:
            if text[i] == '{':
                brace_count = 1
                j = i + 1
                in_string = False
                escape = False
                
                while j < text_len and brace_count > 0:
                    char = text[j]
                    
                    if escape:
                        j += 1
                        escape = False
                        continue
                    
                    if char == '\\':
                        escape = True
                        j += 1
                        continue
                    
                    if char == '"':
                        in_string = not in_string
                        j += 1
                        continue
                    
                    if not in_string:
                        if char == '{':
                            brace_count += 1
                        elif char == '}':
                            brace_count -= 1
                    
                    j += 1
                
                if brace_count == 0:
                    obj_str = text[i:j]
                    item = self._parse_single_object_simple(obj_str)
                    items.append(item)
                
                i = j
            else:
                i += 1
        
        return items
    
    def _parse_single_object_simple(self, obj_str):
        """解析单个对象字符串，处理嵌套数组"""
        item = {}
        i = 1
        obj_len = len(obj_str)
        in_string = False
        escape = False
        key = ''
        value = ''
        state = 'key'
        
        while i < obj_len - 1:
            char = obj_str[i]
            
            if escape:
                value += char
                i += 1
                escape = False
                continue
            
            if char == '\\':
                escape = True
                i += 1
                continue
            
            if char == '"':
                in_string = not in_string
                i += 1
                continue
            
            if in_string:
                if state == 'key':
                    key += char
                else:
                    value += char
                i += 1
                continue
            
            if char == ':':
                state = 'value'
                i += 1
                continue
            
            if char == '[' and state == 'value':
                bracket_count = 1
                j = i + 1
                while j < obj_len and bracket_count > 0:
                    if obj_str[j] == '[':
                        bracket_count += 1
                    elif obj_str[j] == ']':
                        bracket_count -= 1
                    j += 1
                value = obj_str[i:j].strip()
                i = j
                continue
            
            if char == ',' or char == '}':
                key = key.strip()
                value = value.strip()
                
                if value.isdigit():
                    item[key] = int(value)
                elif value.lower() == 'true':
                    item[key] = True
                elif value.lower() == 'false':
                    item[key] = False
                elif value == 'null':
                    item[key] = None
                else:
                    item[key] = value
                
                key = ''
                value = ''
                state = 'key'
                
                if char == '}':
                    break
                
                i += 1
                continue
            
            if state == 'key':
                key += char
            else:
                value += char
            
            i += 1
        
        return item
    
    def _parse_json_objects(self, text):
        """解析JSON对象数组字符串"""
        objects = []
        i = 0
        while i < len(text):
            if text[i] == '{':
                obj_str, new_i = self._extract_object(text, i)
                if obj_str:
                    obj = self._parse_single_object(obj_str)
                    objects.append(obj)
                i = new_i
            elif text[i] in ' \t\n\r,':
                i += 1
            else:
                i += 1
        return objects
    
    def _extract_object(self, text, start):
        """从start位置提取一个完整的JSON对象"""
        brace_count = 0
        i = start
        in_string = False
        escape = False
        
        while i < len(text):
            char = text[i]
            
            if escape:
                i += 1
                escape = False
                continue
            
            if char == '\\':
                escape = True
                i += 1
                continue
            
            if char == '"':
                in_string = not in_string
                i += 1
                continue
            
            if not in_string:
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        return text[start:i+1], i + 1
            
            i += 1
        
        return None, i
    
    def _parse_single_object(self, obj_str):
        """解析单个JSON对象字符串"""
        obj = {}
        i = 1
        in_string = False
        escape = False
        key = None
        value_start = None
        state = 'key'
        
        while i < len(obj_str) - 1:
            char = obj_str[i]
            
            if escape:
                i += 1
                escape = False
                continue
            
            if char == '\\':
                escape = True
                i += 1
                continue
            
            if char == '"':
                in_string = not in_string
                if state == 'key' and not in_string:
                    key_end = i
                    key = obj_str[value_start:key_end] if value_start else ''
                    state = 'colon'
                elif state == 'value' and not in_string:
                    value_end = i
                    value = obj_str[value_start:value_end]
                    obj[key] = value
                    state = 'comma'
                else:
                    if state == 'key' or state == 'value':
                        value_start = i + 1
                i += 1
                continue
            
            if not in_string:
                if state == 'key':
                    if char != ' ':
                        value_start = i
                        state = 'key_value'
                elif state == 'key_value':
                    if char == ':':
                        key = obj_str[value_start:i].strip()
                        state = 'colon'
                elif state == 'colon':
                    if char != ' ':
                        value_start = i
                        state = 'value'
                elif state == 'value':
                    if char == ',':
                        value = obj_str[value_start:i].strip()
                        obj[key] = value
                        state = 'comma'
                    elif char == '}':
                        value = obj_str[value_start:i].strip()
                        obj[key] = value
                        state = 'end'
                        break
                elif state == 'comma':
                    if char != ' ':
                        state = 'key'
                        value_start = i
            
            i += 1
        
        return obj
    
    def _resolve_values(self, obj, value_map):
        """将对象中的参数名替换为实际值"""
        result = {}
        for key, val in obj.items():
            if isinstance(val, str) and val in value_map:
                resolved = value_map[val]
                if isinstance(resolved, str):
                    result[key] = resolved
                elif isinstance(resolved, bool):
                    result[key] = resolved
                elif resolved is None:
                    result[key] = None
                else:
                    result[key] = resolved
            elif isinstance(val, str) and val.isdigit():
                result[key] = int(val)
            elif isinstance(val, str) and val.lower() == 'true':
                result[key] = True
            elif isinstance(val, str) and val.lower() == 'false':
                result[key] = False
            elif isinstance(val, str) and val == 'null':
                result[key] = None
            else:
                result[key] = val
        return result
    
    def _parse_js_array(self, js_str):
        """解析JavaScript数组字符串，返回值列表"""
        result = []
        i = 0
        while i < len(js_str):
            if js_str[i] == ',':
                i += 1
                continue
            
            if js_str[i] == '"':
                end = js_str.find('"', i + 1)
                if end == -1:
                    end = len(js_str)
                result.append(js_str[i + 1:end])
                i = end + 1
            elif js_str[i] == "'":
                end = js_str.find("'", i + 1)
                if end == -1:
                    end = len(js_str)
                result.append(js_str[i + 1:end])
                i = end + 1
            elif js_str[i].isdigit() or js_str[i] == '-':
                end = i
                while end < len(js_str) and (js_str[end].isdigit() or js_str[end] == '.'):
                    end += 1
                val = js_str[i:end]
                if '.' in val:
                    result.append(float(val))
                else:
                    result.append(int(val))
                i = end
            elif js_str[i:i+5] == 'false':
                result.append(False)
                i += 5
            elif js_str[i:i+4] == 'true':
                result.append(True)
                i += 4
            elif js_str[i:i+4] == 'null':
                result.append(None)
                i += 4
            else:
                i += 1
        
        return result
    
    def _replace_params(self, text, value_map):
        """将文本中的参数替换为实际值"""
        result = []
        i = 0
        
        while i < len(text):
            matched = False
            
            for param, value in value_map.items():
                if isinstance(value, str):
                    replacement = f'"{value}"'
                elif value is True:
                    replacement = 'true'
                elif value is False:
                    replacement = 'false'
                elif value is None:
                    replacement = 'null'
                elif isinstance(value, (int, float)):
                    replacement = str(value)
                else:
                    replacement = str(value)
                
                param_len = len(param)
                
                if i + param_len <= len(text):
                    before = text[i - 1] if i > 0 else ' '
                    after = text[i + param_len] if i + param_len < len(text) else ' '
                    
                    if text[i:i + param_len] == param:
                        is_word_char_before = before.isalnum() or before == '_'
                        is_word_char_after = after.isalnum() or after == '_'
                        
                        if not is_word_char_before and not is_word_char_after:
                            result.append(replacement)
                            i += param_len
                            matched = True
                            break
            
            if not matched:
                result.append(text[i])
                i += 1
        
        return ''.join(result)
    
    def parse_expert_recommend(self, html):
        """
        解析专家推荐页面，获取最新推荐期号
        URL: https://m.china-lottery.cn/expert/recommend/1004/{userId}
        
        这个页面数据较少，主要通过页面中的期号信息获取最新推荐期号
        """
        try:
            nuxt_start = html.find('window.__NUXT__=')
            if nuxt_start == -1:
                logger.warning('专家推荐页面-未找到window.__NUXT__数据')
                return None
            
            nuxt_start += len('window.__NUXT__=')
            func_start = html.find('function(', nuxt_start)
            if func_start == -1:
                logger.warning('专家推荐页面-未找到function(部分')
                return None
            
            params_start = func_start + len('function(')
            params_end = html.find(')', params_start)
            if params_end == -1:
                logger.warning('专家推荐页面-未找到参数结束符)')
                return None
            
            params_str = html[params_start:params_end]
            param_names = [p.strip() for p in params_str.split(',') if p.strip()]
            
            body_start = params_end + 1
            brace_count = 0
            body_end = -1
            for i in range(body_start, len(html)):
                if html[i] == '{':
                    brace_count += 1
                elif html[i] == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        body_end = i
                        break
            
            if body_end == -1:
                logger.warning('专家推荐页面-未找到函数结束符')
                return None
            
            call_start = body_end + 2
            call_end = html.find(');', call_start)
            if call_end == -1:
                logger.warning('专家推荐页面-未找到函数调用结束符');
                return None
            
            call_str = html[call_start:call_end]
            call_values = self._parse_js_array(call_str)
            
            value_map = {}
            for i, name in enumerate(param_names):
                if i < len(call_values):
                    value_map[name] = call_values[i]
            
            body_str = html[body_start:body_end]
            replaced_str = self._replace_params(body_str, value_map)
            
            issue_pattern = r'issueName:"([^"]+)"'
            issue_matches = re.findall(issue_pattern, replaced_str)
            
            if issue_matches:
                latest_issue = max(issue_matches, key=lambda x: int(x))
                logger.info(f'获取到专家最新推荐期号: {latest_issue}')
                return latest_issue
            
            return None
            
        except Exception as e:
            logger.error(f'解析专家推荐页面失败: {e}')
            return None
    
    def parse_expert_schema(self, html):
        """
        解析专家推荐详情页面，提取推荐内容和预测期数
        URL: https://m.china-lottery.cn/expert/schema/1004/{userId}/{issue}
        
        数据结构：
        - issueInfo: 期号信息（issueName, issueEndTime, issueNo, issueOpenTime）
        - detail: 专家详情（userId, nickName, schemeList, summary, intro, createTime等）
        """
        try:
            nuxt_start = html.find('window.__NUXT__=')
            if nuxt_start == -1:
                logger.warning('专家详情页面-未找到window.__NUXT__数据')
                return None
            
            nuxt_start += len('window.__NUXT__=')
            func_start = html.find('function(', nuxt_start)
            if func_start == -1:
                logger.warning('专家详情页面-未找到function(部分')
                return None
            
            params_start = func_start + len('function(')
            params_end = html.find(')', params_start)
            if params_end == -1:
                logger.warning('专家详情页面-未找到参数结束符)')
                return None
            
            params_str = html[params_start:params_end]
            param_names = [p.strip() for p in params_str.split(',') if p.strip()]
            logger.info(f'参数名称数量: {len(param_names)}')
            
            body_start = params_end + 1
            brace_count = 0
            body_end = -1
            for i in range(body_start, len(html)):
                if html[i] == '{':
                    brace_count += 1
                elif html[i] == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        body_end = i
                        break
            
            if body_end == -1:
                logger.warning('专家详情页面-未找到函数结束符')
                return None
            
            call_start = body_end + 2
            call_end = html.find(');', call_start)
            if call_end == -1:
                logger.warning('专家详情页面-未找到函数调用结束符');
                return None
            
            call_str = html[call_start:call_end]
            call_values = self._parse_js_array(call_str)
            logger.info(f'参数值数量: {len(call_values)}')
            
            value_map = {}
            for i, name in enumerate(param_names):
                if i < len(call_values):
                    value_map[name] = call_values[i]
            
            body_str = html[body_start:body_end]
            logger.info(f'body_str长度: {len(body_str)}')
            
            replaced_str = self._replace_params(body_str, value_map)
            logger.info(f'replaced_str长度: {len(replaced_str)}')
            
            issue_info_pattern = r'issueInfo:\{([^}]*?)\}'
            issue_info_match = re.search(issue_info_pattern, replaced_str)
            
            if not issue_info_match:
                logger.warning('专家详情页面-未找到issueInfo数据')
                idx = replaced_str.find('issueInfo')
                if idx > 0:
                    logger.info(f'issueInfo附近内容: {replaced_str[idx:idx+200]}')
                return None
            
            issue_info_str = issue_info_match.group(1)
            issue_info = {}
            for field in issue_info_str.split(','):
                field = field.strip()
                if ':' in field:
                    key, val = field.split(':', 1)
                    key = key.strip().strip('"')
                    val = val.strip().strip('"')
                    issue_info[key] = val
            logger.info(f'issue_info: {issue_info}')
            
            detail_start = replaced_str.find('detail:{')
            if detail_start == -1:
                logger.warning('专家详情页面-未找到detail数据')
                return None
            
            detail_start += len('detail:{')
            depth = 1
            detail_end = detail_start
            while detail_end < len(replaced_str) and depth > 0:
                if replaced_str[detail_end] == '{':
                    depth += 1
                elif replaced_str[detail_end] == '}':
                    depth -= 1
                detail_end += 1
            
            detail_str = replaced_str[detail_start:detail_end-1]
            logger.info(f'detail_str长度: {len(detail_str)}')
            
            nick_name_match = re.search(r'nickName:"([^"]*)"', detail_str)
            nick_name = nick_name_match.group(1) if nick_name_match else ''
            
            user_id_match = re.search(r'userId:(\d+)', detail_str)
            user_id = int(user_id_match.group(1)) if user_id_match else 0
            
            create_time_match = re.search(r'createTime:"([^"]*)"', detail_str)
            create_time = create_time_match.group(1) if create_time_match else ''
            
            summary_match = re.search(r'summary:"([^"]*)"', detail_str)
            summary = summary_match.group(1) if summary_match else ''
            
            intro_match = re.search(r'intro:"([^"]*)"', detail_str)
            intro = intro_match.group(1) if intro_match else ''
            
            schemes = []
            scheme_list_start = detail_str.find('schemeList:[')
            if scheme_list_start != -1:
                scheme_list_start += len('schemeList:[')
                depth = 1
                scheme_list_end = scheme_list_start
                while scheme_list_end < len(detail_str) and depth > 0:
                    if detail_str[scheme_list_end] == '[':
                        depth += 1
                    elif detail_str[scheme_list_end] == ']':
                        depth -= 1
                    elif detail_str[scheme_list_end] == '"':
                        scheme_list_end += 1
                        while scheme_list_end < len(detail_str) and detail_str[scheme_list_end] != '"':
                            if detail_str[scheme_list_end] == '\\' and scheme_list_end + 1 < len(detail_str):
                                scheme_list_end += 2
                            else:
                                scheme_list_end += 1
                    scheme_list_end += 1
                
                scheme_list_str = detail_str[scheme_list_start:scheme_list_end-1]
                logger.info(f'scheme_list_str长度: {len(scheme_list_str)}')
                
                obj_pattern = r'\{([^{}]*?)\}'
                scheme_items = re.findall(obj_pattern, scheme_list_str)
                
                for scheme_item in scheme_items:
                    fields = []
                    start = 0
                    in_string = False
                    in_array = False
                    array_depth = 0
                    
                    for i, char in enumerate(scheme_item):
                        if char == '"' and (i == 0 or scheme_item[i-1] != '\\'):
                            in_string = not in_string
                        elif not in_string:
                            if char == '[':
                                in_array = True
                                array_depth += 1
                            elif char == ']':
                                array_depth -= 1
                                if array_depth == 0:
                                    in_array = False
                            elif char == ',' and not in_array:
                                field = scheme_item[start:i].strip()
                                if field:
                                    fields.append(field)
                                start = i + 1
                    
                    field = scheme_item[start:].strip()
                    if field:
                        fields.append(field)
                    
                    scheme_data = {}
                    for field in fields:
                        if ':' in field:
                            key, val = field.split(':', 1)
                            key = key.strip()
                            val = val.strip().strip('"')
                            
                            if val in value_map:
                                val = value_map[val]
                            
                            if isinstance(val, str):
                                if val.isdigit():
                                    scheme_data[key] = int(val)
                                elif val == 'true':
                                    scheme_data[key] = True
                                elif val == 'false':
                                    scheme_data[key] = False
                                elif val == 'null':
                                    scheme_data[key] = None
                                elif val.startswith('[') and val.endswith(']'):
                                    inner = val[1:-1].strip()
                                    if inner:
                                        numbers = []
                                        for n in inner.split(','):
                                            n = n.strip().strip('"')
                                            if n in value_map:
                                                n = str(value_map[n])
                                            if n.isdigit():
                                                numbers.append(int(n))
                                            elif n:
                                                numbers.append(n)
                                        scheme_data[key] = numbers
                                    else:
                                        scheme_data[key] = []
                                else:
                                    scheme_data[key] = val
                            else:
                                scheme_data[key] = val
                    
                    if 'playtypeName' in scheme_data:
                        schemes.append(scheme_data)
            
            logger.info(f'解析到方案数量: {len(schemes)}')
            for s in schemes:
                logger.info(f'  方案: {s.get("playtypeName")}, 号码: {s.get("numberList")}')
            
            result = {
                'user_id': user_id,
                'nick_name': nick_name,
                'issue_name': issue_info.get('issueName', ''),
                'issue_no': issue_info.get('issueNo', ''),
                'issue_end_time': issue_info.get('issueEndTime', ''),
                'issue_open_time': issue_info.get('issueOpenTime', ''),
                'summary': summary,
                'intro': intro,
                'create_time': create_time,
                'schemes': schemes,
                'scheme_count': len(schemes)
            }
            
            logger.info(f'成功解析专家详情: {nick_name}, 期号: {issue_info.get("issueName", "")}, 方案数: {len(schemes)}')
            return result
            
        except Exception as e:
            logger.error(f'解析专家详情页面失败: {e}')
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    def _extract_js_objects(self, text):
        """从文本中提取所有JS对象"""
        objects = []
        i = 0
        
        while i < len(text):
            if text[i] == '{':
                depth = 1
                start = i
                i += 1
                while i < len(text) and depth > 0:
                    if text[i] == '{':
                        depth += 1
                    elif text[i] == '}':
                        depth -= 1
                    elif text[i] == '"':
                        i += 1
                        while i < len(text) and text[i] != '"':
                            if text[i] == '\\' and i + 1 < len(text):
                                i += 2
                            else:
                                i += 1
                    i += 1
                objects.append(text[start:i])
            else:
                i += 1
        
        return objects
    
    def _parse_js_fields(self, text):
        """解析JS对象字段，正确处理嵌套数组和对象"""
        fields = []
        start = 0
        in_string = False
        depth = 0
        
        for i, char in enumerate(text):
            if char == '"' and (i == 0 or text[i-1] != '\\'):
                in_string = not in_string
            elif not in_string:
                if char in '{[':
                    depth += 1
                elif char in '}]':
                    depth -= 1
                elif char == ',' and depth == 0:
                    field = text[start:i].strip()
                    if field:
                        fields.append(field)
                    start = i + 1
        
        field = text[start:].strip()
        if field:
            fields.append(field)
        
        return fields
    
    def _parse_scheme_fields(self, text):
        """解析方案对象字段，正确处理数组值"""
        fields = []
        start = 0
        in_string = False
        in_array = False
        array_depth = 0
        
        for i, char in enumerate(text):
            if char == '"' and (i == 0 or text[i-1] != '\\'):
                in_string = not in_string
            elif not in_string:
                if char == '[':
                    in_array = True
                    array_depth += 1
                elif char == ']':
                    array_depth -= 1
                    if array_depth == 0:
                        in_array = False
                elif char == ',' and not in_array:
                    field = text[start:i].strip()
                    if field:
                        fields.append(field)
                    start = i + 1
        
        field = text[start:].strip()
        if field:
            fields.append(field)
        
        return fields
    
    def crawl_expert_list(self) -> List[Dict[str, Any]]:
        """
        爬取专家列表，获取前3位专家信息
        """
        logger.info('开始爬取排列5专家列表')
        
        url = 'https://m.china-lottery.cn/expert/1004?frm=z_baidu&lotteryId=1004&ct=T_BACK_THREE&lotteryLength=1000'
        html = self._get_page(url)
        
        if not html:
            logger.error('获取专家列表页面失败')
            return []
        
        experts = self.parse_expert_list(html)
        logger.info(f'专家列表爬取完成，共获取 {len(experts)} 位专家')
        return experts
    
    def crawl_expert_recommendation(self, expert_user_id: int, latest_issue: str = None) -> Optional[Dict[str, Any]]:
        """
        爬取指定专家的最新推荐详情
        
        Args:
            expert_user_id: 专家用户ID
            latest_issue: 目标期号（可选，不指定则使用默认期号）
        
        Returns:
            专家推荐详情数据
        """
        logger.info(f'开始爬取专家推荐详情，专家ID: {expert_user_id}')
        
        if not latest_issue:
            recommend_url = f'https://m.china-lottery.cn/expert/recommend/1004/{expert_user_id}?frm=z_baidu&lotteryId=1004&ct=T_BACK_THREE&lotteryLength=1000'
            html = self._get_page(recommend_url)
            
            if not html:
                logger.error(f'获取专家推荐页面失败，专家ID: {expert_user_id}')
                return None
            
            latest_issue = self.parse_expert_recommend(html)
            if not latest_issue:
                latest_issue = '2026163'
                logger.warning(f'未获取到专家最新推荐期号，使用默认期号: {latest_issue}')
            
            time.sleep(random.uniform(2, 4))
        
        schema_url = f'https://m.china-lottery.cn/expert/schema/1004/{expert_user_id}/{latest_issue}?frm=z_baidu&lotteryId=1004&ct=T_BACK_THREE&lotteryLength=1000'
        html = self._get_page(schema_url)
        
        if not html:
            logger.error(f'获取专家详情页面失败，专家ID: {expert_user_id}, 期号: {latest_issue}')
            return None
        
        result = self.parse_expert_schema(html)
        if result:
            logger.info(f'专家推荐详情爬取成功，专家: {result["nick_name"]}, 期号: {result["issue_name"]}')
        else:
            logger.error(f'解析专家详情失败，专家ID: {expert_user_id}, 期号: {latest_issue}')
        
        return result
    
    def crawl_top_experts_recommendations(self, expert_count: int = 3) -> List[Dict[str, Any]]:
        """
        爬取排名前N位专家的最新推荐详情
        
        Args:
            expert_count: 获取专家数量，默认3位
        
        Returns:
            专家推荐详情列表
        """
        logger.info(f'开始爬取排名前{expert_count}位专家的最新推荐')
        
        experts = self.crawl_expert_list()
        if not experts:
            logger.error('未获取到专家列表')
            return []
        
        recommendations = []
        for expert in experts[:expert_count]:
            user_id = expert.get('user_id', 0)
            if not user_id:
                continue
            
            time.sleep(random.uniform(3, 6))
            
            detail = self.crawl_expert_recommendation(user_id)
            if detail:
                detail['expert_info'] = expert
                recommendations.append(detail)
            
            time.sleep(random.uniform(2, 4))
        
        logger.info(f'专家推荐爬取完成，共获取 {len(recommendations)} 位专家的推荐')
        return recommendations


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
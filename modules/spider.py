import requests
from bs4 import BeautifulSoup
import logging
import time
import random
import os

# 确保日志目录存在
os.makedirs('logs', exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/spider.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class QXCSpider:
    """
    七星彩数据爬虫类
    
    负责从指定网站爬取历史开奖数据和走势图数据
    支持自动重试、请求间隔控制等功能
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
                            numbers = [c for c in text_content if c.isdigit()]
                        
                        # 确保提取到7个号码
                        if len(numbers) != 7:
                            logger.warning(f'期号 {issue} 号码数量异常: {len(numbers)}')
                            continue
                        
                        # 提取扩展字段
                        hezhi = cells[3].get_text(strip=True) if len(cells) > 3 else ''
                        hezhi_type = cells[4].get_text(strip=True) if len(cells) > 4 else ''
                        odd_even_ratio = cells[5].get_text(strip=True) if len(cells) > 5 else ''
                        odd_even_pattern = cells[6].get_text(strip=True) if len(cells) > 6 else ''
                        span = cells[7].get_text(strip=True) if len(cells) > 7 else ''
                        
                        item = {
                            'issue': issue,
                            'date': draw_date,
                            'numbers': numbers,
                            'hezhi': hezhi,
                            'hezhi_type': hezhi_type,
                            'odd_even_ratio': odd_even_ratio,
                            'odd_even_pattern': odd_even_pattern,
                            'span': span
                        }
                        data.append(item)
                        logger.debug(f'解析数据: {issue} - {numbers}')
                        
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
                                    for cell in cells[1:]:
                                        val = cell.get_text(strip=True)
                                        if val:
                                            trend_values.append(val)
                                       
                                    item = {
                                        'issue': issue,
                                        'trend': trend_values
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
        爬取走势图数据
        
        Args:
            record: 获取的记录数量
        
        Returns:
            走势数据列表
        """
        url = f'{self.trend_url}?record={record}'
        logger.info(f'开始爬取七星彩走势图数据')
        logger.info(f'正在请求: {url}')
        
        html = self._get_page(url)
        if html:
            data = self._parse_trend_page(html)
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

if __name__ == '__main__':
    spider = QXCSpider()
    
    # 测试历史数据爬取
    print('=== 测试历史数据爬取 ===')
    history_data = spider.crawl_history_data()
    print(f'获取到 {len(history_data)} 条历史数据')
    if history_data:
        print('前5条数据:')
        for item in history_data[:5]:
            print(f'期号: {item["issue"]}, 日期: {item["date"]}, 号码: {" ".join(item["numbers"])}')
            print(f'  和值: {item["hezhi"]}, 奇偶比: {item["odd_even_ratio"]}, 跨度: {item["span"]}')
    
    # 测试走势图数据爬取
    print('\n=== 测试走势图数据爬取 ===')
    trend_data = spider.crawl_trend_data(record=120)
    print(f'获取到 {len(trend_data)} 条走势图数据')
    if trend_data:
        print(f'第一条走势图数据:')
        print(f'期号: {trend_data[0]["issue"]}')
        print(f'走势数据长度: {len(trend_data[0]["trend"])}')
        print(f'走势数据预览: {trend_data[0]["trend"][:5]}...')
import requests
from bs4 import BeautifulSoup
import time
import random
import logging

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
    def __init__(self):
        self.base_url = 'https://kaijiang.78500.cn/qxc/'
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Referer': 'https://kaijiang.78500.cn/',
            'Connection': 'keep-alive'
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)

    def _get_page(self, url, max_retries=3):
        for attempt in range(max_retries):
            try:
                response = self.session.get(url, timeout=15)
                response.raise_for_status()
                response.encoding = 'gb2312'
                return response.text
            except requests.exceptions.RequestException as e:
                logger.error(f'请求失败，第 {attempt + 1} 次尝试: {e}')
                if attempt < max_retries - 1:
                    time.sleep(random.uniform(2, 5))
                else:
                    logger.error(f'请求失败，已达最大重试次数: {url}')
                    return None

    def _parse_page(self, html):
        if not html:
            logger.warning('HTML为空')
            return []
        
        soup = BeautifulSoup(html, 'html.parser')
        data = []
        
        try:
            table = soup.find('table', {'class': 'kjls'})
            if not table:
                logger.warning('未找到class=kjls的表格')
                return []
            
            tbody_list = table.find_all('tbody')
            
            if len(tbody_list) >= 2:
                data_tbody = tbody_list[1]
                rows = data_tbody.find_all('tr')
                
                for row in rows:
                    cells = row.find_all('td')
                    if len(cells) >= 10:
                        try:
                            issue = cells[0].get_text(strip=True)
                            date = cells[1].get_text(strip=True)
                            numbers_cell = cells[3]
                            
                            numbers = []
                            a_tag = numbers_cell.find('a')
                            if a_tag:
                                em_tag = a_tag.find('em')
                                if em_tag:
                                    em_text = em_tag.get_text(strip=True)
                                    a_text = a_tag.get_text(strip=True).replace(em_text, ' ' + em_text)
                                else:
                                    a_text = a_tag.get_text(strip=True)
                                
                                parts = a_text.split()
                                for part in parts:
                                    if part.isdigit():
                                        numbers.append(part)
                            
                            if len(numbers) >= 7:
                                data.append({
                                    'issue': issue,
                                    'date': date,
                                    'numbers': numbers[:7]
                                })
                        except Exception as e:
                            logger.error(f'解析行数据失败: {e}')
                            continue
        except Exception as e:
            logger.error(f'解析页面失败: {e}')
        
        return data

    def crawl(self, pages=5):
        all_data = []
        logger.info(f'开始爬取七星彩数据，共 {pages} 页')
        
        for page in range(1, pages + 1):
            if page == 1:
                url = self.base_url
            else:
                url = f'{self.base_url}index_{page}.html'
            
            logger.info(f'正在爬取第 {page} 页: {url}')
            html = self._get_page(url)
            page_data = self._parse_page(html)
            
            if page_data:
                all_data.extend(page_data)
                logger.info(f'第 {page} 页获取到 {len(page_data)} 条数据')
            else:
                logger.warning(f'第 {page} 页未获取到数据')
            
            if page < pages:
                time.sleep(random.uniform(3, 6))
        
        logger.info(f'爬取完成，共获取 {len(all_data)} 条数据')
        return all_data

if __name__ == '__main__':
    spider = QXCSpider()
    data = spider.crawl(pages=1)
    print(f'获取到 {len(data)} 条开奖数据')
    if data:
        print('前5条数据示例:')
        for item in data[:5]:
            print(f"期号: {item['issue']}, 日期: {item['date']}, 号码: {' '.join(item['numbers'])}")

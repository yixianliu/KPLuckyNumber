from modules.spider_p5 import P5Spider
from bs4 import BeautifulSoup
import logging

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

spider = P5Spider()
print('获取走势图页面...')
html = spider._get_page('https://www.55128.cn/zs/3_32.htm')

if html:
    soup = BeautifulSoup(html, 'html.parser')
    tables = soup.find_all('table')
    print(f'找到 {len(tables)} 个表格')
    
    for idx, table in enumerate(tables):
        print(f'\n=== 表格 {idx + 1} ===')
        rows = table.find_all('tr')
        print(f'行数: {len(rows)}')
        
        # 只看前5行
        for row_idx, row in enumerate(rows[:5]):
            cells = row.find_all('td')
            cell_texts = [c.get_text(strip=True)[:20] for c in cells[:15]]
            print(f'  行{row_idx}: {len(cells)} 列, 前15列: {cell_texts}')

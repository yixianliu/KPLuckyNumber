"""
排列5网页数据爬取模块

爬取https://www.ydniu.com/info/pl5/zjtj/的专家推荐和走势统计数据

技术要求：
- 设置合理的请求频率限制，避免对目标网站造成过大负载
- 数据提取具备容错机制，处理可能的网页结构变化
- 支持断点续爬和数据增量更新
"""

import logging
import os
import time
import re
import json
from datetime import datetime
from typing import Dict, List, Any, Optional

import requests
from bs4 import BeautifulSoup

os.makedirs('logs', exist_ok=True)

logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler = logging.FileHandler('logs/ydniu_spider.log', encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


class YDNiuSpider:
    """
    亿点牛网站排列5数据爬取器

    爬取专家推荐、走势统计等数据
    """

    def __init__(self):
        self.base_url = "https://www.ydniu.com"
        self.pl5_zjtj_url = "https://www.ydniu.com/info/pl5/zjtj/"
        
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                          '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Referer': 'https://www.ydniu.com/',
            'Connection': 'keep-alive'
        }
        
        self.request_interval = 2
        self.max_retries = 3
        self.retry_delay = 5

    def _make_request(self, url: str, method: str = 'GET', data: Optional[Dict] = None) -> Optional[str]:
        """
        发起HTTP请求，带重试和频率限制

        Args:
            url: 请求URL
            method: 请求方法
            data: POST数据

        Returns:
            响应内容，失败返回None
        """
        for attempt in range(self.max_retries):
            try:
                logger.info(f'请求URL: {url} (尝试 {attempt + 1}/{self.max_retries})')
                
                if method.upper() == 'POST':
                    response = requests.post(url, headers=self.headers, data=data, timeout=30)
                else:
                    response = requests.get(url, headers=self.headers, timeout=30)
                
                response.raise_for_status()
                
                time.sleep(self.request_interval)
                
                return response.text
                
            except requests.exceptions.RequestException as e:
                logger.error(f'请求失败: {e}')
                if attempt < self.max_retries - 1:
                    logger.info(f'等待 {self.retry_delay} 秒后重试...')
                    time.sleep(self.retry_delay)
        
        logger.error(f'请求{url}超过最大重试次数')
        return None

    def parse_zx_list_links(self, html: str) -> List[Dict[str, Any]]:
        """
        解析主页面中class为"zx_list_subbox_left"的DOM元素下的所有超链接

        Args:
            html: 网页HTML内容

        Returns:
            超链接列表，包含URL和标题
        """
        links = []
        
        try:
            soup = BeautifulSoup(html, 'html.parser')
            
            # 查找所有class为"zx_list_subbox_left"的元素
            list_boxes = soup.find_all('div', class_='zx_list_subbox_left')
            
            logger.info(f'找到 {len(list_boxes)} 个zx_list_subbox_left元素')
            
            for box in list_boxes:
                # 查找该元素下的所有超链接
                a_tags = box.find_all('a', href=True)
                
                for a_tag in a_tags:
                    url = a_tag.get('href', '')
                    title = a_tag.get_text(strip=True)
                    
                    # 补全完整URL
                    if url and not url.startswith('http'):
                        if url.startswith('/'):
                            url = self.base_url + url
                        else:
                            url = self.base_url + '/' + url
                    
                    if url:
                        links.append({
                            'url': url,
                            'title': title,
                            'crawl_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        })
            
            logger.info(f'解析到 {len(links)} 个超链接')
            
        except Exception as e:
            logger.error(f'解析超链接失败: {e}')
        
        return links

    def filter_links_by_issue(self, links: List[Dict[str, Any]], target_issue: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        根据期号过滤超链接

        Args:
            links: 超链接列表
            target_issue: 目标期号，如果为None则返回所有链接

        Returns:
            过滤后的超链接列表
        """
        if not target_issue:
            return links
        
        filtered_links = []
        issue_pattern = re.compile(r'(\d{5,8})期')
        
        for link in links:
            # 检查URL或标题中是否包含目标期号
            if target_issue in link['url'] or target_issue in link['title']:
                filtered_links.append(link)
            else:
                # 检查是否包含其他期号格式
                url_match = issue_pattern.search(link['url'])
                title_match = issue_pattern.search(link['title'])
                
                if url_match and url_match.group(1) == target_issue:
                    filtered_links.append(link)
                elif title_match and title_match.group(1) == target_issue:
                    filtered_links.append(link)
        
        logger.info(f'根据期号 {target_issue} 过滤后剩余 {len(filtered_links)} 个链接')
        return filtered_links

    def parse_zx_article_content(self, html: str) -> Dict[str, Any]:
        """
        解析页面中class为"zx_article"的内容区块

        Args:
            html: 网页HTML内容

        Returns:
            文章内容数据
        """
        content_data = {
            'title': '',
            'content': '',
            'author': '',
            'publish_time': '',
            'tags': [],
            'raw_html': ''
        }
        
        try:
            soup = BeautifulSoup(html, 'html.parser')
            
            # 查找class为"zx_article"的元素
            article_div = soup.find('div', class_='zx_article')
            
            if article_div:
                # 提取标题
                title_tag = article_div.find(['h1', 'h2', 'h3'])
                if title_tag:
                    content_data['title'] = title_tag.get_text(strip=True)
                else:
                    # 尝试从页面其他位置获取标题
                    title_tag = soup.find('title')
                    if title_tag:
                        content_data['title'] = title_tag.get_text(strip=True)
                
                # 提取正文内容
                # 移除script和style标签
                for script in article_div(['script', 'style']):
                    script.decompose()
                
                content_data['content'] = article_div.get_text(separator='\n', strip=True)
                
                # 提取作者信息
                author_tag = article_div.find(['span', 'div'], class_=re.compile(r'author|writer'))
                if author_tag:
                    content_data['author'] = author_tag.get_text(strip=True)
                
                # 提取发布时间
                time_tag = article_div.find(['span', 'div', 'time'], class_=re.compile(r'time|date|publish'))
                if time_tag:
                    content_data['publish_time'] = time_tag.get_text(strip=True)
                
                # 提取标签
                tag_tags = article_div.find_all('a', class_=re.compile(r'tag|label'))
                content_data['tags'] = [tag.get_text(strip=True) for tag in tag_tags]
                
                # 保存原始HTML（用于调试）
                content_data['raw_html'] = str(article_div)
                
                logger.info(f'成功解析文章内容，标题: {content_data["title"][:50]}...')
            else:
                logger.warning('未找到class为"zx_article"的元素')
                
                # 尝试查找其他可能的文章容器
                possible_containers = soup.find_all('div', class_=re.compile(r'article|content|detail'))
                for container in possible_containers:
                    text = container.get_text(strip=True)
                    if len(text) > 100:  # 假设文章内容至少100个字符
                        content_data['content'] = text
                        logger.info('使用备用解析方式获取文章内容')
                        break
            
        except Exception as e:
            logger.error(f'解析文章内容失败: {e}')
        
        return content_data

    def crawl_article_page(self, url: str) -> Optional[Dict[str, Any]]:
        """
        爬取单个文章页面

        Args:
            url: 文章URL

        Returns:
            文章数据
        """
        logger.info(f'开始爬取文章页面: {url}')
        
        html = self._make_request(url)
        if not html:
            logger.error(f'无法获取文章页面内容: {url}')
            return None
        
        # 解析文章内容
        article_data = self.parse_zx_article_content(html)
        article_data['url'] = url
        article_data['crawl_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        return article_data

    def crawl_all_articles(self, target_issue: Optional[str] = None) -> Dict[str, Any]:
        """
        爬取所有符合条件的文章

        Args:
            target_issue: 目标期号，如果为None则爬取所有文章

        Returns:
            爬取结果
        """
        logger.info('=' * 80)
        logger.info('开始爬取所有文章')
        logger.info('=' * 80)
        
        result = {
            'crawl_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'source_url': self.pl5_zjtj_url,
            'target_issue': target_issue,
            'total_links': 0,
            'filtered_links': 0,
            'successful_articles': 0,
            'articles': []
        }
        
        # 1. 爬取主页面
        logger.info('步骤1：爬取主页面...')
        main_html = self._make_request(self.pl5_zjtj_url)
        if not main_html:
            logger.error('无法获取主页面内容')
            return result
        
        # 2. 解析超链接
        logger.info('步骤2：解析超链接...')
        all_links = self.parse_zx_list_links(main_html)
        result['total_links'] = len(all_links)
        
        # 3. 根据期号过滤
        logger.info('步骤3：根据期号过滤超链接...')
        filtered_links = self.filter_links_by_issue(all_links, target_issue)
        result['filtered_links'] = len(filtered_links)
        
        if not filtered_links:
            logger.warning('没有找到符合条件的超链接')
            return result
        
        # 4. 爬取文章内容
        logger.info(f'步骤4：开始爬取 {len(filtered_links)} 篇文章...')
        for i, link in enumerate(filtered_links, 1):
            logger.info(f'爬取进度: {i}/{len(filtered_links)} - {link["url"]}')
            
            article_data = self.crawl_article_page(link['url'])
            
            if article_data and article_data.get('content'):
                article_data['link_title'] = link['title']
                article_data['link_url'] = link['url']
                result['articles'].append(article_data)
                result['successful_articles'] += 1
            else:
                logger.warning(f'文章内容为空或解析失败: {link["url"]}')
        
        logger.info('=' * 80)
        logger.info('文章爬取完成')
        logger.info(f'总链接数: {result["total_links"]}')
        logger.info(f'过滤后链接数: {result["filtered_links"]}')
        logger.info(f'成功爬取文章数: {result["successful_articles"]}')
        logger.info('=' * 80)
        
        return result

    def _parse_expert_recommendations(self, html: str) -> List[Dict[str, Any]]:
        """
        解析专家推荐数据

        Args:
            html: 网页HTML内容

        Returns:
            专家推荐列表
        """
        recommendations = []
        
        try:
            soup = BeautifulSoup(html, 'html.parser')
            
            expert_containers = soup.find_all('div', class_=re.compile(r'expert|recommend|zjtj'))
            
            for container in expert_containers:
                try:
                    expert_name = container.find('div', class_=re.compile(r'name|author'))
                    if expert_name:
                        expert_name = expert_name.get_text(strip=True)
                    else:
                        expert_name = container.find('span', class_=re.compile(r'name|author'))
                        expert_name = expert_name.get_text(strip=True) if expert_name else '未知专家'
                    
                    forecast_numbers = container.find_all('span', class_=re.compile(r'number|ball|num'))
                    numbers = []
                    for num in forecast_numbers:
                        text = num.get_text(strip=True)
                        if re.match(r'^\d+$', text) and len(text) <= 5:
                            numbers.append(text)
                    
                    confidence = container.find('div', class_=re.compile(r'confidence|rate|准确率'))
                    confidence = confidence.get_text(strip=True) if confidence else '未知'
                    
                    analysis = container.find('div', class_=re.compile(r'analysis|content|desc'))
                    analysis = analysis.get_text(strip=True) if analysis else ''
                    
                    if numbers:
                        recommendations.append({
                            'expert_name': expert_name,
                            'forecast_numbers': numbers,
                            'confidence': confidence,
                            'analysis': analysis,
                            'crawl_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        })
                        
                except Exception as e:
                    logger.warning(f'解析单个专家推荐失败: {e}')
                    continue
            
            if not recommendations:
                logger.info('未找到专家推荐数据，尝试其他解析方式')
                tables = soup.find_all('table')
                for table in tables:
                    try:
                        rows = table.find_all('tr')
                        for row in rows[1:]:
                            cols = row.find_all('td')
                            if len(cols) >= 3:
                                expert_name = cols[0].get_text(strip=True) if cols[0] else '未知专家'
                                forecast = cols[1].get_text(strip=True) if cols[1] else ''
                                numbers = re.findall(r'\d{1,5}', forecast)
                                confidence = cols[2].get_text(strip=True) if len(cols) > 2 else '未知'
                                
                                if numbers:
                                    recommendations.append({
                                        'expert_name': expert_name,
                                        'forecast_numbers': numbers,
                                        'confidence': confidence,
                                        'analysis': '',
                                        'crawl_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                                    })
                    except Exception as e:
                        logger.warning(f'解析表格失败: {e}')
            
            logger.info(f'解析到 {len(recommendations)} 条专家推荐数据')
            
        except Exception as e:
            logger.error(f'解析专家推荐数据失败: {e}')
        
        return recommendations

    def _parse_trend_statistics(self, html: str) -> Dict[str, Any]:
        """
        解析走势统计数据

        Args:
            html: 网页HTML内容

        Returns:
            走势统计数据
        """
        statistics = {
            'wanwei': [],
            'qianwei': [],
            'baiwei': [],
            'shiwei': [],
            'gewei': [],
            'overall': {}
        }
        
        try:
            soup = BeautifulSoup(html, 'html.parser')
            
            trend_tables = soup.find_all('table', class_=re.compile(r'trend|走势|统计'))
            
            for table in trend_tables:
                try:
                    caption = table.find('caption')
                    table_name = caption.get_text(strip=True) if caption else ''
                    
                    rows = table.find_all('tr')
                    if len(rows) < 2:
                        continue
                    
                    headers = [th.get_text(strip=True) for th in rows[0].find_all('th')]
                    
                    for row in rows[1:]:
                        cols = row.find_all('td')
                        if len(cols) != len(headers):
                            continue
                        
                        row_data = dict(zip(headers, [col.get_text(strip=True) for col in cols]))
                        
                        if '万' in table_name or '万位' in table_name:
                            statistics['wanwei'].append(row_data)
                        elif '千' in table_name or '千位' in table_name:
                            statistics['qianwei'].append(row_data)
                        elif '百' in table_name or '百位' in table_name:
                            statistics['baiwei'].append(row_data)
                        elif '十' in table_name or '十位' in table_name:
                            statistics['shiwei'].append(row_data)
                        elif '个' in table_name or '个位' in table_name:
                            statistics['gewei'].append(row_data)
                        else:
                            statistics['overall'][table_name] = statistics['overall'].get(table_name, []) + [row_data]
                            
                except Exception as e:
                    logger.warning(f'解析走势表格失败: {e}')
                    continue
            
            if not any(statistics[k] for k in ['wanwei', 'qianwei', 'baiwei', 'shiwei', 'gewei']):
                logger.info('尝试提取页面中的号码走势数据')
                number_blocks = soup.find_all('div', class_=re.compile(r'number|ball|digit'))
                for block in number_blocks:
                    try:
                        text = block.get_text(strip=True)
                        match = re.match(r'(\d{1,5})期?\s*(\d)', text)
                        if match:
                            issue = match.group(1)
                            digit = match.group(2)
                            if len(issue) == 5:
                                statistics['overall']['raw_data'] = statistics['overall'].get('raw_data', []) + [{
                                    'issue': issue,
                                    'digit': digit
                                }]
                    except Exception as e:
                        continue
            
            logger.info(f'解析走势统计完成：万位{len(statistics["wanwei"])}条, '
                       f'千位{len(statistics["qianwei"])}条, '
                       f'百位{len(statistics["baiwei"])}条, '
                       f'十位{len(statistics["shiwei"])}条, '
                       f'个位{len(statistics["gewei"])}条')
            
        except Exception as e:
            logger.error(f'解析走势统计数据失败: {e}')
        
        return statistics

    def _parse_current_issue(self, html: str) -> Optional[str]:
        """
        解析当前期号

        Args:
            html: 网页HTML内容

        Returns:
            当前期号，失败返回None
        """
        try:
            soup = BeautifulSoup(html, 'html.parser')
            
            issue_pattern = re.compile(r'(\d{5,8})期')
            
            issue_text = soup.find(text=issue_pattern)
            if issue_text:
                match = issue_pattern.search(issue_text)
                if match:
                    return match.group(1)
            
            issue_tags = soup.find_all('span', class_=re.compile(r'issue|期号'))
            for tag in issue_tags:
                match = issue_pattern.search(tag.get_text())
                if match:
                    return match.group(1)
            
            logger.info('未解析到当前期号')
            return None
            
        except Exception as e:
            logger.error(f'解析当前期号失败: {e}')
            return None

    def crawl_all_data(self) -> Dict[str, Any]:
        """
        爬取所有数据

        Returns:
            完整爬取数据
        """
        logger.info('=' * 60)
        logger.info('开始爬取亿点牛排列5数据')
        logger.info('=' * 60)
        
        html = self._make_request(self.pl5_zjtj_url)
        if not html:
            logger.error('无法获取网页内容')
            return {}
        
        result = {
            'crawl_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'source_url': self.pl5_zjtj_url,
            'current_issue': self._parse_current_issue(html),
            'expert_recommendations': self._parse_expert_recommendations(html),
            'trend_statistics': self._parse_trend_statistics(html)
        }
        
        logger.info('=' * 60)
        logger.info('爬取完成')
        logger.info(f'当前期号: {result["current_issue"]}')
        logger.info(f'专家推荐数量: {len(result["expert_recommendations"])}')
        logger.info('=' * 60)
        
        return result

    def save_to_file(self, data: Dict[str, Any], filename: Optional[str] = None) -> str:
        """
        保存数据到文件

        Args:
            data: 爬取数据
            filename: 文件名，默认为自动生成

        Returns:
            保存的文件路径
        """
        if not filename:
            filename = f'ydniu_pl5_data_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
        
        os.makedirs('data', exist_ok=True)
        filepath = os.path.join('data', filename)
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            logger.info(f'数据已保存到: {filepath}')
            return filepath
            
        except Exception as e:
            logger.error(f'保存数据失败: {e}')
            return ''


if __name__ == '__main__':
    spider = YDNiuSpider()
    data = spider.crawl_all_data()
    if data:
        spider.save_to_file(data)
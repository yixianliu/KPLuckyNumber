"""
亿点牛网站排列5数据爬取模块

爬取 https://www.ydniu.com/info/pl5/zjtj/ 的专家推荐文章和走势统计数据

核心功能：
1. 分页爬取文章链接（crawl_with_pagination，最多5页）
2. 按期号过滤文章（filter_links_by_issue）
3. 解析文章内容（parse_zx_article_content → zx_article 区块）
4. 批量爬取文章（crawl_all_articles → 统一的批处理入口）

调用路径：
    ArticleAnalyzer / ArticleProcessor → YDNiuSpider.crawl_all_articles()
                                      → YDNiuSpider.crawl_article_page()

爬取策略：
- 请求间隔: 2秒（request_interval），避免对网站造成过大负载
- 重试机制: 最多3次（max_retries），间隔5秒（retry_delay）
- 解析策略: 3层降级（zx_list_subbox_left → pl5相关链接 → list/article容器）
- 断点续爬: 支持按max_articles限制数量，按target_issue按需过滤

技术要求：
- 设置合理的请求频率限制，避免对目标网站造成过大负载
- 数据提取具备容错机制，处理可能的网页结构变化
- 支持分页爬取和期号过滤
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

    爬取目标: https://www.ydniu.com/info/pl5/zjtj/（排列5专家推荐板块）

    主要方法:
    - crawl_all_articles(): 批量爬取文章（支持分页+期号过滤+数量限制）【主入口】
    - crawl_article_page(): 爬取单篇文章详情
    - crawl_with_pagination(): 分页获取文章链接列表（最多5页）
    - filter_links_by_issue(): 按期号过滤文章链接
    - parse_zx_list_links(): 解析列表页中的所有文章链接（3层降级策略）
    - parse_zx_article_content(): 解析文章详情页的 zx_article 内容区块

    配置:
    - request_interval: 请求间隔2秒
    - max_retries: 最大重试3次
    - retry_delay: 重试延迟5秒

    调用方:
    - ArticleAnalyzer: 调用 crawl_all_articles() / crawl_article_page()
    - ArticleProcessor: 调用 crawl_article_page()
    """

    def __init__(self):
        """初始化爬虫：设置目标URL、HTTP请求头和频率控制参数"""
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

        self.request_interval = 2  # 请求间隔（秒）
        self.max_retries = 3  # 最大重试次数
        self.retry_delay = 5  # 重试等待时间（秒）

    def _make_request(self, url: str, method: str = 'GET', data: Optional[Dict] = None) -> Optional[str]:
        """
        发起HTTP请求（带重试和频率限制）

        每次请求后自动sleep(request_interval)秒以确保频率控制。
        若请求失败，等待retry_delay秒后重试，最多max_retries次。

        Args:
            url: 请求URL
            method: HTTP方法（GET/POST）
            data: POST请求的表单数据

        Returns:
            响应HTML文本，失败返回None
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
        解析列表页中所有文章超链接（3层降级策略）

        策略1: 查找 class='zx_list_subbox_left' 容器中的 <a> 标签
        策略2: 查找所有包含 pl5/排列5 关键词的 <a> 标签（href或text匹配）
        策略3: 查找 class 含 list/article/content 容器中 /info/ 或 /article/ 路径的链接
        最后: 对URL去重（seen_urls集合）

        Args:
            html: 列表页HTML

        Returns:
            去重后的 [{url, title, crawl_time}, ...] 列表
        """
        links = []

        try:
            soup = BeautifulSoup(html, 'html.parser')

            # 策略1：查找所有class为"zx_list_subbox_left"的元素
            list_boxes = soup.find_all('div', class_='zx_list_subbox_left')
            logger.info(f'策略1 - 找到 {len(list_boxes)} 个zx_list_subbox_left元素')

            for box in list_boxes:
                a_tags = box.find_all('a', href=True)
                for a_tag in a_tags:
                    links = self._add_link(links, a_tag)

            # 策略2：查找所有包含"pl5"或"排列5"相关的链接
            all_a_tags = soup.find_all('a', href=True)
            logger.info(f'策略2 - 找到 {len(all_a_tags)} 个超链接')

            for a_tag in all_a_tags:
                href = a_tag.get('href', '')
                text = a_tag.get_text(strip=True)

                if ('pl5' in href.lower() or '排列5' in text or
                        '/info/pl5/' in href or '/article/pl5/' in href):
                    links = self._add_link(links, a_tag)

            # 策略3：查找class包含"list"或"article"的容器下的链接
            article_containers = soup.find_all('div', class_=re.compile(r'list|article|content'))
            logger.info(f'策略3 - 找到 {len(article_containers)} 个list/article容器')

            for container in article_containers:
                a_tags = container.find_all('a', href=True)
                for a_tag in a_tags:
                    href = a_tag.get('href', '')
                    if '/info/' in href or '/article/' in href:
                        links = self._add_link(links, a_tag)

            # 去重
            seen_urls = set()
            unique_links = []
            for link in links:
                if link['url'] not in seen_urls:
                    seen_urls.add(link['url'])
                    unique_links.append(link)

            links = unique_links
            logger.info(f'去重后解析到 {len(links)} 个超链接')

        except Exception as e:
            logger.error(f'解析超链接失败: {e}')

        return links

    def _add_link(self, links: List[Dict[str, Any]], a_tag) -> List[Dict[str, Any]]:
        """
        添加链接到列表，处理URL补全

        Args:
            links: 当前链接列表
            a_tag: 超链接标签

        Returns:
            更新后的链接列表
        """
        url = a_tag.get('href', '')
        title = a_tag.get_text(strip=True)

        if not url or not title:
            return links

        if not url.startswith('http'):
            if url.startswith('/'):
                url = self.base_url + url
            else:
                url = self.base_url + '/' + url

        links.append({
            'url': url,
            'title': title,
            'crawl_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })

        return links

    def crawl_with_pagination(self, max_pages: int = 5) -> List[Dict[str, Any]]:
        """
        分页爬取文章列表（支持自动终止）

        分页URL格式:
        - 第1页: {pl5_zjtj_url}（即 https://www.ydniu.com/info/pl5/zjtj/）
        - 第N页: {pl5_zjtj_url}index_{N}.html

        终止条件:
        1. 某页爬取失败 → 停止
        2. 某页链接少于5个 → 可能已到最后一页，停止
        3. 达到 max_pages 页 → 停止

        Args:
            max_pages: 最大爬取页数（默认5页）

        Returns:
            所有页面去重后的链接列表 [{url, title, crawl_time}, ...]
        """
        all_links = []

        for page in range(1, max_pages + 1):
            if page == 1:
                url = self.pl5_zjtj_url
            else:
                url = f'{self.pl5_zjtj_url}index_{page}.html'

            logger.info(f'爬取第 {page} 页: {url}')

            html = self._make_request(url)
            if not html:
                logger.warning(f'第 {page} 页爬取失败，停止分页')
                break

            page_links = self.parse_zx_list_links(html)
            all_links.extend(page_links)
            logger.info(f'第 {page} 页解析到 {len(page_links)} 个链接')

            if len(page_links) < 5:
                logger.info(f'第 {page} 页链接少于5个，可能已到最后一页')
                break

        # 去重
        seen_urls = set()
        unique_links = []
        for link in all_links:
            if link['url'] not in seen_urls:
                seen_urls.add(link['url'])
                unique_links.append(link)

        logger.info(f'分页爬取完成，共获取 {len(unique_links)} 个唯一链接')
        return unique_links

    def filter_links_by_issue(self, links: List[Dict[str, Any]], target_issue: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        按期号过滤文章链接列表

        过滤逻辑:
        1. 若 target_issue 为 None → 返回全部链接
        2. 检查 URL 或 title 是否包含期号字符串
        3. 用正则 r'(\\d{5,8})期' 从 URL 和 title 中提取期号并比对

        Args:
            links: 文章链接列表
            target_issue: 目标期号（如"2026165"），None表示不过滤

        Returns:
            过滤后的链接列表
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
        解析文章详情页的 zx_article 内容区块

        提取字段:
        - title: 从 <h1>/<h2>/<h3> 或 <title> 标签提取
        - content: zx_article 区块的纯文本（移除script/style标签）
        - author: class含author/writer的元素文本
        - publish_time: class含time/date/publish的元素文本
        - tags: class含tag/label的<a>标签文本列表
        - raw_html: zx_article的原始HTML（调试用）

        降级策略: 若未找到 zx_article，搜索 class 含 article/content/detail 的容器，
                取第一个文本长度>100的作为内容

        Args:
            html: 文章页HTML

        Returns:
            {title, content, author, publish_time, tags, raw_html}
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
        爬取单篇文章详情页

        流程: _make_request(url) → parse_zx_article_content(html) → 附加 url 和 crawl_time

        Args:
            url: 文章完整URL

        Returns:
            {title, content, author, publish_time, tags, raw_html, url, crawl_time}，
            失败返回None
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

    def crawl_all_articles(self, target_issue: Optional[str] = None,
                           max_articles: int = 30) -> Dict[str, Any]:
        """
        批量爬取所有符合条件的文章（主入口方法）

        流程:
        步骤1: 分页爬取链接 (crawl_with_pagination, max_pages=5)
        步骤2: 按期号过滤 (filter_links_by_issue)
        步骤3: 限制数量 (截取前max_articles篇)
        步骤4: 逐篇爬取详情 (crawl_article_page)

        调用方:
        - ArticleAnalyzer.analyze_article_workflow()
        - ArticleAnalyzer.save_all_articles_bulk_to_redis()

        Args:
            target_issue: 目标期号（如"2026165"），None则爬取全部最新文章
            max_articles: 最大爬取文章数（默认30）

        Returns:
            {
                crawl_time: 爬取时间,
                source_url: 源URL,
                target_issue: 目标期号,
                total_links: 总链接数,
                filtered_links: 过滤后链接数,
                successful_articles: 成功爬取文章数,
                articles: [{每篇文章的解析结果}, ...]
            }
        """
        logger.info('=' * 80)
        logger.info('开始爬取所有文章')
        logger.info(f'目标期号: {target_issue}, 最大文章数: {max_articles}')
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

        # 1. 分页爬取链接
        logger.info('步骤1：分页爬取文章链接...')
        all_links = self.crawl_with_pagination(max_pages=5)
        result['total_links'] = len(all_links)

        if not all_links:
            logger.warning('未获取到任何链接')
            return result

        # 2. 根据期号过滤
        logger.info('步骤2：根据期号过滤超链接...')
        filtered_links = self.filter_links_by_issue(all_links, target_issue)
        result['filtered_links'] = len(filtered_links)

        if not filtered_links:
            logger.warning('没有找到符合条件的超链接，使用全部链接')
            filtered_links = all_links

        # 3. 限制数量
        filtered_links = filtered_links[:max_articles]
        logger.info(f'步骤3：准备爬取 {len(filtered_links)} 篇文章...')

        # 4. 爬取文章内容
        for i, link in enumerate(filtered_links, 1):
            logger.info(f'爬取进度: {i}/{len(filtered_links)} - {link["url"]}')

            article_data = self.crawl_article_page(link['url'])

            if article_data and article_data.get('content'):
                article_data['link_title'] = link['title']
                article_data['link_url'] = link['url']
                article_data['article_index'] = i
                result['articles'].append(article_data)
                result['successful_articles'] += 1

                if result['successful_articles'] >= max_articles:
                    logger.info(f'已达到最大文章数 {max_articles}，停止爬取')
                    break
            else:
                logger.warning(f'文章内容为空或解析失败: {link["url"]}')

        logger.info('=' * 80)
        logger.info('文章爬取完成')
        logger.info(f'总链接数: {result["total_links"]}')
        logger.info(f'过滤后链接数: {result["filtered_links"]}')
        logger.info(f'成功爬取文章数: {result["successful_articles"]}')
        logger.info('=' * 80)

        return result

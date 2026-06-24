#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
测试文章爬取功能
"""

from modules.ydniu_spider import YDNiuSpider

def test_parse_links():
    """测试解析超链接"""
    print('=' * 80)
    print('测试解析超链接')
    print('=' * 80)

    spider = YDNiuSpider()

    # 爬取主页面
    html = spider._make_request(spider.pl5_zjtj_url)

    if not html:
        print('无法获取主页面内容')
        return

    # 解析超链接
    links = spider.parse_zx_list_links(html)

    print(f'\n解析到 {len(links)} 个超链接')

    if links:
        print('\n前5个链接：')
        for i, link in enumerate(links[:5], 1):
            print(f'{i}. {link["title"][:60]}')
            print(f'   URL: {link["url"]}')
            print()

def test_crawl_articles():
    """测试爬取文章"""
    print('=' * 80)
    print('测试爬取文章')
    print('=' * 80)

    spider = YDNiuSpider()

    # 爬取所有文章
    result = spider.crawl_all_articles(target_issue=None)

    print(f'\n总链接数: {result["total_links"]}')
    print(f'过滤后链接数: {result["filtered_links"]}')
    print(f'成功爬取文章数: {result["successful_articles"]}')

    if result['articles']:
        print('\n第一篇文章信息：')
        article = result['articles'][0]
        print(f'标题: {article.get("title", "未知")}')
        print(f'作者: {article.get("author", "未知")}')
        print(f'发布时间: {article.get("publish_time", "未知")}')
        print(f'内容长度: {len(article.get("content", ""))} 字符')
        print(f'\n内容预览: {article.get("content", "")[:200]}...')

if __name__ == '__main__':
    test_parse_links()
    print('\n' + '=' * 80 + '\n')
    test_crawl_articles()
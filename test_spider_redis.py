"""
测试爬虫与Redis集成
"""
import sys
import os
import json
import re
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.ydniu_spider import YDNiuSpider
from modules.redis_client import RedisClient
import json

def test_spider():
    """测试爬虫"""
    print("=" * 60)
    print("测试1: 爬取文章并分析过滤逻辑")
    print("=" * 60)

    spider = YDNiuSpider()

    # 爬取主页面
    html = spider._make_request(spider.pl5_zjtj_url)
    if not html:
        print("无法获取主页面内容")
        return

    # 解析链接
    all_links = spider.parse_zx_list_links(html)
    print(f"\n原始解析到的链接数: {len(all_links)}")

    if all_links:
        print("\n前5个链接示例:")
        for i, link in enumerate(all_links[:5]):
            print(f"  {i+1}. 标题: {link['title'][:50]}...")
            print(f"      URL: {link['url'][:80]}...")

    # 测试期号过滤
    print("\n" + "-" * 40)
    print("测试过滤功能（使用常见期号格式）")
    print("-" * 40)

    # 提取可能的期号
    import re
    sample_links_with_issue = []
    for link in all_links:
        match = re.search(r'(\d{5,8})', link['title'] + link['url'])
        if match:
            issue = match.group(1)
            if len(issue) >= 6:  # 期号通常是6-7位
                sample_links_with_issue.append((issue, link))
                break

    if sample_links_with_issue:
        sample_issue, sample_link = sample_links_with_issue[0]
        print(f"\n提取到的示例期号: {sample_issue}")
        print(f"对应链接标题: {sample_link['title']}")

        # 测试过滤
        filtered = spider.filter_links_by_issue(all_links, sample_issue)
        print(f"过滤后链接数: {len(filtered)}")

    return all_links

def test_redis_connection():
    """测试Redis连接"""
    print("\n" + "=" * 60)
    print("测试2: Redis连接")
    print("=" * 60)

    redis_client = RedisClient()

    if redis_client.is_connected():
        print("Redis连接成功")
        print(f"键前缀: {redis_client.get_key_prefix()}")

        # 查看现有键
        keys = redis_client.client.keys(f"{redis_client.get_key_prefix()}*")
        print(f"现有键数量: {len(keys)}")
        if keys:
            print("键列表:")
            for key in keys[:10]:
                print(f"  - {key}")
        return True
    else:
        print("Redis连接失败")
        return False

def test_save_article_to_redis(links):
    """测试保存文章数据到Redis"""
    print("\n" + "=" * 60)
    print("测试3: 保存文章数据到Redis")
    print("=" * 60)

    if not links:
        print("没有链接可供测试")
        return

    spider = YDNiuSpider()
    redis_client = RedisClient()

    if not redis_client.is_connected():
        print("Redis未连接，跳过存储测试")
        return

    # 选择一个链接进行测试
    test_link = links[0]
    print(f"\n测试链接: {test_link['title']}")
    print(f"URL: {test_link['url']}")

    # 爬取文章内容
    article_data = spider.crawl_article_page(test_link['url'])

    if article_data and article_data.get('content'):
        print(f"\n成功获取文章内容")
        print(f"标题: {article_data.get('title', '未知')}")
        print(f"内容长度: {len(article_data.get('content', ''))} 字符")

        # 提取期号（使用顶部已导入的re模块）
        issue = None
        # 尝试从标题提取
        title_match = re.search(r'(\d{5,8})', article_data.get('title', ''))
        if title_match:
            issue = title_match.group(1)

        if not issue:
            # 尝试从URL提取
            url_match = re.search(r'(\d{5,8})', test_link['url'])
            if url_match:
                issue = url_match.group(1)

        if not issue:
            # 使用当前日期生成临时期号
            issue = datetime.now().strftime('%Y%m%d')

        print(f"提取的期号: {issue}")

        # 保存到Redis
        test_data = {
            'issue': issue,
            'article_data': article_data,
            'ai_analysis': {},
            'save_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }

        key = f'{redis_client.get_key_prefix()}article_analysis:{issue}'
        redis_client.client.setex(key, 86400 * 7, json.dumps(test_data, ensure_ascii=False))

        print(f"\n数据已保存到Redis")
        print(f"键名: {key}")
        print(f"过期时间: 7天")

        # 验证保存
        saved_data = redis_client.client.get(key)
        if saved_data:
            print("验证保存成功: 数据可以正常读取")
        else:
            print("验证失败: 数据无法读取")

    else:
        print("获取文章内容失败")

if __name__ == '__main__':
    print("开始测试爬虫与Redis集成")
    print("=" * 60)

    # 测试1: 爬虫
    links = test_spider()

    # 测试2: Redis连接
    redis_ok = test_redis_connection()

    # 测试3: 保存到Redis
    if redis_ok:
        test_save_article_to_redis(links)

    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)

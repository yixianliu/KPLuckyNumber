"""检查Redis存储数据"""
from modules.redis_client import RedisClient
import json

r = RedisClient()
keys = r.client.keys('kpluckynumber:pl5:*')
print('Redis键列表:', keys)

for key in keys:
    key_str = key if isinstance(key, str) else key.decode()
    
    # 检查键类型
    key_type = r.client.type(key)
    print(f'\n{key_str} (类型: {key_type}):')
    
    if key_type == 'string':
        data = r.client.get(key)
        if data:
            data_str = data if isinstance(data, str) else data.decode()
            try:
                parsed = json.loads(data_str)
                print(f'  数据类型: {type(parsed).__name__}')
                if isinstance(parsed, dict):
                    print(f'  字段: {list(parsed.keys())[:10]}')
                    if 'issue' in parsed:
                        print(f'  期号: {parsed["issue"]}')
                    if 'article_data' in parsed:
                        art = parsed['article_data']
                        print(f'  文章标题: {art.get("title", "未知")[:50]}...')
                    if 'ai_analysis' in parsed:
                        ai = parsed['ai_analysis']
                        print(f'  AI分析期号: {ai.get("issue_number", "未知")}')
            except:
                print(f'  原始数据: {data_str[:200]}...')
    elif key_type == 'zset':
        # 有序集合，显示成员数量
        members = r.client.zrange(key, 0, -1)
        print(f'  成员数量: {len(members)}')
        print(f'  成员列表: {members[:10]}')
    elif key_type == 'set':
        # 集合
        members = r.client.smembers(key)
        print(f'  成员数量: {len(members)}')
        print(f'  成员列表: {list(members)[:10]}')
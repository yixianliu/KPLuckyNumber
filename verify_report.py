import redis
import json

r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
key = 'kpluckynumber:article:report:b819f598d6432a04'
data = json.loads(r.get(key))

print('=' * 70)
print('Redis数据验证')
print('=' * 70)
print('键名:', key)
print('URL:', data['url'])
print('报告长度:', data['report_length'])
print('处理时间:', data['process_time'])
print('过期天数:', data['expire_days'])
print('期号:', data['metadata']['issue'])
print('AI模型:', data['metadata']['ai_model'])
print()
print('报告内容中是否包含换行符:', '\n' in data['report'])
print('报告内容中是否包含回车符:', '\r' in data['report'])
print()
print('报告前500字符预览:')
print('-' * 70)
print(data['report'][:500])
print()
print('=' * 70)
print('验证完成')
print('=' * 70)
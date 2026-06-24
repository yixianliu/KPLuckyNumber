import redis
import json

r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
keys = r.keys('kpluckynumber:pl5:article:*')

print(f'Redis中共有 {len(keys)} 篇文章')
print()

for key in keys:
    if key.endswith(':list'):
        continue
    
    data = json.loads(r.get(key))
    print(f'=' * 70)
    print(f'文章ID: {key}')
    print(f'  期号: {data.get("issue")}')
    print(f'  标题: {data.get("article_data", {}).get("title", "未知")[:60]}')
    print(f'  内容长度: 原始={data.get("content_length", {}).get("raw", 0)}, 清洗后={data.get("content_length", {}).get("clean", 0)}')
    
    # 纯文本预览
    plain = data.get('content_plain', '')
    if plain:
        print(f'  纯文本预览: {plain[:150]}...' if len(plain) > 150 else f'  纯文本预览: {plain}')
    
    # 内容理解
    if 'content_understanding' in data:
        cu = data['content_understanding']
        print(f'  【AI内容理解】')
        print(f'    - 文章类型: {cu.get("article_type")}')
        print(f'    - 专家: {cu.get("expert_name")}')
        pred_und = cu.get('prediction_understanding', {})
        print(f'    - 有预测: {pred_und.get("has_prediction")}')
        print(f'    - 预测类型: {pred_und.get("prediction_type")}')
        qa = cu.get('quality_assessment', {})
        print(f'    - 信息密度: {qa.get("information_density")}')
        print(f'    - 预测完整性: {qa.get("prediction_completeness")}')
        print(f'    - 整体评分: {qa.get("overall_score")}/100')
    else:
        print(f'  内容理解: 无')
    
    # 预测数据
    if 'prediction_data' in data and data['prediction_data']:
        pd = data['prediction_data']
        print(f'  【预测数据】')
        print(f'    - 质量评分: {data.get("quality_score")}')
        print(f'    - 验证状态: {data.get("validation_status")}')
        pred = pd.get('prediction', {})
        for pos in ['wan', 'qian', 'bai', 'shi', 'ge']:
            if pos in pred and pred[pos].get('numbers'):
                nums = pred[pos]['numbers']
                print(f'    - {pos}: {nums}')
    else:
        print(f'  预测数据: 无')
    
    print()

from modules.database_p5 import P5Database
import json

db = P5Database()
db.connect()

report = db.get_latest_ai_report()
if report:
    print('=' * 80)
    print('AI分析报告数据库验证')
    print('=' * 80)
    print(f'报告UUID: {report["report_uuid"]}')
    print(f'报告日期: {report["report_date"]}')
    print(f'数据条数: {report["data_count"]}')
    print(f'最新期号: {report["latest_issue"]}')
    print(f'预测期号: {report["next_issue"]}')
    print(f'报告格式: {report["report_format"]}')
    print(f'报告内容长度: {len(report["report_content"])}')
    print(f'风险提示: {report["risk_warning"]}')
    
    if report.get('recommended_numbers'):
        nums = json.loads(report['recommended_numbers'])
        print('\n推荐号码:')
        for pos, numbers in nums.items():
            pos_name = {'wan': '万位', 'qian': '千位', 'bai': '百位', 'shi': '十位'}.get(pos, pos)
            print(f'  {pos_name}: {numbers}')
    
    if report.get('confidence_scores'):
        scores = json.loads(report['confidence_scores'])
        print('\n置信度分数:')
        for pos, confs in scores.items():
            pos_name = {'wan': '万位', 'qian': '千位', 'bai': '百位', 'shi': '十位'}.get(pos, pos)
            print(f'  {pos_name}: {confs}')
    
    if report.get('probability_stats'):
        stats = json.loads(report['probability_stats'])
        print(f'\n模型版本: {stats.get("model_version", "未知")}')
    
    print('\n' + '=' * 80)
    print('数据库存储验证通过！')
    print('=' * 80)
else:
    print('未找到报告')

db.disconnect()
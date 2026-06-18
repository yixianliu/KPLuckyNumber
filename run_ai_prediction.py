"""
排列5 AI深度预测分析执行脚本

把走势数据、分析数据、预测模块数据全部给到AI，
让AI预测下一期号码，生成分析报告，录入数据库。
"""

import sys
import json
from modules.ai_analyzer import AILotteryAnalyzer


def main():
    print('=== 开始排列5 AI深度预测分析 ===')
    analyzer = AILotteryAnalyzer()

    print('1. 获取数据（含走势数据、预测模块数据）...')
    result = analyzer.analyze_p5()

    if result.get('status') != 'success':
        print('分析失败:', result.get('message'))
        sys.exit(1)

    print('2. AI分析完成，准备保存到数据库...')
    success = analyzer.save_report_to_database(result)

    if success:
        print('3. 报告已保存到数据库')
    else:
        print('3. 保存数据库失败')

    # 打印报告摘要
    analysis = result.get('analysis_result', {})
    combos = analysis.get('recommended_combinations', [])
    print('\n=== AI预测结果摘要 ===')
    print('目标期号:', analysis.get('data_summary', {}).get('latest_issue', ''))
    print('推荐组合:')
    for c in combos[:5]:
        nums = c.get('numbers', [])
        rank = c.get('rank', 0)
        score = c.get('confidence_score', 0)
        print(f'  第{rank}名: {nums} 置信度={score}')

    # 打印关键结论
    conclusions = analysis.get('key_conclusions', [])
    if conclusions:
        print('\n关键结论:')
        for i, conclusion in enumerate(conclusions[:5], 1):
            print(f'  {i}. {conclusion}')

    print('\n=== 流程完成 ===')


if __name__ == '__main__':
    main()

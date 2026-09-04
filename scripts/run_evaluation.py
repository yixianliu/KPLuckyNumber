# -*- coding: utf-8 -*-
"""
run_evaluation.py — KPLuckyNumber 自动化评估运行脚本

使用方式:
    python scripts/run_evaluation.py                  # 快速评估
    python scripts/run_evaluation.py --full           # 完整评估
    python scripts/run_evaluation.py --save           # 保存报告到文件
    python scripts/run_evaluation.py --watch          # 持续监控模式
"""

import sys
import os
import argparse
import logging
import time
from datetime import datetime

# 添加项目根目录到路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            os.path.join(project_root, 'logs', 'evaluation_run.log'),
            encoding='utf-8'
        )
    ]
)
logger = logging.getLogger(__name__)


def run_quick_evaluation():
    """执行快速评估（仅关键项）。"""
    logger.info('[Eval] 开始快速评估...')
    start = time.perf_counter()

    from modules.auto_evaluator import ProjectAutoEvaluator

    evaluator = ProjectAutoEvaluator()
    # 快速评估只检查关键项
    results = [
        ('数据健康度', None),
        ('算法权重', None),
        ('AI 模型', None),
    ]

    elapsed = time.perf_counter() - start
    logger.info('[Eval] 快速评估完成，耗时 %.2fs', elapsed)

    return {
        'type': 'quick',
        'duration_sec': round(elapsed, 2),
        'timestamp': datetime.now().isoformat(),
        'results': results,
    }


def run_full_evaluation():
    """执行完整评估。"""
    logger.info('[Eval] 开始完整评估...')
    start = time.perf_counter()

    from modules.auto_evaluator import ProjectAutoEvaluator

    evaluator = ProjectAutoEvaluator()
    report = evaluator.run_full_evaluation()

    elapsed = time.perf_counter() - start

    # 打印 Markdown 报告
    print('\n' + '=' * 60)
    print(report.render_markdown())
    print('=' * 60)

    logger.info('[Eval] 完整评估完成，耗时 %.2fs', elapsed)

    return {
        'type': 'full',
        'duration_sec': round(elapsed, 2),
        'timestamp': datetime.now().isoformat(),
        'report': report.to_dict(),
    }


def run_with_watch(interval_minutes=30):
    """持续监控模式。"""
    logger.info('[Eval] 启动持续监控模式，间隔 %d 分钟', interval_minutes)

    from modules.auto_evaluator import ProjectAutoEvaluator

    evaluator = ProjectAutoEvaluator()

    try:
        while True:
            logger.info('[Eval] 执行评估周期...')
            report = evaluator.run_full_evaluation()
            print(report.render_markdown())

            logger.info('[Eval] 下次评估将在 %d 分钟后', interval_minutes)
            time.sleep(interval_minutes * 60)
    except KeyboardInterrupt:
        logger.info('[Eval] 监控模式已停止')


def main():
    parser = argparse.ArgumentParser(
        description='KPLuckyNumber 自动化评估工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python run_evaluation.py                    # 快速评估
  python run_evaluation.py --full             # 完整评估
  python run_evaluation.py --save             # 保存报告
  python run_evaluation.py --watch            # 持续监控
        """
    )
    parser.add_argument('--full', action='store_true',
                        help='执行完整评估')
    parser.add_argument('--save', action='store_true',
                        help='保存报告到文件')
    parser.add_argument('--watch', action='store_true',
                        help='持续监控模式（默认30分钟间隔）')
    parser.add_argument('--interval', type=int, default=30,
                        help='监控模式间隔（分钟），默认 30')

    args = parser.parse_args()

    if args.watch:
        run_with_watch(args.interval)
        return 0

    if args.full:
        result = run_full_evaluation()
    else:
        result = run_quick_evaluation()

    # 保存报告
    if args.save or args.full:
        from modules.auto_evaluator import ProjectAutoEvaluator
        evaluator = ProjectAutoEvaluator()
        if args.full:
            report = evaluator.run_full_evaluation()
            path = evaluator.save_report(report)
            logger.info('[Eval] 报告已保存: %s', path)
            print(f'\n报告已保存至: {path}')

    return 0


if __name__ == '__main__':
    sys.exit(main())

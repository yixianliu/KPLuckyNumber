# -*- coding: utf-8 -*-
"""
apply_optimization.py — 一键应用性能优化补丁

使用方式:
    python scripts/apply_optimization.py          # 应用所有优化
    python scripts/apply_optimization.py --dry-run # 预览将要应用的优化
    python scripts/apply_optimization.py --list    # 列出可用优化项
"""

import sys
import os
import argparse
import logging
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
            os.path.join(project_root, 'logs', 'optimization_apply.log'),
            encoding='utf-8'
        )
    ]
)
logger = logging.getLogger(__name__)


def list_patches():
    """列出可用的优化补丁。"""
    patches = [
        {
            'id': 'ai_session_reuse',
            'name': 'AI Session 复用',
            'file': 'ai_analyzer.py',
            'description': '复用 requests.Session 避免 TCP 握手开销',
            'impact': '高',
            'risk': '低',
        },
        {
            'id': 'ai_db_reuse',
            'name': 'AI DB 连接复用',
            'file': 'ai_analyzer.py',
            'description': '复用数据库连接避免每次新建/断开',
            'impact': '中',
            'risk': '低',
        },
        {
            'id': 'cache_key_opt',
            'name': '缓存 Key 指纹优化',
            'file': 'smart_cache.py',
            'description': '_make_key 仅序列化关键指纹字段',
            'impact': '高',
            'risk': '低',
        },
        {
            'id': 'cache_invalidate_idx',
            'name': '缓存失效逆索引',
            'file': 'smart_cache.py',
            'description': 'invalidate 使用逆索引加速 O(1) 查找',
            'impact': '高',
            'risk': '低',
        },
        {
            'id': 'const_unify',
            'name': '常量统一',
            'file': 'evolution_tuner.py + self_evolution.py',
            'description': '统一 ML_EVAL_MIN 和 WF_MAX_TRAIN 常量',
            'impact': '中',
            'risk': '中',
        },
        {
            'id': 'ckpt_atomic_write',
            'name': '检查点原子写入',
            'file': 'self_evolution.py',
            'description': '使用 os.replace 原子替换防止损坏',
            'impact': '中',
            'risk': '低',
        },
    ]
    return patches


def apply_patches(dry_run=False):
    """应用优化补丁。"""
    from modules.optimization_patches import apply_all_patches

    if dry_run:
        logger.info('[DryRun] 预览模式，不实际修改文件')
        patches = list_patches()
        logger.info('以下优化将被应用:')
        for p in patches:
            logger.info('  [%s] %s - %s (影响:%s, 风险:%s)',
                        p['id'], p['name'], p['description'], p['impact'], p['risk'])
        return True

    logger.info('[Apply] 开始应用性能优化补丁...')
    results = apply_all_patches()

    applied = sum(1 for v in results.values() if v)
    total = len(results)

    logger.info('[Apply] 优化完成: %d/%d 个模块已应用', applied, total)

    # 打印详细结果
    for module, success in results.items():
        status = '✓ 成功' if success else '○ 跳过/无需优化'
        logger.info('  [%s] %s', status, module)

    return True


def main():
    parser = argparse.ArgumentParser(
        description='KPLuckyNumber 性能优化补丁应用工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python apply_optimization.py                    # 应用所有优化
  python apply_optimization.py --dry-run          # 预览优化内容
  python apply_optimization.py --list             # 列出可用优化
        """
    )
    parser.add_argument('--dry-run', action='store_true',
                        help='预览模式，不实际修改文件')
    parser.add_argument('--list', action='store_true',
                        help='列出可用优化补丁')

    args = parser.parse_args()

    if args.list:
        patches = list_patches()
        print('\n可用优化补丁:')
        print('=' * 60)
        for p in patches:
            print(f"\n[{p['id']}] {p['name']}")
            print(f"  文件: {p['file']}")
            print(f"  描述: {p['description']}")
            print(f"  影响: {p['impact']}  风险: {p['risk']}")
        print('\n' + '=' * 60)
        print(f'总计: {len(patches)} 个优化补丁可用')
        return 0

    if args.dry_run:
        print('\n[预览模式] 以下优化将被应用:\n')
        apply_patches(dry_run=True)
        return 0

    # 应用优化
    success = apply_patches(dry_run=False)
    if success:
        print('\n优化已成功应用！')
        print('建议: 运行自动化评估验证效果')
        print('  python -m modules.auto_evaluator')
        return 0
    else:
        print('\n优化应用失败，请检查日志')
        return 1


if __name__ == '__main__':
    sys.exit(main())

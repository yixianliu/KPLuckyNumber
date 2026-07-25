#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
opt_daily_predict_verify.py (v3.16 纯新增, 零改封板)

每日预测验证增强脚本 —— 让监控层 drift 真正随真实开奖流动。

背景:
    原 opt_monitor_daily.py 仅爬取开奖数据入库, 不跑预测验证。
    监控层读取 p5_prediction_record 的 verification_status,
    而该表需系统实际跑预测+验证才累积新记录。
    结果: 监控 drift 是静态评估(基于封板时历史验证记录), 不随每日开奖更新。

本脚本:
    1. 刷新最新开奖(crawl_and_save_incremental)
    2. 计算下一期号 next_issue = latest + 1
    3. 调 Pipeline.execute_pipeline(最小集):
       - 内部无条件执行 verify_pending_predictions() 闭合「已开奖的历史 pending」预测
         -> 产生新 verification_status='verified' 记录(监控 drift 新数据)
       - step4 将下一期预测注册为 pending(未来某次运行被闭合)
    4. 保持冻结: include_online_learning=False / include_backtest=False / include_feature_analysis=False
       (避开权重微调/AI/耗时; 所有外部AI调用在已废弃 step1-3, 不触发)

退出码: 0=成功(刷新+预测完成) / 1=致命失败
"""
import sys
import os
import logging

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger('opt_daily_predict_verify')

# --- 路径锚定(B3修复): 向上搜索项目根(modules/+main.py), 注入 sys.path ---
def _find_project_root(_start):
    _cur = os.path.abspath(_start)
    while True:
        if os.path.isdir(os.path.join(_cur, 'modules')) and \
           os.path.isfile(os.path.join(_cur, 'main.py')):
            return _cur
        _p = os.path.dirname(_cur)
        if _p == _cur:
            return os.path.dirname(os.path.abspath(_start))
        _cur = _p
_PROJECT_ROOT = _find_project_root(__file__)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


def _refresh_latest_draw():
    """刷新最新开奖数据入库(异常非致命, 不影响后续预测)。"""
    try:
        from modules.data_fetcher import P5Spider
        P5Spider().crawl_and_save_incremental()
        logger.info('✓ 开奖数据刷新完成')
        return True
    except Exception as e:
        logger.warning(f'⚠ 开奖刷新失败(非致命, 继续预测): {e}')
        return False


def _get_next_issue():
    """从数据库最新期号推算下一期号。"""
    try:
        from modules.database import P5Database
        db = P5Database()
        if not db.connect():
            return None
        try:
            db.cursor.execute(
                'SELECT issue FROM p5_history_data ORDER BY issue DESC LIMIT 1')
            row = db.cursor.fetchone()
        finally:
            db.disconnect()
        if not row:
            return None
        latest = row.get('issue', '')
        return str(int(latest) + 1)
    except Exception as e:
        logger.warning(f'⚠ 推算下一期号失败: {e}')
        return None


def _run_prediction(next_issue):
    """跑预测(闭合历史 pending + 注册下一期 pending), 保持冻结配置。"""
    from modules.pipeline import Pipeline
    pipeline = Pipeline()
    result = pipeline.execute_pipeline(
        target_issue=next_issue,
        data_limit=60,
        include_verification=True,       # 验证学习(本地, 无害)
        include_online_learning=False,   # 保持冻结, 不微调权重
        include_backtest=False,          # 规避回测(需 matplotlib/耗时)
        include_feature_analysis=False,  # 规避特征分析耗时
    )
    return result


def main():
    logger.info('=' * 70)
    logger.info('  排列5 每日预测验证 启动')
    logger.info('=' * 70)

    # 1. 刷新开奖
    _refresh_latest_draw()

    # 2. 推算下一期号
    next_issue = _get_next_issue()
    if not next_issue:
        logger.error('✗ 无法确定下一期号, 退出')
        return 1
    logger.info(f'  目标期号: {next_issue}')

    # 3. 跑预测(闭合历史 pending + 注册下一期 pending)
    try:
        result = _run_prediction(next_issue)
    except Exception as e:
        logger.error(f'✗ 预测执行失败: {e}', exc_info=True)
        return 1

    success = result.get('success', False)
    logger.info(f'  预测主流程: {"✓ 成功" if success else "✗ 失败"}')

    # 4. 验证闭环统计(历史 pending 被闭合为新 verified)
    closed = result.get('verification_closed', {})
    if isinstance(closed, dict):
        scanned = closed.get('total_scanned', 0)
        verified = closed.get('verified_count', 0)
    else:
        scanned = verified = 0
    logger.info(f'  验证闭环: 扫描 {scanned} 条, 本次闭合 {verified} 条历史预测')
    logger.info(f'  下一期 {next_issue} 预测已注册为 pending, 待开奖后由后续运行闭合')

    if not success:
        return 1
    logger.info('✓ 每日预测验证完成')
    return 0


if __name__ == '__main__':
    sys.exit(main())

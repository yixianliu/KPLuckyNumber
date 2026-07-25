"""
排列五AI预测系统主程序（CLI入口）

整合所有模块，提供完整的命令行操作接口。

功能模块：
1. 数据采集与更新 (update)        - 从多源爬取排列5开奖数据并入库
2. AI预测推理 (predict)           - 基于历史数据的统计模型+AI融合预测
3. 历史回测验证 (backtest)        - 滚动回测验证模型命中率
4. 特征工程分析 (analyze)         - 提取频率/遗漏/012路/连号等统计特征
5. 四步流水线分析 (pipeline)       - 推荐！全新四步串行分析：文章爬取→走势分析→专家整合→最终预测
6. 批量文章保存 (save-articles)   - 批量爬取文章并保存到Redis
7. 单篇/批量文章处理 (process-article/process-articles) - 爬取→AI→预处理→Redis存储
8. 命中率统计 (hitrate)           - 查看历史预测命中率统计报告
"""

import sys
import os
import logging
import argparse
from datetime import datetime
from typing import Optional

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/main.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def update_data():
    """
    更新历史数据：爬取最新开奖数据并写入MySQL数据库

    流程:
    1. 初始化P5Spider爬虫，调用fetch_latest_data()获取最新数据
    2. 连接MySQL数据库
    3. 调用batch_insert()批量写入
    4. 返回成功/失败

    Returns:
        bool: 是否更新成功
    """
    logger.info('=' * 80)
    logger.info('开始更新历史数据')
    logger.info('=' * 80)

    try:
        from modules.data_fetcher import P5Spider
        from modules.database import P5Database

        # 初始化爬虫
        spider = P5Spider()

        # 获取最新数据
        logger.info('开始爬取数据...')
        latest_data = spider.fetch_latest_data()

        if not latest_data:
            logger.warning('未获取到新数据')
            return False

        logger.info(f'获取到 {len(latest_data)} 期新数据')

        # 连接数据库
        db = P5Database()
        if not db.connect():
            logger.error('数据库连接失败')
            return False

        # 插入数据
        logger.info('开始插入数据...')
        inserted_count = db.batch_insert(latest_data)
        db.disconnect()

        logger.info(f'数据更新完成，插入 {inserted_count} 条记录')
        return True

    except Exception as e:
        logger.error(f'数据更新失败: {e}', exc_info=True)
        return False


def predict_next_issue(model='optimized'):
    """
    预测下一期排列5开奖号码

    流程:
    1. 连接数据库，获取最近200期历史数据
    2. 初始化P5Predictor（统计模型+AI融合）
    3. 调用predict()方法执行预测
    4. 输出各位置推荐号码（Top-3）和推荐组合（Top-5）
    5. 保存预测结果到数据库

    预测器返回结构（必须包含的字段）:
    - fused_probabilities: 融合后的各位置概率分布
    - top_combinations: 推荐号码组合
    - predict_time: 预测时间戳
    - predict_uuid: 预测唯一标识
    - risk_warning: 风险提示

    Args:
        model: 'optimized'(默认, 当前 v3.x 配置) 或 'old'(v2.1 基线配置, 不含贝叶斯推断)

    Returns:
        bool: 是否预测成功
    """
    logger.info('=' * 80)
    logger.info(f'开始预测下一期（{model} 模型）')
    logger.info('=' * 80)

    try:
        # 导入模块
        from modules.predictor import P5Predictor as Predictor, P5PredictorConfig

        from modules.database import P5Database

        # 连接数据库
        logger.info('连接数据库...')
        db = P5Database()
        if not db.connect():
            logger.error('数据库连接失败')
            return False

        # 获取历史数据（取最近 200 期：足够覆盖各算法的统计窗口/回看期，又不至于过慢；魔法数，可调）
        logger.info('加载历史数据...')
        history_data = db.get_history_data(limit=200, order_by='issue DESC')

        if not history_data:
            logger.error('历史数据为空')
            db.disconnect()
            return False

        current_issue = history_data[0].get('issue', '')
        logger.info(f'当前最新期号：{current_issue}')
        logger.info(f'历史数据量：{len(history_data)} 期')

        db.disconnect()

        # 初始化预测器（'old' 使用 v2.1 基线配置, 使 --model 真正生效）
        logger.info('初始化预测器...')
        predictor = Predictor(
            config=P5PredictorConfig.baseline_v21() if model == 'old' else None
        )

        # 执行预测
        logger.info('开始预测...')
        result = predictor.predict(history_data, current_issue)

        if 'error' in result:
            logger.error(f'预测失败: {result["error"]}')
            return False

        # 输出预测结果
        logger.info('=' * 80)
        logger.info('预测结果')
        logger.info('=' * 80)
        logger.info(f'目标期号：{result["target_issue"]}')
        logger.info(f'预测时间：{result["predict_time"]}')
        logger.info(f'数据样本：{result["data_samples"]} 期')
        logger.info('')

        # 输出各位置推荐
        logger.info('【各位置推荐号码】')
        for pos in range(5):
            pos_name = ['万位', '千位', '百位', '十位', '个位'][pos]
            pos_probs = result['fused_probabilities'][pos]
            sorted_nums = sorted(pos_probs.items(), key=lambda x: x[1], reverse=True)
            top_3 = sorted_nums[:3]

            logger.info(f'{pos_name}:')
            for rank, (num, prob) in enumerate(top_3, 1):
                logger.info(f'  {rank}. 号码{num} (概率: {prob:.2%})')
            logger.info('')

        # 输出推荐组合
        logger.info('【推荐组合（Top-5）】')
        for combo in result['top_combinations'][:5]:
            logger.info(f"{combo['rank']}. {combo['combination']} (置信度: {combo['confidence']:.2f}%)")

        logger.info('')
        logger.info('=' * 80)
        logger.info(result['risk_warning'])
        logger.info('=' * 80)

        # 保存预测结果到数据库 (v3.3: 统一入库 p5_artifact, 不再写本地 JSON 文件)
        from modules.database import P5Database
        save_db = P5Database()
        if save_db.connect():
            save_db.save_artifact(
                artifact_type='prediction',
                data=result,
                issue=result.get('target_issue', ''),
                meta={'model': model, 'data_samples': result.get('data_samples')}
            )
            save_db.disconnect()
            logger.info(f'预测结果已保存(数据库): 期号={result.get("target_issue", "")}')
        else:
            logger.warning('预测结果保存失败: 数据库连接失败')

        return True

    except Exception as e:
        logger.error(f'预测失败: {e}', exc_info=True)
        return False


def run_backtest(mode='compare', start=50, count=50):
    """
    运行历史回测：在历史数据上滚动验证预测模型命中率

    回测原理:
    1. 加载全部历史数据（至少100期）
    2. 从第start期开始，每期用前N期数据作为训练集预测该期
    3. 统计Top-1命中数（第一名推荐命中）、Top-3命中数（前三名中至少一个命中）
    4. 计算综合得分、命中率、完全猜中次数

    模式说明:
    - compare: 对比"优化前"和"优化后"两个预测器实例的性能差异
    - old: 仅测试"优化前"实例
    - new: 仅测试"优化后"实例

    Args:
        mode: 回测模式（compare/old/new）
        start: 回测起始位置（留出前N期作为训练数据，默认50）
        count: 回测期数（默认50）

    Returns:
        bool: 是否回测成功
    """
    logger.info('=' * 80)
    logger.info(f'开始历史回测（模式：{mode}）')
    logger.info('=' * 80)

    try:
        # 导入模块
        from modules.predictor import P5Predictor
        from modules.backtester import Backtester
        from modules.database import P5Database

        # 初始化数据库
        logger.info('连接数据库...')
        db = P5Database()
        if not db.connect():
            logger.error('数据库连接失败')
            return False

        # 获取历史数据
        logger.info('加载历史数据...')
        history_data = db.get_history_data(limit=None, order='ASC')
        db.disconnect()

        if len(history_data) < 100:
            logger.error(f'历史数据不足：需要至少100期，实际{len(history_data)}期')
            return False

        logger.info(f'历史数据加载完成：共{len(history_data)}期')

        # 初始化预测器
        logger.info('初始化预测器...')
        # 回测对比模式下，old=基线(v2.1, 不含贝叶斯/自适应), new=当前配置, 使对比真正有意义
        from modules.predictor import P5PredictorConfig
        old_predictor = P5Predictor(config=P5PredictorConfig.baseline_v21())
        new_predictor = P5Predictor()

        # 初始化回测引擎
        backtest_engine = Backtester(old_predictor, db)

        logger.info(f'回测配置：起始位置={start}，测试期数={count}')

        # 根据模式执行回测
        if mode == 'compare':
            # 对比新旧模型
            logger.info('开始模型对比测试...')
            comparison_result = backtest_engine.compare_models(
                old_predictor,
                new_predictor,
                start_index=start,
                test_count=count
            )

            if comparison_result.get('status') != 'success':
                logger.error('模型对比失败')
                return False

            # 生成对比报告
            logger.info('生成对比报告...')
            comparison_report_path = backtest_engine.generate_comparison_report(comparison_result)
            logger.info(f'对比报告已保存：{comparison_report_path}')

            # 输出关键指标
            logger.info('=' * 80)
            logger.info('回测对比完成，关键指标如下：')
            logger.info('=' * 80)

            improvements = comparison_result.get('improvements', {})

            if 'avg_overall_score' in improvements:
                imp = improvements['avg_overall_score']
                logger.info(f'综合得分：{imp["old"]:.2f} → {imp["new"]:.2f} (改善: {imp["improvement"]:.2f}, {imp["improvement_rate"]:.2f}%)')

            if 'avg_top1_hit_rate' in improvements:
                imp = improvements['avg_top1_hit_rate']
                logger.info(f'Top-1命中率：{imp["old"]:.2f}% → {imp["new"]:.2f}% (改善: {imp["improvement"]:.2f}%, {imp["improvement_rate"]:.2f}%)')

            if 'avg_top3_hit_rate' in improvements:
                imp = improvements['avg_top3_hit_rate']
                logger.info(f'Top-3命中率：{imp["old"]:.2f}% → {imp["new"]:.2f}% (改善: {imp["improvement"]:.2f}%, {imp["improvement_rate"]:.2f}%)')

        elif mode == 'old':
            # 仅测试旧模型
            backtest_engine.predictor = old_predictor
            old_backtest_result = backtest_engine.run_backtest(start, count)

            if old_backtest_result.get('status') == 'success':
                logger.info('生成旧模型回测报告...')
                old_report_path = backtest_engine.generate_backtest_report(old_backtest_result)
                logger.info(f'旧模型回测报告已保存：{old_report_path}')

        elif mode == 'new':
            # 仅测试新模型
            backtest_engine.predictor = new_predictor
            new_backtest_result = backtest_engine.run_backtest(start, count)

            if new_backtest_result.get('status') == 'success':
                logger.info('生成新模型回测报告...')
                new_report_path = backtest_engine.generate_backtest_report(new_backtest_result)
                logger.info(f'新模型回测报告已保存：{new_report_path}')

                # 生成可视化图表
                logger.info('生成可视化图表...')
                viz_path = backtest_engine.visualize_backtest_results(new_backtest_result)
                if viz_path:
                    logger.info(f'可视化图表已保存：{viz_path}')

        logger.info('=' * 80)
        logger.info('详细报告请查看生成的文本文件')
        logger.info('=' * 80)

        return True

    except Exception as e:
        logger.error(f'回测执行失败: {e}', exc_info=True)
        return False




def analyze_features():
    """
    分析历史数据特征：提取全部统计特征并输出报告

    提取的特征类型:
    - 频率特征: 各位置号码出现频次（热号/温号/冷号分类）
    - 012路特征: 各位置号码模3分布比例
    - 连号特征: 连续号码出现统计
    - 重隔号特征: 与上期重复/间隔号码统计
    - 和值与跨度特征: 五位数和值/跨度分布

    输出: 特征分析结果JSON文件（保存到 reports/features/）

    Returns:
        bool: 是否分析成功
    """
    logger.info('=' * 80)
    logger.info('开始分析历史数据特征')
    logger.info('=' * 80)

    try:
        from modules.features import P5Features
        from modules.database import P5Database

        # 连接数据库
        logger.info('连接数据库...')
        db = P5Database()
        if not db.connect():
            logger.error('数据库连接失败')
            return False

        # 获取历史数据
        logger.info('加载历史数据...')
        history_data = db.get_history_data(limit=None, order='ASC')
        db.disconnect()

        if not history_data:
            logger.error('历史数据为空')
            return False

        logger.info(f'历史数据量：{len(history_data)} 期')

        # 初始化特征工程
        logger.info('初始化特征工程...')
        fe = P5Features()

        # 提取所有特征
        logger.info('开始提取特征...')
        features = fe.extract_all_features(history_data)

        # 输出特征摘要
        logger.info('=' * 80)
        logger.info('特征分析结果')
        logger.info('=' * 80)

        # 基础统计特征
        logger.info('【基础统计特征】')
        freq_features = features.get('frequency', {})
        for pos_name in ['万位', '千位', '百位', '十位', '个位']:
            pos_freq = freq_features.get(pos_name, {})
            hot_numbers = pos_freq.get('hot_numbers', [])
            cold_numbers = pos_freq.get('cold_numbers', [])
            logger.info(f'{pos_name}: 热号={hot_numbers}, 冷号={cold_numbers}')

        # 012路特征
        logger.info('')
        logger.info('【012路特征】')
        road_features = features.get('road_012', {})
        for pos_name in ['万位', '千位', '百位', '十位', '个位']:
            pos_road = road_features.get(pos_name, {})
            road_ratios = pos_road.get('road_ratios', {})
            logger.info(f'{pos_name}: 0路={road_ratios.get(0, 0):.2%}, 1路={road_ratios.get(1, 0):.2%}, 2路={road_ratios.get(2, 0):.2%}')

        # 连号特征
        logger.info('')
        logger.info('【连号特征】')
        consecutive_features = features.get('consecutive', {})
        logger.info(f'平均连号数：{consecutive_features.get("avg_consecutive_count", 0):.2f}')
        logger.info(f'最大连号数：{consecutive_features.get("max_consecutive_count", 0)}')

        # 重隔号特征
        logger.info('')
        logger.info('【重隔号特征】')
        repeat_features = features.get('repeat', {})
        logger.info(f'重号率：{repeat_features.get("repeat_rate", 0):.2%}')
        logger.info(f'隔号率：{repeat_features.get("skip_rate", 0):.2%}')

        # 保存特征分析结果到数据库 (v3.3: 统一入库 p5_artifact, 不再写本地 JSON 文件)
        from modules.database import P5Database
        feat_db = P5Database()
        if feat_db.connect():
            feat_db.save_artifact(
                artifact_type='feature_analysis',
                data=features,
                meta={'data_count': len(history_data) if 'history_data' in dir() else None}
            )
            feat_db.disconnect()
            logger.info('')
            logger.info('特征分析结果已保存(数据库)')
        else:
            logger.warning('特征分析结果保存失败: 数据库连接失败')

        return True

    except Exception as e:
        logger.error(f'特征分析失败: {e}', exc_info=True)
        return False






def run_four_step_pipeline(target_issue: Optional[str] = None, data_limit: int = 60) -> bool:
    """
    执行全新的四步流水线分析

    流程:
    步骤1: 专家文章爬取与结构化AI分析 → Redis存储
    步骤2: 走势图数据分析与AI预测 → Redis存储
    步骤3: 专家报告整合分析 → Redis存储
    步骤4: 最终预测结果生成与入库 → MySQL

    Args:
        target_issue: 目标期号，None则自动推算
        data_limit: 历史数据期数限制

    Returns:
        bool: 是否执行成功
    """
    logger.info('=' * 80)
    logger.info('开始执行四步流水线分析')
    logger.info('=' * 80)

    try:
        from modules.pipeline import run_four_step_pipeline as pipeline_func

        result = pipeline_func(target_issue=target_issue, data_limit=data_limit)

        if result.get('success'):
            logger.info('=' * 80)
            logger.info('四步流水线分析完成')
            logger.info('=' * 80)
            logger.info(f'报告UUID: {result.get("report_uuid", "未知")}')
            logger.info(f'总耗时: {result.get("total_duration", 0):.1f}s')

            logger.info('')
            logger.info('【各步骤执行详情】')
            logger.info('-' * 50)
            for stage in result.get('stages', []):
                icon = '✓' if stage['success'] else '✗'
                logger.info(f'  {icon} 步骤{stage["step"]}: {stage["name"]} ({stage["duration"]:.1f}s)')

            final_report = result.get('final_report', {})
            if final_report:
                logger.info('')
                logger.info('【最终预测结果】')
                logger.info('-' * 50)
                prediction = final_report.get('prediction', {})
                for pos_key, pos_name in zip(['wan', 'qian', 'bai', 'shi', 'ge'],
                                              ['万位', '千位', '百位', '十位', '个位']):
                    pos_data = prediction.get(pos_key, {})
                    nums = pos_data.get('numbers', [])
                    conf = pos_data.get('confidence', [])
                    if nums:
                        logger.info(f'  {pos_name}: 号码{nums}, 置信度{conf}')

                combos = final_report.get('recommended_combinations', [])
                if combos:
                    logger.info('')
                    logger.info('  【推荐组合】')
                    for i, combo in enumerate(combos[:5], 1):
                        if isinstance(combo, dict):
                            c = combo.get('combination', '')
                            conf = combo.get('confidence', 0)
                            logger.info(f'    {i}. {c} (置信度: {conf:.2f})')

            logger.info('')
            logger.info(f'  风险提示: {final_report.get("risk_warning", "理性购彩，量力而行")}')
            logger.info('=' * 80)
            return True
        else:
            logger.error(f'四步流水线分析失败: {result.get("error", "未知错误")}')
            if 'stages' in result:
                for stage in result['stages']:
                    if not stage.get('success'):
                        logger.error(f'  失败步骤{stage["step"]}: {stage.get("details", {}).get("error", "未知")}')
            return False

    except Exception as e:
        logger.error(f'四步流水线执行异常: {e}', exc_info=True)
        return False


# ================================================================
# 命中率统计
# ================================================================

def run_verify_pending() -> bool:
    """
    闭合「预测→开奖」验证闭环：对全部已开奖但仍 pending 的预测记录执行验证。

    这是 v3.16 新增的自动化关键能力——让真实预测获得开奖反馈，
    持续喂养贝叶斯验证学习。可独立运行，也被每日定时任务调用。

    Returns:
        bool: 是否执行成功（即使无 pending 记录也返回 True）
    """
    logger.info('=' * 80)
    logger.info('开始闭合验证闭环（verify pending predictions）')
    logger.info('=' * 80)

    try:
        from modules.pipeline import Pipeline

        pipeline = Pipeline()
        result = pipeline.verify_pending_predictions()

        if not result.get('success'):
            logger.error(f'验证闭环失败: {result.get("error")}')
            return False

        logger.info('=' * 80)
        logger.info('验证闭环完成')
        logger.info('=' * 80)
        logger.info(f'  扫描待验证记录: {result.get("total_scanned", 0)} 条')
        logger.info(f'  已验证: {result.get("verified_count", 0)} 条')
        logger.info(f'  跳过: {result.get("skipped", 0)} 条')
        for d in result.get('details', [])[:20]:
            logger.info(
                f'    - 期号 {d.get("issue")}: 命中 {d.get("match_count")}/5 '
                f'(准确率 {d.get("accuracy_rate")}%)'
            )
        return True

    except Exception as e:
        logger.error(f'验证闭环执行异常: {e}', exc_info=True)
        return False


def run_hit_rate_report(days: int = 30, output_file: Optional[str] = None) -> bool:
    """
    运行命中率统计报告
    
    Args:
        days: 最近N天数据
        output_file: 输出文件路径（可选）
    
    Returns:
        是否成功
    """
    try:
        logger.info(f'开始生成命中率统计报告（最近{days}天）')
        
        from modules.database import P5Database
        from modules.hitrate_tracker import HitRateTracker
        
        db = P5Database()
        if not db.connect():
            logger.error('数据库连接失败')
            return False
        
        tracker = HitRateTracker(db)
        report = tracker.generate_hit_rate_report(days=days)
        
        # 打印到控制台
        print('\n')
        print(report)
        print('\n')
        
        # 如果指定了输出文件，保存到文件
        if output_file:
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(report)
            logger.info(f'命中率报告已保存到: {output_file}')
            print(f'报告已保存到: {output_file}')
        
        db.disconnect()
        return True
        
    except Exception as e:
        logger.error(f'命中率统计失败: {e}', exc_info=True)
        return False


def main():
    """
    主函数：解析命令行参数并分发到对应的功能函数

    支持的命令:
    - update: 更新历史数据
    - predict: 预测下一期（--model optimized/old）
    - backtest: 历史回测（--mode compare/old/new, --start, --count）
    - analyze: 分析历史数据特征
    - pipeline: 四步流水线分析（--issue, --limit）
    """
    parser = argparse.ArgumentParser(description='排列五AI预测系统')
    subparsers = parser.add_subparsers(dest='command', help='可用命令')

    # 更新数据命令
    update_parser = subparsers.add_parser('update', help='更新历史数据')

    # 预测命令
    predict_parser = subparsers.add_parser('predict', help='预测下一期')
    predict_parser.add_argument('--model', type=str, default='optimized',
                               choices=['old', 'optimized'],
                               help='使用的模型（old=原始模型，optimized=优化后模型）')

    # 回测命令
    backtest_parser = subparsers.add_parser('backtest', help='历史回测')
    backtest_parser.add_argument('--mode', type=str, default='compare',
                                choices=['compare', 'old', 'new'],
                                help='回测模式（compare=对比新旧模型，old=仅测试旧模型，new=仅测试新模型）')
    backtest_parser.add_argument('--start', type=int, default=50,
                                help='回测起始位置（留出前N期作为训练数据）')
    backtest_parser.add_argument('--count', type=int, default=50,
                                help='回测期数')

    # 特征分析命令
    analyze_parser = subparsers.add_parser('analyze', help='分析历史数据特征')




    # 四步流水线分析命令（推荐）
    pipeline_parser = subparsers.add_parser('pipeline', help='执行四步流水线分析：走势分析→专家整合→最终预测')
    pipeline_parser.add_argument('--issue', type=str, default=None,
                                 help='目标期号（如2026165），不指定则自动推算下一期')
    pipeline_parser.add_argument('--limit', type=int, default=40,
                                 help='历史数据期数限制（默认40期）')

    # 命中率统计命令
    hitrate_parser = subparsers.add_parser('hitrate', help='查看历史预测命中率统计报告')
    hitrate_parser.add_argument('--days', type=int, default=30, dest='days',
                                help='最近N天数据（默认30天）')
    hitrate_parser.add_argument('--output', type=str, default=None, dest='output',
                                help='输出到文件（可选，默认仅打印到控制台）')

    # 验证闭环命令（闭合「预测→开奖」验证，喂养贝叶斯学习）
    verify_parser = subparsers.add_parser('verify', help='闭合验证闭环：对全部已开奖但未验证的预测记录执行验证')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    # 执行对应命令
    if args.command == 'update':
        success = update_data()
    elif args.command == 'predict':
        success = predict_next_issue(args.model)
    elif args.command == 'backtest':
        success = run_backtest(args.mode, args.start, args.count)
    elif args.command == 'analyze':
        success = analyze_features()
    elif args.command == 'pipeline':
        success = run_four_step_pipeline(target_issue=args.issue, data_limit=args.limit)
    elif args.command == 'hitrate':
        success = run_hit_rate_report(args.days, args.output)
    elif args.command == 'verify':
        success = run_verify_pending()
    else:
        logger.error(f'未知命令：{args.command}')
        success = False

    if success:
        logger.info('执行成功')
        sys.exit(0)
    else:
        logger.error('执行失败')
        sys.exit(1)


if __name__ == '__main__':
    main()
"""
排列五AI预测系统主程序（CLI入口）

整合所有模块，提供完整的命令行操作接口。

功能模块：
1. 数据采集与更新 (update)        - 从多源爬取排列5开奖数据并入库
2. AI预测推理 (predict)           - 基于历史数据的统计模型+AI融合预测
3. 历史回测验证 (backtest)        - 滚动回测验证模型命中率
4. 特征工程分析 (analyze)         - 提取频率/遗漏/012路/连号等统计特征
5. ERNIE AI深度分析 (ernie)       - 调用百度ERNIE模型进行深度分析
6. 四步流水线分析 (pipeline)       - 推荐！全新四步串行分析：文章爬取→走势分析→专家整合→最终预测
7. 批量文章保存 (save-articles)   - 批量爬取文章并保存到Redis
8. 单篇/批量文章处理 (process-article/process-articles) - 爬取→AI→预处理→Redis存储

用法示例：
    python main.py update                    # 更新并入库最新开奖
    python main.py predict --model optimized # 运行优化模型预测
    python main.py backtest --mode compare   # 后测对比
    python main.py analyze                   # 分析历史特征并输出报告
    python main.py ernie --limit 30          # ERNIE深度分析
    python main.py pipeline --issue 2026165  # 四步流水线分析（推荐！自动推算下一期）
    python main.py pipeline --issue 2026165 --limit 50  # 指定期数和数据量
    python main.py save-articles --max 100  # 批量爬取文章保存到Redis
    python main.py process-article --url "..." --title "..."  # 处理单篇文章
    python main.py process-articles --max 10 # 批量处理文章
"""

import sys
import os
import logging
import argparse
from datetime import datetime

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


def predict_next_issue(use_optimized=True):
    """
    预测下一期排列5开奖号码

    流程:
    1. 连接数据库，获取最近200期历史数据
    2. 初始化P5Predictor（统计模型+AI融合）
    3. 调用predict()方法执行预测
    4. 输出各位置推荐号码（Top-3）和推荐组合（Top-5）
    5. 保存预测结果JSON到 predictions/ 目录

    预测器返回结构（必须包含的字段）:
    - fused_probabilities: 融合后的各位置概率分布
    - top_combinations: 推荐号码组合
    - predict_time: 预测时间戳
    - predict_uuid: 预测唯一标识
    - risk_warning: 风险提示

    Args:
        use_optimized: 始终使用优化后的模型（旧版P5Predictor已删除）

    Returns:
        bool: 是否预测成功
    """
    logger.info('=' * 80)
    logger.info(f'开始预测下一期（{"优化后" if use_optimized else "原始"}模型）')
    logger.info('=' * 80)

    try:
        # 导入模块
        # 始终使用优化后的预测器（旧版 p5_predictor.py 已删除，功能已整合到优化版）
        from modules.predictor import P5Predictor as Predictor

        from modules.database import P5Database

        # 连接数据库
        logger.info('连接数据库...')
        db = P5Database()
        if not db.connect():
            logger.error('数据库连接失败')
            return False

        # 获取历史数据
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

        # 初始化预测器
        logger.info('初始化预测器...')
        predictor = Predictor()

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

        # 保存预测结果
        os.makedirs('predictions', exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'predictions/prediction_{timestamp}.json'

        import json
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False, default=str)

        logger.info(f'预测结果已保存：{filename}')

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
        # 回测对比模式下，使用两个独立实例模拟"优化前"和"优化后"对比
        # 两者均为优化后的预测器，但可通过配置区分行为
        old_predictor = P5Predictor()
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


def run_ernie_ai_analysis(data_limit=30):
    """
    执行ERNIE AI深度分析：调用百度ERNIE模型对历史数据进行AI分析

    流程:
    1. 初始化AIAnalyzer（封装Qianfan API调用）
    2. 从数据库获取最近data_limit期历史数据
    3. 构造分析prompt，调用ERNIE模型
    4. 解析AI返回的JSON结果
    5. 保存报告到数据库和reports/目录

    Args:
        data_limit: 获取历史数据的期数限制（默认30期）

    Returns:
        bool: 是否分析成功
    """
    logger.info('=' * 80)
    logger.info('开始执行ERNIE AI分析')
    logger.info('=' * 80)

    try:
        from modules.ai_analyzer import AIAnalyzer

        logger.info(f'配置参数：数据期数={data_limit}')

        analyzer = AIAnalyzer()
        result = analyzer.analyze(data_limit=data_limit)

        if result['success']:
            logger.info('=' * 80)
            logger.info('ERNIE AI分析完成')
            logger.info('=' * 80)
            logger.info(f'报告UUID：{result["report_uuid"]}')
            logger.info(f'最新期号：{result["latest_issue"]}')
            logger.info(f'预测期号：{result["next_issue"]}')
            logger.info(f'数据条数：{result["data_count"]}')
            logger.info(f'模型版本：{result["model_version"]}')
            logger.info(f'报告文件：{result["report_file"]}')
            logger.info('')
            logger.info('【报告内容预览】')
            logger.info('-' * 50)
            report_content = result['report']['report_content']
            preview_lines = report_content.split('\n')[:40]
            logger.info('\n'.join(preview_lines))
            logger.info('...')
            logger.info('')
            logger.info(f'风险提示：{result["risk_warning"]}')
            logger.info('=' * 80)

            return True
        else:
            logger.error(f'ERNIE AI分析失败：{result["error"]}')
            return False

    except Exception as e:
        logger.error(f'ERNIE AI分析执行失败：{e}', exc_info=True)
        return False


def run_comprehensive_analysis(data_limit=30):
    """
    执行综合分析（二次深度分析）：整合多源数据进行深度AI分析

    数据源:
    - Redis中的原始文章数据（从ydniu.com爬取的专家推荐）
    - 网络专家数据（文章中的预测信息）
    - AI初步分析数据（第一次AI分析的中间结果）
    - 数据库历史开奖数据

    流程:
    1. 初始化ComprehensiveAnalyzer
    2. 调用analyze_with_multi_sources()整合多源数据
    3. 输出多源数据汇总和预测结果

    Args:
        data_limit: 获取历史数据的期数限制（默认30期）

    Returns:
        bool: 是否分析成功
    """
    logger.info('=' * 80)
    logger.info('开始执行综合分析（二次深度分析）')
    logger.info('=' * 80)

    try:
        from modules.ai_analyzer import ComprehensiveAnalyzer

        logger.info(f'配置参数：数据期数={data_limit}')

        analyzer = ComprehensiveAnalyzer()
        result = analyzer.analyze_with_multi_sources(data_limit=data_limit)

        if result['success']:
            logger.info('=' * 80)
            logger.info('综合分析完成')
            logger.info('=' * 80)
            logger.info(f'报告UUID：{result["report_uuid"]}')
            logger.info(f'最新期号：{result["latest_issue"]}')
            logger.info(f'预测期号：{result["next_issue"]}')
            logger.info(f'数据条数：{result["data_count"]}')
            logger.info(f'模型版本：{result["model_version"]}')
            logger.info(f'报告文件：{result["report_file"]}')
            
            sources = result.get('data_sources', {})
            logger.info('')
            logger.info('【数据源汇总】')
            logger.info('-' * 50)
            logger.info(f'Redis原始数据：{"已加载" if sources.get("redis_raw_data") else "未加载"}')
            logger.info(f'网络专家数据：{sources.get("expert_data", 0)}条')
            logger.info(f'AI初步分析：{"已加载" if sources.get("ai_preliminary") else "未加载"}')
            
            logger.info('')
            logger.info('【报告内容预览】')
            logger.info('-' * 50)
            report_content = result['report']['report_content']
            preview_lines = report_content.split('\n')[:40]
            logger.info('\n'.join(preview_lines))
            logger.info('...')
            logger.info('')
            logger.info(f'风险提示：{result["report"].get("risk_warning", "")}')
            logger.info('=' * 80)

            return True
        else:
            logger.error(f'综合分析失败：{result["error"]}')
            return False

    except Exception as e:
        logger.error(f'综合分析执行失败：{e}', exc_info=True)
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

        # 保存特征分析结果
        os.makedirs('reports/features', exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'reports/features/feature_analysis_{timestamp}.json'

        import json
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(features, f, indent=2, ensure_ascii=False, default=str)

        logger.info('')
        logger.info(f'特征分析结果已保存：{filename}')

        return True

    except Exception as e:
        logger.error(f'特征分析失败: {e}', exc_info=True)
        return False


def run_article_analysis(target_issue=None, data_limit=30):
    """
    执行文章内容分析工作流：完整的6步分析流水线

    步骤:
    步骤1: 爬取文章 - 从ydniu.com获取专家推荐文章
    步骤2: 第一次AI分析 - 结构化整理文章内容
    步骤3: 保存Redis - 将分析结果缓存到Redis（7天过期）
    步骤4: 加载Redis - 从Redis读取缓存数据
    步骤5: 第二次AI分析 - 整合文章分析+历史数据，综合预测
    步骤6: 保存数据库 - 将最终报告写入MySQL

    Args:
        target_issue: 目标期号（如"2026165"），None则爬取最新文章
        data_limit: 获取历史数据的期数限制（默认30期）

    Returns:
        bool: 是否分析成功
    """
    logger.info('=' * 80)
    logger.info('开始执行文章内容分析工作流')
    logger.info('=' * 80)

    try:
        from modules.article_handler import ArticleAnalyzer

        logger.info(f'配置参数：目标期号={target_issue or "最新"}, 数据期数={data_limit}')

        analyzer = ArticleAnalyzer()
        result = analyzer.analyze_article_workflow(target_issue=target_issue, data_limit=data_limit)

        if result['success']:
            logger.info('=' * 80)
            logger.info('文章分析工作流完成')
            logger.info('=' * 80)
            logger.info(f'报告UUID：{result["report_uuid"]}')
            logger.info(f'预测期号：{result["final_report"].get("next_issue", "未知")}')
            
            logger.info('')
            logger.info('【执行步骤】')
            logger.info('-' * 50)
            logger.info(f'步骤1（爬取文章）：{"成功" if result["step1_crawl"] else "失败"}')
            logger.info(f'步骤2（第一次AI分析）：{"成功" if result["step2_first_ai"] else "失败"}')
            logger.info(f'步骤3（保存Redis）：{"成功" if result["step3_redis_save"] else "失败"}')
            logger.info(f'步骤4（加载Redis）：{"成功" if result["step4_redis_load"] else "失败"}')
            logger.info(f'步骤5（第二次AI分析）：{"成功" if result["step5_second_ai"] else "失败"}')
            logger.info(f'步骤6（保存数据库）：{"成功" if result["step6_db_save"] else "失败"}')
            
            logger.info('')
            logger.info('【预测结果】')
            logger.info('-' * 50)
            prediction = result['final_report'].get('prediction', {})
            for pos_name, pos_key in zip(['万位', '千位', '百位', '十位', '个位'], ['wan', 'qian', 'bai', 'shi', 'ge']):
                if prediction.get(pos_key):
                    pos_data = prediction[pos_key]
                    logger.info(f'{pos_name}：{pos_data.get("numbers", [])} (置信度: {pos_data.get("confidence", [])})')
            
            if result['final_report'].get('recommended_combinations'):
                logger.info('')
                logger.info('【推荐组合】')
                for i, combo in enumerate(result['final_report']['recommended_combinations'], 1):
                    logger.info(f'  组合{i}：{combo}')
            
            logger.info('')
            logger.info(f'风险提示：{result["final_report"].get("risk_warning", "")}')
            logger.info('=' * 80)

            return True
        else:
            logger.error(f'文章分析工作流失败：{result["error"]}')
            return False

    except Exception as e:
        logger.error(f'文章分析工作流执行失败：{e}', exc_info=True)
        return False


def run_save_articles_to_redis(target_issue=None, max_articles=100, extract_predictions=True):
    """
    执行批量保存文章到Redis，并可选提取预测数据

    流程:
    1. 爬取最多max_articles篇文章
    2. 可选提取每篇文章中的预测数据（号码推荐等）
    3. 按质量分数（≥0.7为高质量）分类
    4. 将所有文章和预测数据保存到Redis

    Args:
        target_issue: 目标期号（如"2026165"），None则爬取全部文章
        max_articles: 最大处理文章数（默认100）
        extract_predictions: 是否提取预测数据（默认True）

    Returns:
        bool: 是否保存成功
    """
    logger.info('=' * 80)
    logger.info('开始执行批量保存文章到Redis')
    if extract_predictions:
        logger.info('【启用预测数据自动提取】')
    logger.info('=' * 80)

    try:
        from modules.article_handler import ArticleAnalyzer

        logger.info(f'配置参数：目标期号={target_issue or "全部"}, 最大文章数={max_articles}, 预测提取={extract_predictions}')

        analyzer = ArticleAnalyzer()
        # call the bulk-save variant (renamed to avoid method name collision)
        result = analyzer.save_all_articles_bulk_to_redis(
            target_issue=target_issue, 
            max_articles=max_articles,
            extract_predictions=extract_predictions
        )

        if result['success']:
            logger.info('=' * 80)
            logger.info('批量处理文章完成')
            logger.info('=' * 80)
            logger.info(f'总文章数：{result["total_articles"]}')
            logger.info(f'成功保存：{result["saved_articles"]}')
            logger.info(f'失败保存：{result["failed_articles"]}')
            
            if extract_predictions:
                logger.info('')
                logger.info('【预测数据提取统计】')
                logger.info('-' * 50)
                logger.info(f'预测提取成功：{result["extracted_predictions"]}')
                logger.info(f'高质量预测（≥0.7）：{result["high_quality_predictions"]}')
                
                if result['predictions']:
                    logger.info('')
                    logger.info('【高质量预测数据列表】')
                    logger.info('-' * 50)
                    high_quality = [p for p in result['predictions'] if p['quality_score'] >= 0.7]
                    for i, pred in enumerate(high_quality[:10], 1):
                        logger.info(f'{i}. {pred["article_id"]} - 期号:{pred["issue"]} - 质量:{pred["quality_score"]} - {pred["prediction_summary"]}')
                    if len(high_quality) > 10:
                        logger.info(f'... 还有 {len(high_quality) - 10} 条高质量预测')
            
            logger.info('')
            logger.info('【已保存文章列表】')
            logger.info('-' * 50)
            for i, article in enumerate(result['articles'][:10], 1):
                pred_flag = '✓预测' if article.get('has_prediction') else '无预测'
                quality = f'质量:{article.get("quality_score", 0):.2f}' if article.get('has_prediction') else ''
                logger.info(f'{i}. {article["article_id"]} - 期号:{article["issue"]} - {pred_flag} {quality} - {article["title"]}')
            if len(result['articles']) > 10:
                logger.info(f'... 还有 {len(result["articles"]) - 10} 篇文章')
            
            logger.info('=' * 80)

            return True
        else:
            logger.error(f'批量保存文章失败：{result["error"]}')
            return False

    except Exception as e:
        logger.error(f'批量保存文章执行失败：{e}', exc_info=True)
        return False


def run_process_article(url: str, title: str = '') -> bool:
    """
    处理单篇文章：爬取→AI分析→预处理→Redis存储

    完整流程:
    1. 爬取文章内容（从给定URL）
    2. AI分析（调用Qianfan API进行内容结构化）
    3. 预处理（HTML清洗、格式规范化）
    4. Redis存储（key格式: kpluckynumber:pl5:article:{issue}，7天过期）

    Args:
        url: 文章URL
        title: 文章标题（可选，不提供则从页面提取）

    Returns:
        bool: 是否处理成功
    """
    logger.info('=' * 80)
    logger.info('开始处理单篇文章')
    logger.info('=' * 80)

    try:
        from modules.article_handler import ArticleProcessor

        logger.info(f'文章URL: {url}')
        if title:
            logger.info(f'文章标题: {title}')

        processor = ArticleProcessor()
        result = processor.process_article(url, title)

        if result['success']:
            logger.info('=' * 80)
            logger.info('文章处理完成')
            logger.info('=' * 80)
            
            logger.info('【处理步骤】')
            logger.info('-' * 50)
            for step in result['steps']:
                status_icon = '✓' if step['status'] == '成功' else '✗'
                logger.info(f'  {status_icon} {step["step"]}: {step["status"]}')
                if 'content_length' in step:
                    logger.info(f'      内容长度: {step["content_length"]}')
                if 'report_length' in step:
                    logger.info(f'      报告长度: {step["report_length"]}')
                if 'processed_length' in step:
                    logger.info(f'      处理后长度: {step["processed_length"]}')
                if 'key' in step:
                    logger.info(f'      Redis键: {step["key"]}')
            
            logger.info('')
            logger.info('【报告预览】')
            logger.info('-' * 50)
            if result['report']:
                preview = result['report'][:300] + '...' if len(result['report']) > 300 else result['report']
                logger.info(f'{preview}')
            
            logger.info('')
            logger.info(f'Redis键名: {result["redis_key"]}')
            logger.info(f'过期时间: 7天')
            logger.info('=' * 80)

            return True
        else:
            logger.error(f'文章处理失败：{result["error"]}')
            if result['steps']:
                logger.info('【失败步骤】')
                for step in result['steps']:
                    if step['status'] == '失败':
                        logger.info(f'  {step["step"]}: {step.get("error", "未知错误")}')
            return False

    except Exception as e:
        logger.error(f'文章处理执行失败：{e}', exc_info=True)
        return False


def run_four_step_pipeline(target_issue: Optional[str] = None, data_limit: int = 40) -> bool:
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


def run_process_multiple_articles(max_count: int = 10) -> bool:
    """
    批量处理文章：爬取→AI分析→预处理→Redis存储

    流程:
    1. 通过爬虫获取文章URL列表（最多max_count篇）
    2. 逐篇执行爬取→AI分析→预处理→Redis存储
    3. 汇总统计成功/失败数量

    Args:
        max_count: 最大处理文章数（默认10）

    Returns:
        bool: 至少有一篇处理成功则返回True
    """
    logger.info('=' * 80)
    logger.info(f'开始批量处理 {max_count} 篇文章')
    logger.info('=' * 80)

    try:
        from modules.article_handler import ArticleProcessor

        processor = ArticleProcessor()

        # 获取文章URL列表（从爬虫获取）
        logger.info('获取文章列表...')
        if processor.spider:
            crawl_result = processor.spider.crawl_all_articles()
            articles = crawl_result.get('articles', [])[:max_count]
            urls = [article.get('url', article.get('link_url', '')) for article in articles if article.get('url') or article.get('link_url')]
            
            if not urls:
                logger.error('未获取到文章列表')
                return False
            
            logger.info(f'获取到 {len(urls)} 篇文章')
        else:
            logger.error('爬虫模块不可用')
            return False

        # 批量处理
        summary = processor.process_multiple_articles(urls)

        logger.info('=' * 80)
        logger.info('批量处理完成')
        logger.info('=' * 80)
        logger.info(f'总文章数：{summary["total"]}')
        logger.info(f'成功：{summary["success"]}')
        logger.info(f'失败：{summary["failed"]}')

        if summary['reports']:
            logger.info('')
            logger.info('【成功处理的报告】')
            logger.info('-' * 50)
            for i, report in enumerate(summary['reports'], 1):
                logger.info(f'{i}. {report["url"][:60]}...')
                logger.info(f'   Redis键: {report["redis_key"]}')
                logger.info(f'   期号: {report["issue"]}')
                logger.info(f'   报告长度: {report["report_length"]}')

        if summary['errors']:
            logger.info('')
            logger.info('【处理失败的文章】')
            logger.info('-' * 50)
            for i, error in enumerate(summary['errors'], 1):
                logger.info(f'{i}. {error["url"][:60]}...')
                logger.info(f'   错误: {error["error"]}')

        logger.info('=' * 80)

        return summary['success'] > 0

    except Exception as e:
        logger.error(f'批量处理执行失败：{e}', exc_info=True)
        return False


def main():
    """
    主函数：解析命令行参数并分发到对应的功能函数

    支持的命令:
    - update: 更新历史数据
    - predict: 预测下一期（--model optimized/old）
    - backtest: 历史回测（--mode compare/old/new, --start, --count）
    - analyze: 分析历史数据特征
    - ernie: ERNIE AI深度分析（--limit）
    - comprehensive: 综合分析/二次深度分析（--limit）
    - article: 文章内容分析工作流（--issue, --limit）
    - save-articles: 批量爬取文章并保存到Redis（--issue, --max, --no-extract）
    - process-article: 处理单篇文章（--url, --title）
    - process-articles: 批量处理文章（--max）
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

    # ERNIE AI分析命令
    ernie_parser = subparsers.add_parser('ernie', help='执行ERNIE AI深度分析')
    ernie_parser.add_argument('--limit', type=int, default=30,
                              help='获取历史数据的期数限制（默认30期）')

    # 综合分析命令（二次深度分析）
    comprehensive_parser = subparsers.add_parser('comprehensive', help='执行综合分析（二次深度分析）→ 已弃用，请使用 pipeline 命令')
    comprehensive_parser.add_argument('--limit', type=int, default=30,
                                      help='获取历史数据的期数限制（默认30期）')

    # 文章分析命令（已弃用，使用 pipeline 代替）
    article_parser = subparsers.add_parser('article', help='执行文章内容分析工作流 → 已弃用，请使用 pipeline 命令')
    article_parser.add_argument('--issue', type=str, default=None,
                                help='目标期号（如2026165），不指定则爬取最新文章')
    article_parser.add_argument('--limit', type=int, default=30,
                                help='获取历史数据的期数限制（默认30期）')

    # 四步流水线分析命令（推荐）
    pipeline_parser = subparsers.add_parser('pipeline', help='执行四步流水线分析：文章爬取→走势分析→专家整合→最终预测')
    pipeline_parser.add_argument('--issue', type=str, default=None,
                                 help='目标期号（如2026165），不指定则自动推算下一期')
    pipeline_parser.add_argument('--limit', type=int, default=40,
                                 help='历史数据期数限制（默认40期）')

    # 批量保存文章命令
    save_articles_parser = subparsers.add_parser('save-articles', help='批量爬取文章并保存到Redis，自动提取预测数据')
    save_articles_parser.add_argument('--issue', type=str, default=None,
                                       help='目标期号（如2026165），不指定则爬取所有文章')
    save_articles_parser.add_argument('--max', type=int, default=100,
                                       help='最大处理文章数（默认100）')
    save_articles_parser.add_argument('--no-extract', action='store_true',
                                       help='不提取预测数据，仅保存原始文章')

    # 文章处理命令（爬取→AI→预处理→Redis存储）
    process_article_parser = subparsers.add_parser('process-article', help='处理单篇文章：爬取→AI分析→预处理→Redis存储')
    process_article_parser.add_argument('--url', type=str, required=True,
                                        help='文章URL')
    process_article_parser.add_argument('--title', type=str, default='',
                                        help='文章标题（可选）')

    # 批量文章处理命令
    process_articles_parser = subparsers.add_parser('process-articles', help='批量处理文章：爬取→AI分析→预处理→Redis存储')
    process_articles_parser.add_argument('--max', type=int, default=10,
                                         help='最大处理文章数（默认10）')
    
    # 命中率统计命令
    hitrate_parser = subparsers.add_parser('hitrate', help='查看历史预测命中率统计报告')
    hitrate_parser.add_argument('--days', type=int, default=30, dest='days',
                                help='最近N天数据（默认30天）')
    hitrate_parser.add_argument('--output', type=str, default=None, dest='output',
                                help='输出到文件（可选，默认仅打印到控制台）')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    # 执行对应命令
    if args.command == 'update':
        success = update_data()
    elif args.command == 'predict':
        use_optimized = args.model == 'optimized'
        success = predict_next_issue(use_optimized)
    elif args.command == 'backtest':
        success = run_backtest(args.mode, args.start, args.count)
    elif args.command == 'analyze':
        success = analyze_features()
    elif args.command == 'ernie':
        success = run_ernie_ai_analysis(data_limit=args.limit)
    elif args.command == 'comprehensive':
        logger.warning('⚠️ 综合分析报告已弃用，建议使用 pipeline 命令获得更完整的分析结果')
        success = run_comprehensive_analysis(data_limit=args.limit)
    elif args.command == 'article':
        logger.warning('⚠️ 文章分析工作流已弃用，建议使用 pipeline 命令获得更完整的分析结果')
        success = run_article_analysis(target_issue=args.issue, data_limit=args.limit)
    elif args.command == 'pipeline':
        success = run_four_step_pipeline(target_issue=args.issue, data_limit=args.limit)
    elif args.command == 'save-articles':
        extract_predictions = not args.no_extract
        success = run_save_articles_to_redis(target_issue=args.issue, max_articles=args.max, extract_predictions=extract_predictions)
    elif args.command == 'process-article':
        success = run_process_article(url=args.url, title=args.title)
    elif args.command == 'process-articles':
        success = run_process_multiple_articles(max_count=args.max)
    elif args.command == 'hitrate':
        success = run_hit_rate_report(args.days, args.output)
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
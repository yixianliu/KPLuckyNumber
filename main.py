"""
排列五AI预测系统主程序

整合所有模块，提供完整的预测、回测、数据更新功能。

功能模块：
1. 数据采集与更新
2. 数据清洗与验证
3. 特征工程提取
4. AI预测推理
5. 历史回测验证
6. 可视化报告生成
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
    更新历史数据
    """
    logger.info('=' * 80)
    logger.info('开始更新历史数据')
    logger.info('=' * 80)

    try:
        from modules.spider_p5 import P5Spider
        from modules.database_p5 import P5Database

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
    预测下一期

    Args:
        use_optimized: 是否使用优化后的模型
    """
    logger.info('=' * 80)
    logger.info(f'开始预测下一期（{"优化后" if use_optimized else "原始"}模型）')
    logger.info('=' * 80)

    try:
        # 导入模块
        if use_optimized:
            from modules.optimized_p5_predictor import OptimizedP5Predictor as Predictor
        else:
            from modules.p5_predictor import P5Predictor as Predictor

        from modules.database_p5 import P5Database

        # 连接数据库
        logger.info('连接数据库...')
        db = P5Database()
        if not db.connect():
            logger.error('数据库连接失败')
            return False

        # 获取历史数据
        logger.info('加载历史数据...')
        history_data = db.get_history_data(limit=200, order='DESC')

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
    运行历史回测

    Args:
        mode: 回测模式（compare/old/new）
        start: 回测起始位置
        count: 回测期数
    """
    logger.info('=' * 80)
    logger.info(f'开始历史回测（模式：{mode}）')
    logger.info('=' * 80)

    try:
        # 导入模块
        from modules.p5_predictor import P5Predictor
        from modules.optimized_p5_predictor import OptimizedP5Predictor
        from modules.backtest_engine import P5BacktestEngine
        from modules.database_p5 import P5Database

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
        old_predictor = P5Predictor()
        new_predictor = OptimizedP5Predictor()

        # 初始化回测引擎
        backtest_engine = P5BacktestEngine(old_predictor, db)

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
    执行ERNIE AI分析

    Args:
        data_limit: 获取历史数据的期数限制
    """
    logger.info('=' * 80)
    logger.info('开始执行ERNIE AI分析')
    logger.info('=' * 80)

    try:
        from modules.ernie_ai_analyzer import ERNIEAIAnalyzer

        logger.info(f'配置参数：数据期数={data_limit}')

        analyzer = ERNIEAIAnalyzer()
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
    执行综合分析（二次深度分析）

    整合多源数据进行深度分析：
    - Redis中的原始数据
    - 网络大神分析结果
    - AI模型初步分析数据

    Args:
        data_limit: 获取历史数据的期数限制
    """
    logger.info('=' * 80)
    logger.info('开始执行综合分析（二次深度分析）')
    logger.info('=' * 80)

    try:
        from modules.ernie_ai_analyzer import ComprehensiveAnalyzer

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
    分析历史数据特征
    """
    logger.info('=' * 80)
    logger.info('开始分析历史数据特征')
    logger.info('=' * 80)

    try:
        from modules.feature_engineering import P5FeatureEngineering
        from modules.database_p5 import P5Database

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
        fe = P5FeatureEngineering()

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
    执行文章内容分析工作流

    Args:
        target_issue: 目标期号
        data_limit: 获取历史数据的期数限制
    """
    logger.info('=' * 80)
    logger.info('开始执行文章内容分析工作流')
    logger.info('=' * 80)

    try:
        from modules.article_analyzer import ArticleAnalyzer

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

    Args:
        target_issue: 目标期号
        max_articles: 最大处理文章数
        extract_predictions: 是否提取预测数据
    """
    logger.info('=' * 80)
    logger.info('开始执行批量保存文章到Redis')
    if extract_predictions:
        logger.info('【启用预测数据自动提取】')
    logger.info('=' * 80)

    try:
        from modules.article_analyzer import ArticleAnalyzer

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
    
    Args:
        url: 文章URL
        title: 文章标题
        
    Returns:
        是否成功
    """
    logger.info('=' * 80)
    logger.info('开始处理单篇文章')
    logger.info('=' * 80)

    try:
        from modules.article_processor import ArticleProcessor

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


def run_process_multiple_articles(max_count: int = 10) -> bool:
    """
    批量处理文章：爬取→AI分析→预处理→Redis存储
    
    Args:
        max_count: 最大处理文章数
        
    Returns:
        是否成功
    """
    logger.info('=' * 80)
    logger.info(f'开始批量处理 {max_count} 篇文章')
    logger.info('=' * 80)

    try:
        from modules.article_processor import ArticleProcessor

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
    主函数
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
    comprehensive_parser = subparsers.add_parser('comprehensive', help='执行综合分析（二次深度分析）')
    comprehensive_parser.add_argument('--limit', type=int, default=30,
                                      help='获取历史数据的期数限制（默认30期）')

    # 文章分析命令
    article_parser = subparsers.add_parser('article', help='执行文章内容分析工作流')
    article_parser.add_argument('--issue', type=str, default=None,
                                help='目标期号（如2026165），不指定则爬取最新文章')
    article_parser.add_argument('--limit', type=int, default=30,
                                help='获取历史数据的期数限制（默认30期）')

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
        success = run_comprehensive_analysis(data_limit=args.limit)
    elif args.command == 'article':
        success = run_article_analysis(target_issue=args.issue, data_limit=args.limit)
    elif args.command == 'save-articles':
        extract_predictions = not args.no_extract
        success = run_save_articles_to_redis(target_issue=args.issue, max_articles=args.max, extract_predictions=extract_predictions)
    elif args.command == 'process-article':
        success = run_process_article(url=args.url, title=args.title)
    elif args.command == 'process-articles':
        success = run_process_multiple_articles(max_count=args.max)
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
import schedule
import time
import logging
import os
from datetime import datetime

os.makedirs('logs', exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/scheduler_p5.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class P5DataUpdater:
    """
    排列5数据更新调度器类
    
    负责定时执行排列5数据爬取、清洗、存储和报告生成任务
    """
    
    def __init__(self):
        self.is_running = False
    
    def update_data(self):
        """执行完整的排列5数据更新流程"""
        logger.info('=== 开始执行排列5数据更新任务 ===')
        
        try:
            from modules.spider_p5 import P5Spider
            from modules.database_p5 import P5Database
            from modules.analyzer_p5 import P5Analyzer
            from modules.ai_analyzer import AIAnalyzer
            
            spider = P5Spider()
            db = P5Database()
            analyzer = P5Analyzer()
            ai_analyzer = AIAnalyzer()
            
            logger.info('1. 获取数据库中最新期号...')
            if db.connect():
                last_issue = db.get_latest_issue()
                db.disconnect()
                logger.info(f'数据库中最新期号: {last_issue}')
            else:
                last_issue = None
                logger.warning('数据库连接失败，将执行全量爬取')
            
            logger.info('2. 执行增量爬取...')
            new_data = spider.crawl_incremental_data(last_issue)
            logger.info(f'增量爬取完成，新增 {len(new_data)} 条数据')
            
            if not new_data:
                logger.info('无新增数据，检查是否需要全量更新...')
                all_data = spider.crawl_history_data()
                if len(all_data) > 0:
                    logger.info(f'全量爬取到 {len(all_data)} 条数据')
                    new_data = all_data
            
            if not new_data:
                logger.warning('未爬取到任何数据，跳过本次更新')
                return
            
            logger.info('3. 存储数据到数据库...')
            if db.connect():
                db.create_tables()

                inserted_count = db.insert_history_data(new_data)
                logger.info(f'成功存储 {inserted_count} 条历史数据')

                trend_data = spider.crawl_trend_data(record=120)
                if trend_data:
                    trend_inserted = db.insert_trend_data(trend_data)
                    logger.info(f'成功存储 {trend_inserted} 条走势图数据')

                db.disconnect()

            logger.info('4. 执行数据整合与质量检查...')
            try:
                from modules.data_integrator import P5DataIntegrator
                integrator = P5DataIntegrator()
                dataset = integrator.build_standardized_dataset(limit=120, auto_repair=True)
                if dataset['success']:
                    qr = dataset['quality_report']
                    logger.info(f'数据整合完成: 质量评分 {qr.get("quality_score", 0)}/100, '
                                f'记录数 {dataset["metadata"].get("record_count", 0)}')
                    # 保存质量报告
                    report_path = integrator.save_quality_report(qr)
                    if report_path:
                        logger.info(f'数据质量报告已保存: {report_path}')
                else:
                    logger.warning(f'数据整合未完成: {dataset.get("error", "未知错误")}')
            except Exception as e:
                logger.error(f'数据整合步骤异常: {e}', exc_info=True)

            logger.info('5. 执行AI智能分析...')
            ai_result = ai_analyzer.analyze_p5()
            
            if ai_result.get('status') == 'success':
                logger.info('AI分析成功')
                
                logger.info('6. 保存AI分析报告到数据库...')
                saved = ai_analyzer.save_report_to_database(ai_result)
                if saved:
                    logger.info('AI分析报告已成功保存到数据库')
                else:
                    logger.error('AI分析报告保存失败')
                
                report = ai_result.get('report', '')
                if report:
                    report_file = f'reports/p5_ai_report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.txt'
                    os.makedirs('reports', exist_ok=True)
                    with open(report_file, 'w', encoding='utf-8') as f:
                        f.write(report)
                    logger.info(f'AI分析报告已保存到文件: {report_file}')
            else:
                logger.error(f'AI分析失败: {ai_result.get("message", "未知错误")}')
            
            logger.info('=== 排列5数据更新任务执行完成 ===')
            
        except Exception as e:
            logger.error(f'排列5数据更新任务执行失败: {e}', exc_info=True)
    
    def schedule_daily_update(self, time_str='08:00'):
        """设置每日定时更新"""
        schedule.every().day.at(time_str).do(self.update_data)
        logger.info(f'已设置排列5每日 {time_str} 自动更新数据')
    
    def schedule_multiple_updates(self):
        """设置多个定时更新时间（每日多次检查）"""
        update_times = ['08:00', '12:00', '18:00', '22:00']
        for t in update_times:
            schedule.every().day.at(t).do(self.update_data)
            logger.info(f'已设置排列5每日 {t} 自动更新数据')
    
    def run(self):
        """启动调度器"""
        self.is_running = True
        logger.info('排列5定时任务调度器已启动')
        
        while self.is_running:
            schedule.run_pending()
            time.sleep(60)
    
    def stop(self):
        """停止调度器"""
        self.is_running = False
        logger.info('排列5定时任务调度器已停止')


if __name__ == '__main__':
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    updater = P5DataUpdater()
    
    print('排列5定时更新任务测试（立即执行一次）')
    updater.update_data()
    print('测试完成')
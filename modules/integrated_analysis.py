"""
整合分析模块

整合增强版爬虫和分析器，提供统一的分析接口
支持七星彩和排列5的增强版分析
"""

import logging
import json
from datetime import datetime

# 导入增强版模块
from modules.spider_enhanced import QXCSpiderEnhanced
from modules.spider_p5_enhanced import P5SpiderEnhanced
from modules.analyzer_enhanced import QXCAnalyzerEnhanced
from modules.analyzer_p5_enhanced import P5AnalyzerEnhanced

# 导入原有数据库模块
from modules.database import Database
from modules.database_p5 import P5Database as DatabaseP5

logger = logging.getLogger(__name__)


class IntegratedAnalysis:
    """
    整合分析类
    
    提供统一的接口，整合增强版爬虫和分析器
    支持数据获取、分析和报告生成
    """
    
    def __init__(self, lottery_type='p5'):
        """
        初始化整合分析器
        
        Args:
            lottery_type: 彩票类型，'qxc'或'p5'
        """
        self.lottery_type = lottery_type
        
        if lottery_type == 'qxc':
            self.spider = QXCSpiderEnhanced()
            self.db = Database()
            self.AnalyzerClass = QXCAnalyzerEnhanced
        elif lottery_type == 'p5':
            self.spider = P5SpiderEnhanced()
            self.db = DatabaseP5()
            self.AnalyzerClass = P5AnalyzerEnhanced
        else:
            raise ValueError(f'不支持的彩票类型: {lottery_type}')
        
        logger.info(f'整合分析器初始化完成，类型: {lottery_type}')
    
    def fetch_and_analyze(self, trend_record=120, use_enhanced=True):
        """
        获取数据并执行分析
        
        Args:
            trend_record: 走势图记录数量
            use_enhanced: 是否使用增强版分析
        
        Returns:
            分析结果
        """
        logger.info(f'开始获取和分析{self.lottery_type}数据')
        
        # 1. 获取历史数据
        history_data = self.spider.crawl_history_data()
        
        # 2. 获取走势图数据（增强版）
        trend_data = []
        if use_enhanced:
            trend_data = self.spider.crawl_trend_data(record=trend_record)
        
        # 3. 保存到数据库
        self._save_to_database(history_data, trend_data)
        
        # 4. 执行分析
        if use_enhanced and trend_data:
            # 使用增强版分析器
            analyzer = self.AnalyzerClass(history_data, trend_data)
            analysis_result = analyzer.analyze_comprehensive()
            
            # 添加元数据
            analysis_result['metadata'] = {
                'lottery_type': self.lottery_type,
                'analysis_type': 'enhanced',
                'history_count': len(history_data),
                'trend_count': len(trend_data),
                'analysis_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
        else:
            # 使用原有分析器
            if self.lottery_type == 'qxc':
                from modules.analyzer import ProbabilityAnalyzer
                analyzer = ProbabilityAnalyzer(history_data)
                analysis_result = analyzer.analyze_all()
            else:
                from modules.analyzer_p5 import P5Analyzer
                analyzer = P5Analyzer(history_data)
                analysis_result = analyzer.analyze_all()
            
            analysis_result['metadata'] = {
                'lottery_type': self.lottery_type,
                'analysis_type': 'standard',
                'history_count': len(history_data),
                'analysis_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
        
        return analysis_result
    
    def _save_to_database(self, history_data, trend_data):
        """
        保存数据到数据库
        
        Args:
            history_data: 历史数据
            trend_data: 走势图数据
        """
        try:
            # 保存历史数据
            if history_data:
                if self.lottery_type == 'qxc':
                    self.db.save_history_data(history_data)
                else:
                    self.db.save_history_data(history_data)
                logger.info(f'保存了 {len(history_data)} 条历史数据')
            
            # 保存走势图数据
            if trend_data:
                self._save_trend_data(trend_data)
                logger.info(f'保存了 {len(trend_data)} 条走势图数据')
                
        except Exception as e:
            logger.error(f'保存数据到数据库失败: {e}')
    
    def _save_trend_data(self, trend_data):
        """
        保存走势图数据到数据库
        
        Args:
            trend_data: 走势图数据列表
        """
        try:
            import sqlite3
            
            # 确定表名
            if self.lottery_type == 'qxc':
                table_name = 'qxc_trend_data_enhanced'
            else:
                table_name = 'p5_trend_data_enhanced'
            
            # 连接到数据库
            conn = sqlite3.connect('data/lottery_data.db')
            cursor = conn.cursor()
            
            # 创建表（如果不存在）
            cursor.execute(f'''
                CREATE TABLE IF NOT EXISTS {table_name} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    issue TEXT UNIQUE,
                    numbers TEXT,
                    omissions TEXT,
                    hezhi TEXT,
                    odd_even_ratio TEXT,
                    big_small_ratio TEXT,
                    prime_composite_ratio TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # 插入数据
            for item in trend_data:
                try:
                    cursor.execute(f'''
                        INSERT OR REPLACE INTO {table_name} 
                        (issue, numbers, omissions, hezhi, odd_even_ratio, big_small_ratio, prime_composite_ratio)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        item.get('issue', ''),
                        json.dumps(item.get('numbers', [])),
                        json.dumps(item.get('omissions', {})),
                        item.get('hezhi', ''),
                        item.get('odd_even_ratio', ''),
                        item.get('big_small_ratio', ''),
                        item.get('prime_composite_ratio', '')
                    ))
                except Exception as e:
                    logger.debug(f'插入走势数据失败: {e}')
            
            conn.commit()
            conn.close()
            logger.info(f'走势图数据已保存到表 {table_name}')
            
        except Exception as e:
            logger.error(f'保存走势图数据失败: {e}')
    
    def generate_report(self, analysis_result, output_file=None):
        """
        生成分析报告
        
        Args:
            analysis_result: 分析结果
            output_file: 输出文件路径
        
        Returns:
            报告文本
        """
        report = []
        
        # 报告头
        metadata = analysis_result.get('metadata', {})
        report.append('=' * 60)
        report.append(f'{self.lottery_type.upper()} 彩票分析报告')
        report.append(f'分析时间: {metadata.get("analysis_time", "未知")}')
        report.append(f'分析类型: {metadata.get("analysis_type", "标准版")}')
        report.append(f'历史数据: {metadata.get("history_count", 0)} 条')
        if metadata.get('trend_count'):
            report.append(f'走势数据: {metadata.get("trend_count", 0)} 条')
        report.append('=' * 60)
        
        # 最优号码推荐
        if 'optimal_numbers' in analysis_result:
            report.append('\n【最优号码推荐】')
            optimal = analysis_result['optimal_numbers']
            
            for pos, data in optimal.items():
                pos_name = self._get_position_name(pos)
                top_numbers = data.get('top_numbers', [])
                if top_numbers:
                    nums_str = ', '.join([f'{item["number"]}({item["probability"]:.3f})' for item in top_numbers[:3]])
                    report.append(f'  {pos_name}: {nums_str}')
        
        # 推荐组合
        if 'recommended_combinations' in analysis_result:
            report.append('\n【推荐组合】')
            combinations = analysis_result['recommended_combinations']
            for i, combo in enumerate(combinations[:5], 1):
                nums = combo.get('numbers', [])
                prob = combo.get('combined_probability', 0)
                report.append(f'  组合{i}: {" ".join(map(str, nums))} (概率: {prob:.6f})')
        
        # 遗漏值趋势
        if 'omission_trend' in analysis_result:
            report.append('\n【遗漏值趋势分析】')
            omission_trend = analysis_result['omission_trend']
            
            # 找出遗漏值最高的号码
            max_omissions = []
            for pos, pos_data in omission_trend.items():
                pos_name = self._get_position_name(pos)
                for num, num_data in pos_data.items():
                    max_omissions.append({
                        'position': pos_name,
                        'number': num,
                        'current_omission': num_data.get('current_omission', 0),
                        'level': num_data.get('omission_level', 'unknown'),
                        'rebound_prob': num_data.get('rebound_probability', 0)
                    })
            
            # 按遗漏值排序
            max_omissions.sort(key=lambda x: x['current_omission'], reverse=True)
            
            report.append('  当前遗漏值最高的号码（前10个）:')
            for item in max_omissions[:10]:
                report.append(f'    {item["position"]} 号码{item["number"]}: '
                             f'遗漏{item["current_omission"]}期 '
                             f'({item["level"]}, 回补概率: {item["rebound_prob"]:.3f})')
        
        # 冷热转换
        if 'cold_hot_transitions' in analysis_result:
            report.append('\n【冷热转换分析】')
            transitions = analysis_result['cold_hot_transitions']
            
            cold_to_hot = []
            hot_to_cold = []
            
            for pos, pos_data in transitions.items():
                pos_name = self._get_position_name(pos)
                for num, num_data in pos_data.items():
                    trans_type = num_data.get('transition_type', 'stable')
                    if trans_type == 'cold_to_hot':
                        cold_to_hot.append({
                            'position': pos_name,
                            'number': num,
                            'confidence': num_data.get('confidence', 0)
                        })
                    elif trans_type == 'hot_to_cold':
                        hot_to_cold.append({
                            'position': pos_name,
                            'number': num,
                            'confidence': num_data.get('confidence', 0)
                        })
            
            if cold_to_hot:
                report.append('  冷转热号码（值得关注）:')
                for item in sorted(cold_to_hot, key=lambda x: x['confidence'], reverse=True)[:5]:
                    report.append(f'    {item["position"]} 号码{item["number"]} '
                                 f'(置信度: {item["confidence"]:.3f})')
            
            if hot_to_cold:
                report.append('  热转冷号码（需要谨慎）:')
                for item in sorted(hot_to_cold, key=lambda x: x['confidence'], reverse=True)[:5]:
                    report.append(f'    {item["position"]} 号码{item["number"]} '
                                 f'(置信度: {item["confidence"]:.3f})')
        
        # 走势斜率
        if 'trend_slopes' in analysis_result:
            report.append('\n【走势斜率分析】')
            trend_slopes = analysis_result['trend_slopes']
            
            rising_strong = []
            for pos, pos_data in trend_slopes.items():
                pos_name = self._get_position_name(pos)
                for num, num_data in pos_data.items():
                    direction = num_data.get('trend_direction', 'flat')
                    if direction == 'rising_strong':
                        rising_strong.append({
                            'position': pos_name,
                            'number': num,
                            'slope': num_data.get('latest_slope', 0)
                        })
            
            if rising_strong:
                report.append('  遗漏值快速上升的号码（即将回补）:')
                for item in sorted(rising_strong, key=lambda x: x['slope'], reverse=True)[:5]:
                    report.append(f'    {item["position"]} 号码{item["number"]} '
                                 f'(斜率: {item["slope"]:.4f})')
        
        report.append('\n' + '=' * 60)
        
        report_text = '\n'.join(report)
        
        # 保存到文件
        if output_file:
            try:
                with open(output_file, 'w', encoding='utf-8') as f:
                    f.write(report_text)
                logger.info(f'报告已保存到: {output_file}')
            except Exception as e:
                logger.error(f'保存报告失败: {e}')
        
        return report_text
    
    def _get_position_name(self, pos):
        """
        获取位置名称
        
        Args:
            pos: 位置索引
        
        Returns:
            位置名称
        """
        if self.lottery_type == 'qxc':
            names = ['第1位', '第2位', '第3位', '第4位', '第5位', '第6位', '特别号']
        else:
            names = ['万位', '千位', '百位', '十位', '个位']
        
        return names[pos] if pos < len(names) else f'位置{pos}'
    
    def compare_analysis(self, history_data, trend_data):
        """
        对比标准版和增强版分析结果
        
        Args:
            history_data: 历史数据
            trend_data: 走势图数据
        
        Returns:
            对比结果
        """
        # 标准版分析
        if self.lottery_type == 'qxc':
            from modules.analyzer import ProbabilityAnalyzer
            standard_analyzer = ProbabilityAnalyzer(history_data)
            standard_result = standard_analyzer.analyze_all()
        else:
            from modules.analyzer_p5 import P5Analyzer
            standard_analyzer = P5Analyzer()
            standard_result = standard_analyzer.calculate_probability(history_data, trend_data)
        
        # 增强版分析
        enhanced_analyzer = self.AnalyzerClass(history_data, trend_data)
        enhanced_result = enhanced_analyzer.analyze_comprehensive()
        
        return {
            'standard': standard_result,
            'enhanced': enhanced_result,
            'comparison': {
                'standard_type': '基于历史频率',
                'enhanced_type': '基于历史频率+走势遗漏值',
                'enhanced_features': [
                    '遗漏值趋势分析',
                    '冷热转换预测',
                    '走势斜率分析',
                    '遗漏回补模型',
                    '多维度综合概率'
                ]
            }
        }


def run_integrated_analysis(lottery_type='p5', use_enhanced=True, save_report=True):
    """
    运行整合分析
    
    Args:
        lottery_type: 彩票类型
        use_enhanced: 是否使用增强版
        save_report: 是否保存报告
    
    Returns:
        分析结果
    """
    analysis = IntegratedAnalysis(lottery_type=lottery_type)
    
    # 执行分析
    result = analysis.fetch_and_analyze(use_enhanced=use_enhanced)
    
    # 生成报告
    if save_report:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_file = f'reports/{lottery_type}_enhanced_report_{timestamp}.txt'
        report = analysis.generate_report(result, output_file=output_file)
        print(report)
    
    return result


if __name__ == '__main__':
    # 测试整合分析
    print('=== 测试排列5增强版整合分析 ===')
    result = run_integrated_analysis(lottery_type='p5', use_enhanced=True, save_report=False)
    print(f'分析完成，结果包含: {list(result.keys())}')

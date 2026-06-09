import os
import logging
import matplotlib.pyplot as plt
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/report_generator.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class ReportGenerator:
    def __init__(self, output_dir='reports/'):
        self.output_dir = output_dir
        self._ensure_dir()
    
    def _ensure_dir(self):
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)
    
    def generate_frequency_chart(self, freq_data, output_name='frequency.png'):
        try:
            plt.figure(figsize=(12, 8))
            
            for pos in range(6):
                x = list(range(10))
                y = []
                for num in x:
                    if num in freq_data:
                        y.append(freq_data[num]['frequency'][pos])
                    else:
                        y.append(0)
                
                plt.subplot(2, 3, pos + 1)
                plt.bar(x, y, color='skyblue')
                plt.title(f'位置 {pos + 1} 号码频率分布')
                plt.xlabel('数字')
                plt.ylabel('出现次数')
                plt.xticks(x)
                plt.grid(axis='y', linestyle='--', alpha=0.7)
            
            plt.tight_layout()
            plt.savefig(os.path.join(self.output_dir, output_name), dpi=100, bbox_inches='tight')
            plt.close()
            logger.info(f'频率分布图已保存: {output_name}')
        except Exception as e:
            logger.error(f'生成频率分布图失败: {e}')
    
    def generate_probability_chart(self, predictions, output_name='probability.png'):
        try:
            plt.figure(figsize=(10, 6))
            
            numbers = list(predictions.keys())
            probabilities = [pred['probability'] for pred in predictions.values()]
            confidences = [pred['confidence'] for pred in predictions.values()]
            
            x = range(len(numbers))
            width = 0.35
            
            plt.bar([i - width/2 for i in x], probabilities, width, label='概率', color='blue')
            plt.bar([i + width/2 for i in x], confidences, width, label='置信度', color='orange')
            
            plt.title('数字概率与置信度分布')
            plt.xlabel('数字')
            plt.ylabel('概率/置信度')
            plt.xticks(x, numbers)
            plt.legend()
            plt.grid(axis='y', linestyle='--', alpha=0.7)
            
            plt.savefig(os.path.join(self.output_dir, output_name), dpi=100, bbox_inches='tight')
            plt.close()
            logger.info(f'概率分布图已保存: {output_name}')
        except Exception as e:
            logger.error(f'生成概率分布图失败: {e}')
    
    def save_text_report(self, report_text, output_name=None):
        try:
            if output_name is None:
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                output_name = f'report_{timestamp}.txt'
            
            filepath = os.path.join(self.output_dir, output_name)
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(report_text)
            
            logger.info(f'分析报告已保存: {filepath}')
            return filepath
        except Exception as e:
            logger.error(f'保存报告失败: {e}')
            return None
    
    def generate_full_report(self, analysis_result, analyzer):
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        text_report = analyzer.generate_report(analysis_result)
        text_report += f'\n\n生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}'
        
        self.save_text_report(text_report, f'report_{timestamp}.txt')
        
        self.generate_frequency_chart(analysis_result['frequency'], f'frequency_{timestamp}.png')
        self.generate_probability_chart(analysis_result['predictions'], f'probability_{timestamp}.png')
        
        logger.info('完整分析报告已生成')
        return {
            'text_report': text_report,
            'frequency_chart': f'frequency_{timestamp}.png',
            'probability_chart': f'probability_{timestamp}.png'
        }

if __name__ == '__main__':
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    from database import Database
    from analyzer import ProbabilityAnalyzer
    
    db = Database()
    if db.connect():
        data = db.query_all()
        db.disconnect()
        
        if data:
            analyzer = ProbabilityAnalyzer()
            result = analyzer.calculate_probability(data)
            
            generator = ReportGenerator()
            generator.generate_full_report(result, analyzer)
            print('报告生成完成')
        else:
            print('数据库中没有数据')

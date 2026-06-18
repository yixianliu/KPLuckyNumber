"""
数据整合与标准化模块

负责对排列5历史数据和走势图数据进行系统性质量检查、修复、整合与标准化，
构建高质量标准化数据集，作为AI分析系统的输入数据源。

核心能力：
1. 数据质量检查引擎 - 识别缺失值、异常值、不一致性
2. 数据修复引擎 - 自动计算缺失指标、修正错误数据、对齐多源数据
3. 标准化数据集构建 - 统一格式、时间粒度、指标定义
4. AI输入接口 - 提供可直接用于AI分析的标准化数据
"""

import logging
import os
import json
import re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple

os.makedirs('logs', exist_ok=True)

logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler = logging.FileHandler('logs/data_integrator.log', encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


class P5DataIntegrator:
    """
    排列5数据整合器

    提供从数据库读取 → 质量检查 → 数据修复 → 标准化输出的一站式数据整合服务。
    """

    def __init__(self):
        self.position_names = ['万位', '千位', '百位', '十位', '个位']
        self.primes = {2, 3, 5, 7}
        self.composites = {0, 4, 6, 8, 9}

    # ==================== 1. 数据加载 ====================

    def load_data_from_database(self, limit: Optional[int] = None) -> Dict[str, Any]:
        """
        从MySQL数据库加载排列5历史数据和走势图数据

        Args:
            limit: 限制加载最近多少期，None表示全部加载

        Returns:
            包含history_data和trend_data的字典
        """
        try:
            from modules.database_p5 import P5Database
            db = P5Database()
            if not db.connect():
                logger.error('数据库连接失败，无法加载数据')
                return {'history_data': [], 'trend_data': [], 'error': '数据库连接失败'}

            history_data = db.get_history_data(limit=limit, order='DESC')
            trend_data = db.get_trend_data(limit=limit, order='DESC')
            db.disconnect()

            logger.info(f'数据库加载完成: 历史数据 {len(history_data)} 条, 走势数据 {len(trend_data)} 条')
            return {
                'history_data': history_data,
                'trend_data': trend_data,
                'error': None
            }
        except Exception as e:
            logger.error(f'从数据库加载数据失败: {e}')
            return {'history_data': [], 'trend_data': [], 'error': str(e)}

    # ==================== 2. 数据质量检查引擎 ====================

    def check_missing_values(self, data: List[Dict]) -> Dict[str, Any]:
        """
        检测缺失值

        检查字段：期号、日期、号码、和值、跨度、奇偶比、大小比、质合比
        """
        missing_report = {
            'missing_issue': [],
            'missing_date': [],
            'missing_numbers': [],
            'missing_hezhi': [],
            'missing_span': [],
            'missing_odd_even_ratio': [],
            'missing_big_small_ratio': [],
            'missing_prime_composite_ratio': [],
            'summary': {}
        }

        for item in data:
            issue = item.get('issue', '')
            if not issue:
                missing_report['missing_issue'].append(item)
            if not item.get('date'):
                missing_report['missing_date'].append(issue)
            if not item.get('numbers'):
                missing_report['missing_numbers'].append(issue)
            if item.get('hezhi') is None:
                missing_report['missing_hezhi'].append(issue)
            if item.get('span') is None:
                missing_report['missing_span'].append(issue)
            if not item.get('odd_even_ratio'):
                missing_report['missing_odd_even_ratio'].append(issue)
            if not item.get('big_small_ratio'):
                missing_report['missing_big_small_ratio'].append(issue)
            if not item.get('prime_composite_ratio'):
                missing_report['missing_prime_composite_ratio'].append(issue)

        total = len(data)
        missing_report['summary'] = {
            'total_records': total,
            'missing_issue_count': len(missing_report['missing_issue']),
            'missing_date_count': len(missing_report['missing_date']),
            'missing_numbers_count': len(missing_report['missing_numbers']),
            'missing_hezhi_count': len(missing_report['missing_hezhi']),
            'missing_span_count': len(missing_report['missing_span']),
            'missing_odd_even_ratio_count': len(missing_report['missing_odd_even_ratio']),
            'missing_big_small_ratio_count': len(missing_report['missing_big_small_ratio']),
            'missing_prime_composite_ratio_count': len(missing_report['missing_prime_composite_ratio'])
        }

        logger.info(f'缺失值检查完成: 总记录 {total} 条')
        return missing_report

    def check_anomalies(self, data: List[Dict]) -> Dict[str, Any]:
        """
        检测异常值

        检查项：
        - 号码范围异常（非0-9）
        - 号码数量异常（非5个）
        - 和值计算异常
        - 跨度计算异常
        - 奇偶比异常
        - 大小比异常
        - 质合比异常
        - 重复期号
        - 期号不连续（排列5每天开奖，期号应连续递增）
        """
        anomaly_report = {
            'invalid_numbers': [],
            'wrong_hezhi': [],
            'wrong_span': [],
            'wrong_odd_even_ratio': [],
            'wrong_big_small_ratio': [],
            'wrong_prime_composite_ratio': [],
            'duplicate_issues': [],
            'discontinuous_issues': [],
            'summary': {}
        }

        issue_set = set()
        issue_list = []

        for item in data:
            issue = str(item.get('issue', ''))
            numbers = item.get('numbers', [])
            flags = []

            # 1. 号码数量和范围检查
            if not isinstance(numbers, list) or len(numbers) != 5:
                flags.append(f'号码数量异常: {len(numbers) if isinstance(numbers, list) else type(numbers)}')
            else:
                for pos, num in enumerate(numbers):
                    try:
                        n = int(num)
                        if n < 0 or n > 9:
                            flags.append(f'第{pos+1}位号码{n}超出范围(0-9)')
                    except (ValueError, TypeError):
                        flags.append(f'第{pos+1}位号码"{num}"不是有效整数')

            # 2. 和值校验
            if isinstance(numbers, list) and len(numbers) == 5:
                try:
                    nums = [int(n) for n in numbers]
                    expected_hezhi = sum(nums)
                    actual_hezhi = item.get('hezhi')
                    if actual_hezhi is not None:
                        try:
                            if int(actual_hezhi) != expected_hezhi:
                                flags.append(f'和值不一致: 记录{actual_hezhi}, 计算{expected_hezhi}')
                                anomaly_report['wrong_hezhi'].append({
                                    'issue': issue, 'recorded': actual_hezhi, 'calculated': expected_hezhi
                                })
                        except (ValueError, TypeError):
                            flags.append(f'和值格式异常: {actual_hezhi}')
                            anomaly_report['wrong_hezhi'].append({
                                'issue': issue, 'recorded': actual_hezhi, 'calculated': expected_hezhi
                            })
                except Exception:
                    pass

            # 3. 跨度校验
            if isinstance(numbers, list) and len(numbers) == 5:
                try:
                    nums = [int(n) for n in numbers]
                    expected_span = max(nums) - min(nums)
                    actual_span = item.get('span')
                    if actual_span is not None:
                        try:
                            if int(actual_span) != expected_span:
                                flags.append(f'跨度不一致: 记录{actual_span}, 计算{expected_span}')
                                anomaly_report['wrong_span'].append({
                                    'issue': issue, 'recorded': actual_span, 'calculated': expected_span
                                })
                        except (ValueError, TypeError):
                            flags.append(f'跨度格式异常: {actual_span}')
                            anomaly_report['wrong_span'].append({
                                'issue': issue, 'recorded': actual_span, 'calculated': expected_span
                            })
                except Exception:
                    pass

            # 4. 奇偶比校验
            if isinstance(numbers, list) and len(numbers) == 5:
                try:
                    nums = [int(n) for n in numbers]
                    odd_count = sum(1 for n in nums if n % 2 == 1)
                    even_count = 5 - odd_count
                    expected_ratio = f"{odd_count}:{even_count}"
                    actual_ratio = item.get('odd_even_ratio', '')
                    if actual_ratio and actual_ratio != expected_ratio:
                        flags.append(f'奇偶比不一致: 记录{actual_ratio}, 计算{expected_ratio}')
                        anomaly_report['wrong_odd_even_ratio'].append({
                            'issue': issue, 'recorded': actual_ratio, 'calculated': expected_ratio
                        })
                except Exception:
                    pass

            # 5. 大小比校验 (0-4小, 5-9大)
            if isinstance(numbers, list) and len(numbers) == 5:
                try:
                    nums = [int(n) for n in numbers]
                    big_count = sum(1 for n in nums if n >= 5)
                    small_count = 5 - big_count
                    expected_ratio = f"{big_count}:{small_count}"
                    actual_ratio = item.get('big_small_ratio', '')
                    if actual_ratio and actual_ratio != expected_ratio:
                        flags.append(f'大小比不一致: 记录{actual_ratio}, 计算{expected_ratio}')
                        anomaly_report['wrong_big_small_ratio'].append({
                            'issue': issue, 'recorded': actual_ratio, 'calculated': expected_ratio
                        })
                except Exception:
                    pass

            # 6. 质合比校验
            if isinstance(numbers, list) and len(numbers) == 5:
                try:
                    nums = [int(n) for n in numbers]
                    prime_count = sum(1 for n in nums if n in self.primes)
                    composite_count = sum(1 for n in nums if n in self.composites)
                    expected_ratio = f"{prime_count}:{composite_count}"
                    actual_ratio = item.get('prime_composite_ratio', '')
                    if actual_ratio and actual_ratio != expected_ratio:
                        flags.append(f'质合比不一致: 记录{actual_ratio}, 计算{expected_ratio}')
                        anomaly_report['wrong_prime_composite_ratio'].append({
                            'issue': issue, 'recorded': actual_ratio, 'calculated': expected_ratio
                        })
                except Exception:
                    pass

            # 7. 重复期号检查
            if issue:
                if issue in issue_set:
                    flags.append(f'重复期号: {issue}')
                    anomaly_report['duplicate_issues'].append(issue)
                issue_set.add(issue)
                issue_list.append(issue)

            if flags:
                anomaly_report['invalid_numbers'].append({'issue': issue, 'flags': flags})

        # 8. 期号连续性检查（排列5期号格式通常为YYYYXXX，每年重新编号，只需检查年内连续性）
        anomaly_report['discontinuous_issues'] = self._check_issue_continuity(issue_list)

        anomaly_report['summary'] = {
            'total_records': len(data),
            'invalid_numbers_count': len(anomaly_report['invalid_numbers']),
            'wrong_hezhi_count': len(anomaly_report['wrong_hezhi']),
            'wrong_span_count': len(anomaly_report['wrong_span']),
            'wrong_odd_even_ratio_count': len(anomaly_report['wrong_odd_even_ratio']),
            'wrong_big_small_ratio_count': len(anomaly_report['wrong_big_small_ratio']),
            'wrong_prime_composite_ratio_count': len(anomaly_report['wrong_prime_composite_ratio']),
            'duplicate_issues_count': len(set(anomaly_report['duplicate_issues'])),
            'discontinuous_issues_count': len(anomaly_report['discontinuous_issues'])
        }

        logger.info(f'异常值检查完成: 总记录 {len(data)} 条, 异常记录 {len(anomaly_report["invalid_numbers"])} 条')
        return anomaly_report

    def _check_issue_continuity(self, issue_list: List[str]) -> List[Dict]:
        """
        检查期号连续性

        排列5期号格式为7位数字：前4位年份，后3位年内序号（每年从001开始）。
        只检查同一年内的期号是否连续。
        """
        discontinuous = []
        if not issue_list:
            return discontinuous

        # 过滤纯数字期号并转为整数
        valid_issues = []
        for issue in issue_list:
            if issue and issue.isdigit():
                valid_issues.append(issue)

        if len(valid_issues) < 2:
            return discontinuous

        # 按数值排序（因为期内序号是3位，数值排序等同于时间排序）
        sorted_issues = sorted(valid_issues, key=lambda x: int(x))

        # 分组按年检查
        year_groups = defaultdict(list)
        for issue in sorted_issues:
            if len(issue) >= 4:
                year = issue[:4]
                seq = int(issue[4:]) if issue[4:].isdigit() else 0
                year_groups[year].append(seq)

        for year, seqs in year_groups.items():
            seqs = sorted(set(seqs))
            for i in range(1, len(seqs)):
                gap = seqs[i] - seqs[i - 1]
                if gap > 1:
                    discontinuous.append({
                        'year': year,
                        'from_seq': seqs[i - 1],
                        'to_seq': seqs[i],
                        'gap': gap - 1,
                        'message': f'{year}年期号从{seqs[i-1]:03d}跳到{seqs[i]:03d}，缺失{gap-1}期'
                    })

        return discontinuous

    def check_inconsistency(self, history_data: List[Dict], trend_data: List[Dict]) -> Dict[str, Any]:
        """
        检测历史数据与走势图数据之间的不一致性

        检查项：
        - 相同期号号码不一致
        - 历史数据有但走势图缺失
        - 走势图有但历史数据缺失
        """
        inconsistency_report = {
            'number_mismatch': [],
            'history_only': [],
            'trend_only': [],
            'summary': {}
        }

        history_dict = {str(item.get('issue', '')): item for item in history_data if item.get('issue')}
        trend_dict = {str(item.get('issue', '')): item for item in trend_data if item.get('issue')}

        history_issues = set(history_dict.keys())
        trend_issues = set(trend_dict.keys())

        # 1. 号码不一致检查
        common_issues = history_issues & trend_issues
        for issue in common_issues:
            h_item = history_dict[issue]
            t_item = trend_dict[issue]
            h_numbers = h_item.get('numbers', [])
            t_numbers = t_item.get('numbers', [])

            if not isinstance(h_numbers, list) or not isinstance(t_numbers, list):
                continue

            if len(h_numbers) == 5 and len(t_numbers) == 5:
                h_nums = [int(n) for n in h_numbers]
                t_nums = [int(n) for n in t_numbers]
                if h_nums != t_nums:
                    inconsistency_report['number_mismatch'].append({
                        'issue': issue,
                        'history_numbers': h_nums,
                        'trend_numbers': t_nums
                    })

        # 2. 历史数据独有的期号
        inconsistency_report['history_only'] = sorted(list(history_issues - trend_issues))

        # 3. 走势图独有的期号
        inconsistency_report['trend_only'] = sorted(list(trend_issues - history_issues))

        inconsistency_report['summary'] = {
            'history_total': len(history_issues),
            'trend_total': len(trend_issues),
            'common': len(common_issues),
            'number_mismatch_count': len(inconsistency_report['number_mismatch']),
            'history_only_count': len(inconsistency_report['history_only']),
            'trend_only_count': len(inconsistency_report['trend_only'])
        }

        logger.info(f'不一致性检查完成: 共同期号 {len(common_issues)} 条, 号码不一致 {len(inconsistency_report["number_mismatch"])} 条')
        return inconsistency_report

    def run_full_quality_check(self, history_data: List[Dict], trend_data: List[Dict]) -> Dict[str, Any]:
        """
        执行全面数据质量检查

        Args:
            history_data: 历史数据列表
            trend_data: 走势图数据列表

        Returns:
            完整质量检查报告
        """
        logger.info('=== 开始全面数据质量检查 ===')

        missing_report = self.check_missing_values(history_data)
        anomaly_report = self.check_anomalies(history_data)
        inconsistency_report = self.check_inconsistency(history_data, trend_data)

        # 综合评分 (100分制)
        total = len(history_data)
        if total == 0:
            quality_score = 0
        else:
            anomaly_count = anomaly_report['summary'].get('invalid_numbers_count', 0)
            missing_count = missing_report['summary'].get('missing_numbers_count', 0)
            mismatch_count = inconsistency_report['summary'].get('number_mismatch_count', 0)
            # 扣分逻辑：异常记录每条扣2分，缺失号码每条扣5分，不一致每条扣3分
            deduction = min(100, anomaly_count * 2 + missing_count * 5 + mismatch_count * 3)
            quality_score = max(0, 100 - deduction)

        report = {
            'check_time': datetime.now().isoformat(),
            'history_total': total,
            'trend_total': len(trend_data),
            'quality_score': quality_score,
            'missing_values': missing_report,
            'anomalies': anomaly_report,
            'inconsistency': inconsistency_report
        }

        logger.info(f'全面数据质量检查完成: 质量评分 {quality_score}/100')
        return report

    # ==================== 3. 数据修复引擎 ====================

    def repair_data(self, data: List[Dict], quality_report: Dict[str, Any]) -> Tuple[List[Dict], Dict[str, Any]]:
        """
        自动修复数据质量问题

        修复策略：
        - 和值/跨度/奇偶比/大小比/质合比缺失或错误 → 根据号码重新计算
        - 号码格式不统一 → 统一转为整数列表
        - 日期格式不统一 → 统一为YYYY-MM-DD
        - 重复期号 → 保留第一条

        Args:
            data: 原始数据列表
            quality_report: 质量检查报告

        Returns:
            (修复后数据列表, 修复报告)
        """
        repaired_data = []
        repair_report = {
            'repaired_hezhi': 0,
            'repaired_span': 0,
            'repaired_odd_even': 0,
            'repaired_big_small': 0,
            'repaired_prime_composite': 0,
            'repaired_date': 0,
            'removed_duplicates': 0,
            'removed_invalid': 0,
            'total_input': len(data),
            'total_output': 0
        }

        seen_issues = set()

        for item in data:
            issue = str(item.get('issue', ''))
            if not issue:
                repair_report['removed_invalid'] += 1
                continue

            # 去重：保留第一次出现的期号
            if issue in seen_issues:
                repair_report['removed_duplicates'] += 1
                continue
            seen_issues.add(issue)

            numbers = item.get('numbers', [])
            if not isinstance(numbers, list) or len(numbers) != 5:
                repair_report['removed_invalid'] += 1
                continue

            # 统一号码格式为整数
            try:
                nums = [int(n) for n in numbers]
                if any(n < 0 or n > 9 for n in nums):
                    repair_report['removed_invalid'] += 1
                    continue
            except (ValueError, TypeError):
                repair_report['removed_invalid'] += 1
                continue

            repaired_item = {
                'issue': issue,
                'numbers': nums,
                'date': self._standardize_date(item.get('date', '')),
                'hezhi': None,
                'span': None,
                'odd_even_ratio': '',
                'odd_even_pattern': '',
                'big_small_ratio': '',
                'prime_composite_ratio': ''
            }

            # 重新计算所有衍生指标（确保准确性）
            repaired_item['hezhi'] = sum(nums)
            repair_report['repaired_hezhi'] += 1

            repaired_item['span'] = max(nums) - min(nums)
            repair_report['repaired_span'] += 1

            odd_count = sum(1 for n in nums if n % 2 == 1)
            even_count = 5 - odd_count
            repaired_item['odd_even_ratio'] = f"{odd_count}:{even_count}"
            # 奇偶形态：如 奇奇偶偶奇
            odd_even_pattern = ''.join(['奇' if n % 2 == 1 else '偶' for n in nums])
            repaired_item['odd_even_pattern'] = odd_even_pattern
            repair_report['repaired_odd_even'] += 1

            big_count = sum(1 for n in nums if n >= 5)
            small_count = 5 - big_count
            repaired_item['big_small_ratio'] = f"{big_count}:{small_count}"
            repair_report['repaired_big_small'] += 1

            prime_count = sum(1 for n in nums if n in self.primes)
            composite_count = sum(1 for n in nums if n in self.composites)
            repaired_item['prime_composite_ratio'] = f"{prime_count}:{composite_count}"
            repair_report['repaired_prime_composite'] += 1

            # 保留原始字段中未被覆盖但有价值的信息
            if item.get('hezhi_feature'):
                repaired_item['hezhi_feature'] = item['hezhi_feature']

            repaired_data.append(repaired_item)

        repair_report['total_output'] = len(repaired_data)
        logger.info(f'数据修复完成: 输入 {repair_report["total_input"]} 条, 输出 {repair_report["total_output"]} 条')
        return repaired_data, repair_report

    def _standardize_date(self, date_str: str) -> str:
        """
        标准化日期格式为 YYYY-MM-DD
        """
        if not date_str:
            return ''
        date_str = str(date_str).strip()
        # 尝试解析多种格式
        patterns = [
            (r'(\d{4})-(\d{1,2})-(\d{1,2})', lambda m: f'{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}'),
            (r'(\d{4})/(\d{1,2})/(\d{1,2})', lambda m: f'{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}'),
            (r'(\d{4})(\d{2})(\d{2})', lambda m: f'{m.group(1)}-{m.group(2)}-{m.group(3)}'),
        ]
        for pattern, formatter in patterns:
            match = re.match(pattern, date_str)
            if match:
                return formatter(match)
        return date_str

    def align_history_and_trend(self, history_data: List[Dict], trend_data: List[Dict]) -> List[Dict]:
        """
        对齐历史数据与走势图数据，以历史数据为基准，补充走势数据中的扩展字段

        Args:
            history_data: 已修复的历史数据
            trend_data: 原始走势图数据

        Returns:
            增强后的历史数据（包含走势扩展字段）
        """
        trend_dict = {}
        for item in trend_data:
            issue = str(item.get('issue', ''))
            if issue:
                trend_dict[issue] = item

        aligned_data = []
        for item in history_data:
            issue = item['issue']
            aligned_item = dict(item)

            if issue in trend_dict:
                t_item = trend_dict[issue]
                # 补充走势数据中的扩展字段（如果历史数据中没有）
                if not aligned_item.get('big_small_ratio') and t_item.get('big_small_ratio'):
                    aligned_item['big_small_ratio'] = t_item['big_small_ratio']
                if not aligned_item.get('prime_composite_ratio') and t_item.get('prime_composite_ratio'):
                    aligned_item['prime_composite_ratio'] = t_item['prime_composite_ratio']
                # 保留走势JSON数据供后续使用
                trend_values = t_item.get('trend', {})
                if trend_values:
                    aligned_item['trend_snapshot'] = trend_values
            else:
                aligned_item['trend_snapshot'] = {}

            aligned_data.append(aligned_item)

        logger.info(f'数据对齐完成: 历史数据 {len(history_data)} 条, 成功对齐走势数据 {len([d for d in aligned_data if d.get("trend_snapshot")])} 条')
        return aligned_data

    # ==================== 4. 标准化数据集构建 ====================

    def build_standardized_dataset(self, limit: Optional[int] = None,
                                    auto_repair: bool = True) -> Dict[str, Any]:
        """
        构建标准化数据集（主入口）

        执行流程：
        1. 从数据库加载历史数据和走势图数据
        2. 执行全面质量检查
        3. 自动修复数据问题
        4. 对齐多源数据
        5. 生成标准化数据集

        Args:
            limit: 限制加载最近多少期
            auto_repair: 是否自动修复数据问题

        Returns:
            标准化数据集字典，包含metadata、data、quality_report、repair_report
        """
        logger.info('=== 开始构建排列5标准化数据集 ===')

        # 1. 加载数据
        raw = self.load_data_from_database(limit=limit)
        if raw.get('error'):
            return {
                'success': False,
                'error': raw['error'],
                'metadata': {},
                'data': [],
                'quality_report': {},
                'repair_report': {}
            }

        history_data = raw['history_data']
        trend_data = raw['trend_data']

        if not history_data:
            return {
                'success': False,
                'error': '数据库中没有历史数据',
                'metadata': {},
                'data': [],
                'quality_report': {},
                'repair_report': {}
            }

        # 2. 质量检查
        quality_report = self.run_full_quality_check(history_data, trend_data)

        # 3. 数据修复
        if auto_repair:
            repaired_history, repair_report = self.repair_data(history_data, quality_report)
            # 对齐走势图数据
            standardized_data = self.align_history_and_trend(repaired_history, trend_data)
        else:
            repair_report = {'status': 'skipped'}
            standardized_data = history_data

        # 4. 生成元数据
        latest_issue = standardized_data[0]['issue'] if standardized_data else ''
        earliest_issue = standardized_data[-1]['issue'] if standardized_data else ''

        metadata = {
            'lottery_type': '排列5',
            'dataset_version': '1.0',
            'build_time': datetime.now().isoformat(),
            'record_count': len(standardized_data),
            'latest_issue': latest_issue,
            'earliest_issue': earliest_issue,
            'fields': [
                'issue', 'date', 'numbers', 'hezhi', 'span',
                'odd_even_ratio', 'odd_even_pattern', 'big_small_ratio', 'prime_composite_ratio',
                'trend_snapshot'
            ],
            'quality_score': quality_report.get('quality_score', 0),
            'auto_repair_enabled': auto_repair
        }

        # 5. 数据按数值排序（确保时间序列正确）
        standardized_data = sorted(standardized_data, key=lambda x: int(x['issue']) if x['issue'].isdigit() else 0, reverse=True)

        result = {
            'success': True,
            'metadata': metadata,
            'data': standardized_data,
            'quality_report': quality_report,
            'repair_report': repair_report
        }

        logger.info(f'标准化数据集构建完成: 共 {len(standardized_data)} 条记录, 质量评分 {metadata["quality_score"]}')
        return result

    # ==================== 5. AI分析系统输入接口 ====================

    def get_ai_analysis_input(self, limit: int = 120,
                               include_trend_snapshot: bool = False) -> Dict[str, Any]:
        """
        获取AI分析系统所需的标准化输入数据

        Args:
            limit: 获取最近多少期
            include_trend_snapshot: 是否包含走势快照（AI prompt可能较长，默认False）

        Returns:
            AI分析输入数据字典，结构与ai_analyzer.fetch_p5_data期望的格式一致
        """
        dataset = self.build_standardized_dataset(limit=limit, auto_repair=True)

        if not dataset['success']:
            return {
                'error': dataset.get('error', '数据加载失败'),
                'data_count': 0,
                'latest_issue': '',
                'latest_date': '',
                'history_data': [],
                'analysis': {},
                'quality_report': dataset.get('quality_report', {})
            }

        standardized_data = dataset['data']

        # 转换为AI分析器期望的格式
        history_data = []
        for item in standardized_data:
            ai_item = {
                'issue': item['issue'],
                'date': item['date'],
                'numbers': item['numbers'],
                'hezhi': item['hezhi'],
                'span': item['span'],
                'odd_even_ratio': item['odd_even_ratio'],
                'big_small_ratio': item['big_small_ratio'],
                'prime_composite_ratio': item['prime_composite_ratio']
            }
            if include_trend_snapshot:
                ai_item['trend_snapshot'] = item.get('trend_snapshot', {})
            history_data.append(ai_item)

        # 执行基础统计分析（供AI prompt使用）
        from modules.analyzer_p5 import P5Analyzer
        analyzer = P5Analyzer()
        analysis = {
            'frequency': analyzer.analyze_frequency(history_data),
            'hezhi': analyzer.analyze_hezhi(history_data),
            'odd_even': analyzer.analyze_odd_even(history_data),
            'span': analyzer.analyze_span(history_data),
            'big_small': analyzer.analyze_big_small(history_data),
            'prime_composite': analyzer.analyze_prime_composite(history_data),
            'repeats': analyzer.analyze_repeats(history_data),
            'consecutive': analyzer.analyze_consecutive(history_data)
        }

        latest = history_data[0] if history_data else {}

        return {
            'data_count': len(history_data),
            'latest_issue': latest.get('issue', ''),
            'latest_date': latest.get('date', ''),
            'history_data': history_data,
            'analysis': analysis,
            'quality_report': dataset.get('quality_report', {}),
            'metadata': dataset.get('metadata', {})
        }

    def save_quality_report(self, report: Dict[str, Any], filepath: Optional[str] = None) -> str:
        """
        保存数据质量报告到文件

        Args:
            report: 质量报告字典
            filepath: 保存路径，None则自动生成

        Returns:
            保存的文件路径
        """
        if filepath is None:
            os.makedirs('reports', exist_ok=True)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filepath = f'reports/p5_data_quality_report_{timestamp}.json'

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            logger.info(f'数据质量报告已保存: {filepath}')
            return filepath
        except Exception as e:
            logger.error(f'保存质量报告失败: {e}')
            return ''


# ==================== 便捷函数 ====================

def get_standardized_p5_dataset(limit: Optional[int] = None) -> Dict[str, Any]:
    """
    便捷函数：获取排列5标准化数据集
    """
    integrator = P5DataIntegrator()
    return integrator.build_standardized_dataset(limit=limit, auto_repair=True)


def get_p5_ai_input(limit: int = 120) -> Dict[str, Any]:
    """
    便捷函数：获取排列5 AI分析输入数据
    """
    integrator = P5DataIntegrator()
    return integrator.get_ai_analysis_input(limit=limit)


if __name__ == '__main__':
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    print('=== 测试排列5数据整合器 ===')
    integrator = P5DataIntegrator()
    dataset = integrator.build_standardized_dataset(limit=50, auto_repair=True)

    if dataset['success']:
        print(f'标准化数据集构建成功')
        print(f'记录数: {dataset["metadata"]["record_count"]}')
        print(f'质量评分: {dataset["metadata"]["quality_score"]}')
        print(f'最新期号: {dataset["metadata"]["latest_issue"]}')
        print(f'最早期号: {dataset["metadata"]["earliest_issue"]}')

        # 打印质量报告摘要
        qr = dataset['quality_report']
        print(f'\n质量检查摘要:')
        print(f'  缺失值: 和值缺失{qr["missing_values"]["summary"]["missing_hezhi_count"]}条, 跨度缺失{qr["missing_values"]["summary"]["missing_span_count"]}条')
        print(f'  异常值: 无效记录{qr["anomalies"]["summary"]["invalid_numbers_count"]}条, 重复期号{qr["anomalies"]["summary"]["duplicate_issues_count"]}条')
        print(f'  不一致: 号码不匹配{qr["inconsistency"]["summary"]["number_mismatch_count"]}条')

        # 保存报告
        integrator.save_quality_report(qr)
    else:
        print(f'构建失败: {dataset["error"]}')

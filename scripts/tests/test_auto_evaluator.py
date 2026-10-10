#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Unit tests for auto_evaluator.py
"""

import unittest
import sys
import os
from unittest.mock import Mock, patch, MagicMock

# Add the project root to sys.path so we can import modules
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from modules.auto_evaluator import (
    EvalResult,
    EvaluationReport,
    ProjectAutoEvaluator,
    DataHealthEvaluator,
    PredictionPerformanceEvaluator,
    AlgorithmWeightEvaluator,
    CachePerformanceEvaluator,
    EvolutionEngineEvaluator,
    ModuleDependencyEvaluator,
    AIModelEvaluator,
    CodeQualityEvaluator,
)


class TestEvalResult(unittest.TestCase):
    """Test cases for EvalResult class."""

    def setUp(self):
        self.result = EvalResult(
            name='test',
            category='test_category',
            status='healthy',
            score=85,
            details={'key': 'value'},
            recommendations=['Rec 1', 'Rec 2']
        )

    def test_initialization(self):
        self.assertEqual(self.result.name, 'test')
        self.assertEqual(self.result.category, 'test_category')
        self.assertEqual(self.result.status, 'healthy')
        self.assertEqual(self.result.score, 85)
        self.assertEqual(self.result.details, {'key': 'value'})
        self.assertEqual(self.result.recommendations, ['Rec 1', 'Rec 2'])
        # Check that recommendation states are initialized
        self.assertEqual(len(self.result.recommendation_states), 2)
        for rec_id, state in self.result.recommendation_states.items():
            self.assertEqual(state['status'], 'pending')
            self.assertIsNone(state['implemented_at'])
            self.assertIsNone(state['validated_at'])
            self.assertIsNone(state['effectiveness'])
            self.assertIsNone(state['validation_data'])

    def test_generate_recommendation_id(self):
        # Test that IDs are unique and follow the pattern
        rec_id_0 = self.result._generate_recommendation_id(0)
        rec_id_1 = self.result._generate_recommendation_id(1)
        self.assertIn('test_test_category_0_', rec_id_0)
        self.assertIn('test_test_category_1_', rec_id_1)
        self.assertNotEqual(rec_id_0, rec_id_1)

    def test_mark_recommendation_implemented(self):
        # Get a recommendation ID
        rec_id = list(self.result.recommendation_states.keys())[0]
        # Mark as implemented
        result = self.result.mark_recommendation_implemented(rec_id)
        self.assertTrue(result)
        state = self.result.recommendation_states[rec_id]
        self.assertEqual(state['status'], 'implemented')
        self.assertIsNotNone(state['implemented_at'])
        # Check that the timestamp is a valid ISO format string
        self.assertRegex(state['implemented_at'], r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$')
        # Trying to mark a non-existent ID should return False
        self.assertFalse(self.result.mark_recommendation_implemented('non_existent_id'))

    def test_mark_recommendation_validated(self):
        rec_id = list(self.result.recommendation_states.keys())[0]
        effectiveness = 0.5
        validation_data = {'test': 'data'}
        result = self.result.mark_recommendation_validated(rec_id, effectiveness, validation_data)
        self.assertTrue(result)
        state = self.result.recommendation_states[rec_id]
        self.assertEqual(state['status'], 'validated')
        self.assertEqual(state['effectiveness'], 0.5)
        self.assertEqual(state['validation_data'], validation_data)
        self.assertIsNotNone(state['validated_at'])
        # Test clamping of effectiveness
        self.result.mark_recommendation_validated(rec_id, 1.5, {})  # Should clamp to 1.0
        self.assertEqual(self.result.recommendation_states[rec_id]['effectiveness'], 1.0)
        self.result.mark_recommendation_validated(rec_id, -1.5, {})  # Should clamp to -1.0
        self.assertEqual(self.result.recommendation_states[rec_id]['effectiveness'], -1.0)
        # Non-existent ID
        self.assertFalse(self.result.mark_recommendation_validated('non_existent_id', 0.5, {}))

    def test_to_dict(self):
        result_dict = self.result.to_dict()
        self.assertEqual(result_dict['name'], 'test')
        self.assertEqual(result_dict['category'], 'test_category')
        self.assertEqual(result_dict['status'], 'healthy')
        self.assertEqual(result_dict['score'], 85)
        self.assertEqual(result_dict['details'], {'key': 'value'})
        self.assertEqual(len(result_dict['recommendations']), 2)
        for rec in result_dict['recommendations']:
            self.assertIn('id', rec)
            self.assertIn('text', rec)
            self.assertIn('status', rec)
            self.assertIn('implemented_at', rec)
            self.assertIn('validated_at', rec)
            self.assertIn('effectiveness', rec)
            self.assertIn('validation_data', rec)
        self.assertIn('timestamp', result_dict)


class TestEvaluationReport(unittest.TestCase):
    """Test cases for EvaluationReport class."""

    def setUp(self):
        self.result1 = EvalResult(
            name='Test 1',
            category='Category A',
            status='healthy',
            score=90,
            details={'detail1': 'value1'},
            recommendations=['Rec A1', 'Rec A2']
        )
        self.result2 = EvalResult(
            name='Test 2',
            category='Category A',
            status='warning',
            score=60,
            details={'detail2': 'value2'},
            recommendations=['Rec B1']
        )
        self.result3 = EvalResult(
            name='Test 3',
            category='Category B',
            status='critical',
            score=30,
            details={'detail3': 'value3'},
            recommendations=[]
        )
        self.results = [self.result1, self.result2, self.result3]
        self.report = EvaluationReport(
            version='1.0.0',
            scan_time='2026-10-06T12:00:00',
            results=self.results
        )

    def test_initialization(self):
        self.assertEqual(self.report.version, '1.0.0')
        self.assertEqual(self.report.scan_time, '2026-10-06T12:00:00')
        self.assertEqual(self.report.results, self.results)
        self.assertIsInstance(self.report.summary, dict)

    def test_compute_summary(self):
        summary = self.report.summary
        self.assertEqual(summary['total_checks'], 3)
        self.assertEqual(summary['healthy'], 1)
        self.assertEqual(summary['warning'], 1)
        self.assertEqual(summary['critical'], 1)
        # Overall score: (90 + 60 + 30) / 3 = 60
        self.assertEqual(summary['overall_score'], 60.0)
        self.assertEqual(summary['health_level'], 'degraded')
        # Recommendation stats
        rec_stats = summary['recommendation_stats']
        self.assertEqual(rec_stats['total'], 3)  # 2 + 1 + 0
        self.assertEqual(rec_stats['implemented'], 0)
        self.assertEqual(rec_stats['validated'], 0)
        self.assertEqual(rec_stats['positive_effect'], 0)

    def test_health_level(self):
        self.assertEqual(EvaluationReport._health_level(85), 'healthy')
        self.assertEqual(EvaluationReport._health_level(75), 'degraded')
        self.assertEqual(EvaluationReport._health_level(45), 'critical')
        self.assertEqual(EvaluationReport._health_level(50), 'degraded')
        self.assertEqual(EvaluationReport._health_level(80), 'healthy')

    def test_to_dict(self):
        report_dict = self.report.to_dict()
        self.assertEqual(report_dict['version'], '1.0.0')
        self.assertEqual(report_dict['scan_time'], '2026-10-06T12:00:00')
        self.assertEqual(report_dict['summary'], self.report.summary)
        self.assertEqual(len(report_dict['results']), 3)
        for i, result_dict in enumerate(report_dict['results']):
            self.assertEqual(result_dict, self.results[i].to_dict())

    def test_render_markdown(self):
        # Just ensure it runs without error and returns a string
        markdown = self.report.render_markdown()
        self.assertIsInstance(markdown, str)
        self.assertIn('# KPLuckyNumber 项目自动化评估报告', markdown)
        self.assertIn('## 总体健康度', markdown)
        self.assertIn('## 建议执行统计', markdown)
        self.assertIn('## Category A', markdown)
        self.assertIn('## Category B', markdown)
        self.assertIn('## 优先级排序的改进清单（含验证结果）', markdown)


class TestProjectAutoEvaluator(unittest.TestCase):
    """Test cases for ProjectAutoEvaluator class."""

    def setUp(self):
        self.evaluator = ProjectAutoEvaluator()

    def test_initialization(self):
        self.assertIsInstance(self.evaluator.version, str)
        # 与 ProjectAutoEvaluator._evaluators 注册数保持一致
        # （9 → 10：新增 ResultVerificationEvaluator，§4.4 结果自验证 + 分级建议）
        self.assertEqual(len(self.evaluator._evaluators), 10)
        self.assertIsInstance(self.evaluator._scan_history, list)
        self.assertIsNone(self.evaluator._schedule_thread)
        self.assertIsInstance(self.evaluator._schedule_stop, type(__import__('threading').Event()))

    @patch('modules.auto_evaluator.ProjectAutoEvaluator._get_version')
    def test_get_version(self, mock_get_version):
        mock_get_version.return_value = '2.0.0'
        version = self.evaluator._get_version()
        self.assertEqual(version, '2.0.0')
        mock_get_version.assert_called_once()

    def test_validate_historical_recommendations_no_history(self):
        # Create a report with some results
        result = EvalResult(
            name='Test',
            category='TestCat',
            status='healthy',
            score=80,
            details={},
            recommendations=['Rec 1']
        )
        # Mark one recommendation as implemented
        rec_id = list(result.recommendation_states.keys())[0]
        result.mark_recommendation_implemented(rec_id)
        report = EvaluationReport(
            version='1.0.0',
            scan_time='2026-10-06T12:00:00',
            results=[result]
        )
        # No history, should return the same report
        returned_report = self.evaluator._validate_historical_recommendations(report)
        self.assertIs(returned_report, report)
        # The recommendation should still be implemented but not validated
        state = returned_report.results[0].recommendation_states[rec_id]
        self.assertEqual(state['status'], 'implemented')
        self.assertIsNone(state['validated_at'])
        self.assertIsNone(state['effectiveness'])

    def test_validate_historical_recommendations_with_history(self):
        # Create a historical report
        historical_result = EvalResult(
            name='Test',
            category='TestCat',
            status='healthy',
            score=70,  # Lower score
            details={},
            recommendations=['Rec 1']
        )
        hist_report_dict = historical_result.to_dict()
        # Build a report-level history entry — the exact shape that
        # run_full_evaluation() stores (report.to_dict() → {'results': [...]}),
        # so _validate_historical_recommendations can read latest_history['results'].
        hist_entry = {
            'version': '1.0.0',
            'scan_time': '2026-10-05T12:00:00',
            'results': [hist_report_dict],
        }
        # Add to history
        self.evaluator._scan_history.append(hist_entry)

        # Create current report with improved score
        current_result = EvalResult(
            name='Test',
            category='TestCat',
            status='healthy',
            score=90,  # Improved
            details={},
            recommendations=['Rec 1']
        )
        # Mark the recommendation as implemented
        rec_id = list(current_result.recommendation_states.keys())[0]
        current_result.mark_recommendation_implemented(rec_id)
        current_report = EvaluationReport(
            version='1.0.0',
            scan_time='2026-10-06T12:00:00',
            results=[current_result]
        )
        # Mock the _calculate_recommendation_effectiveness method to return a fixed effectiveness
        with patch.object(self.evaluator, '_calculate_recommendation_effectiveness', return_value=0.5) as mock_eff:
            # Run validation
            validated_report = self.evaluator._validate_historical_recommendations(current_report)
            # Verify that the effectiveness method was called with appropriate arguments
            mock_eff.assert_called_once()
            # The recommendation should now be validated
            state = validated_report.results[0].recommendation_states[rec_id]
            self.assertEqual(state['status'], 'validated')
            self.assertIsNotNone(state['validated_at'])
            self.assertEqual(state['effectiveness'], 0.5)
            # Check that validation data is present
            self.assertIsInstance(state['validation_data'], dict)
            self.assertIn('historical_score', state['validation_data'])
            self.assertIn('current_score', state['validation_data'])
            self.assertEqual(state['validation_data']['historical_score'], 70)
            self.assertEqual(state['validation_data']['current_score'], 90)

    def test_calculate_recommendation_effectiveness(self):
        historical_data = {'score': 50}
        current_data = {'score': 70, 'name': 'Test', 'category': 'TestCat', 'details': {}}
        recommendation_text = 'Some recommendation'
        effectiveness = self.evaluator._calculate_recommendation_effectiveness(
            historical_data, current_data, recommendation_text
        )
        # Score improved from 50 to 70, relative improvement = (70-50)/50 = 0.4
        # Effectiveness = 0.4 * 10 = 4.0, but clamped to 1.0
        self.assertEqual(effectiveness, 1.0)

        # Score decreased
        current_data_worse = {'score': 40, 'name': 'Test', 'category': 'TestCat', 'details': {}}
        effectiveness_worse = self.evaluator._calculate_recommendation_effectiveness(
            historical_data, current_data_worse, recommendation_text
        )
        # Relative improvement = (40-50)/50 = -0.2
        # Effectiveness = -0.2 * 10 = -2.0, clamped to -1.0
        self.assertEqual(effectiveness_worse, -1.0)

        # No change
        current_data_same = {'score': 50, 'name': 'Test', 'category': 'TestCat', 'details': {}}
        effectiveness_same = self.evaluator._calculate_recommendation_effectiveness(
            historical_data, current_data_same, recommendation_text
        )
        self.assertEqual(effectiveness_same, 0.0)

        # Edge case: historical score zero
        historical_data_zero = {'score': 0}
        effectiveness_zero = self.evaluator._calculate_recommendation_effectiveness(
            historical_data_zero, current_data, recommendation_text
        )
        self.assertIsNone(effectiveness_zero)

    @patch('modules.auto_evaluator.ModelMonitorHealthEvaluator.evaluate')
    @patch('modules.auto_evaluator.DataHealthEvaluator.evaluate')
    @patch('modules.auto_evaluator.PredictionPerformanceEvaluator.evaluate')
    @patch('modules.auto_evaluator.AlgorithmWeightEvaluator.evaluate')
    @patch('modules.auto_evaluator.CachePerformanceEvaluator.evaluate')
    @patch('modules.auto_evaluator.EvolutionEngineEvaluator.evaluate')
    @patch('modules.auto_evaluator.ModuleDependencyEvaluator.evaluate')
    @patch('modules.auto_evaluator.AIModelEvaluator.evaluate')
    @patch('modules.auto_evaluator.CodeQualityEvaluator.evaluate')
    @patch('modules.auto_evaluator.ResultVerificationEvaluator.evaluate')
    def test_run_full_evaluation(
        self,
        mock_result_verification,
        mock_code_quality,
        mock_ai_model,
        mock_module_dep,
        mock_evolution,
        mock_cache,
        mock_algorithm_weight,
        mock_prediction_perf,
        mock_data_health,
        mock_model_monitor
    ):
        # Set up mock return values
        mock_data_health.return_value = EvalResult(
            name='Data Health', category='Data Layer', status='healthy', score=90,
            details={}, recommendations=[]
        )
        mock_prediction_perf.return_value = EvalResult(
            name='Prediction Perf', category='Prediction Performance', status='healthy', score=85,
            details={}, recommendations=[]
        )
        mock_algorithm_weight.return_value = EvalResult(
            name='Algorithm Weight', category='Algorithm Weight', status='healthy', score=95,
            details={}, recommendations=[]
        )
        mock_cache.return_value = EvalResult(
            name='Cache Perf', category='Cache Performance', status='healthy', score=80,
            details={}, recommendations=[]
        )
        mock_evolution.return_value = EvalResult(
            name='Evolution Engine', category='Self Evolution', status='healthy', score=75,
            details={}, recommendations=[]
        )
        mock_module_dep.return_value = EvalResult(
            name='Module Dep', category='Architecture Health', status='healthy', score=88,
            details={}, recommendations=[]
        )
        mock_ai_model.return_value = EvalResult(
            name='AI Model', category='AI Model', status='healthy', score=92,
            details={}, recommendations=[]
        )
        mock_code_quality.return_value = EvalResult(
            name='Code Quality', category='Code Quality', status='healthy', score=85,
            details={}, recommendations=[]
        )
        mock_model_monitor.return_value = EvalResult(
            name='Model Monitor', category='Model Monitor', status='healthy', score=100,
            details={'hit_rate_points': 0}, recommendations=[]
        )
        mock_result_verification.return_value = EvalResult(
            name='Result Verification', category='结果验证', status='healthy', score=88,
            details={'已验证批次': 1}, recommendations=[]
        )

        # Run the evaluation
        report = self.evaluator.run_full_evaluation()

        # Assertions
        self.assertIsInstance(report, EvaluationReport)
        self.assertEqual(report.version, self.evaluator.version)
        # 每个已注册评估器产出一条结果；用注册数断言，避免新增评估器后需改死数字
        self.assertEqual(len(report.results), len(self.evaluator._evaluators))
        # Check that each evaluator was called
        mock_data_health.assert_called_once()
        mock_prediction_perf.assert_called_once()
        mock_algorithm_weight.assert_called_once()
        mock_cache.assert_called_once()
        mock_evolution.assert_called_once()
        mock_module_dep.assert_called_once()
        mock_ai_model.assert_called_once()
        mock_code_quality.assert_called_once()
        mock_model_monitor.assert_called_once()
        mock_result_verification.assert_called_once()
        # Check overall score (average of all scores) with a tolerance of 0.1
        scores = [
            mock_result_verification.return_value.score,
            mock_data_health.return_value.score,
            mock_prediction_perf.return_value.score,
            mock_algorithm_weight.return_value.score,
            mock_cache.return_value.score,
            mock_evolution.return_value.score,
            mock_module_dep.return_value.score,
            mock_ai_model.return_value.score,
            mock_code_quality.return_value.score,
            mock_model_monitor.return_value.score
        ]
        expected_score = sum(scores) / len(scores)
        self.assertAlmostEqual(report.summary['overall_score'], expected_score, delta=0.1)

    @patch('modules.auto_evaluator.DataHealthEvaluator.evaluate')
    @patch('modules.auto_evaluator.AlgorithmWeightEvaluator.evaluate')
    @patch('modules.auto_evaluator.AIModelEvaluator.evaluate')
    def test_run_quick_evaluation(
        self,
        mock_ai_model,
        mock_algorithm_weight,
        mock_data_health
    ):
        # Set up mock return values
        mock_data_health.return_value = EvalResult(
            name='Data Health', category='Data Layer', status='healthy', score=90,
            details={}, recommendations=[]
        )
        mock_algorithm_weight.return_value = EvalResult(
            name='Algorithm Weight', category='Algorithm Weight', status='healthy', score=95,
            details={}, recommendations=[]
        )
        mock_ai_model.return_value = EvalResult(
            name='AI Model', category='AI Model', status='healthy', score=92,
            details={}, recommendations=[]
        )

        # Run the quick evaluation
        report = self.evaluator.run_quick_evaluation()

        # Assertions
        self.assertIsInstance(report, EvaluationReport)
        self.assertEqual(len(report.results), 3)
        mock_data_health.assert_called_once()
        mock_algorithm_weight.assert_called_once()
        mock_ai_model.assert_called_once()
        # Overall score should be average of the three with a tolerance of 0.1
        scores = [
            mock_data_health.return_value.score,
            mock_algorithm_weight.return_value.score,
            mock_ai_model.return_value.score
        ]
        expected_score = sum(scores) / len(scores)
        self.assertAlmostEqual(report.summary['overall_score'], expected_score, delta=0.1)


if __name__ == '__main__':
    unittest.main()
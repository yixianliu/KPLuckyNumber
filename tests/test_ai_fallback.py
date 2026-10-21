# -*- coding: utf-8 -*-
"""
AI 结果校验 / 重试 / 降级 单元测试（v5.0 §6.1.3 / §6.1.4 / §6.3 校验节点）
=========================================================================

验收断言（对应 docs/KPLuckyNumber_upgrade_plan_v5.md §6.2）：
1. AI 返回非合规 JSON 时自动重试 1 次（backup_model），仍失败降级不崩溃
2. 校验器强制 number∈[0,9]、confidence∈[0,1]
3. 配置经 reload_agnes_config() 免重启生效
4. 全程不发真实网络请求（_call_ai_model 被 mock）

【风险提示】排列五开奖为完全随机的概率事件，降级路径只保证「不产出误导性报告」，
不保证任何命中率。
"""

import json
import os
import sys
import time
import unittest
from unittest import mock

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules import ai_analyzer  # noqa: E402
from modules.ai_analyzer import AIAnalyzer, validate_ai_result  # noqa: E402
import config  # noqa: E402


VALID_RESULT = {
    'predictions': {
        'wan': [{'number': 3, 'confidence': 0.24, 'reason': '近20期出现5次'}],
        'qian': [{'number': 7, 'confidence': 0.22, 'reason': '遗漏3期'}],
        'bai': [{'number': 1, 'confidence': 0.21, 'reason': '频率偏低'}],
        'shi': [{'number': 9, 'confidence': 0.23, 'reason': '近5期出现2次'}],
    },
    'risk_warning': '本分析仅为历史数据的统计描述，请理性购彩。',
}

INVALID_NUMBER_RESULT = {
    'predictions': {'wan': [{'number': 13, 'confidence': 0.24,
                             'reason': 'x'}]},
    'risk_warning': 'y',
}

INVALID_CONF_RESULT = {
    'predictions': {'wan': [{'number': 3, 'confidence': 1.5, 'reason': 'x'}]},
    'risk_warning': 'y',
}


def _make_data():
    """构造最小可用的数据源字典（无需数据库）。"""
    history = [{
        'issue': '2026001', 'draw_date': '2026-01-01',
        'wan': 1, 'qian': 2, 'bai': 3, 'shi': 4, 'ge': 5,
        'hezhi': 15, 'span': 4,
        'odd_even_ratio': '2:3', 'big_small_ratio': '2:3',
    }]
    return {
        'data_count': 1, 'latest_issue': '2026000', 'history_data': history,
        'trend_data': [], 'wan_trend_data': [], 'qian_trend_data': [],
        'bai_trend_data': [], 'shi_trend_data': [],
    }


class TestValidateAiResult(unittest.TestCase):
    """§6.1.3 字段白名单 + 值域校验"""

    def test_valid_result_passes(self):
        result = validate_ai_result(VALID_RESULT)
        self.assertTrue(result['ok'])
        self.assertEqual(result['errors'], [])

    def test_number_out_of_range_fails(self):
        result = validate_ai_result(INVALID_NUMBER_RESULT)
        self.assertFalse(result['ok'])
        self.assertTrue(any('超出 0-9' in e for e in result['errors']))

    def test_confidence_out_of_range_fails(self):
        result = validate_ai_result(INVALID_CONF_RESULT)
        self.assertFalse(result['ok'])
        self.assertTrue(any('超出 0-1' in e for e in result['errors']))

    def test_missing_predictions_fails(self):
        result = validate_ai_result({'risk_warning': 'x'})
        self.assertFalse(result['ok'])
        self.assertTrue(any('predictions' in e for e in result['errors']))

    def test_empty_predictions_list_fails(self):
        result = validate_ai_result({'predictions': {'wan': []}})
        self.assertFalse(result['ok'])

    def test_non_dict_prediction_item_fails(self):
        result = validate_ai_result({'predictions': {'wan': ['not-a-dict']}})
        self.assertFalse(result['ok'])

    def test_non_integer_number_fails(self):
        result = validate_ai_result({
            'predictions': {'wan': [{'number': 'abc', 'confidence': 0.5}]}})
        self.assertFalse(result['ok'])

    def test_missing_confidence_fails(self):
        result = validate_ai_result({'predictions': {'wan': [{'number': 3}]}})
        self.assertFalse(result['ok'])

    def test_none_and_empty_fail(self):
        for bad in (None, {}, [], 'text', 42):
            result = validate_ai_result(bad)
            self.assertFalse(result['ok'], f'{bad!r} 应判不合规')

    def test_high_confidence_warns_but_passes(self):
        """高置信度是诚实边界冲突，记 warning 但不阻断（避免误杀正常输出）。"""
        result = validate_ai_result({
            'predictions': {'wan': [{'number': 3, 'confidence': 0.95}]},
            'risk_warning': 'x',
        })
        self.assertTrue(result['ok'])
        self.assertTrue(any('偏高' in w for w in result['warnings']))

    def test_missing_risk_warning_warns_only(self):
        result = validate_ai_result({
            'predictions': {'wan': [{'number': 3, 'confidence': 0.2}]}})
        self.assertTrue(result['ok'])
        self.assertTrue(any('risk_warning' in w for w in result['warnings']))

    def test_boundary_values_accepted(self):
        """number 0 与 9、confidence 0 与 1 都是合法边界值。"""
        result = validate_ai_result({
            'predictions': {
                'wan': [
                    {'number': 0, 'confidence': 0.0},
                    {'number': 9, 'confidence': 1.0},
                ]},
            'risk_warning': 'x',
        })
        self.assertTrue(result['ok'], f'边界值应通过: {result["errors"]}')

    def test_string_number_coerced(self):
        """AI 常把数字输出成字符串，校验器应宽容地接受可转整数的形式。"""
        result = validate_ai_result({
            'predictions': {'wan': [{'number': '3', 'confidence': '0.2'}]},
            'risk_warning': 'x',
        })
        self.assertTrue(result['ok'], f'字符串数字应接受: {result["errors"]}')

    def test_errors_are_actionable(self):
        """错误信息应定位到具体字段，便于排查而非笼统报错。"""
        result = validate_ai_result(INVALID_NUMBER_RESULT)
        self.assertTrue(any('wan' in e and 'number' in e
                            for e in result['errors']))


class TestAnalyzeFallback(unittest.TestCase):
    """§6.1.3 analyze：非合规 JSON → 换 backup_model 重试 → 仍失败降级不崩溃"""

    @classmethod
    def setUpClass(cls):
        cls.ai = AIAnalyzer()
        cls.ai.ai_available = True
        cls.ai.api_key = 'test-key'
        cls.ai.backup_model = 'agnes-2.5-flash'
        cls.ai.model_name = 'agnes-3.0-flash'

    def _patch_fetch(self):
        return mock.patch.object(self.ai, '_fetch_data_from_database',
                                 return_value=_make_data())

    def test_valid_result_returns_success(self):
        with self._patch_fetch(), \
                mock.patch.object(self.ai, '_call_ai_model',
                                  return_value=json.dumps(VALID_RESULT)):
            result = self.ai.analyze(data_limit=5)
        self.assertTrue(result['success'], result.get('error'))
        self.assertIn('report', result)

    def test_invalid_then_backup_retry_succeeds(self):
        """主模型返回非法结果，备份模型返回合规结果 → 采用备份模型结果。"""
        calls = []

        def _fake_call(messages, max_tokens=None, temperature=None,
                       model=None, **kw):
            calls.append(model or self.ai.model_name)
            if (model or self.ai.model_name) == self.ai.model_name:
                return json.dumps(INVALID_NUMBER_RESULT)
            return json.dumps(VALID_RESULT)
        with self._patch_fetch(), mock.patch.object(
                self.ai, '_call_ai_model', side_effect=_fake_call):
            result = self.ai.analyze(data_limit=5)
        self.assertTrue(result['success'], result.get('error'))
        self.assertEqual(len(calls), 2, '应恰好重试一次')
        self.assertEqual(calls[0], self.ai.model_name)
        self.assertEqual(calls[1], self.ai.backup_model)

    def test_invalid_both_models_degrades_without_crash(self):
        """两次都不合规 → 降级返回 success=False，不抛异常、不产出报告。"""
        with self._patch_fetch(), \
                mock.patch.object(
                    self.ai, '_call_ai_model',
                    return_value=json.dumps(INVALID_NUMBER_RESULT)):
            result = self.ai.analyze(data_limit=5)
        self.assertFalse(result['success'])
        self.assertIsNone(result['report'])
        self.assertIn('降级', result['error'])

    def test_retry_disabled_skips_backup(self):
        """retry_on_invalid=False 时应直接降级，不多打一次备份模型。"""
        fake_cfg = {
            'history_periods': 20, 'inject_target_issue': True,
            'few_shot': True, 'honesty_boundary': True,
            'retry_on_invalid': False,
            'history_periods_min': 5, 'history_periods_max': 60,
        }
        calls = []

        def _fake_call(messages, max_tokens=None, temperature=None,
                       model=None, **kw):
            calls.append(model or self.ai.model_name)
            return json.dumps(INVALID_NUMBER_RESULT)
        with self._patch_fetch(), \
                mock.patch.object(ai_analyzer, '_load_prompt_config',
                                  return_value=fake_cfg), \
                mock.patch.object(self.ai, '_call_ai_model',
                                  side_effect=_fake_call):
            result = self.ai.analyze(data_limit=5)
        self.assertFalse(result['success'])
        self.assertEqual(len(calls), 1, '关闭重试后不应调用备份模型')

    def test_unparseable_response_degrades(self):
        """完全无法解析的响应 → 降级，不崩溃。"""
        with self._patch_fetch(), \
                mock.patch.object(self.ai, '_call_ai_model',
                                  return_value='这不是 JSON'):
            result = self.ai.analyze(data_limit=5)
        self.assertFalse(result['success'])
        self.assertIn('解析失败', result['error'])

    def test_model_call_failure_degrades(self):
        """网络层完全失败（返回 None）→ 降级为调用失败。"""
        with self._patch_fetch(), \
                mock.patch.object(self.ai, '_call_ai_model',
                                  return_value=None):
            result = self.ai.analyze(data_limit=5)
        self.assertFalse(result['success'])
        self.assertIn('调用失败', result['error'])

    def test_no_crash_on_any_path(self):
        """所有降级路径都不得抛异常（诚实边界：宁可不给，不给错）。"""
        with self._patch_fetch(), \
                mock.patch.object(
                    self.ai, '_call_ai_model',
                    return_value=json.dumps(INVALID_CONF_RESULT)):
            result = self.ai.analyze(data_limit=5)
        self.assertIsInstance(result, dict)
        self.assertIn('success', result)

    def test_uses_system_role_in_messages(self):
        """请求必须带独立 system 段（含诚实边界），而非只有一句泛化描述。"""
        captured = {}

        def _fake_call(messages, **kw):
            captured['messages'] = messages
            return json.dumps(VALID_RESULT)
        with self._patch_fetch(), \
                mock.patch.object(self.ai, '_call_ai_model',
                                  side_effect=_fake_call):
            self.ai.analyze(data_limit=5)
        roles = [m['role'] for m in captured['messages']]
        self.assertIn('system', roles)
        self.assertIn('user', roles)
        system_text = captured['messages'][0]['content']
        self.assertIn('诚实边界', system_text)


_MISSING = object()


class _EnvPatch:
    """只增删目标环境变量键的补丁。

    不用 mock.patch.dict(os.environ, ...) —— 机器上存在超长环境变量
    （>32767 字符）时，patch.dict 在还原阶段会对整个 environ 做 update 并抛
    ValueError。这里只保存/恢复被改动的键，避开该陷阱。
    """

    def __init__(self, **kv):
        self._kv = kv
        self._saved = {}

    def __enter__(self):
        for k, v in self._kv.items():
            self._saved[k] = os.environ.get(k, _MISSING)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return self

    def __exit__(self, *exc):
        for k, v in self._saved.items():
            if v is _MISSING:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return False


class TestReloadAgnesConfig(unittest.TestCase):
    """§6.1.4 配置热更新：改环境变量后免重启生效"""

    @classmethod
    def setUpClass(cls):
        cls.ai = AIAnalyzer()

    def test_reload_reads_env_override(self):
        with _EnvPatch(**{'AGNES_MODEL_NAME': 'agnes-test-model'}):
            ok = self.ai.reload_agnes_config()
        self.assertIsInstance(ok, bool)
        self.assertEqual(self.ai.model_name, 'agnes-test-model')

    def test_reload_without_key_returns_false(self):
        """密钥为空时应返回 False（同时清掉 config 里可能残留的真实密钥）。"""
        with _EnvPatch(**{'AGNES_API_KEY': ''}), \
                mock.patch.dict(config.AGNES_API_CONFIG, api_key=''):
            self.ai.api_key = ''
            ok = self.ai.reload_agnes_config()
        self.assertFalse(ok)

    def test_reload_updates_timeout_from_env(self):
        with _EnvPatch(**{'AGNES_TIMEOUT': '99'}):
            self.ai.reload_agnes_config()
        self.assertEqual(self.ai.timeout, 99)

    def test_reload_parses_bool_env_correctly(self):
        """bool('false') 为 True，必须显式解析，否则开关会反了。"""
        with _EnvPatch(**{'AGNES_STREAM': 'false'}):
            self.ai.reload_agnes_config()
        self.assertFalse(self.ai.stream)
        with _EnvPatch(**{'AGNES_STREAM': 'true'}):
            self.ai.reload_agnes_config()
        self.assertTrue(self.ai.stream)

    def test_reload_never_raises(self):
        """热更新失败必须静默降级，不得影响 AI 调用主流程。"""
        import config
        with _EnvPatch(**{'AGNES_API_KEY': ''}), \
                mock.patch.dict(config.AGNES_API_CONFIG, api_key=''), \
                mock.patch.object(self.ai, '_init_ai_config',
                                  side_effect=RuntimeError('boom')):
            try:
                self.ai.reload_agnes_config()
            except Exception:
                self.fail('reload_agnes_config 不应抛出异常')

    def test_headers_built_when_key_present(self):
        self.ai.api_key = ''
        self.ai.headers = {}
        with _EnvPatch(**{'AGNES_API_KEY': 'live-key-123'}):
            ok = self.ai.reload_agnes_config()
        self.assertTrue(ok)
        self.assertEqual(self.ai.api_key, 'live-key-123')
        self.assertEqual(self.ai.headers.get('Authorization'),
                         'Bearer live-key-123')

    def test_apply_api_config_reflects_into_attributes(self):
        """api_config 改动必须回写到实例属性——这是热更新生效的前提。"""
        self.ai.api_config = {
            'api_url': 'http://example.invalid/v1/chat/completions',
            'api_key': 'k', 'model_name': 'm-1', 'backup_model': 'm-2',
            'timeout': 7, 'max_tokens': 512, 'temperature': 0.1,
            'top_p': 0.9, 'stream': True, 'max_retries': 2,
            'retry_backoff_factor': 1.0, 'pool_connections': 3,
            'pool_maxsize': 4, 'response_format': 'json_object',
        }
        self.ai._apply_api_config()
        self.assertEqual(self.ai.timeout, 7)
        self.assertEqual(self.ai.default_max_tokens, 512)
        self.assertEqual(self.ai.model_name, 'm-1')
        self.assertEqual(self.ai.max_retries, 2)
        self.assertTrue(self.ai.stream)
        self.assertTrue(self.ai.ai_available)


class _FakeResp:
    """最小 HTTP 响应替身（仅实现 _call_ai_model 消费的契约）。"""

    def __init__(self, payload=None, status_code=200, json_raises=None):
        self.status_code = status_code
        self._payload = payload
        self._json_raises = json_raises

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(
                f'HTTP {self.status_code}', response=self)

    def json(self):
        if self._json_raises is not None:
            raise self._json_raises
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _FakeSession:
    """录制型假 Session：全程不发真实网络请求（A24 用例的核心替身）。

    behaviors 按调用次序依次消费（耗尽后重复最后一项）：
        - _FakeResp 实例      → 直接作为响应返回；
        - Exception 实例      → 在 request() 入口抛出（连接/超时类故障）；
        - dict                → 包装为 200 成功响应体；
        - 可调用对象(timeout) → 以分级超时元组为参求值，结果按上述规则解释。
    sleep_read_to=True 时先睡读超时（timeout[1]），模拟「响应慢到读超时」。
    """

    def __init__(self, behaviors, sleep_read_to=False):
        self.calls = []
        self._behaviors = list(behaviors)
        self._sleep_read_to = sleep_read_to

    def request(self, method, url, headers=None, data=None, timeout=None):
        self.calls.append({
            'method': method, 'url': url, 'headers': headers,
            'data': data, 'timeout': timeout,
        })
        if self._sleep_read_to and timeout and len(timeout) == 2:
            time.sleep(timeout[1])
        behavior = self._behaviors[min(len(self.calls) - 1,
                                       len(self._behaviors) - 1)]
        if callable(behavior):
            behavior = behavior(timeout)
        if isinstance(behavior, _FakeResp):
            return behavior
        if isinstance(behavior, Exception):
            raise behavior
        return _FakeResp(behavior)


def _ai_content():
    """构造一次合规的 AI 成功响应体（choices→message→content 为 JSON 串）。"""
    content = {
        'insight': '后验分布整体分散，公平摇号下无稳定规律。',
        'flagged_digits': {'wan': [3], 'qian': [7], 'bai': [1],
                           'shi': [9], 'ge': [5]},
        'confidence_notes': '本洞察仅描述历史统计特征，存在局限。',
        'caution': '请理性购彩。',
    }
    return {
        'choices': [{'message': {'content': json.dumps(content)},
                     'finish_reason': 'stop'}],
        'usage': {'total_tokens': 10},
    }


class TestAiCallBudget(unittest.TestCase):
    """ALG-02 AI 调用预算治理（v5.2）：分级超时 + run 级熔断 + 降级不崩。

    验收口径（对应项目方案 A22-A25）：
      A22 单次调用墙钟 ≤30s（connect 一级=soft，read 一级 ≤soft 且 ≤剩余墙钟）
      A23 单期（一次 predict）总墙钟 ≤60s，超预算 100% 降级且用户可见提示
      A24 超时 / 4xx / 熔断 / backup / 断网 / 空响应 六类降级路径
      A25 _ai_augment_bayesian 任何异常都不得击穿融合流程（一律降级为 {}）

    诚实边界：排列五开奖为完全随机事件，本组用例只验证「预算治理与降级
    不崩」的工程契约，不验证任何命中率提升。
    """

    @classmethod
    def setUpClass(cls):
        # 懒加载：与 tests/test_predictor.py 同一惯例，避免导入期重依赖
        from modules.predictor import P5Predictor
        cls.P5Predictor = P5Predictor

    def setUp(self):
        self.predictor = self.P5Predictor()
        p = self.predictor
        # 构造「AI 可用」最小子配置（真实请求被 _build_ai_session 打假截断）
        p.api_key = 'test-key'
        p.ai_available = True
        p.ai_auxiliary_available = True
        p.headers = {'Authorization': 'Bearer test-key'}
        p.api_url = 'http://example.invalid/v1/chat/completions'
        p.model_name = 'main-model'
        p.backup_model = 'backup-model'
        # 默认给「宽裕」预算：4xx/backup/成功类用例无需再配置；
        # 预算敏感用例在各自 _set_budget 中显式收窄
        self._set_budget(soft=5.0, hard=10.0, per_run=2)

    def _set_budget(self, soft, hard, per_run, enabled=True, used=0.0):
        """设置实例级预算（字段契约对齐 _init_ai_config 的加载结果）。"""
        p = self.predictor
        p.ai_budget_enabled = enabled
        p.ai_soft_timeout = soft
        p.ai_hard_timeout = hard
        p.ai_per_run_max_calls = per_run
        p.ai_run_budget_seconds = per_run * hard
        p._ai_run_wall_seconds = used
        p._ai_budget_exhausted = ''

    def _fake_session(self, behaviors, sleep_read_to=False):
        """构造录制型假 Session。"""
        return _FakeSession(behaviors, sleep_read_to=sleep_read_to)

    # ==================== A22：单次调用 ≤30s ====================

    def test_production_budget_timeout_tuple_within_hard_limit(self):
        """生产默认预算下，传给 Session 的分级超时必须落在硬上限内。"""
        self._set_budget(soft=20.0, hard=30.0, per_run=2)   # 生产默认值
        session = self._fake_session([_ai_content()])
        with mock.patch.object(self.P5Predictor, '_build_ai_session',
                               return_value=session):
            content = self.predictor._call_ai_model('测试提示词')
        self.assertTrue(content)
        self.assertEqual(len(session.calls), 1)
        soft, read_to = session.calls[0]['timeout']
        self.assertEqual(soft, 20.0)                 # connect 一级 = soft
        self.assertLessEqual(read_to, 30.0)          # A22：read 一级 ≤ hard

    def test_slow_response_cut_by_wall_clock_cap(self):
        """慢响应（读超时）场景：单次总墙钟受 hard 上限约束，不留死角。"""
        self._set_budget(soft=0.5, hard=1.5, per_run=2)
        session = self._fake_session(
            [requests.exceptions.ReadTimeout('slow')], sleep_read_to=True)
        _t0 = time.monotonic()
        with mock.patch.object(self.P5Predictor, '_build_ai_session',
                               return_value=session):
            result = self.predictor._call_ai_model('测试提示词')
        _elapsed = time.monotonic() - _t0
        self.assertIsNone(result)
        # A22：单次墙钟 ≤ hard（含退避 sleep 与调度余量）
        self.assertLess(_elapsed, 1.5 + 0.35)
        for _c in session.calls:
            self.assertLessEqual(_c['timeout'][1], 1.5)
        # 预算截断必须留下用户可见提示（避免降级被误读为故障）
        self.assertTrue(self.predictor._ai_budget_exhausted)

    # ==================== A23：单期总预算 ≤60s / 100% 降级 ====================

    def test_run_budget_breaker_blocks_new_request(self):
        """run 预算耗尽后直接熔断降级：不发请求、留用户可见提示。"""
        # run_budget=0.2，已用 0.2（耗尽态）
        self._set_budget(soft=0.1, hard=0.2, per_run=1, used=0.2)
        session = self._fake_session([_ai_content()])
        with mock.patch.object(self.P5Predictor, '_build_ai_session',
                               return_value=session):
            result = self.predictor._call_ai_model('测试提示词')
        self.assertIsNone(result)
        self.assertEqual(session.calls, [])          # 未发任何请求
        self.assertTrue(self.predictor._ai_budget_exhausted)

    def test_remaining_budget_below_margin_stops_attempts(self):
        """剩余预算不足 0.5s 安全余量时不再起新尝试，直接降级。"""
        # run_budget=0.5，已用 0.3 → call_cap=0.2 < 0.5 安全余量
        self._set_budget(soft=0.1, hard=0.5, per_run=1, used=0.3)
        session = self._fake_session([_ai_content()])
        with mock.patch.object(self.P5Predictor, '_build_ai_session',
                               return_value=session):
            result = self.predictor._call_ai_model('测试提示词')
        self.assertIsNone(result)
        self.assertEqual(session.calls, [])
        self.assertTrue(self.predictor._ai_budget_exhausted)

    def test_run_budget_shared_between_main_and_auxiliary(self):
        """主调用与贝叶斯辅助共用单期预算：主调用耗尽后辅助零请求降级。"""
        self._set_budget(soft=0.6, hard=1.0, per_run=1)   # run_budget=1.0
        session = self._fake_session(
            [requests.exceptions.ReadTimeout('slow')], sleep_read_to=True)
        with mock.patch.object(self.P5Predictor, '_build_ai_session',
                               return_value=session):
            self.predictor._call_ai_model('主提示词')      # 唯一一次尝试
            aux = self.predictor._ai_augment_bayesian(
                [{n: 0.1 for n in range(10)} for _ in range(5)],
                {'verification_count': 0, 'full_match_rate': 0.0})
        self.assertEqual(aux, {})                          # 辅助降级为 {}
        self.assertEqual(len(session.calls), 1)            # 辅助未发新请求
        self.assertTrue(self.predictor._ai_budget_exhausted)

    def test_auxiliary_succeeds_when_budget_sufficient(self):
        """预算充足时贝叶斯辅助正常产出洞察（治理不误伤正常路径）。"""
        self._set_budget(soft=2.0, hard=3.0, per_run=2)    # run_budget=6.0
        session = self._fake_session([_ai_content()])
        with mock.patch.object(self.P5Predictor, '_build_ai_session',
                               return_value=session):
            aux = self.predictor._ai_augment_bayesian(
                [{n: 0.1 for n in range(10)} for _ in range(5)],
                {'verification_count': 3, 'full_match_rate': 0.0})
        self.assertIn('insight', aux)
        self.assertEqual(aux['model'], self.predictor.model_name)
        self.assertEqual(self.predictor._ai_budget_exhausted, '')

    def test_predict_degrades_with_notice_and_survives(self):
        """集成（A23）：AI 熔断时 predict() 不崩，结果带用户可见降级提示。"""
        self._set_budget(soft=0.1, hard=0.2, per_run=1)    # run_budget=0.2
        session = self._fake_session(
            [requests.exceptions.ReadTimeout('slow')], sleep_read_to=True)
        mock_dist = [{n: 0.1 for n in range(10)} for _ in range(5)]
        p = self.predictor
        history = [{'issue': f'2026{i:04d}',
                    'numbers': [i % 10, (i + 1) % 10, (i + 2) % 10,
                                (i + 3) % 10, (i + 4) % 10]}
                   for i in range(100)]
        # 算法层全部打假（惯例见 tests/test_predictor.py），聚焦 AI 预算段
        with mock.patch.object(p, '_algo_frequency_weighted',
                               return_value=mock_dist), \
                mock.patch.object(p, '_algo_omission_regression',
                                  return_value=mock_dist), \
                mock.patch.object(p, '_algo_bayesian_inference',
                                  return_value=mock_dist), \
                mock.patch.object(p, '_algo_trend_momentum',
                                  return_value=mock_dist), \
                mock.patch.object(p, '_algo_markov_transition',
                                  return_value=mock_dist), \
                mock.patch.object(p, '_algo_pattern_continuation',
                                  return_value=mock_dist), \
                mock.patch.object(p, '_algo_feature_engineering',
                                  return_value=mock_dist), \
                mock.patch.object(p, '_build_ai_prompt',
                                  return_value='测试提示词'), \
                mock.patch.object(self.P5Predictor, '_build_ai_session',
                                  return_value=session):
            result = p.predict(history, '20260100')
        self.assertNotIn('error', result)
        self.assertTrue(result['ai_degraded_notice'])
        self.assertEqual(result['ai_result'], {})
        self.assertEqual(result['bayesian_ai_auxiliary'], {})

    # ==================== A24：六类降级路径 ====================

    def test_http_429_returns_none_without_retry(self):
        """4xx（限流）不重试：一次失败即降级，避免浪费配额。"""
        session = self._fake_session([_FakeResp(None, 429)])
        with mock.patch.object(self.P5Predictor, '_build_ai_session',
                               return_value=session):
            result = self.predictor._call_ai_model('测试提示词')
        self.assertIsNone(result)
        self.assertEqual(len(session.calls), 1)

    def test_http_404_falls_back_to_backup_model(self):
        """404/410 降级 backup_model：主模型不存在时备份模型成功返回。"""
        session = self._fake_session([_FakeResp(None, 404), _ai_content()])
        with mock.patch.object(self.P5Predictor, '_build_ai_session',
                               return_value=session):
            result = self.predictor._call_ai_model('测试提示词')
        self.assertTrue(result)
        self.assertEqual(len(session.calls), 2)
        models = [json.loads(_c['data'])['model'] for _c in session.calls]
        self.assertEqual(models, ['main-model', 'backup-model'])

    def test_connection_error_with_explicit_timeout_override(self):
        """断网 + 显式关键字覆盖预算：异常必须被吞掉降级，不上抛。"""
        session = self._fake_session(
            [requests.exceptions.ConnectionError('网络不可达')])
        with mock.patch.object(self.P5Predictor, '_build_ai_session',
                               return_value=session):
            result = self.predictor._call_ai_model(
                '测试提示词', soft_timeout=0.5, hard_timeout=1.0)
        self.assertIsNone(result)
        # 剩余预算不足安全余量即停，不会重试到 4 次耗尽配额
        self.assertEqual(len(session.calls), 1)

    def test_empty_content_returns_none(self):
        """200 但 content 为空：判格式异常返回 None（由调用方走降级）。"""
        session = self._fake_session(
            [{'choices': [{'message': {'content': ''}}]}])
        with mock.patch.object(self.P5Predictor, '_build_ai_session',
                               return_value=session):
            result = self.predictor._call_ai_model('测试提示词')
        self.assertIsNone(result)
        self.assertEqual(len(session.calls), 1)

    def test_json_decode_error_returns_none(self):
        """服务端返回非 JSON 体：解析异常被吞掉降级，不上抛。"""
        session = self._fake_session([
            _FakeResp(None, json_raises=json.JSONDecodeError('boom', '', 0)),
        ])
        with mock.patch.object(self.P5Predictor, '_build_ai_session',
                               return_value=session):
            result = self.predictor._call_ai_model('测试提示词')
        self.assertIsNone(result)
        self.assertEqual(len(session.calls), 1)

    def test_config_budget_defaults_match_acceptance(self):
        """config.AI_CALL_BUDGET 默认值必须落在 A22/A23 验收区间内。"""
        import importlib
        ai_env_keys = ('ENABLE_AI_BUDGET', 'AI_SOFT_TIMEOUT',
                       'AI_HARD_TIMEOUT', 'AI_PER_RUN_MAX_CALLS')
        # 清空 AI_* 环境变量后 reload，观测「出厂默认值」
        with _EnvPatch(**{k: None for k in ai_env_keys}):
            try:
                importlib.reload(config)
                budget = config.AI_CALL_BUDGET
                self.assertTrue(budget['enabled'])
                self.assertLess(budget['soft_timeout'],
                                budget['hard_timeout'])
                self.assertLessEqual(budget['hard_timeout'], 30.0)   # A22
                self.assertLessEqual(
                    budget['per_run_max_calls'] * budget['hard_timeout'],
                    60.0)                                            # A23
            finally:
                importlib.reload(config)   # 还原进程级配置，防污染其他用例

    # ==================== A25：异常时融合流程不崩 ====================

    def test_augment_returns_empty_when_ai_call_raises(self):
        """_call_ai_model 抛异常时 _ai_augment_bayesian 必须降级为 {}。"""
        with mock.patch.object(self.predictor, '_call_ai_model',
                               side_effect=RuntimeError('boom')):
            aux = self.predictor._ai_augment_bayesian(
                [{n: 0.1 for n in range(10)} for _ in range(5)],
                {'verification_count': 0, 'full_match_rate': 0.0})
        self.assertEqual(aux, {})

    def test_augment_returns_empty_when_parse_raises(self):
        """_parse_ai_response 抛异常时同样降级为 {}，不击穿融合流程。"""
        session = self._fake_session([_ai_content()])
        with mock.patch.object(self.P5Predictor, '_build_ai_session',
                               return_value=session), \
                mock.patch.object(self.predictor, '_parse_ai_response',
                                  side_effect=ValueError('bad json')):
            aux = self.predictor._ai_augment_bayesian(
                [{n: 0.1 for n in range(10)} for _ in range(5)],
                {'verification_count': 0, 'full_match_rate': 0.0})
        self.assertEqual(aux, {})

    def test_augment_returns_empty_when_gate_raises(self):
        """门控求值异常（门控内移入 try 之后）也必须降级为 {}。"""
        with mock.patch.object(self.predictor.config, 'get_global_param',
                               side_effect=RuntimeError('boom')):
            aux = self.predictor._ai_augment_bayesian(
                [{n: 0.1 for n in range(10)} for _ in range(5)],
                {'verification_count': 0, 'full_match_rate': 0.0})
        self.assertEqual(aux, {})

    def test_bayesian_inference_survives_aux_failure(self):
        """贝叶斯算法外层：辅助失败（返回 {} 或抛异常）时后验概率保持原值。"""
        history = [{'issue': f'2026{i:04d}',
                    'numbers': [i % 10, (i + 1) % 10, (i + 2) % 10,
                                (i + 3) % 10, (i + 4) % 10]}
                   for i in range(100)]
        # 构造 60 条验证记录：绕过「<min_verification_samples(50) 退化为纯先验并
        # 提前返回」的分支，确保算法真正走到 AI 辅助处理块（A25 的考察点）
        records = [{
            'target_issue': f'2026{i:04d}',
            'predicted_numbers': {'wan': [1], 'qian': [2], 'bai': [3],
                                  'shi': [4], 'ge': [5]},
            'actual_numbers': [1, 2, 3, 4, 5],
            'match_count': 3,
        } for i in range(60)]
        # 打假验证记录读取：避免用例依赖本机数据库/本地产物
        with mock.patch.object(self.predictor, '_load_verification_records',
                               return_value=records):
            with mock.patch.object(self.predictor, '_ai_augment_bayesian',
                                   return_value={}):
                baseline = self.predictor._algo_bayesian_inference(history)
            with mock.patch.object(self.predictor, '_ai_augment_bayesian',
                                   side_effect=RuntimeError('boom')):
                degraded = self.predictor._algo_bayesian_inference(history)
        self.assertEqual(baseline, degraded)      # 后验概率逐位一致
        self.assertEqual(self.predictor._bayesian_ai_auxiliary, {})


if __name__ == '__main__':
    unittest.main()

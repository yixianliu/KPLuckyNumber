# -*- coding: utf-8 -*-
"""ALG-01 ML 模型持久化缓存单元测试（PUP-20261019-013100 v5.2）。

覆盖验收面：
  - A18  训练次数埋点：miss 期 train_invocations == 5；hit 期增量 == 0
  - A20  同 fp 两次调用 Top-5 位分布 diff = 0（fp 相同 -> 确定性）
  - 指纹失效：行数变 / 最新期号变 -> 指纹变；同数据 -> 指纹稳定
  - 滚动淘汰：同版本目录保留 MODEL_CACHE_KEEP 版
  - 容错降级：损坏缓存按 miss 处理；joblib 缺失静默不缓存
  - 失败位不污染缓存：任一位训练失败即不落盘

测试纪律：
  - models 目录全部重定向到临时目录（patch ml.MODEL_CACHE_ROOT），
    不读写仓库真实 models/，tearDown 清理；
  - _train_gbml_model 用确定性桩替换，真实 GBDT 训练耗时与数值正确性
    由 tests/test_ml_predictor_sklearn_v370.py / tests/test_ml_predictor_v370.py
    / tests/test_gbrt.py 保证（A21），本文件只验证缓存层语义；
  - DB 以 _connect_db 返回 None 隔离（三个 loader 均 None-safe），
    数据用 >= ML_MIN_SAMPLES 的合成历史，不触库。
"""
import os
import shutil
import unittest
from unittest import mock

import modules.ml_predictor as ml  # noqa: E402


def _make_history(n: int, first_issue: int = 20001) -> list:
    """构造 n 期确定性合成历史（数字公式生成，保证可复现）。"""
    rows = []
    for i in range(n):
        nums = [(i * 7 + j * 3) % 10 for j in range(5)]
        rows.append({
            'issue': str(first_issue + i),
            'numbers': nums,
            'hezhi': sum(nums),
        })
    return rows


def _fake_train_factory(calls: list):
    """生成确定性桩训练：各位分布互异，便于验证 5 位顺序与逐位一致性。"""
    def _fake_train(p, issues, digits, hezhi, pos_trend, spj, hz, n,
                    progress_callback=None):
        calls.append(p)
        p_idx = ml.POS.index(p)
        return {d: 0.05 + 0.001 * (p_idx * 10 + d) for d in range(10)}
    return _fake_train


class TestMlModelCache(unittest.TestCase):
    """ALG-01 缓存层语义验证。"""

    def setUp(self):
        # 隔离目录落在项目内 .cleanup_trash_*（.gitignore 豁免，tearDown 清理）；
        # 不用 tempfile.mkdtemp —— 本机 TMPDIR=E:\Temp 会触发 OSError [Errno 22]
        self._tmp = os.path.abspath('.cleanup_trash_ml_cache_test')
        shutil.rmtree(self._tmp, ignore_errors=True)
        os.makedirs(self._tmp, exist_ok=True)
        self._root = os.path.join(self._tmp, 'models')
        # models 根重定向到临时目录 + 统计清零
        patcher = mock.patch.object(ml, 'MODEL_CACHE_ROOT', self._root)
        patcher.start()
        self.addCleanup(patcher.stop)
        ml._reset_ml_cache_stats()
        self.addCleanup(ml._reset_ml_cache_stats)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ---- 辅助 ----

    def _predict(self, data, target_issue=None):
        with mock.patch.object(ml, '_connect_db', return_value=None), \
                mock.patch.object(ml, '_train_gbml_model',
                                  _fake_train_factory(self._calls)):
            return ml.predict_next(data, target_issue)

    def setUpCalls(self):
        self._calls = []

    # ---- 用例 ----

    def test_miss_then_hit_train_counts(self):
        """A18：首次 miss 训练 5 次并落盘；同数据二次命中训练 0 次。"""
        self.setUpCalls()
        data = _make_history(200)
        r1 = self._predict(data, '20201')
        self.assertEqual(ml._ML_CACHE_STATS['cache_misses'], 1)
        self.assertEqual(ml._ML_CACHE_STATS['cache_hits'], 0)
        self.assertEqual(ml._ML_CACHE_STATS['train_invocations'], 5)
        self.assertEqual(ml._ML_CACHE_STATS['cache_saves'], 1)
        self.assertEqual(len(self._calls), 5)

        self._calls.clear()
        r2 = self._predict(data, '20201')
        self.assertEqual(ml._ML_CACHE_STATS['cache_hits'], 1)
        self.assertEqual(ml._ML_CACHE_STATS['train_invocations'], 5)  # 不增
        self.assertEqual(len(self._calls), 0)  # 桩训练零调用
        self.assertIsNotNone(r1)
        self.assertIsNotNone(r2)

    def test_same_fingerprint_predictions_identical(self):
        """A20：fp 相同 -> 两次调用 Top-5 位分布 diff = 0。"""
        self.setUpCalls()
        data = _make_history(200)
        r1 = self._predict(data, '20201')
        r2 = self._predict(data, '20201')
        self.assertEqual(len(r1), 5)
        self.assertEqual(len(r2), 5)
        for p_idx in range(5):
            for d in range(10):
                self.assertAlmostEqual(
                    r1[p_idx][d], r2[p_idx][d], places=15,
                    msg='位 %d 数字 %d 两次调用不一致' % (p_idx, d))

    def test_fingerprint_stable_and_changes(self):
        """指纹稳定性：同数据相同；行数变 / 最新期号变 -> 变。"""
        data = _make_history(200)
        fp1 = ml._model_fingerprint(data)
        fp2 = ml._model_fingerprint(data)
        self.assertEqual(fp1, fp2)
        self.assertEqual(len(fp1), 32)

        data_more = _make_history(201)  # 行数变
        self.assertNotEqual(fp1, ml._model_fingerprint(data_more))

        data_shift = _make_history(200, first_issue=20002)  # 同期数换期号
        self.assertNotEqual(fp1, ml._model_fingerprint(data_shift))

    def test_new_data_invalidates_cache(self):
        """新期落库（+1 期）后同 target_issue 也必须重训。"""
        self.setUpCalls()
        self._predict(_make_history(200), '20201')
        self._calls.clear()
        self._predict(_make_history(201), '20201')
        self.assertEqual(ml._ML_CACHE_STATS['cache_misses'], 2)
        self.assertEqual(ml._ML_CACHE_STATS['train_invocations'], 10)
        self.assertEqual(len(self._calls), 5)

    def test_cache_file_written_and_readable(self):
        """落盘文件存在于临时 models/v5.x/ 且可被 joblib 读回。"""
        self.setUpCalls()
        fp = ml._model_fingerprint(_make_history(200))
        self._predict(_make_history(200), '20201')
        path = os.path.join(ml._model_cache_dir(), fp + '.joblib')
        self.assertTrue(os.path.exists(path))
        bundle = ml._load_cached_model(fp)
        self.assertEqual(bundle['fingerprint'], fp)
        self.assertEqual(sorted(bundle['positions'].keys()),
                         sorted(ml.POS))
        self.assertIn('schema_hash', bundle)
        self.assertEqual(bundle['meta']['history_rows'], 200)

    def test_eviction_keeps_three_versions(self):
        """滚动淘汰：MODEL_CACHE_KEEP=2 时写 4 版仅留 2 版。"""
        with mock.patch.object(ml, 'MODEL_CACHE_KEEP', 2):
            ml._reset_ml_cache_stats()
            for k in range(4):
                fp = '%032x' % k
                ml._save_model(fp, {
                    'fingerprint': fp,
                    'schema_hash': 'x',
                    'positions': {p: {d: 0.1 for d in range(10)}
                                  for p in ml.POS},
                    'meta': {},
                })
                # 显式错开 mtime，避免同秒写入导致排序不确定
                path = os.path.join(ml._model_cache_dir(), fp + '.joblib')
                os.utime(path, (1000 + k, 1000 + k))
            files = [f for f in os.listdir(ml._model_cache_dir())
                     if f.endswith('.joblib')]
            self.assertEqual(len(files), 2)
            self.assertEqual(ml._ML_CACHE_STATS['cache_evictions'], 2)

    def test_corrupt_cache_file_falls_back(self):
        """损坏缓存文件 → load 返回 None → 按 miss 走训练。"""
        self.setUpCalls()
        data = _make_history(200)
        fp = ml._model_fingerprint(data)
        path = os.path.join(ml._model_cache_dir(), fp + '.joblib')
        with open(path, 'wb') as f:
            f.write(b'not-a-joblib-file')
        self.assertIsNone(ml._load_cached_model(fp))
        r = self._predict(data, '20201')
        self.assertEqual(ml._ML_CACHE_STATS['cache_misses'], 1)
        self.assertEqual(ml._ML_CACHE_STATS['train_invocations'], 5)
        self.assertEqual(len(r), 5)

    def test_fingerprint_mismatch_rejected(self):
        """bundle 指纹与文件名不一致 → 拒绝（防手工张冠李戴）。"""
        fp_a = '%032x' % 1
        fp_b = '%032x' % 2
        ml._save_model(fp_a, {
            'fingerprint': fp_a, 'schema_hash': 'x',
            'positions': {p: {d: 0.1 for d in range(10)} for p in ml.POS},
            'meta': {}})
        # 以 fp_b 请求读到的是 fp_a 的 bundle
        self.assertIsNone(ml._load_cached_model(fp_b))

    def test_joblib_missing_degrades_to_no_cache(self):
        """joblib 不可用：不落盘、load miss，predict_next 仍走训练返回。"""
        self.setUpCalls()
        data = _make_history(200)
        with mock.patch.object(ml, '_JOBLIB_AVAILABLE', False):
            self.assertIsNone(ml._load_cached_model('deadbeef'))
            r = self._predict(data, '20201')
            self.assertEqual(len(r), 5)
            self.assertEqual(ml._ML_CACHE_STATS['cache_saves'], 0)
            self.assertEqual(ml._ML_CACHE_STATS['train_invocations'], 5)

    def test_failed_position_not_cached(self):
        """任一位训练失败（返回 None）→ 不落盘，防失败位污染缓存。"""
        self.setUpCalls()
        calls = []

        def _train_fail_ge(p, issues, digits, hezhi, pos_trend, spj, hz, n,
                           progress_callback=None):
            calls.append(p)
            if p == 'ge':
                return None
            p_idx = ml.POS.index(p)
            return {d: 0.05 + 0.001 * (p_idx * 10 + d) for d in range(10)}

        data = _make_history(200)
        with mock.patch.object(ml, '_connect_db', return_value=None), \
                mock.patch.object(ml, '_train_gbml_model', _train_fail_ge):
            r = ml.predict_next(data, '20201')
        self.assertEqual(len(r), 5)
        # ge 位回退均匀分布
        self.assertAlmostEqual(r[4][0], 0.1, places=12)
        self.assertEqual(ml._ML_CACHE_STATS['cache_saves'], 0)
        self.assertEqual(ml._ML_CACHE_STATS['cache_misses'], 1)

    def test_cache_hit_returns_copies_not_shared(self):
        """命中返回 dict 拷贝：调用方改结果不影响二次读取。"""
        self.setUpCalls()
        data = _make_history(200)
        r1 = self._predict(data, '20201')
        r1[0][0] = 999.0  # 污染第一次结果
        r2 = self._predict(data, '20201')
        self.assertNotEqual(r2[0][0], 999.0)

    def test_insufficient_samples_returns_none(self):
        """样本不足且 DB 不可用 → None（契约不变）。"""
        self.setUpCalls()
        data = _make_history(100)  # < ML_MIN_SAMPLES=160
        with mock.patch.object(ml, '_connect_db', return_value=None):
            r = ml.predict_next(data, '20201')
        self.assertIsNone(r)
        self.assertEqual(ml._ML_CACHE_STATS['train_invocations'], 0)


if __name__ == '__main__':
    unittest.main()

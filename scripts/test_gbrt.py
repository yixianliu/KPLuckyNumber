import sys
sys.path.insert(0, r'D:\PythonProject\KPLuckyNumber')
import numpy as np
np.random.seed(42)
from modules.ml_predictor import _train_gbml_model, _build_feature, _gbrt_predict, _build_tree, _tree_predict
import traceback

# 用更大数据集测试（300期），确保样本充足
n = 300
issues = [f'2025{i:05d}' for i in range(1, n+1)]
digits = {p: [np.random.randint(0, 10) for _ in range(n)] for p in ['pos0','pos1','pos2','pos3','pos4']}
hezhi = [sum(digits[p][i] for p in digits) for i in range(n)]

print(f'数据量: {n} 期')

# 测试 GBRT 核心函数
print('测试 GBDT 核心函数...')
try:
    # 构造特征数据
    X, y_labels = [], []
    for i in range(60, n):
        feat = _build_feature('pos0', i, issues, digits, hezhi, {}, {}, {})
        if feat is not None:
            X.append(feat)
            y_labels.append(digits['pos0'][i])
    X = np.array(X, dtype=float)
    y = np.array(y_labels, dtype=int)
    print(f'训练样本数: {len(X)}, 特征维度: {X.shape[1]}')

    # 直接测试 _gbrt_predict
    result = _gbrt_predict(X, (y==0).astype(float), 10, 0.1, 3, len(X))
    print(f'GBRT 预测值(数字0类): {result:.4f}')
    result1 = _gbrt_predict(X, (y==5).astype(float), 10, 0.1, 3, len(X))
    print(f'GBRT 预测值(数字5类): {result1:.4f}')
    print('GBRT 核心函数: SUCCESS')
except Exception as e:
    print(f'GBRT 核心函数异常: {e}')
    traceback.print_exc()

# 测试完整训练流程
print('开始完整训练...')
try:
    result = _train_gbml_model('pos0', issues, digits, hezhi, {}, {}, {}, n)
    if result:
        total = sum(result.values())
        print(f'概率和: {total:.4f}')
        top = sorted(result.items(), key=lambda x: -x[1])[:3]
        print(f'Top3: {top}')
        print('SUCCESS')
    else:
        print('FAILED: result is None')
except Exception as e:
    print(f'训练异常: {e}')
    traceback.print_exc()

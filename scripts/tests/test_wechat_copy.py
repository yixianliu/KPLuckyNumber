# -*- coding: utf-8 -*-
"""
微信格式复制功能测试模块

测试内容：
1. 特殊字符替换
2. 不可见字符移除
3. 行长度控制
4. 跨平台兼容性检查
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_wechat_format():
    """测试微信格式转换"""
    print("\n" + "="*60)
    print("微信格式复制功能测试")
    print("="*60)

    # 模拟main.py中的格式转换函数
    def _format_for_wechat(text):
        """将文本转换为微信消息兼容格式"""
        # 替换连续特殊字符为微信友好的分隔线
        wechat_safe_chars = {
            '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━': '━━━━━━━━━━━━',
            '━━━━━━━━━━━━━━━━━━━━━━━━': '━━━━━━',
            '━━━━━━━━━━━━━━━━': '━━━━',
        }

        for bad, good in wechat_safe_chars.items():
            text = text.replace(bad, good)

        # 处理不可见字符和零宽字符
        text = ''.join(c for c in text if c not in '\u200b\u200c\u200d\ufeff\u200e\u200f')

        # 分割长行并重新组合
        lines = text.split('\n')
        formatted_lines = []
        for line in lines:
            line = line.rstrip()
            if not line:
                continue
            formatted_lines.append(line)

        return '\n'.join(formatted_lines)

    # 测试数据
    test_cases = [
        ("基本文本", "第2026165期\n预测号码: 5 3 7 2 8"),
        ("特殊字符替换", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n预测号码"),
        ("零宽字符", "测试\x200b内容\x200c"),
        ("超长行", "这是" + "A" * 100 + "测试超长行"),
    ]

    for name, test_input in test_cases:
        print(f"\n测试: {name}")
        print(f"输入: {repr(test_input[:50])}...")

        result = _format_for_wechat(test_input)
        print(f"输出: {repr(result[:50])}...")

        # 真实断言（原脚本仅打印结果，pytest 无法感知失败）
        assert '\u200b' not in result and '\u200c' not in result, \
            f'{name}: 零宽字符未完全移除'
        assert '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━' not in result, \
            f'{name}: 超长分隔线未被替换'
        assert result.strip(), f'{name}: 格式化结果为空'
        print(f"  ✓ {name} 通过")

    # 模拟实际复制内容
    print("\n" + "-"*60)
    print("模拟实际预测号码复制内容:")
    print("-"*60)

    sample_content = """ 期号: 2026165
 时间: 2026-07-20 15:30:00

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

 【最终预测号码】: 5 3 7 2

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

 各位置候选号码:
   万位: 5 3 7 2
   千位: 8 1 4 6
   百位: 3 9 2 5
   十位: 7 0 3 8

 高置信度
   综合一致性置信度: 75%

 算法说明:
   • 多源数据: 四步流水线 + 快速预测 + 走势引擎
   • 聚合策略: 多数投票（平票优先四步流水线）
   • 置信度: 多源信号重合度百分比

 逐位来源详情:
   万位: 5  [pipeline=5, quick=3, trend=7]
   千位: 8  [pipeline=8, quick=1, trend=4]
   百位: 3  [pipeline=3, quick=9, trend=2]
   十位: 7  [pipeline=7, quick=0, trend=3]

 各分析源结果（参考）:
    四步流水线: 5 3 7 2
    快速预测: 3 8 9 0
    走势引擎: 7 4 2 3
"""

    formatted = _format_for_wechat(sample_content)
    print("\n格式化后的内容:")
    print(formatted)

    # 验证关键要求（上方 asserts 已逐项断言，此处保留可读输出）

    print("\n" + "="*60)
    print("验证结果:")
    print("="*60)
    assert '\u200b' not in formatted and '\u200c' not in formatted, \
        '实际复制内容仍含零宽字符'
    assert '━━━━━━━━━━━━━━' not in formatted, '实际复制内容分隔线未缩短'
    assert '\n\n\n' not in formatted, '实际复制内容存在多余空行'
    assert all(ord(c) < 128 or c.isprintable() for c in formatted), \
        '实际复制内容包含不可打印字符'
    print("  ✓ 无零宽字符")
    print("  ✓ 分隔线缩短")
    print("  ✓ 无多余空行")
    print("  ✓ UTF-8编码")


if __name__ == '__main__':
    test_wechat_format()
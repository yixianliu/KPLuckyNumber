# -*- coding: utf-8 -*-
"""gui —— GUI 模块化包（GUI-11，v5.2 方案 §337）。

main.py 8125 行单文件按构建期/渲染期/轮询/业务协调拆分：
  gui/panels/output_panel.py  OutputPanelMixin（构建期 BUILD）
  gui/dashboard.py            DashboardMixin（渲染期 RENDER）
  gui/scheduler.py            SchedulerMixin（轮询+执行 SCHED）
main.LotteryGUI 以 Mixin 组合，业务协调（MAIN）与薄入口留 main.py。

【风险提示】排列五开奖为完全随机概率事件，历史数据不影响未来结果，
本系统仅提供历史数据量化评估与娱乐性参考，不构成购彩建议。
"""

# -*- mode: python ; coding: utf-8 -*-
"""KPLuckyNumber PyInstaller 打包配置（Windows / onedir / 无控制台）

构建命令（项目根目录）：
    python -m PyInstaller --noconfirm --clean KPLuckyNumber.spec

产物：
    dist/KPLuckyNumber/KPLuckyNumber.exe     主程序（窗口模式，无控制台）
    dist/KPLuckyNumber/_internal/...         运行库（tkinter/Tcl-Tk、numpy、sklearn 等）

路径约定（与 paths.py 对应）：
    - 包内只读资源：_internal（sys._MEIPASS），含 favicon.ico、config/.env.example；
    - 包外可写数据：exe 同级目录的 config/.env、data/、logs/、reports/、
      predictions/、models/（首次启动自动生成 config/.env 空白模板，不内嵌密钥）。
"""

# main.py 通过 _LazyClass + importlib.import_module 动态导入以下模块，
# 静态分析无法保证全部收集，这里显式声明全部业务模块（GUI 包由 main.py 静态导入）。
hiddenimports = [
    'gui.dashboard',
    'gui.scheduler',
    'gui.panels.output_panel',
    'modules.ai_analyzer',
    'modules.ai_prompt',
    'modules.auto_evaluator',
    'modules.backtester',
    'modules.cache',
    'modules.calibration',
    'modules.clipboard_builder',
    'modules.cli_log_sink',
    'modules.code_analyzer',
    'modules.database',
    'modules.database_utils',
    'modules.data_fetcher',
    'modules.evaluation',
    'modules.evolution_tuner',
    'modules.exceptions',
    'modules.features',
    'modules.json_repair',
    'modules.logging_utils',
    'modules.metrics_dashboard',
    'modules.ml_predictor',
    'modules.model_monitor',
    'modules.monitor_feedback',
    'modules.monitor_visualizer',
    'modules.mysql_storage_manager',
    'modules.online_learner',
    'modules.optimization_patches',
    'modules.perf_monitor',
    'modules.persistence',
    'modules.pipeline',
    'modules.prediction_enhancer',
    'modules.predictor',
    'modules.redis_storage_manager',
    'modules.result_verifier',
    'modules.selection_strategy',
    'modules.self_evolution',
    'modules.smart_cache',
    'modules.stats_utils',
    'modules.storage',
    'modules.system_health',
    'modules.task_manager',
    'modules.trend_analyzer',
    'modules.ui_styles',
    'modules.ui_theme_spec',
    'modules.ui_toast',
    'modules.validator',
    'modules.visualizer',
]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        # 放到 _MEIPASS 根，供 main.py 的 _get_icon_path 直接命中
        ('assets/favicon.ico', '.'),
        # 供首次启动时释放到 exe/config/ 作为填写参考
        ('config/.env.example', 'config'),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    # 项目只需 TkAgg 后端；显式限定 matplotlib 后端，可避免 hook 收集
    # webagg/其他后端及其可选依赖（tornado 等），显著缩短分析与减小体积
    hooksconfig={'matplotlib': {'backends': ['TkAgg']}},
    runtime_hooks=[],
    # 排除与本项目无关的 GUI 框架、测试套件与可选数据集成。
    # Anaconda 环境装有大量可选包（plotly/altair/skimage/selenium 等），
    # 若不排除，PyInstaller 会逐个执行其 hook（首轮实测 22 分钟仍未完成），
    # 且会显著放大产物体积。本项目均未使用这些包。
    excludes=[
        'PySide6', 'PyQt5', 'PyQt6',
        'pytest', 'IPython', 'notebook', 'jupyter', 'nbconvert',
        'sphinx', 'docutils',
        'plotly', 'altair', 'narwhals', 'dash', 'bokeh',
        'selenium', 'xyzservices', 'skimage', 'intake',
        'lxml', 'markdown', 'seaborn', 'statsmodels',
        'pyarrow', 'tornado',
        # sklearn 的可选数组后端（dask/cupy/torch）会触发 dask->distributed->bokeh/panel
        # 的整条收集链；本项目只用 GradientBoostingClassifier，不需要这些后端
        'dask', 'distributed', 'panel', 'pyviz_comms', 'xarray',
        'numba', 'llvmlite', 'cupy', 'torch',
        'conda', 'conda_env', 'asyncssh',
        'ipywidgets', 'ipykernel', 'jupyter_client', 'jupyter_core', 'nbformat',
        'matplotlib.tests', 'numpy.tests', 'scipy.tests',
        'sklearn.tests', 'pandas.tests',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='KPLuckyNumber',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/favicon.ico'],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='KPLuckyNumber',
)

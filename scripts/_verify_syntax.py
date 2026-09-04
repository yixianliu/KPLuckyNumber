import subprocess
envs = [
    r'F:\anaconda3\envs\PythonGui\python.exe',
    r'D:\anaconda3\python.exe',
]
files = [
    r'D:\PythonProject\KPLuckyNumber\modules\ml_predictor.py',
    r'D:\PythonProject\KPLuckyNumber\modules\self_evolution.py',
]
for env in envs:
    for f in files:
        r = subprocess.run([env, '-m', 'py_compile', f], capture_output=True, text=True)
        name = f.split('\\')[-1]
        env_name = env.split('\\')[-1]
        status = 'OK' if r.returncode == 0 else 'FAIL: ' + r.stderr[:120]
        print(f'{env_name:20s} {name:25s} {status}')

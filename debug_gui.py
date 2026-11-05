import subprocess
import sys
import time

# Run main.py with stderr redirected to a file
with open('gui_debug.log', 'w') as f:
    proc = subprocess.Popen(
        [sys.executable, 'main.py'],
        cwd=r'd:\PythonProject\KPLuckyNumber',
        stdout=subprocess.PIPE,
        stderr=f,
    )

# Wait a bit for GUI to initialize
time.sleep(5)

# Check if process is still running
if proc.poll() is None:
    print('Process is still running (GUI likely open)')
    proc.terminate()
    proc.wait(timeout=5)
    print('Process terminated')
else:
    print(f'Process exited with code: {proc.returncode}')
    with open('gui_debug.log', 'r') as log_f:
        print('--- Debug Log ---')
        print(log_f.read())
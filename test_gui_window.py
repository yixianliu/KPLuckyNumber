import subprocess
import sys
import time

# Run main.py and check if GUI window appears
proc = subprocess.Popen(
    [sys.executable, 'main.py'],
    cwd=r'd:\PythonProject\KPLuckyNumber',
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)

# Wait a bit for GUI to initialize
time.sleep(3)

# Check if process is still running
if proc.poll() is None:
    print('Process is still running (GUI likely open)')
    # Try to terminate after a bit
    proc.terminate()
    proc.wait(timeout=5)
    print('Process terminated')
else:
    print(f'Process exited with code: {proc.poll()}')
    stdout, stderr = proc.communicate(timeout=5)
    print(f'Stdout: {stdout.decode(errors="replace")}')
    print(f'Stderr: {stderr.decode(errors="replace")}')
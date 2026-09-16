"""在 Windows 上啟動 logger（模擬裝置），8 秒後送 Ctrl-Break，確認能優雅收尾並產生合併表。

要在有真實主控台的環境（PowerShell / cmd）執行：uv run python scripts/test_ctrl_break.py
"""
import glob
import shutil
import signal
import subprocess
import sys
import time

out = "data/ctrlbreak_test"
shutil.rmtree(out, ignore_errors=True)
p = subprocess.Popen([sys.executable, "polar_hr_logger.py", "--out", out, "--status-interval", "3",
                      "sim:A", "sim:B@drop=5"],
                     creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
time.sleep(8)
p.send_signal(signal.CTRL_BREAK_EVENT)
try:
    outp, _ = p.communicate(timeout=20)
except subprocess.TimeoutExpired:
    p.kill()
    outp, _ = p.communicate()
    print(outp[-1500:])
    print("!! 沒有在 20 秒內結束")
    sys.exit(1)
print(outp[-1500:])
print("exit code", p.returncode)
merged = glob.glob(out + "/merged_*.csv")
print("merged:", merged)
sys.exit(0 if p.returncode == 0 and merged else 1)

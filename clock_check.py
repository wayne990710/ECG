"""檢查這臺電腦的時鐘和網路時間差多少（SNTP）。兩臺電腦分工收錄時，時間軸靠這個對齊。

用法：uv run python clock_check.py        # 印出偏差，超過 1 秒會警告
程式內：offset = clock_offset()           # 秒；正值代表電腦時鐘比標準時間快；None = 查不到
"""
from __future__ import annotations

import socket
import struct
import sys
import time

NTP_SERVERS = ("time.windows.com", "time.google.com", "pool.ntp.org", "time.stdtime.gov.tw")
NTP_EPOCH_OFFSET = 2208988800  # 1900 → 1970


def _query(server: str, timeout: float = 2.0) -> float | None:
    """回傳 電腦時鐘 − NTP 時間（秒）。"""
    pkt = b"\x1b" + 47 * b"\0"
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            t1 = time.time()
            s.sendto(pkt, (server, 123))
            data, _ = s.recvfrom(48)
            t4 = time.time()
    except OSError:
        return None
    if len(data) < 48:
        return None
    sec, frac = struct.unpack("!II", data[32:40])   # receive timestamp
    t2 = sec - NTP_EPOCH_OFFSET + frac / 2**32
    sec, frac = struct.unpack("!II", data[40:48])   # transmit timestamp
    t3 = sec - NTP_EPOCH_OFFSET + frac / 2**32
    return ((t1 - t2) + (t4 - t3)) / 2


def clock_offset() -> float | None:
    samples = [o for o in (_query(s) for s in NTP_SERVERS) if o is not None]
    if not samples:
        return None
    samples.sort()
    return samples[len(samples) // 2]


def main() -> int:
    off = clock_offset()
    if off is None:
        print("查不到網路時間（沒有網路？）。無法確認時鐘，請兩臺電腦都手動按一次「立即同步」。")
        return 1
    print(f"這臺電腦的時鐘比標準時間{'快' if off > 0 else '慢'} {abs(off):.3f} 秒")
    if abs(off) > 1.0:
        print("!!! 偏差超過 1 秒。請到「設定 → 時間與語言 → 日期和時間」按「立即同步」再重新檢查。")
        return 2
    print("OK，偏差在 1 秒內。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""壓力測試：同時連多顆 TriBLE 貼片，收 N 秒，統計每顆的封包率、取樣率、最大間隔、斷線。

用法：uv run python scripts/trible_multi_test.py [秒數=60] [掃描秒數=8]
"""
import asyncio
import sys
import time

from bleak import BleakClient, BleakScanner

CH = "0000a001-0000-1000-8000-00805f9b34fb"
FRAME = 3  # 每個取樣點 3 個通道、各 1 byte


class Dev:
    def __init__(self, d):
        self.d = d
        self.name = d.name
        self.n_pk = 0
        self.n_bytes = 0
        self.t_first = None
        self.t_last = None
        self.max_gap = 0.0
        self.gaps_over = 0          # 間隔 > 0.2 s 的次數（正常約 0.036 s）
        self.odd_sizes = 0
        self.disconnects = 0
        self.connect_s = None
        self.err = None

    def on_data(self, _, data):
        now = time.perf_counter()
        if self.t_first is None:
            self.t_first = now
        else:
            g = now - self.t_last
            self.max_gap = max(self.max_gap, g)
            if g > 0.2:
                self.gaps_over += 1
        self.t_last = now
        self.n_pk += 1
        self.n_bytes += len(data)
        if len(data) % FRAME:
            self.odd_sizes += 1


async def run_one(dev: Dev, seconds: float, start_delay: float):
    await asyncio.sleep(start_delay)
    t_end = time.perf_counter() + seconds + (0)  # 每顆都收到同一個絕對時間點
    deadline = T_STOP
    while time.perf_counter() < deadline:
        gone = asyncio.Event()
        t0 = time.perf_counter()
        try:
            async with BleakClient(dev.d, timeout=20, disconnected_callback=lambda c: gone.set()) as c:
                if dev.connect_s is None:
                    dev.connect_s = time.perf_counter() - t0
                await c.start_notify(CH, dev.on_data)
                try:
                    await asyncio.wait_for(gone.wait(), timeout=max(0.1, deadline - time.perf_counter()))
                    dev.disconnects += 1
                    print(f"  !! {dev.name} 斷線 @ {time.perf_counter() - T0:.1f}s", flush=True)
                except asyncio.TimeoutError:
                    pass
        except Exception as e:
            dev.err = f"{type(e).__name__}: {e}"
            print(f"  !! {dev.name} 例外 {dev.err}", flush=True)
            await asyncio.sleep(2)


async def main():
    global T0, T_STOP
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 60
    scan_s = float(sys.argv[2]) if len(sys.argv) > 2 else 8
    found = {}

    def cb(d, a):
        if (d.name or a.local_name or "").startswith("TriBLE"):
            found[d.address] = d

    async with BleakScanner(cb):
        await asyncio.sleep(scan_s)
    devs = [Dev(d) for d in sorted(found.values(), key=lambda d: d.name)]
    print(f"掃到 {len(devs)} 顆：{[d.name for d in devs]}", flush=True)
    if not devs:
        return
    T0 = time.perf_counter()
    stagger = 1.5
    T_STOP = T0 + stagger * len(devs) + 15 + seconds

    async def progress():
        while time.perf_counter() < T_STOP:
            await asyncio.sleep(10)
            print(f"  [{time.perf_counter() - T0:5.1f}s] " +
                  " | ".join(f"{d.name[-7:]}:{d.n_pk}" for d in devs), flush=True)

    await asyncio.gather(progress(), *(run_one(d, seconds, i * stagger) for i, d in enumerate(devs)))

    print("\n=== 結果 ===")
    print(f"{'裝置':<16}{'連線s':>6}{'封包':>7}{'包/s':>7}{'Hz/通道':>9}{'最大間隔s':>10}{'>0.2s':>6}{'斷線':>5}{'怪包':>5}")
    for d in devs:
        if d.t_first is None or d.t_last == d.t_first:
            print(f"{d.name:<16}  沒收到資料  {d.err or ''}")
            continue
        dur = d.t_last - d.t_first
        print(f"{d.name:<16}{d.connect_s or 0:>6.1f}{d.n_pk:>7}{d.n_pk / dur:>7.1f}"
              f"{d.n_bytes / FRAME / dur:>9.1f}{d.max_gap:>10.3f}{d.gaps_over:>6}{d.disconnects:>5}{d.odd_sizes:>5}")


asyncio.run(main())

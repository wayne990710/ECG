"""壓力測試：同時連所有 TriBLE 貼片 + 所有 Polar 手環，收 N 秒，統計每顆的封包率、缺口、斷線。

用法：uv run python scripts/ble12_test.py [秒數=180] [掃描秒數=10]
"""
import asyncio
import sys
import time
from pathlib import Path

from bleak import BleakClient, BleakScanner

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from polar_hr_logger import load_devices  # noqa: E402

ECG_CH = "0000a001-0000-1000-8000-00805f9b34fb"
HR_CH = "00002a37-0000-1000-8000-00805f9b34fb"


class Dev:
    def __init__(self, d, kind, label):
        self.d, self.kind, self.label = d, kind, label
        self.ch = ECG_CH if kind == "ecg" else HR_CH
        self.gap_limit = 0.2 if kind == "ecg" else 3.0   # 正常間隔：貼片 0.036 s、Polar 1 s
        self.n_pk = self.n_bytes = 0
        self.t_first = self.t_last = None
        self.max_gap = 0.0
        self.gaps_over = 0
        self.disconnects = 0
        self.connect_s = None
        self.attempts = 0
        self.errs = []
        self.last_hr = None

    def on_data(self, _, data):
        now = time.perf_counter()
        if self.t_first is None:
            self.t_first = now
        else:
            g = now - self.t_last
            self.max_gap = max(self.max_gap, g)
            if g > self.gap_limit:
                self.gaps_over += 1
        self.t_last = now
        self.n_pk += 1
        self.n_bytes += len(data)
        if self.kind == "hr" and len(data) >= 2:
            self.last_hr = data[1]


async def run_one(dev: Dev, start_delay: float):
    await asyncio.sleep(start_delay)
    while time.perf_counter() < T_STOP:
        gone = asyncio.Event()
        t0 = time.perf_counter()
        dev.attempts += 1
        try:
            async with BleakClient(dev.d, timeout=25, disconnected_callback=lambda c: gone.set()) as c:
                if dev.connect_s is None:
                    dev.connect_s = time.perf_counter() - t0
                await c.start_notify(dev.ch, dev.on_data)
                try:
                    await asyncio.wait_for(gone.wait(), timeout=max(0.1, T_STOP - time.perf_counter()))
                    dev.disconnects += 1
                    print(f"  !! {dev.label} 斷線 @ {time.perf_counter() - T0:.1f}s", flush=True)
                except asyncio.TimeoutError:
                    pass
        except Exception as e:
            dev.errs.append(f"{type(e).__name__}: {e}"[:80])
            print(f"  !! {dev.label} 連線例外 @ {time.perf_counter() - T0:.1f}s：{dev.errs[-1]}", flush=True)
            await asyncio.sleep(2)


async def main():
    global T0, T_STOP
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 180
    scan_s = float(sys.argv[2]) if len(sys.argv) > 2 else 10
    labels, skips = load_devices()
    found = {}

    def cb(d, a):
        name = d.name or a.local_name or ""
        if name.startswith("TriBLE"):
            found[d.address] = Dev(d, "ecg", name.replace("TriBLE ", "E"))
        elif name.startswith("Polar"):
            pid = name.split()[-1]
            if pid not in skips:
                found[d.address] = Dev(d, "hr", labels.get(pid, pid))

    async with BleakScanner(cb):
        await asyncio.sleep(scan_s)
    hr_first = len(sys.argv) > 3 and sys.argv[3] == "hrfirst"
    devs = sorted(found.values(), key=lambda x: ((x.kind != "hr") if hr_first else (x.kind != "ecg"), x.label))
    n_ecg = sum(d.kind == "ecg" for d in devs)
    print(f"掃到 {len(devs)} 顆：貼片 {n_ecg}、Polar {len(devs) - n_ecg} → {[d.label for d in devs]}", flush=True)
    if not devs:
        return
    stagger = 2.0
    T0 = time.perf_counter()
    T_STOP = T0 + stagger * len(devs) + 20 + seconds

    async def progress():
        while time.perf_counter() < T_STOP - 1:
            await asyncio.sleep(20)
            up = sum(1 for d in devs if d.t_last and time.perf_counter() - d.t_last < d.gap_limit * 2)
            print(f"  [{time.perf_counter() - T0:5.0f}s] 活著 {up}/{len(devs)} | " +
                  " ".join(f"{d.label}:{d.n_pk}" for d in devs), flush=True)

    await asyncio.gather(progress(), *(run_one(d, i * stagger) for i, d in enumerate(devs)))

    print("\n=== 結果 ===")
    print(f"{'裝置':<10}{'類型':<5}{'連線s':>6}{'嘗試':>5}{'封包':>7}{'包/s':>7}{'Hz/通道':>9}{'最大間隔s':>10}{'缺口':>5}{'斷線':>5}  備註")
    for d in devs:
        if d.t_first is None or d.t_last == d.t_first:
            print(f"{d.label:<10}{d.kind:<5}  沒收到資料  嘗試 {d.attempts} 次  {d.errs[-1] if d.errs else ''}")
            continue
        dur = d.t_last - d.t_first
        hz = f"{d.n_bytes / 3 / dur:9.1f}" if d.kind == "ecg" else f"{'-':>9}"
        note = f"HR={d.last_hr}" if d.kind == "hr" else ""
        if d.errs:
            note += f" 例外{len(d.errs)}次"
        print(f"{d.label:<10}{d.kind:<5}{d.connect_s or 0:>6.1f}{d.attempts:>5}{d.n_pk:>7}{d.n_pk / dur:>7.1f}"
              f"{hz}{d.max_gap:>10.3f}{d.gaps_over:>5}{d.disconnects:>5}  {note}")


asyncio.run(main())

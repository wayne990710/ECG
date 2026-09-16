"""觀察裝置廣播是否連續（每筆廣播的時間戳），以及重複連線的存活時間規律。"""
import asyncio, sys, time
from bleak import BleakClient, BleakScanner

HR_UUID = "00002a37-0000-1000-8000-00805f9b34fb"

async def watch_adv(device_id, seconds):
    t0 = time.time(); last = [None]; n = [0]
    def cb(d, a):
        name = d.name or a.local_name or ""
        if device_id in name:
            now = time.time(); n[0] += 1
            gap = 0 if last[0] is None else now - last[0]
            if gap > 2.0 or n[0] <= 3:
                print(f"  adv #{n[0]} t={now-t0:6.1f}s gap={gap:5.1f}s rssi={a.rssi} data={ {k: v.hex() for k,v in a.manufacturer_data.items()} }", flush=True)
            last[0] = now
    async with BleakScanner(cb):
        await asyncio.sleep(seconds)
    print(f"  共 {n[0]} 筆廣播，{seconds}s，平均間隔 {seconds/max(n[0],1):.2f}s")

async def repeat_connect(device_id, rounds):
    for r in range(rounds):
        t0 = time.time()
        dev = await BleakScanner.find_device_by_filter(
            lambda d, a: device_id in (d.name or a.local_name or ""), timeout=15)
        if dev is None:
            print(f"  round {r}: 找不到"); continue
        t_found = time.time() - t0
        gone = asyncio.Event(); n = [0]
        def cb(_, d): n[0] += 1
        try:
            async with BleakClient(dev, timeout=20, disconnected_callback=lambda c: gone.set()) as client:
                t_conn = time.time() - t0
                await client.start_notify(HR_UUID, cb)
                try:
                    await asyncio.wait_for(gone.wait(), timeout=90)
                    outcome = "斷線"
                except asyncio.TimeoutError:
                    outcome = "存活 90s"
                t_end = time.time() - t0
            print(f"  round {r}: 找到 {t_found:.1f}s 連上 {t_conn:.1f}s {outcome} @ {t_end:.1f}s 通知 {n[0]} 筆  ({time.strftime('%H:%M:%S')})", flush=True)
        except Exception as e:
            print(f"  round {r}: 例外 {e}", flush=True)
        await asyncio.sleep(1)

async def main():
    device_id = sys.argv[1] if len(sys.argv) > 1 else "0C2D7633"
    print("=== 廣播觀察 90s ===", flush=True)
    await watch_adv(device_id, 90)
    print("=== 重複連線 6 回合 ===", flush=True)
    await repeat_connect(device_id, 6)

asyncio.run(main())

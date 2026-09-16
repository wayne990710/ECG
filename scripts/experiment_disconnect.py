"""實驗：找出為什麼裝置連線後約 20 秒就主動斷線。依序跑幾種模式並記錄斷線時間。"""
import asyncio, sys, time
from bleak import BleakClient, BleakScanner

HR_UUID = "00002a37-0000-1000-8000-00805f9b34fb"
BATT_UUID = "00002a19-0000-1000-8000-00805f9b34fb"
PMD_CP = "fb005c81-02e7-f387-1cad-8acd2d8df0c8"
PMD_DATA = "fb005c82-02e7-f387-1cad-8acd2d8df0c8"

async def find(device_id):
    return await BleakScanner.find_device_by_filter(
        lambda d, a: device_id in (d.name or a.local_name or ""), timeout=15)

async def run_mode(dev, mode, seconds):
    t0 = time.time()
    log = lambda m: print(f"  [{mode}] [{time.time()-t0:6.1f}s] {m}", flush=True)
    n = 0
    gone = asyncio.Event()
    def cb(_, data):
        nonlocal n; n += 1
        if n <= 3 or n % 20 == 0:
            log(f"HR notify #{n} raw={bytes(data).hex()}")
    def on_dc(_): log("!! 斷線"); gone.set()
    kw = {}
    try:
        async with BleakClient(dev, timeout=20, disconnected_callback=on_dc, **kw) as client:
            log("已連線")
            if mode == "pair":
                try:
                    r = await client.pair(); log(f"pair() -> {r}")
                except Exception as e:
                    log(f"pair 失敗: {e}")
            if mode in ("notify", "notify+poll", "pair", "pmd"):
                await client.start_notify(HR_UUID, cb); log("已訂閱 HR")
            if mode == "pmd":
                try:
                    await client.start_notify(PMD_CP, lambda s, d: log(f"PMD CP ind: {bytes(d).hex()}"))
                    v = await client.read_gatt_char(PMD_CP); log(f"PMD CP read: {bytes(v).hex()}")
                    await client.write_gatt_char(PMD_CP, bytes([0x01, 0x02]), response=True)  # get PPI settings
                    log("PMD CP write sent")
                except Exception as e:
                    log(f"PMD 失敗: {e}")
            next_poll = time.time() + 3
            while not gone.is_set() and time.time() - t0 < seconds:
                await asyncio.sleep(0.5)
                if mode == "notify+poll" and time.time() >= next_poll:
                    try:
                        b = await client.read_gatt_char(BATT_UUID); log(f"poll batt={b[0]}")
                    except Exception as e:
                        log(f"poll 失敗: {e}"); break
                    next_poll = time.time() + 3
            log(f"結束，通知 {n} 筆，連線存活 {time.time()-t0:.1f}s，仍連線={client.is_connected}")
    except Exception as e:
        log(f"例外: {e}")

async def main():
    device_id = sys.argv[1] if len(sys.argv) > 1 else "0C2D7633"
    modes = sys.argv[2].split(",") if len(sys.argv) > 2 else ["idle", "notify", "notify+poll", "pair", "pmd"]
    seconds = float(sys.argv[3]) if len(sys.argv) > 3 else 60
    for mode in modes:
        print(f"=== 模式 {mode} ===", flush=True)
        dev = await find(device_id)
        if dev is None:
            print("  找不到裝置"); continue
        await run_mode(dev, mode, seconds)
        await asyncio.sleep(3)

asyncio.run(main())

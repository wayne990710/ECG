"""實驗：明確訂閱 Service Changed (0x2A05) 指示，並在連線後立刻讀 HR 特徵的 CCCD，看能否避免 19 秒斷線。"""
import asyncio, time
from bleak import BleakClient, BleakScanner
HR = "00002a37-0000-1000-8000-00805f9b34fb"
SVC_CHG = "00002a05-0000-1000-8000-00805f9b34fb"
BATT = "00002a19-0000-1000-8000-00805f9b34fb"

async def main():
    dev = await BleakScanner.find_device_by_filter(lambda d,a: "0C2D7633" in (d.name or ""), timeout=15)
    t0 = time.time(); gone = asyncio.Event(); n=[0]
    log = lambda m: print(f"[{time.time()-t0:6.1f}s] {m}", flush=True)
    async with BleakClient(dev, timeout=25, disconnected_callback=lambda c: (log("!! 斷線"), gone.set())) as c:
        log("已連線")
        for s in c.services:
            for ch in s.characteristics:
                if ch.uuid == SVC_CHG:
                    log(f"有 Service Changed 特徵 props={ch.properties}")
        try:
            await c.start_notify(SVC_CHG, lambda s,d: log(f"Service Changed 指示: {bytes(d).hex()}"))
            log("已訂閱 Service Changed")
        except Exception as e:
            log(f"訂閱 Service Changed 失敗: {e}")
        try:
            await c.start_notify(BATT, lambda s,d: log(f"電量通知 {d[0]}"))
        except Exception as e:
            log(f"訂閱電量失敗: {e}")
        await c.start_notify(HR, lambda s,d: (n.__setitem__(0, n[0]+1), n[0] <= 3 and log(f"HR {bytes(d).hex()}")))
        log("已訂閱 HR")
        try:
            await asyncio.wait_for(gone.wait(), timeout=90)
        except asyncio.TimeoutError:
            log("存活 90 s")
        log(f"通知 {n[0]} 筆")
asyncio.run(main())

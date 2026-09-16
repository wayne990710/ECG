import asyncio, sys, time
from bleak import BleakClient, BleakScanner
HR_UUID = "00002a37-0000-1000-8000-00805f9b34fb"
ADDR = "24:AC:AC:0C:2D:76"

async def one(label, target, seconds=60, **kw):
    t0 = time.time(); gone = asyncio.Event(); n = [0]
    def cb(_, d):
        n[0] += 1
        if n[0] <= 2: print(f"  [{label}] notify {bytes(d).hex()} @ {time.time()-t0:.1f}s", flush=True)
    try:
        async with BleakClient(target, timeout=25, disconnected_callback=lambda c: gone.set(), **kw) as client:
            tc = time.time()-t0
            await client.start_notify(HR_UUID, cb)
            try:
                await asyncio.wait_for(gone.wait(), timeout=seconds); res = "disconnect"
            except asyncio.TimeoutError:
                res = f"alive {seconds}s"
            print(f"  [{label}] 連上 {tc:.1f}s -> {res} @ {time.time()-t0:.1f}s, 通知 {n[0]}", flush=True)
    except Exception as e:
        print(f"  [{label}] 例外 {e}", flush=True)

async def main():
    print("=== nocache（use_cached_services=False）===")
    dev = await BleakScanner.find_device_by_filter(lambda d,a: "0C2D7633" in (d.name or ""), timeout=15)
    await one("nocache", dev, winrt=dict(use_cached_services=False))
    await asyncio.sleep(2)
    print("=== addr（直接用 MAC）===")
    await one("addr", ADDR)
    await asyncio.sleep(2)
    print("=== scanning（連線期間持續掃描）===")
    async with BleakScanner(lambda d,a: None):
        dev = await BleakScanner.find_device_by_filter(lambda d,a: "0C2D7633" in (d.name or ""), timeout=15)
        await one("scanning", dev)

asyncio.run(main())

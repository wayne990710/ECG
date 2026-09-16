"""讀每顆 Polar 的 Device Information Service（韌體版本、硬體版本、序號等），用來比對差異。"""
import asyncio, sys
from bleak import BleakClient, BleakScanner
DIS = {
    "00002a24-0000-1000-8000-00805f9b34fb": "model",
    "00002a25-0000-1000-8000-00805f9b34fb": "serial",
    "00002a26-0000-1000-8000-00805f9b34fb": "firmware",
    "00002a27-0000-1000-8000-00805f9b34fb": "hardware",
    "00002a28-0000-1000-8000-00805f9b34fb": "software",
    "00002a29-0000-1000-8000-00805f9b34fb": "manufacturer",
    "00002a23-0000-1000-8000-00805f9b34fb": "system_id",
}
async def one(device_id):
    dev = await BleakScanner.find_device_by_filter(lambda d,a: device_id in (d.name or ""), timeout=15)
    if not dev: print(device_id, "找不到"); return
    async with BleakClient(dev, timeout=20) as c:
        out = {}
        for u, k in DIS.items():
            try:
                v = await c.read_gatt_char(u)
                out[k] = v.hex() if k == "system_id" else v.decode(errors="replace")
            except Exception as e:
                out[k] = f"ERR {type(e).__name__}"
        svcs = sorted(str(s.uuid)[:8] for s in c.services)
        print(device_id, out, "services:", svcs, flush=True)
async def main():
    for d in sys.argv[1:]:
        try: await one(d)
        except Exception as e: print(d, "例外", e)
        await asyncio.sleep(1)
asyncio.run(main())

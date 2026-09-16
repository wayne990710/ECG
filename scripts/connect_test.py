"""連線到一顆 Polar Verity Sense，訂閱標準 Heart Rate Measurement 特徵 N 秒，印出心率與 RR。"""
import asyncio, sys, time
from bleak import BleakClient, BleakScanner

HR_UUID = "00002a37-0000-1000-8000-00805f9b34fb"
BATT_UUID = "00002a19-0000-1000-8000-00805f9b34fb"

def parse_hr(data: bytes):
    flags = data[0]
    if flags & 0x01:
        hr = int.from_bytes(data[1:3], "little"); i = 3
    else:
        hr = data[1]; i = 2
    if flags & 0x08:
        i += 2
    rr = []
    if flags & 0x10:
        while i + 1 < len(data):
            rr.append(int.from_bytes(data[i:i+2], "little") / 1024 * 1000); i += 2
    return hr, rr, (flags >> 1) & 0x03

async def main(device_id: str, seconds: float):
    t0 = time.time()
    log = lambda m: print(f"[{time.time()-t0:6.1f}s] {m}", flush=True)
    log(f"尋找 {device_id} ...")
    dev = await BleakScanner.find_device_by_filter(
        lambda d, a: device_id in (d.name or a.local_name or ""), timeout=15)
    if dev is None:
        log("找不到裝置"); return 1
    log(f"找到 {dev.name} @ {dev.address}，連線中...")
    n = 0
    gone = asyncio.Event()
    def cb(_, data):
        nonlocal n; n += 1
        hr, rr, contact = parse_hr(bytes(data))
        log(f"HR={hr:3d} bpm  RR={[round(x) for x in rr]}  contact={contact}  raw={bytes(data).hex()}")
    def on_dc(_):
        log("!! 裝置斷線"); gone.set()
    async with BleakClient(dev, timeout=20, disconnected_callback=on_dc) as client:
        log("已連線。服務：")
        for s in client.services:
            log(f"  {s.uuid}  {s.description}")
            for c in s.characteristics:
                log(f"      {c.uuid}  {c.properties}")
        try:
            b = await client.read_gatt_char(BATT_UUID); log(f"電量 {b[0]}%")
        except Exception as e:
            log(f"讀電量失敗: {e}")
        await client.start_notify(HR_UUID, cb)
        log("已訂閱 HR，等待通知...")
        try:
            await asyncio.wait_for(gone.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass
        if client.is_connected:
            await client.stop_notify(HR_UUID)
    log(f"結束，共收到 {n} 筆通知。")
    return 0

if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "0C2D7633",
                              float(sys.argv[2]) if len(sys.argv) > 2 else 30)))

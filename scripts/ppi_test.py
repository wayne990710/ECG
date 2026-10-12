"""實驗：用 Polar 私有的 PMD 服務串流 PPI（逐拍脈搏間隔），看能不能拿到算 HRV 用的逐拍資料。

用法：uv run python scripts/ppi_test.py 0C2D3F30 [秒數=90]
同時也訂閱標準 HR 服務做對照。結果存到 data/ppi_test_<ID>.csv
"""
import asyncio
import csv
import struct
import sys
import time
from datetime import datetime

from bleak import BleakClient, BleakScanner

HR_UUID = "00002a37-0000-1000-8000-00805f9b34fb"
SW_UUID = "00002a28-0000-1000-8000-00805f9b34fb"
PMD_CP = "fb005c81-02e7-f387-1cad-8acd2d8df0c8"
PMD_DATA = "fb005c82-02e7-f387-1cad-8acd2d8df0c8"
PPI_TYPE = 3
TYPE_NAMES = {0: "ECG", 1: "PPG", 2: "ACC", 3: "PPI", 5: "GYRO", 6: "MAG", 9: "SDK_MODE"}


def parse_ppi_frame(data: bytes):
    """PMD 資料框：type(1) timestamp(8) frame_type(1) payload。PPI 每筆 6 bytes：hr(1) ppi_ms(2) err_ms(2) flags(1)。"""
    mtype, ts, ftype = data[0], struct.unpack_from("<Q", data, 1)[0], data[9]
    samples = []
    if mtype == PPI_TYPE and ftype == 0:
        for off in range(10, len(data) - 5, 6):
            hr, ppi, err, flags = struct.unpack_from("<BHHB", data, off)
            samples.append({"hr": hr, "ppi_ms": ppi, "err_ms": err,
                            "blocker": flags & 1, "contact": (flags >> 1) & 1, "contact_supported": (flags >> 2) & 1})
    return mtype, ts, ftype, samples


async def main():
    dev_id = sys.argv[1] if len(sys.argv) > 1 else "0C2D3F30"
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 90
    t0 = time.time()
    log = lambda m: print(f"[{time.time() - t0:6.1f}s] {m}", flush=True)
    dev = await BleakScanner.find_device_by_filter(lambda d, a: dev_id in (d.name or ""), timeout=15)
    if not dev:
        log("找不到"); return
    rows, hr_rows = [], []
    cp_events = []
    gone = asyncio.Event()

    def on_cp(_, data):
        b = bytes(data)
        cp_events.append(b)
        log(f"CP 回應: {b.hex(' ')}")

    def on_data(_, data):
        b = bytes(data)
        mtype, ts, ftype, samples = parse_ppi_frame(b)
        now = time.time()
        if mtype == PPI_TYPE:
            for s in samples:
                rows.append({"pc_time": datetime.fromtimestamp(now).isoformat(timespec="milliseconds"), **s})
            log(f"PPI 框 {len(samples)} 筆: " + " ".join(f"{s['ppi_ms']}ms" for s in samples[:8])
                + (" …" if len(samples) > 8 else "") + f"  (hr={samples[0]['hr'] if samples else '-'}, "
                f"err={samples[0]['err_ms'] if samples else '-'}, contact={samples[0]['contact'] if samples else '-'})")
        else:
            log(f"其他 PMD 框 type={mtype} ftype={ftype} len={len(b)}")

    def on_hr(_, data):
        hr_rows.append((time.time(), data[1]))

    async with BleakClient(dev, timeout=25, disconnected_callback=lambda c: gone.set()) as c:
        sw = (await c.read_gatt_char(SW_UUID)).decode(errors="replace")
        log(f"已連線 {dev.name}，軟體版本 {sw}")
        feat = await c.read_gatt_char(PMD_CP)
        log(f"PMD 功能: {bytes(feat).hex(' ')}")
        if len(feat) >= 2:
            bits = feat[1]
            log("支援的串流: " + ", ".join(n for t, n in TYPE_NAMES.items() if bits & (1 << t)))
        await c.start_notify(PMD_CP, on_cp)
        await c.start_notify(PMD_DATA, on_data)
        await c.start_notify(HR_UUID, on_hr)
        log("查 PPI 設定 (0x01 0x03)")
        await c.write_gatt_char(PMD_CP, bytes([0x01, PPI_TYPE]), response=True)
        await asyncio.sleep(1)
        log("啟動 PPI 串流 (0x02 0x03)")
        await c.write_gatt_char(PMD_CP, bytes([0x02, PPI_TYPE]), response=True)
        try:
            await asyncio.wait_for(gone.wait(), timeout=seconds)
            log("!! 斷線")
        except asyncio.TimeoutError:
            pass
        if c.is_connected:
            try:
                await c.write_gatt_char(PMD_CP, bytes([0x03, PPI_TYPE]), response=True)
                log("已送停止")
            except Exception as e:
                log(f"停止失敗 {e}")

    log(f"結束：PPI {len(rows)} 筆，標準 HR {len(hr_rows)} 筆")
    if rows:
        import statistics
        good = [r["ppi_ms"] for r in rows if not r["blocker"]]
        log(f"PPI 平均 {statistics.mean(good):.0f} ms（HR {60000 / statistics.mean(good):.1f}），"
            f"有效 {len(good)}/{len(rows)}，SDNN {statistics.pstdev(good):.0f} ms")
        with open(f"data/ppi_test_{dev_id}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
        log(f"存到 data/ppi_test_{dev_id}.csv")


asyncio.run(main())

"""模擬的 Polar 裝置，用來在沒有 4 顆真機時驗證多裝置邏輯與自動重連。

裝置 ID 以 `sim:` 開頭即啟用，例如 `sim:A`、`sim:B`。
可加參數：`sim:A@drop=20` 表示每連線 20 秒就假裝斷線一次；`sim:B@fail=2` 表示前 2 次連線失敗。
"""
from __future__ import annotations

import asyncio
import math
import random
import time
from dataclasses import dataclass

from bleak.exc import BleakError

HR_UUID = "00002a37-0000-1000-8000-00805f9b34fb"
BATT_UUID = "00002a19-0000-1000-8000-00805f9b34fb"
PMD_CP = "fb005c81-02e7-f387-1cad-8acd2d8df0c8"
PMD_DATA = "fb005c82-02e7-f387-1cad-8acd2d8df0c8"


def encode_ppi_frame(samples) -> bytes:
    """組出 PMD PPI 資料框：samples = [(hr, ppi_ms, err_ms, blocker, contact), …]"""
    import struct
    body = bytes([3]) + (0).to_bytes(8, "little") + bytes([0])
    for hr, ppi, err, blocker, contact in samples:
        body += struct.pack("<BHHB", hr, ppi, err, (blocker & 1) | ((contact & 1) << 1) | 4)
    return body


@dataclass
class SimDevice:
    name: str
    address: str


def parse_sim_id(device_id: str) -> tuple[str, dict]:
    """`sim:A@drop=20,fail=2` -> ("sim:A", {"drop": 20.0, "fail": 2.0})"""
    base, _, opts = device_id.partition("@")
    kv = {}
    for item in filter(None, opts.split(",")):
        k, _, v = item.partition("=")
        kv[k.strip()] = float(v) if v else 1.0
    return base, kv


def encode_hr(hr: int, rr_ms: list[float] = (), contact: bool = True) -> bytes:
    """組出標準 Heart Rate Measurement 封包（8-bit HR，可帶 RR）。"""
    flags = 0x06 if contact else 0x04
    body = bytes([hr & 0xFF])
    if rr_ms:
        flags |= 0x10
        for rr in rr_ms:
            body += int(round(rr / 1000 * 1024)).to_bytes(2, "little")
    return bytes([flags]) + body


class SimClient:
    """介面對齊 bleak.BleakClient 的子集：async with、start_notify、stop_notify、read_gatt_char、is_connected。"""

    _connect_counts: dict[str, int] = {}

    def __init__(self, device: SimDevice, timeout: float = 20.0, disconnected_callback=None, **_):
        self.device = device
        self.disconnected_callback = disconnected_callback
        base, opts = parse_sim_id(device.name)
        self.base = base
        self.drop_after = opts.get("drop")
        self.fail_first = int(opts.get("fail", 0))
        self._connected = False
        self._task: asyncio.Task | None = None
        self._cb = None
        self._ppi_cb = None
        self._ppi_task: asyncio.Task | None = None
        self.ppi_enabled = "noppi" not in opts
        self._battery = random.randint(60, 100)
        self._phase = random.random() * math.tau
        self._base_hr = random.randint(55, 80)

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def __aenter__(self):
        await asyncio.sleep(0.3)
        n = SimClient._connect_counts.get(self.base, 0)
        SimClient._connect_counts[self.base] = n + 1
        if n < self.fail_first:
            raise BleakError(f"模擬連線失敗（第 {n + 1} 次）")
        self._connected = True
        self._t_conn = time.time()
        return self

    async def __aexit__(self, *exc):
        await self.disconnect()

    async def disconnect(self):
        if self._task:
            self._task.cancel()
            self._task = None
        if self._ppi_task:
            self._ppi_task.cancel()
            self._ppi_task = None
        if self._connected:
            self._connected = False
            if self.disconnected_callback:
                self.disconnected_callback(self)

    async def read_gatt_char(self, uuid):
        if not self._connected:
            raise BleakError("Not connected")
        if uuid == BATT_UUID:
            return bytes([self._battery])
        if uuid == PMD_CP and self.ppi_enabled:
            return bytes([0x0F, 0x6E, 0x62] + [0] * 14)     # 真機 2.2.6 回的功能清單（含 PPI）
        raise BleakError(f"未模擬的特徵 {uuid}")

    async def start_notify(self, uuid, callback):
        if uuid == PMD_CP and self.ppi_enabled:
            return
        if uuid == PMD_DATA and self.ppi_enabled:
            self._ppi_cb = callback
            return
        if uuid != HR_UUID:
            raise BleakError(f"未模擬的特徵 {uuid}")
        self._cb = callback
        self._task = asyncio.create_task(self._emit())

    async def write_gatt_char(self, uuid, data, response=False):
        if uuid != PMD_CP or not self.ppi_enabled:
            raise BleakError(f"未模擬的寫入 {uuid}")
        if bytes(data)[:2] == bytes([0x02, 3]):
            self._ppi_task = asyncio.create_task(self._emit_ppi())
        elif bytes(data)[:2] == bytes([0x03, 3]) and self._ppi_task:
            self._ppi_task.cancel()
            self._ppi_task = None

    async def _emit_ppi(self):
        await asyncio.sleep(1.5)        # 真機約 16–20 s 才來第一批；模擬縮短
        while self._connected:
            rr = 60000 / self._base_hr
            samples = [(self._base_hr, int(rr + random.gauss(0, 30)), random.randint(5, 30), 0, 1) for _ in range(5)]
            samples[0] = (samples[0][0], samples[0][1], 400, 1, 1)   # 每框第 1 筆標 blocker，測有效率統計
            if self._ppi_cb:
                self._ppi_cb(None, bytearray(encode_ppi_frame(samples)))
            await asyncio.sleep(1.0)

    async def stop_notify(self, uuid):
        if self._task:
            self._task.cancel()
            self._task = None

    async def _emit(self):
        last_rr = 60000 / self._base_hr
        while self._connected:
            await asyncio.sleep(1.0)
            t = time.time()
            hr = int(self._base_hr + 8 * math.sin(t / 30 + self._phase) + random.gauss(0, 1))
            rr = 60000 / max(hr, 30) + random.gauss(0, 15)
            last_rr = rr
            if self._cb:
                self._cb(None, bytearray(encode_hr(hr, [rr])))
            if self.drop_after and t - self._t_conn >= self.drop_after:
                # 模擬裝置端主動斷線
                self._connected = False
                if self.disconnected_callback:
                    self.disconnected_callback(self)
                return


async def find_sim(device_id: str) -> SimDevice:
    await asyncio.sleep(0.2)
    base, _ = parse_sim_id(device_id)
    return SimDevice(name=device_id, address=f"SIM:{base[4:]}")

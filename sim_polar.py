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
        if self._connected:
            self._connected = False
            if self.disconnected_callback:
                self.disconnected_callback(self)

    async def read_gatt_char(self, uuid):
        if not self._connected:
            raise BleakError("Not connected")
        if uuid == BATT_UUID:
            return bytes([self._battery])
        raise BleakError(f"未模擬的特徵 {uuid}")

    async def start_notify(self, uuid, callback):
        if uuid != HR_UUID:
            raise BleakError(f"未模擬的特徵 {uuid}")
        self._cb = callback
        self._task = asyncio.create_task(self._emit())

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

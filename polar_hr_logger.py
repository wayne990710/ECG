"""同時連線多顆 Polar Verity Sense，收錄標準 Heart Rate 服務的心率與 RR 間隔，寫成 CSV。

用法：
    uv run python polar_hr_logger.py 0C2D7633 1A2B3C4D ...      # 給裝置 ID（名稱尾碼）
    uv run python polar_hr_logger.py --duration 3600 0C2D7633   # 錄 1 小時後自動停
    Ctrl-C 隨時停止，會把檔案收尾並產生合併表。

每顆裝置一個檔：data/hr_<ID>_<session>.csv
結束時另產生：  data/merged_<session>.csv（每秒一列、每顆一欄）
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import re
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

HR_UUID = "00002a37-0000-1000-8000-00805f9b34fb"
BATT_UUID = "00002a19-0000-1000-8000-00805f9b34fb"
# Polar 私有 PMD 服務：PPI（逐拍脈搏間隔）串流，HRV 要用這個
PMD_CP_UUID = "fb005c81-02e7-f387-1cad-8acd2d8df0c8"
PMD_DATA_UUID = "fb005c82-02e7-f387-1cad-8acd2d8df0c8"
PMD_PPI_TYPE = 3
PPI_FIELDS = ["pc_time", "beat_time", "device", "label", "ppi_ms", "err_ms", "blocker", "contact", "hr"]

CSV_FIELDS = ["pc_time", "elapsed_s", "device", "label", "hr_bpm", "rr_ms", "contact", "battery"]
DEVICES_FILE = Path(__file__).with_name("devices.json")


def load_devices(path: Path = DEVICES_FILE) -> tuple[dict[str, str], dict[str, str]]:
    """讀 devices.json。值可以是 "1P"，或 {"label": "1P", "skip": true, "note": "…"}。

    回傳 (labels, skips)：labels = {ID: 編號}，skips = {ID: 略過原因}。沒有檔案就回空表。
    """
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return {}, {}
    labels: dict[str, str] = {}
    skips: dict[str, str] = {}
    for k, v in raw.items():
        dev_id = str(k).upper()
        if isinstance(v, dict):
            labels[dev_id] = str(v.get("label", dev_id))
            if v.get("skip"):
                skips[dev_id] = str(v.get("note", "devices.json 標記為略過"))
        else:
            labels[dev_id] = str(v)
    return labels, skips


def load_labels(path: Path = DEVICES_FILE) -> dict[str, str]:
    return load_devices(path)[0]


def load_device_table(path: Path = DEVICES_FILE) -> dict[str, dict]:
    """完整讀 devices.json：{ID: {"label", "type": "polar"|"ecg", "skip": bool, "note"}}。"""
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return {}
    table = {}
    for k, v in raw.items():
        dev_id = str(k).upper()
        if isinstance(v, dict):
            e = dict(v)
            e.setdefault("label", dev_id)
        else:
            e = {"label": str(v)}
        e.setdefault("type", "ecg" if "-" in dev_id else "polar")
        e["skip"] = bool(e.get("skip"))
        e["id"] = k if e["type"] == "ecg" else dev_id
        table[dev_id] = e
    return table


log = logging.getLogger("polar")


# ---------------------------------------------------------------- 封包解析

def parse_hr(data: bytes) -> tuple[int, list[float], int]:
    """解析 Heart Rate Measurement (0x2A37)。回傳 (hr_bpm, rr_ms 列表, contact 旗標)。

    contact：bit1 = 接觸偵測到、bit2 = 支援接觸偵測；回傳 (flags >> 1) & 3。
    """
    if len(data) < 2:
        raise ValueError("封包太短")
    flags = data[0]
    if flags & 0x01:
        hr = int.from_bytes(data[1:3], "little")
        i = 3
    else:
        hr = data[1]
        i = 2
    if flags & 0x08:  # Energy Expended，跳過 2 bytes
        i += 2
    rr: list[float] = []
    if flags & 0x10:
        while i + 1 < len(data):
            rr.append(int.from_bytes(data[i:i + 2], "little") / 1024.0 * 1000.0)
            i += 2
    return hr, rr, (flags >> 1) & 0x03


def parse_ppi_frame(data: bytes) -> list[dict]:
    """PMD 資料框：type(1) timestamp(8) frame_type(1) payload。
    PPI 每筆 6 bytes：hr(1) ppi_ms(2) err_ms(2) flags(1)；flags bit0 = blocker（此拍不可信）、bit1 = 皮膚接觸。
    不是 PPI 框就回空列表。"""
    import struct
    if len(data) < 10 or data[0] != PMD_PPI_TYPE or data[9] != 0:
        return []
    out = []
    for off in range(10, len(data) - 5, 6):
        hr, ppi, err, flags = struct.unpack_from("<BHHB", data, off)
        out.append({"hr": hr, "ppi_ms": ppi, "err_ms": err, "blocker": flags & 1, "contact": (flags >> 1) & 1})
    return out


# ---------------------------------------------------------------- 單顆裝置

class DeviceLogger:
    """負責一顆裝置：掃描 → 連線 → 訂閱 → 寫 CSV → 斷線就重連，直到 stop 被設定。"""

    def __init__(self, device_id: str, out_dir: Path, session: str, t0: float,
                 stop: asyncio.Event, battery_interval: float = 300.0,
                 client_factory=None, no_data_warn: float = 15.0,
                 stale_reconnect: float = 60.0, label: str | None = None, want_ppi: bool = True):
        self.device_id = device_id
        self.want_ppi = want_ppi
        self.raw_id = device_id.split("@")[0]        # 去掉模擬參數的裝置 ID
        self.label = label or self.raw_id            # 顯示名稱：1P、2P…，沒對照到就是 ID
        self.tag = self.label if self.label == self.raw_id else f"{self.label} {self.raw_id}"
        self.session = session
        self.t0 = t0
        self.stop = stop
        self.battery_interval = battery_interval
        self.no_data_warn = no_data_warn
        self.stale_reconnect = stale_reconnect
        self.is_sim = device_id.startswith("sim:")
        if self.is_sim:
            from sim_polar import SimClient
            self.client_factory = SimClient
        else:
            self.client_factory = client_factory or BleakClient
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", self.label)
        self.path = out_dir / f"hr_{safe}_{session}.csv"
        self._fh = open(self.path, "a", newline="", encoding="utf-8")
        self._w = csv.DictWriter(self._fh, fieldnames=CSV_FIELDS)
        if self._fh.tell() == 0:
            self._w.writeheader()
            self._fh.flush()
        self.ppi_path = out_dir / f"ppi_{safe}_{session}.csv"
        self._ppi_fh = None     # 第一次收到 PPI 才建檔，沒有 PPI 的裝置就不會留空檔
        self._ppi_w = None
        self.n_ppi = 0
        self.n_ppi_valid = 0
        self.ppi_supported: bool | None = None
        self.battery: int | None = None
        self.n_samples = 0
        self.n_disconnects = 0
        self.n_connects = 0
        self.n_parse_errors = 0
        self.last_sample: float | None = None
        self.last_hr: int | None = None
        self._gap_times: list[float] = []   # 通知間隔統計用
        self._disconnected = asyncio.Event()
        self.address: str | None = None
        self._last_notfound_log = 0.0

    # ---- callbacks
    def _on_hr(self, _sender, data: bytearray) -> None:
        now = time.time()
        try:
            hr, rr, contact = parse_hr(bytes(data))
        except Exception as e:  # 不讓壞封包弄掛整個 task
            self.n_parse_errors += 1
            log.warning("[%s] 解析失敗 %s: %s", self.tag, bytes(data).hex(), e)
            return
        if self.last_sample is not None:
            self._gap_times.append(now - self.last_sample)
        self.last_sample = now
        self.last_hr = hr
        self.n_samples += 1
        self._w.writerow({
            "pc_time": datetime.fromtimestamp(now).isoformat(timespec="milliseconds"),
            "elapsed_s": f"{now - self.t0:.3f}",
            "device": self.raw_id,
            "label": self.label,
            "hr_bpm": hr,
            "rr_ms": ";".join(f"{x:.1f}" for x in rr),
            "contact": contact,
            "battery": "" if self.battery is None else self.battery,
        })
        self._fh.flush()

    def _on_ppi(self, _sender, data: bytearray) -> None:
        now = time.time()
        samples = parse_ppi_frame(bytes(data))
        if not samples:
            return
        if self._ppi_fh is None:
            self._ppi_fh = open(self.ppi_path, "a", newline="", encoding="utf-8")
            self._ppi_w = csv.DictWriter(self._ppi_fh, fieldnames=PPI_FIELDS)
            if self._ppi_fh.tell() == 0:
                self._ppi_w.writeheader()
        # 裝置不給時間戳；以「整框最後一拍結束於到達時刻」往回推估每拍時間（有數秒延遲，僅供分段用）
        total = sum(x["ppi_ms"] for x in samples) / 1000.0
        t = now - total
        pc = datetime.fromtimestamp(now).isoformat(timespec="milliseconds")
        for x in samples:
            t += x["ppi_ms"] / 1000.0
            self._ppi_w.writerow({"pc_time": pc, "beat_time": datetime.fromtimestamp(t).isoformat(timespec="milliseconds"),
                                  "device": self.raw_id, "label": self.label, **x})
            self.n_ppi += 1
            self.n_ppi_valid += 0 if x["blocker"] else 1
        self._ppi_fh.flush()

    async def _start_ppi(self, client) -> None:
        """有 PMD 服務且支援 PPI 就啟動；失敗不影響標準心率收錄。"""
        try:
            feat = await client.read_gatt_char(PMD_CP_UUID)
        except Exception:
            self.ppi_supported = False
            return
        if len(feat) < 2 or not (feat[1] & (1 << PMD_PPI_TYPE)):
            self.ppi_supported = False
            log.info("[%s] 此裝置不支援 PPI 串流，只收標準心率", self.tag)
            return
        try:
            await client.start_notify(PMD_CP_UUID, lambda s, d: None)
            await client.start_notify(PMD_DATA_UUID, self._on_ppi)
            await client.write_gatt_char(PMD_CP_UUID, bytes([0x02, PMD_PPI_TYPE]), response=True)
            self.ppi_supported = True
            log.info("[%s] 已啟動 PPI（逐拍間隔）串流，第一批資料約 20 秒後到", self.tag)
        except Exception as e:
            self.ppi_supported = False
            log.warning("[%s] PPI 啟動失敗：%s（仍收標準心率）", self.tag, e)

    def _on_disconnect(self, _client) -> None:
        self._disconnected.set()

    # ---- main loop
    async def run(self) -> None:
        backoff = 1.0
        try:
            while not self.stop.is_set():
                t_start = time.time()
                try:
                    await self._session_once()
                    backoff = 1.0
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    if self.n_connects:
                        self.n_disconnects += 1
                    if time.time() - t_start > 60:
                        backoff = 1.0   # 這次連線有撐過 1 分鐘，視為正常，重置退避
                    if "找不到" in str(e):
                        # 還沒開機的裝置會一直找；訊息每分鐘最多印一次，避免洗版
                        if time.time() - self._last_notfound_log >= 60:
                            log.warning("[%s] 還沒找到裝置，持續尋找中（請確認已開機、在附近）", self.tag)
                            self._last_notfound_log = time.time()
                        backoff = 1.0
                    else:
                        log.warning("[%s] %s（%.0f 秒後重試）", self.tag, e, backoff)
                if self.stop.is_set():
                    break
                await _sleep_or_stop(backoff, self.stop)
                backoff = min(backoff * 2, 30.0)
        finally:
            self._fh.close()
            if self._ppi_fh is not None:
                self._ppi_fh.close()

    async def _find(self):
        if self.is_sim:
            from sim_polar import find_sim
            dev = await find_sim(self.device_id)
            self.address = dev.address
            return dev
        dev = await BleakScanner.find_device_by_filter(
            lambda d, a: self.device_id in (d.name or a.local_name or ""), timeout=15.0)
        if dev is None:
            raise BleakError("掃描 15 秒找不到裝置")
        self.address = dev.address
        return dev

    async def _session_once(self) -> None:
        dev = await self._find()
        log.info("[%s] 找到 %s @ %s，連線中", self.tag, dev.name, dev.address)
        self._disconnected.clear()
        async with self.client_factory(dev, timeout=20.0,
                                       disconnected_callback=self._on_disconnect) as client:
            self.n_connects += 1
            log.info("[%s] 已連線（第 %d 次）", self.tag, self.n_connects)
            await self._read_battery(client)
            await client.start_notify(HR_UUID, self._on_hr)
            if self.want_ppi:
                await self._start_ppi(client)
            next_batt = time.time() + self.battery_interval
            t_sub = time.time()
            warned_no_data = False
            while not self.stop.is_set() and not self._disconnected.is_set():
                await _wait_any([self.stop, self._disconnected], timeout=1.0)
                last = self.last_sample if (self.last_sample or 0) >= t_sub else None
                silent = time.time() - (last or t_sub)
                if silent >= self.no_data_warn and not warned_no_data:
                    log.warning("[%s] 已連線但 %.0f 秒沒有心率通知（手環是否開機、貼在皮膚上？）",
                                self.tag, silent)
                    warned_no_data = True
                elif silent < self.no_data_warn:
                    warned_no_data = False
                if self.stale_reconnect and silent >= self.stale_reconnect:
                    # 看門狗：Windows 有時不會回報斷線，連線會卡在「已連線但永遠沒資料」
                    raise BleakError(f"連線 {silent:.0f} 秒沒有資料，強制重連")
                if time.time() >= next_batt and client.is_connected:
                    await self._read_battery(client)
                    next_batt = time.time() + self.battery_interval
            if self._disconnected.is_set() and not self.stop.is_set():
                raise BleakError("裝置斷線")
            if client.is_connected:
                try:
                    if self.ppi_supported:
                        await client.write_gatt_char(PMD_CP_UUID, bytes([0x03, PMD_PPI_TYPE]), response=True)
                    await client.stop_notify(HR_UUID)
                except BleakError:
                    pass

    async def _read_battery(self, client) -> None:
        try:
            b = await client.read_gatt_char(BATT_UUID)
            self.battery = int(b[0])
            log.info("[%s] 電量 %d%%", self.tag, self.battery)
        except Exception as e:
            log.warning("[%s] 讀電量失敗：%s", self.tag, e)

    # ---- 統計
    def gap_stats(self) -> dict:
        g = self._gap_times
        if not g:
            return {}
        s = sorted(g)
        return {
            "n": len(g),
            "p50": s[len(s) // 2],
            "p99": s[min(len(s) - 1, int(len(s) * 0.99))],
            "max": s[-1],
            "gaps_over_3s": sum(1 for x in g if x > 3.0),
        }


async def _sleep_or_stop(seconds: float, stop: asyncio.Event) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass


async def _wait_any(events: list[asyncio.Event], timeout: float) -> None:
    tasks = [asyncio.ensure_future(e.wait()) for e in events]
    try:
        await asyncio.wait(tasks, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()


# ---------------------------------------------------------------- 合併

def merge_csvs(paths: list[Path], out: Path) -> int:
    """把多個 hr_*.csv 合併成每秒一列、每顆一欄的寬表。同一秒多筆取平均，HR=0 視為缺值。回傳列數。"""
    import pandas as pd
    frames = []
    for p in paths:
        df = pd.read_csv(p)
        if df.empty:
            continue
        dev = str(df["label"].iloc[0]) if "label" in df.columns else str(df["device"].iloc[0])
        t = pd.to_datetime(df["pc_time"]).dt.floor("1s")
        s = pd.Series(df["hr_bpm"].to_numpy(dtype=float), index=t).replace(0, float("nan"))
        s = s.groupby(level=0).mean().rename(f"hr_{dev}")
        frames.append(s)
    if not frames:
        return 0
    wide = pd.concat(frames, axis=1, sort=True)
    wide = wide.resample("1s").mean()
    wide.index.name = "time"
    wide.round(1).to_csv(out)
    return len(wide)


# ---------------------------------------------------------------- 主程式

async def discover_polar(seconds: float) -> list[str]:
    """掃描 seconds 秒，回傳所有 Polar Sense 的裝置 ID（名稱尾碼）。"""
    found: dict[str, int] = {}
    def cb(d, adv):
        name = d.name or adv.local_name or ""
        if name.startswith("Polar"):
            found[name.split()[-1]] = adv.rssi
    async with BleakScanner(cb):
        await asyncio.sleep(seconds)
    labels, skips = load_devices()
    keep = []
    for dev_id, rssi in sorted(found.items()):
        tag = f" = {labels[dev_id]}" if dev_id in labels else "（未對照）"
        if dev_id in skips:
            log.info("掃到 Polar %s%s（RSSI %d）→ 略過：%s", dev_id, tag, rssi, skips[dev_id])
        else:
            log.info("掃到 Polar %s%s（RSSI %d）", dev_id, tag, rssi)
            keep.append(dev_id)
    return keep


async def main_async(args, client_factory=None) -> int:
    if args.auto:
        log.info("自動掃描 %.0f 秒…", args.scan_time)
        args.devices = list(dict.fromkeys(args.devices + await discover_polar(args.scan_time)))
    if not args.devices:
        log.error("沒有任何裝置。請確認手環已開機（藍燈）並在附近，或直接給裝置 ID。")
        return 2
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    session = datetime.now().strftime("%Y%m%d_%H%M%S")
    t0 = time.time()
    stop = asyncio.Event()

    loop = asyncio.get_running_loop()

    def _request_stop(*_):
        log.info("收到停止訊號，收尾中…")
        loop.call_soon_threadsafe(stop.set)

    if sys.platform == "win32":
        signal.signal(signal.SIGINT, _request_stop)
        signal.signal(signal.SIGBREAK, _request_stop)
    else:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _request_stop)

    labels = load_labels()
    loggers = [DeviceLogger(d, out_dir, session, t0, stop, args.battery_interval, client_factory,
                            args.no_data_warn, args.stale_reconnect,
                            labels.get(d.split("@")[0].upper()))
               for d in args.devices]
    if labels:
        log.info("對照表：%s", ", ".join(f"{v}={k}" for k, v in sorted(labels.items(), key=lambda kv: kv[1])))
    log.info("session %s，裝置 %s，輸出 %s", session, args.devices, out_dir)

    # 逐一啟動（間隔 stagger 秒），避免 Windows 同時發起多條 BLE 連線
    tasks = []
    for i, lg in enumerate(loggers):
        if i:
            await _sleep_or_stop(args.stagger, stop)
        tasks.append(asyncio.create_task(lg.run(), name=lg.device_id))

    async def status_printer():
        while not stop.is_set():
            await _sleep_or_stop(args.status_interval, stop)
            if stop.is_set():
                break
            parts = []
            for lg in loggers:
                age = "-" if lg.last_sample is None else f"{time.time() - lg.last_sample:.0f}s前"
                parts.append(f"{lg.label}: HR={lg.last_hr} n={lg.n_samples} ppi={lg.n_ppi} "
                             f"最近{age} 斷線{lg.n_disconnects} 電{lg.battery}%")
            log.info("狀態 %.0f 分 | %s", (time.time() - t0) / 60, " | ".join(parts))

    async def duration_timer():
        if args.duration:
            await _sleep_or_stop(args.duration, stop)
            if not stop.is_set():
                log.info("達到設定時長 %.0f 秒，停止。", args.duration)
                stop.set()

    merged = out_dir / f"merged_{session}.csv"

    def do_merge(final: bool = False) -> None:
        try:
            n = merge_csvs([lg.path for lg in loggers], merged)
            if final:
                log.info("合併表 %s（%d 列）", merged.name, n)
        except Exception as e:
            log.warning("合併失敗：%s", e)

    async def periodic_merge():
        # 視窗被直接關掉時不會有收尾，所以每隔一段時間就先把合併表寫出來
        while not stop.is_set():
            await _sleep_or_stop(args.merge_interval, stop)
            if not stop.is_set():
                await asyncio.get_running_loop().run_in_executor(None, do_merge)

    aux = [asyncio.create_task(status_printer()), asyncio.create_task(duration_timer()),
           asyncio.create_task(periodic_merge())]
    try:
        await stop.wait()
    finally:
        stop.set()
        done, pending = await asyncio.wait(tasks, timeout=10.0)
        for t in pending:
            t.cancel()
        for t in aux:
            t.cancel()
        await asyncio.gather(*tasks, *aux, return_exceptions=True)

    # 總結
    log.info("=== 總結（%.1f 分鐘）===", (time.time() - t0) / 60)
    for lg in loggers:
        gs = lg.gap_stats()
        gtxt = "" if not gs else (f"間隔 p50={gs['p50']:.2f}s p99={gs['p99']:.2f}s "
                                  f"max={gs['max']:.1f}s >3s 有 {gs['gaps_over_3s']} 次")
        if lg.n_ppi:
            log.info("[%s] PPI 逐拍 %d 筆（有效 %d，%.0f%%）→ %s", lg.tag, lg.n_ppi, lg.n_ppi_valid,
                     100.0 * lg.n_ppi_valid / lg.n_ppi, lg.ppi_path.name)
        log.info("[%s] 筆數 %d，連線 %d 次，斷線 %d 次，電量 %s%%。%s → %s",
                 lg.tag, lg.n_samples, lg.n_connects, lg.n_disconnects,
                 lg.battery, gtxt, lg.path.name)
    do_merge(final=True)
    return 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("devices", nargs="*", help="裝置 ID（名稱尾碼，如 0C2D7633）；不給就要加 --auto")
    p.add_argument("--auto", action="store_true", help="自動掃描 10 秒，連線所有掃到的 Polar Sense")
    p.add_argument("--scan-time", type=float, default=10.0, help="--auto 的掃描秒數")
    p.add_argument("--out", default="data", help="輸出資料夾（預設 data）")
    p.add_argument("--duration", type=float, default=0, help="錄多久（秒），0 = 直到 Ctrl-C")
    p.add_argument("--stagger", type=float, default=2.0, help="各裝置啟動間隔秒數")
    p.add_argument("--battery-interval", type=float, default=300, help="讀電量間隔秒數")
    p.add_argument("--status-interval", type=float, default=60, help="印狀態行間隔秒數")
    p.add_argument("--merge-interval", type=float, default=300,
                   help="每隔幾秒自動更新一次合併表（防止視窗被直接關掉時沒有合併表）")
    p.add_argument("--no-data-warn", type=float, default=15, help="連線後幾秒沒收到心率就警告")
    p.add_argument("--stale-reconnect", type=float, default=60,
                   help="連線後幾秒沒收到心率就強制斷線重連（0 = 不做）")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("bleak").setLevel(logging.WARNING)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())

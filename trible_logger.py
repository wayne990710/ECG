"""同時連線多顆 TriBLE 心電貼片，收錄原始 3 通道波形。

用法：
    uv run python trible_logger.py --auto                 # 掃描並連所有 TriBLE
    uv run python trible_logger.py 2512-03 2603-09        # 指定貼片（名稱尾碼）
    Ctrl-C 停止；結束時自動做後處理（極性矯正、R 波、每秒心率）。

每顆貼片兩個原始檔（逐包即時寫入，視窗被關掉也不會丟）：
    data/ecg_<編號>_<session>.bin         原始位元組：3 通道交錯的 uint8（CH1,CH2,CH3,CH1,…）
    data/ecg_<編號>_<session>_index.csv   每個封包一列：電腦收到的時間、連線段編號、位元組位移、長度
後處理產生（也可事後用 ecg_process.py 補做）：
    data/ecgrr_<編號>_<session>.csv       每一拍：時間、RR 間隔、是否異常
    data/ecghr_<編號>_<session>.csv       每秒心率
    data/merged_ecg_<session>.csv         每秒一列、每顆貼片一欄

貼片的取樣時脈每顆差到 ±1.3%，所以時間軸一律以電腦收到封包的時間為準。
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import re
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

from polar_hr_logger import _sleep_or_stop, _wait_any, load_devices

ECG_UUID = "0000a001-0000-1000-8000-00805f9b34fb"
NAME_PREFIX = "TriBLE"
INDEX_FIELDS = ["pc_time", "elapsed_s", "segment", "byte_offset", "n_bytes"]

log = logging.getLogger("trible")


def patch_id(name: str) -> str:
    """'TriBLE 2512-03' -> '2512-03'"""
    return name.replace(NAME_PREFIX, "").strip()


class PatchLogger:
    """負責一顆貼片：掃描 → 連線 → 訂閱 → 寫原始檔 → 斷線重連。"""

    def __init__(self, pid: str, out_dir: Path, session: str, t0: float, stop: asyncio.Event,
                 label: str | None = None, stale_reconnect: float = 10.0):
        self.pid = pid
        self.label = label or f"E{pid}"
        self.tag = f"{self.label} {pid}" if label else self.label
        self.t0 = t0
        self.stop = stop
        self.stale_reconnect = stale_reconnect
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", self.label)
        self.bin_path = out_dir / f"ecg_{safe}_{session}.bin"
        self.idx_path = out_dir / f"ecg_{safe}_{session}_index.csv"
        self._bin = open(self.bin_path, "ab")
        self._idx_fh = open(self.idx_path, "a", newline="", encoding="utf-8")
        self._idx = csv.writer(self._idx_fh)
        if self._idx_fh.tell() == 0:
            self._idx.writerow(INDEX_FIELDS)
        self.n_bytes = self._bin.tell()
        self.segment = 0
        self.n_packets = 0
        self.n_connects = 0
        self.n_disconnects = 0
        self.last_packet: float | None = None
        self.max_gap = 0.0
        self._disconnected = asyncio.Event()
        self._rate_mark = (time.time(), self.n_bytes)

    def _on_data(self, _sender, data: bytearray) -> None:
        now = time.time()
        if self.last_packet is not None and self.n_packets:
            self.max_gap = max(self.max_gap, now - self.last_packet)
        self.last_packet = now
        self.n_packets += 1
        self._bin.write(data)
        self._idx.writerow([datetime.fromtimestamp(now).isoformat(timespec="milliseconds"),
                            f"{now - self.t0:.4f}", self.segment, self.n_bytes, len(data)])
        self.n_bytes += len(data)

    def flush(self) -> None:
        self._bin.flush()
        self._idx_fh.flush()

    def rate_hz(self) -> float:
        """上次呼叫以來的每通道取樣率。"""
        now = time.time()
        t, b = self._rate_mark
        self._rate_mark = (now, self.n_bytes)
        return (self.n_bytes - b) / 3 / max(now - t, 1e-6)

    def _on_disconnect(self, _client) -> None:
        self._disconnected.set()

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
                        backoff = 1.0
                    log.warning("[%s] %s（%.0f 秒後重試）", self.tag, e, backoff)
                if self.stop.is_set():
                    break
                await _sleep_or_stop(backoff, self.stop)
                backoff = min(backoff * 2, 15.0)
        finally:
            self.flush()
            self._bin.close()
            self._idx_fh.close()

    async def _session_once(self) -> None:
        dev = await BleakScanner.find_device_by_filter(
            lambda d, a: (d.name or a.local_name or "") == f"{NAME_PREFIX} {self.pid}", timeout=15.0)
        if dev is None:
            raise BleakError("掃描 15 秒找不到貼片")
        self._disconnected.clear()
        async with BleakClient(dev, timeout=25.0, disconnected_callback=self._on_disconnect) as client:
            self.n_connects += 1
            self.segment += 1
            self.last_packet = None
            log.info("[%s] 已連線（第 %d 次）", self.tag, self.n_connects)
            await client.start_notify(ECG_UUID, self._on_data)
            t_sub = time.time()
            while not self.stop.is_set() and not self._disconnected.is_set():
                await _wait_any([self.stop, self._disconnected], timeout=1.0)
                self.flush()
                silent = time.time() - (self.last_packet or t_sub)
                if self.stale_reconnect and silent >= self.stale_reconnect:
                    raise BleakError(f"連線 {silent:.0f} 秒沒有資料，強制重連")
            if self._disconnected.is_set() and not self.stop.is_set():
                raise BleakError("貼片斷線")
            if client.is_connected:
                try:
                    await client.stop_notify(ECG_UUID)
                except BleakError:
                    pass


async def discover(seconds: float) -> list[str]:
    found: dict[str, int] = {}

    def cb(d, adv):
        name = d.name or adv.local_name or ""
        if name.startswith(NAME_PREFIX):
            found[patch_id(name)] = adv.rssi

    async with BleakScanner(cb):
        await asyncio.sleep(seconds)
    labels, skips = load_devices()
    keep = []
    for pid, rssi in sorted(found.items()):
        tag = f" = {labels[pid.upper()]}" if pid.upper() in labels else ""
        if pid.upper() in skips:
            log.info("掃到貼片 %s%s（RSSI %d）→ 略過：%s", pid, tag, rssi, skips[pid.upper()])
        else:
            log.info("掃到貼片 %s%s（RSSI %d）", pid, tag, rssi)
            keep.append(pid)
    return keep


async def main_async(args) -> int:
    if args.auto:
        log.info("自動掃描 %.0f 秒…", args.scan_time)
        args.devices = list(dict.fromkeys(args.devices + await discover(args.scan_time)))
    if not args.devices:
        log.error("沒有任何貼片。請確認貼片已開機並在附近，且沒有被手機 App 連著。")
        return 2
    if len(args.devices) > 9:
        log.warning("共 %d 顆。一般筆電藍牙同時最多約 9 條連線，超過的會連不上。", len(args.devices))

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

    labels, _ = load_devices()
    loggers = [PatchLogger(p, out_dir, session, t0, stop, labels.get(p.upper()), args.stale_reconnect)
               for p in args.devices]
    log.info("session %s，貼片 %s，輸出 %s", session, [lg.label for lg in loggers], out_dir)

    tasks = []
    for i, lg in enumerate(loggers):
        if i:
            await _sleep_or_stop(args.stagger, stop)
        tasks.append(asyncio.create_task(lg.run(), name=lg.label))

    async def status_printer():
        while not stop.is_set():
            await _sleep_or_stop(args.status_interval, stop)
            if stop.is_set():
                break
            parts = []
            for lg in loggers:
                age = "-" if lg.last_packet is None else f"{time.time() - lg.last_packet:.1f}s前"
                parts.append(f"{lg.label}: {lg.rate_hz():.0f}Hz 包{lg.n_packets} 最近{age} 斷線{lg.n_disconnects}")
            log.info("狀態 %.0f 分 | %s", (time.time() - t0) / 60, " | ".join(parts))

    async def duration_timer():
        if args.duration:
            await _sleep_or_stop(args.duration, stop)
            if not stop.is_set():
                log.info("達到設定時長 %.0f 秒，停止。", args.duration)
                stop.set()

    aux = [asyncio.create_task(status_printer()), asyncio.create_task(duration_timer())]
    try:
        await stop.wait()
    finally:
        stop.set()
        _, pending = await asyncio.wait(tasks, timeout=10.0)
        for t in pending:
            t.cancel()
        for t in aux:
            t.cancel()
        await asyncio.gather(*tasks, *aux, return_exceptions=True)

    log.info("=== 總結（%.1f 分鐘）===", (time.time() - t0) / 60)
    for lg in loggers:
        log.info("[%s] 封包 %d，%.1f MB，連線 %d 次，斷線 %d 次，最大封包間隔 %.2f s → %s",
                 lg.tag, lg.n_packets, lg.n_bytes / 1e6, lg.n_connects, lg.n_disconnects,
                 lg.max_gap, lg.bin_path.name)

    if not args.no_process:
        log.info("後處理中（極性矯正、R 波偵測、每秒心率）…")
        try:
            from ecg_process import process_session
            process_session(out_dir, session)
        except Exception as e:
            log.warning("後處理失敗：%s。原始檔都在，可事後執行 ecg_process.py --session %s", e, session)
    return 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("devices", nargs="*", help="貼片 ID（名稱尾碼，如 2512-03）；不給就要加 --auto")
    p.add_argument("--auto", action="store_true", help="自動掃描，連線所有掃到的 TriBLE 貼片")
    p.add_argument("--scan-time", type=float, default=10.0)
    p.add_argument("--out", default="data")
    p.add_argument("--duration", type=float, default=0, help="錄多久（秒），0 = 直到 Ctrl-C")
    p.add_argument("--stagger", type=float, default=2.0, help="各顆啟動間隔秒數")
    p.add_argument("--status-interval", type=float, default=60)
    p.add_argument("--stale-reconnect", type=float, default=10,
                   help="連線後幾秒沒收到資料就強制重連（0 = 不做）")
    p.add_argument("--no-process", action="store_true", help="結束時不做後處理")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("bleak").setLevel(logging.ERROR)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())

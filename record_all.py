"""一個視窗同時收錄 TriBLE 心電貼片與 Polar 手環（同一臺電腦、同一個 session）。

用法：
    uv run python record_all.py                       # 自動掃描，連所有貼片與手環
    uv run python record_all.py --duration 2700       # 錄 45 分鐘後自動停
    uv run python record_all.py --ecg 2512-03 --polar 0C2DC632 0C2CCC37   # 指定裝置，不掃描
    Ctrl-C 停止：自動合併手環心率、計算貼片心率，並產生總表。

一臺筆電的藍牙同時最多約 9 條連線（--max-connections）。超過時保留全部貼片，手環依編號取到滿為止。

輸出（data/）：Polar 的 hr_*.csv / merged_*.csv、貼片的 ecg_*.bin / ecgrr_* / ecghr_* / merged_ecg_*，
另外加一張 merged_all_<session>.csv：每秒一列，手環與貼片的心率並排。
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
from bleak import BleakScanner

from polar_hr_logger import DeviceLogger, _sleep_or_stop, load_devices, merge_csvs
from trible_logger import NAME_PREFIX, PatchLogger, patch_id

log = logging.getLogger("record")


async def discover(seconds: float) -> tuple[list[str], list[str]]:
    """掃描一次，回傳 (貼片 ID 列表, Polar ID 列表)，已套用 devices.json 的略過設定。"""
    ecg: dict[str, int] = {}
    polar: dict[str, int] = {}

    def cb(d, adv):
        name = d.name or adv.local_name or ""
        if name.startswith(NAME_PREFIX):
            ecg[patch_id(name)] = adv.rssi
        elif name.startswith("Polar"):
            polar[name.split()[-1]] = adv.rssi

    async with BleakScanner(cb):
        await asyncio.sleep(seconds)
    labels, skips = load_devices()
    keep_ecg, keep_polar = [], []
    for kind, found, keep in (("貼片", ecg, keep_ecg), ("手環", polar, keep_polar)):
        for dev_id, rssi in sorted(found.items(), key=lambda kv: labels.get(kv[0].upper(), kv[0])):
            tag = f" = {labels[dev_id.upper()]}" if dev_id.upper() in labels else ""
            if dev_id.upper() in skips:
                log.info("掃到%s %s%s（RSSI %d）→ 略過：%s", kind, dev_id, tag, rssi, skips[dev_id.upper()])
            else:
                log.info("掃到%s %s%s（RSSI %d）", kind, dev_id, tag, rssi)
                keep.append(dev_id)
    return keep_ecg, keep_polar


def merge_all(out_dir: Path, session: str) -> int:
    """把 Polar 合併表與貼片合併表依時間並排成一張總表。回傳列數。"""
    frames = []
    for name in (f"merged_{session}.csv", f"merged_ecg_{session}.csv"):
        p = out_dir / name
        if p.exists() and p.stat().st_size > 0:
            df = pd.read_csv(p, parse_dates=["time"]).set_index("time")
            if len(df.columns):
                frames.append(df)
    if not frames:
        return 0
    allm = pd.concat(frames, axis=1, sort=True).resample("1s").mean().round(1)
    allm.index.name = "time"
    allm.to_csv(out_dir / f"merged_all_{session}.csv")
    return len(allm)


async def main_async(args) -> int:
    if args.ecg or args.polar:
        ecg_ids, polar_ids = list(args.ecg), list(args.polar)
    else:
        log.info("自動掃描 %.0f 秒…", args.scan_time)
        ecg_ids, polar_ids = await discover(args.scan_time)
    if not ecg_ids and not polar_ids:
        log.error("沒有掃到任何貼片或手環。請確認都已開機、在附近，且貼片沒有被手機 App 連著。")
        return 2
    total = len(ecg_ids) + len(polar_ids)
    if total > args.max_connections:
        room = max(args.max_connections - len(ecg_ids), 0)
        dropped = polar_ids[room:]
        polar_ids = polar_ids[:room]
        ecg_drop = ecg_ids[args.max_connections:]
        ecg_ids = ecg_ids[:args.max_connections]
        log.warning("共掃到 %d 顆，超過藍牙上限 %d。這次不連：%s", total, args.max_connections,
                    ", ".join(dropped + ecg_drop))

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
    patches = [PatchLogger(p, out_dir, session, t0, stop, labels.get(p.upper()), args.ecg_stale)
               for p in ecg_ids]
    polars = [DeviceLogger(d, out_dir, session, t0, stop, 300.0, None, 15.0, args.polar_stale,
                           labels.get(d.split("@")[0].upper()))
              for d in polar_ids]
    log.info("session %s | 貼片 %d 顆 %s | 手環 %d 顆 %s | 輸出 %s", session,
             len(patches), [p.label for p in patches], len(polars), [d.label for d in polars], out_dir)

    # 逐一啟動：先貼片（資料量大、先佔好連線），再手環
    tasks = []
    for i, lg in enumerate([*patches, *polars]):
        if i:
            await _sleep_or_stop(args.stagger, stop)
        tasks.append(asyncio.create_task(lg.run(), name=lg.label))

    async def status_printer():
        while not stop.is_set():
            await _sleep_or_stop(args.status_interval, stop)
            if stop.is_set():
                break
            now = time.time()
            e = " ".join(
                f"{p.label}:{p.rate_hz():.0f}Hz" + ("" if p.last_packet and now - p.last_packet < 3 else "(無資料!)")
                + (f"/斷{p.n_disconnects}" if p.n_disconnects else "") for p in patches)
            h = " ".join(
                f"{d.label}:{d.last_hr if d.last_sample and now - d.last_sample < 5 else '無資料!'}"
                + (f"/斷{d.n_disconnects}" if d.n_disconnects else "") for d in polars)
            log.info("狀態 %3.0f 分 | 貼片 %s | 手環HR %s", (now - t0) / 60, e or "-", h or "-")

    async def duration_timer():
        if args.duration:
            await _sleep_or_stop(args.duration, stop)
            if not stop.is_set():
                log.info("達到設定時長 %.0f 秒，停止。", args.duration)
                stop.set()

    def polar_merge() -> int:
        return merge_csvs([d.path for d in polars], out_dir / f"merged_{session}.csv") if polars else 0

    async def periodic_merge():
        while not stop.is_set():
            await _sleep_or_stop(args.merge_interval, stop)
            if not stop.is_set() and polars:
                try:
                    await loop.run_in_executor(None, polar_merge)
                except Exception as ex:
                    log.warning("合併失敗：%s", ex)

    aux = [asyncio.create_task(c()) for c in (status_printer, duration_timer, periodic_merge)]
    try:
        await stop.wait()
    finally:
        stop.set()
        _, pending = await asyncio.wait(tasks, timeout=12.0)
        for t in pending:
            t.cancel()
        for t in aux:
            t.cancel()
        await asyncio.gather(*tasks, *aux, return_exceptions=True)

    log.info("=== 總結（%.1f 分鐘）===", (time.time() - t0) / 60)
    for p in patches:
        log.info("[貼片 %s] 封包 %d，%.1f MB，連線 %d 次，斷線 %d 次，最大封包間隔 %.2f s",
                 p.tag, p.n_packets, p.n_bytes / 1e6, p.n_connects, p.n_disconnects, p.max_gap)
    for d in polars:
        gs = d.gap_stats()
        log.info("[手環 %s] 筆數 %d，連線 %d 次，斷線 %d 次，電量 %s%%%s", d.tag, d.n_samples, d.n_connects,
                 d.n_disconnects, d.battery, f"，>3 s 缺口 {gs['gaps_over_3s']} 次" if gs else "")
    try:
        if polars:
            log.info("手環合併表 merged_%s.csv（%d 列）", session, polar_merge())
    except Exception as ex:
        log.warning("手環合併失敗：%s", ex)
    if patches and not args.no_process:
        log.info("貼片後處理中（極性矯正、R 波、每秒心率）…")
        try:
            from ecg_process import process_session
            process_session(out_dir, session)
        except Exception as ex:
            log.warning("貼片後處理失敗：%s。原始檔都在，可事後雙擊 PROCESS_ECG.cmd 補做。", ex)
    try:
        n = merge_all(out_dir, session)
        if n:
            log.info("總表 merged_all_%s.csv（%d 列，手環與貼片心率並排）", session, n)
    except Exception as ex:
        log.warning("總表產生失敗：%s", ex)
    return 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ecg", nargs="*", default=[], help="指定貼片 ID（如 2512-03）；有指定就不自動掃描")
    p.add_argument("--polar", nargs="*", default=[], help="指定 Polar ID（如 0C2DC632）")
    p.add_argument("--scan-time", type=float, default=12.0)
    p.add_argument("--max-connections", type=int, default=9, help="這臺電腦藍牙的同時連線上限")
    p.add_argument("--out", default="data")
    p.add_argument("--duration", type=float, default=0, help="錄多久（秒），0 = 直到 Ctrl-C")
    p.add_argument("--stagger", type=float, default=2.0)
    p.add_argument("--status-interval", type=float, default=60)
    p.add_argument("--merge-interval", type=float, default=300)
    p.add_argument("--ecg-stale", type=float, default=10, help="貼片幾秒沒資料就強制重連")
    p.add_argument("--polar-stale", type=float, default=60, help="手環幾秒沒資料就強制重連")
    p.add_argument("--no-process", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("bleak").setLevel(logging.ERROR)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())

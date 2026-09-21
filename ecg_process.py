"""TriBLE 心電原始檔後處理：時間軸校正 → 極性自動矯正 → R 波偵測 → RR 間隔與每秒心率。

用法：
    uv run python ecg_process.py --session 20260921_101500      # 處理某一次收錄的所有貼片
    uv run python ecg_process.py --all                          # 處理 data/ 下所有還沒處理過的收錄
    uv run python ecg_process.py --session … --export-txt       # 另外匯出 main.py 讀得懂的單通道 txt

輸出：
    ecgrr_<編號>_<session>.csv   每一拍：time, rr_ms, bad（1 = 被判為異常，不應納入 HRV）
    ecghr_<編號>_<session>.csv   每秒：time, hr_ecg
    merged_ecg_<session>.csv     每秒一列、每顆貼片一欄
    ecg_summary_<session>.csv    每顆貼片一列：實際取樣率、是否翻轉極性、拍數、異常比例、SDNN、RMSSD
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.signal import butter, filtfilt, find_peaks

log = logging.getLogger("ecgproc")

NOMINAL_FS = 1000.0
N_CH = 3


# ---------------------------------------------------------------- 讀檔與時間軸

def load_raw(bin_path: Path, idx_path: Path):
    """回傳 (samples[n,3] uint8, index DataFrame)。"""
    raw = np.fromfile(bin_path, dtype=np.uint8)
    raw = raw[: len(raw) // N_CH * N_CH].reshape(-1, N_CH)
    idx = pd.read_csv(idx_path)
    # pandas 3 解析字串時解析度不一定是奈秒，明確換算成 epoch 秒
    idx["t"] = (pd.to_datetime(idx["pc_time"]) - pd.Timestamp(0)) / pd.Timedelta(seconds=1)
    return raw, idx


def sample_times(idx: pd.DataFrame, n_samples: int) -> tuple[np.ndarray, list[dict]]:
    """用封包到達時間，對每個連線段做線性擬合，算出每個取樣點的電腦時間（epoch 秒）。

    貼片取樣時脈每顆差 ±1.3%，且封包到達時間有 0.1 s 級抖動，所以用整段的線性擬合把抖動平均掉。
    回傳 (times[n_samples], 各段資訊)。
    """
    times = np.full(n_samples, np.nan)
    segs = []
    for seg_id, g in idx.groupby("segment"):
        first = int(g["byte_offset"].iloc[0]) // N_CH
        end_counts = (g["byte_offset"].to_numpy() + g["n_bytes"].to_numpy()) // N_CH   # 每包結束時的累計點數
        last = min(int(end_counts[-1]), n_samples)
        if last <= first:
            continue
        t = g["t"].to_numpy()
        if len(g) >= 100 and t[-1] - t[0] >= 5:
            slope, intercept = np.polyfit(end_counts.astype(float), t, 1)     # t = intercept + slope * count
            fs = 1.0 / slope
            if not (0.9 * NOMINAL_FS < fs < 1.1 * NOMINAL_FS):
                fs, intercept = NOMINAL_FS, t[0] - end_counts[0] / NOMINAL_FS
        else:   # 段太短，擬合不可靠
            fs, intercept = NOMINAL_FS, t[0] - end_counts[0] / NOMINAL_FS
        n = np.arange(first, last)
        times[first:last] = intercept + (n + 1) / fs
        segs.append({"segment": int(seg_id), "first": first, "last": last, "fs": fs,
                     "start": times[first], "end": times[last - 1]})
    return times, segs


# ---------------------------------------------------------------- 訊號處理

def bandpass(x: np.ndarray, fs: float, low: float, high: float) -> np.ndarray:
    b, a = butter(3, [low, high], fs=fs, btype="band")
    return filtfilt(b, a, x)


def detect_polarity(x: np.ndarray, fs: float) -> tuple[int, float]:
    """回傳 (+1 正常 / -1 反相, 信心值 0–1)。

    做法：先不管方向找出 QRS 位置（|訊號| 的尖峰），把幾十拍疊起來取中位數得到平均波形，
    再看平均波形裡絕對值最大的偏折是朝上還是朝下。整段只判一次，比逐拍判斷穩。
    """
    if len(x) < fs * 5:
        return 1, 0.0
    y = bandpass(x, fs, 5, 30)
    dist = int(0.3 * fs)
    pk, props = find_peaks(np.abs(y), distance=dist, height=np.percentile(np.abs(y), 98) * 0.4)
    if len(pk) < 5:
        return 1, 0.0
    order = np.argsort(props["peak_heights"])
    pk = pk[order[len(order) // 4:]][:400]            # 丟掉最小的 1/4（多半是雜訊）
    w = int(0.08 * fs)
    wide = bandpass(x, fs, 1, 40)
    beats = [wide[p - w:p + w] - np.median(wide[p - 3 * w:p - w]) for p in pk if p - 3 * w >= 0 and p + w < len(x)]
    if len(beats) < 5:
        return 1, 0.0
    tpl = np.median(np.array(beats), axis=0)
    up, down = tpl.max(), -tpl.min()
    sign = 1 if up >= down else -1
    conf = abs(up - down) / (up + down + 1e-12)
    return sign, float(conf)


def detect_r_peaks(x: np.ndarray, fs: float) -> np.ndarray:
    """x 需為極性已矯正的訊號。回傳 R 波的取樣點位置。"""
    y = bandpass(x, fs, 15, 35)
    dist = int(0.3 * fs)
    _, props = find_peaks(y, height=0, distance=dist)
    if len(props["peak_heights"]) == 0:
        return np.array([], dtype=int)
    height = 0.3 * np.percentile(props["peak_heights"], 90)
    pk, _ = find_peaks(y, height=height, distance=dist)
    # 把位置細修到原訊號（1–40 Hz）在 ±30 ms 內的最大值，拍點時間比較準
    wide = bandpass(x, fs, 1, 40)
    w = int(0.03 * fs)
    out = []
    for p in pk:
        a, b = max(p - w, 0), min(p + w + 1, len(x))
        out.append(a + int(np.argmax(wide[a:b])))
    return np.unique(np.array(out, dtype=int))


def mark_bad_rr(rr_ms: np.ndarray, peaks: np.ndarray, raw: np.ndarray, gap_mask: np.ndarray,
                rr_low=300, rr_high=2000, local_tol=0.3, window=31) -> np.ndarray:
    """標記不可信的 RR：生理範圍外、與附近中位數差太多、跨越斷線缺口、或該拍內原始訊號飽和。"""
    bad = (rr_ms < rr_low) | (rr_ms > rr_high)
    for _ in range(3):
        s = pd.Series(np.where(bad, np.nan, rr_ms))
        med = s.rolling(window, center=True, min_periods=3).median().bfill().ffill().to_numpy()
        bad |= np.abs(rr_ms - med) > local_tol * med
    for i in range(len(rr_ms)):
        a, b = peaks[i], peaks[i + 1] + 1
        seg = raw[a:b]
        if gap_mask[a:b].any() or np.mean((seg == 0) | (seg == 255)) > 0.02:
            bad[i] = True
    return bad


# ---------------------------------------------------------------- 單顆處理

def process_one(bin_path: Path, idx_path: Path, channel: int = 0) -> dict:
    raw, idx = load_raw(bin_path, idx_path)
    n = len(raw)
    if n < NOMINAL_FS * 10:
        raise ValueError(f"資料太短（{n} 點）")
    times, segs = sample_times(idx, n)
    fs = float(np.median([s["fs"] for s in segs])) if segs else NOMINAL_FS

    # 連線段之間的交界標成缺口，跨缺口的 RR 不採用
    gap_mask = np.isnan(times)
    for s in segs[1:]:
        gap_mask[s["first"]] = True

    x = raw[:, channel].astype(float)
    x = x - np.median(x)
    sign, conf = detect_polarity(x, fs)
    x *= sign

    peaks = detect_r_peaks(x, fs)
    if len(peaks) < 3:
        raise ValueError("偵測不到足夠的 R 波（貼片可能沒貼好）")
    # 每一拍的時間用各段擬合後的取樣時間，不用「點數 ÷ 取樣率」
    t_beats = times[peaks]
    ok = ~np.isnan(t_beats)
    peaks, t_beats = peaks[ok], t_beats[ok]
    rr_ms = np.diff(t_beats) * 1000.0
    bad = mark_bad_rr(rr_ms, peaks, raw[:, channel], gap_mask)

    beat_time = pd.to_datetime(t_beats[1:], unit="s")
    rr = pd.DataFrame({"time": beat_time, "rr_ms": np.round(rr_ms, 1), "bad": bad.astype(int)})

    hr_beat = pd.Series(np.where(bad, np.nan, 60000.0 / rr_ms), index=beat_time)
    hr_1s = hr_beat.resample("1s").mean().interpolate(limit=3)

    good = rr_ms[~bad]
    adj = (~bad[:-1]) & (~bad[1:])
    summary = {
        "n_samples": n, "minutes": round(n / fs / 60, 2), "fs_hz": round(fs, 2),
        "segments": len(segs), "polarity": "inverted→flipped" if sign < 0 else "normal",
        "polarity_conf": round(conf, 2), "beats": int(len(peaks)), "bad_rr_pct": round(float(bad.mean() * 100), 1),
        "mean_hr": round(float(60000 / good.mean()), 1) if len(good) else np.nan,
        "sdnn_ms": round(float(np.std(good, ddof=1)), 1) if len(good) > 2 else np.nan,
        "rmssd_ms": round(float(np.sqrt(np.mean(np.diff(rr_ms)[adj] ** 2))), 1) if adj.sum() > 2 else np.nan,
    }
    return {"rr": rr, "hr_1s": hr_1s, "summary": summary, "signal": x, "times": times, "fs": fs}


def export_txt(result: dict, path: Path) -> None:
    """匯出極性矯正後的單通道波形，每行一個值，檔名含實際取樣率，main.py 的 load_patch 可直接讀。"""
    x = result["signal"]
    np.savetxt(path, np.round(x - x.min(), 1), fmt="%.1f")


# ---------------------------------------------------------------- 整個 session

SESSION_RE = re.compile(r"^ecg_(?P<label>.+)_(?P<session>\d{8}_\d{6})\.bin$")


def process_session(data_dir: Path, session: str, export: bool = False) -> pd.DataFrame | None:
    data_dir = Path(data_dir)
    cols, rows = [], []
    for bin_path in sorted(data_dir.glob(f"ecg_*_{session}.bin")):
        label = SESSION_RE.match(bin_path.name)["label"]
        idx_path = bin_path.with_name(bin_path.stem + "_index.csv")
        try:
            r = process_one(bin_path, idx_path)
        except Exception as e:
            log.warning("[%s] 無法處理：%s", label, e)
            rows.append({"label": label, "error": str(e)})
            continue
        r["rr"].to_csv(data_dir / f"ecgrr_{label}_{session}.csv", index=False,
                       date_format="%Y-%m-%dT%H:%M:%S.%f")
        hr = r["hr_1s"].rename("hr_ecg").rename_axis("time").round(1)
        hr.to_csv(data_dir / f"ecghr_{label}_{session}.csv")
        cols.append(hr.rename(f"hr_{label}"))
        s = r["summary"]
        rows.append({"label": label, **s})
        log.info("[%s] %.1f 分，實際 %.1f Hz，極性 %s（信心 %.2f），%d 拍，異常 RR %.1f%%，平均 HR %.1f，SDNN %.0f，RMSSD %.0f",
                 label, s["minutes"], s["fs_hz"], s["polarity"], s["polarity_conf"], s["beats"],
                 s["bad_rr_pct"], s["mean_hr"], s["sdnn_ms"], s["rmssd_ms"])
        if export:
            start = pd.to_datetime(np.nanmin(r["times"]), unit="s").strftime("%Y%m%d%H%M%S")
            export_txt(r, data_dir / f"TriBLE_{label}_{start}_({r['fs']:.1f}Hz).txt")
    if rows:
        pd.DataFrame(rows).to_csv(data_dir / f"ecg_summary_{session}.csv", index=False)
    if not cols:
        return None
    merged = pd.concat(cols, axis=1, sort=True).resample("1s").mean().round(1)
    merged.index.name = "time"
    merged.to_csv(data_dir / f"merged_ecg_{session}.csv")
    log.info("合併表 merged_ecg_%s.csv（%d 列）", session, len(merged))
    return merged


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--session", help="檔名尾巴的 YYYYMMDD_HHMMSS")
    p.add_argument("--all", action="store_true", help="處理所有還沒有 ecg_summary 的收錄")
    p.add_argument("--data-dir", default="data")
    p.add_argument("--export-txt", action="store_true", help="另外匯出 main.py 可讀的單通道 txt")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    d = Path(a.data_dir)
    if a.all:
        sessions = sorted({SESSION_RE.match(f.name)["session"] for f in d.glob("ecg_*.bin") if SESSION_RE.match(f.name)})
        sessions = [s for s in sessions if not (d / f"ecg_summary_{s}.csv").exists()]
        if not sessions:
            log.info("沒有需要處理的收錄。")
    elif a.session:
        sessions = [a.session]
    else:
        p.error("請給 --session 或 --all")
    for s in sessions:
        log.info("=== session %s ===", s)
        process_session(d, s, a.export_txt)
    return 0


if __name__ == "__main__":
    sys.exit(main())

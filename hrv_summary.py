"""把逐拍資料算成每 5 分鐘一段的 HRV：手環的 ppi_*.csv（PPI）與貼片的 ecgrr_*.csv（RR）都可以。

用法：
    uv run python hrv_summary.py --session 20261012_093000          # 某一次收錄的所有裝置
    uv run python hrv_summary.py --day 20261012                      # 某一天全部
    uv run python hrv_summary.py --window 5min                       # 分段長度（預設 5min）
輸出：data/hrv_<session 或 day>.csv，每列 = 一顆裝置 × 一個時段：
    label, window_start, n_beats, n_valid, pct_valid, mean_hr, sdnn_ms, rmssd_ms, pnn50
只用「有效」的拍：手環 blocker=0、貼片 bad=0；一段內有效拍少於 60 就不算（留空）。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

MIN_BEATS = 60


def load_beats(path: Path) -> tuple[str, pd.DataFrame] | None:
    """回傳 (label, DataFrame[time, rr_ms, valid])。"""
    name = path.name
    df = pd.read_csv(path)
    if df.empty:
        return None
    if name.startswith("ppi_"):
        label = re.match(r"ppi_(.+)_\d{8}_\d{6}\.csv$", name)["label"] if False else name[4:].rsplit("_", 2)[0]
        out = pd.DataFrame({"time": pd.to_datetime(df["beat_time"]), "rr_ms": df["ppi_ms"].astype(float),
                            "valid": df["blocker"].astype(int) == 0})
        label = df["label"].iloc[0] if "label" in df.columns else label
    elif name.startswith("ecgrr_"):
        label = name[6:].rsplit("_", 2)[0]
        out = pd.DataFrame({"time": pd.to_datetime(df["time"]), "rr_ms": df["rr_ms"].astype(float),
                            "valid": df["bad"].astype(int) == 0})
    else:
        return None
    return str(label), out.sort_values("time").reset_index(drop=True)


def hrv_windows(beats: pd.DataFrame, window: str) -> pd.DataFrame:
    rows = []
    for start, g in beats.set_index("time").groupby(pd.Grouper(freq=window)):
        if g.empty:
            continue
        v = g[g["valid"]]["rr_ms"].to_numpy()
        row = {"window_start": start, "n_beats": len(g), "n_valid": len(v),
               "pct_valid": round(100.0 * len(v) / len(g), 1)}
        if len(v) >= MIN_BEATS:
            # 相鄰差只算「連續兩拍都有效」的
            ok = g["valid"].to_numpy()
            rr = g["rr_ms"].to_numpy()
            adj = ok[:-1] & ok[1:]
            diffs = np.diff(rr)[adj]
            row.update({"mean_hr": round(60000.0 / v.mean(), 1), "sdnn_ms": round(float(np.std(v, ddof=1)), 1),
                        "rmssd_ms": round(float(np.sqrt(np.mean(diffs ** 2))), 1) if len(diffs) > 1 else np.nan,
                        "pnn50": round(100.0 * float(np.mean(np.abs(diffs) > 50)), 1) if len(diffs) > 1 else np.nan})
        rows.append(row)
    return pd.DataFrame(rows)


def summarize(files: list[Path], window: str) -> pd.DataFrame:
    parts = []
    for f in files:
        loaded = load_beats(f)
        if loaded is None:
            continue
        label, beats = loaded
        w = hrv_windows(beats, window)
        if not w.empty:
            w.insert(0, "label", label)
            w.insert(1, "source", "polar_ppi" if f.name.startswith("ppi_") else "ecg_patch")
            parts.append(w)
    if not parts:
        return pd.DataFrame()
    cols = ["label", "source", "window_start", "n_beats", "n_valid", "pct_valid", "mean_hr", "sdnn_ms", "rmssd_ms", "pnn50"]
    return pd.concat(parts, ignore_index=True).reindex(columns=cols).sort_values(["window_start", "label"])


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--session", help="YYYYMMDD_HHMMSS")
    p.add_argument("--day", help="YYYYMMDD")
    p.add_argument("--data-dir", default="data")
    p.add_argument("--window", default="5min")
    p.add_argument("-o", "--out")
    a = p.parse_args(argv)
    d = Path(a.data_dir)
    key = a.session or a.day
    if not key:
        p.error("請給 --session 或 --day")
    files = sorted(d.glob(f"ppi_*_{key}*.csv")) + sorted(d.glob(f"ecgrr_*_{key}*.csv"))
    if not files:
        print(f"找不到 {key} 的 ppi_*.csv / ecgrr_*.csv")
        return 1
    df = summarize(files, a.window)
    out = Path(a.out) if a.out else d / f"hrv_{key}.csv"
    df.to_csv(out, index=False)
    print(f"{len(files)} 個檔 → {out}（{len(df)} 列）")
    with pd.option_context("display.width", 200, "display.max_rows", 60):
        print(df.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

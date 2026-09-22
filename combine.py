"""把兩臺電腦各自收錄的結果合成一張總表（每秒一列，手環與貼片心率並排）。

用法：
    uv run python combine.py 20260923                 # 合併 data/ 裡這一天所有的 merged_*.csv 與 merged_ecg_*.csv
    uv run python combine.py 20260923 --data-dir data --other-dir "D:\\從主機複製來的data"
    uv run python combine.py --files data/merged_20260923_101500.csv data/merged_ecg_20260923_101430.csv

另一臺電腦的檔案（整個 data 資料夾）複製到這臺後，放在同一個 data/ 或用 --other-dir 指定即可。
兩臺的 session 時間戳不同沒關係，用 time 欄位對齊。
輸出：data/merged_all_<日期>.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

PATTERNS = ("merged_{d}*.csv", "merged_ecg_{d}*.csv")


def collect(dirs: list[Path], day: str) -> list[Path]:
    files = []
    for d in dirs:
        for pat in PATTERNS:
            files += sorted(d.glob(pat.format(d=day)))
    # 去掉總表自己與重複
    return [f for f in dict.fromkeys(files) if not f.name.startswith("merged_all_")]


def combine(files: list[Path], out: Path) -> tuple[int, list[str]]:
    frames, cols = [], []
    for f in files:
        df = pd.read_csv(f, parse_dates=["time"]).set_index("time")
        df = df[[c for c in df.columns if c.startswith("hr_")]]
        if df.empty or not len(df.columns):
            continue
        frames.append(df)
    if not frames:
        return 0, []
    allm = pd.concat(frames, axis=1, sort=True)
    # 同一顆裝置若出現在兩個檔（例如同一天錄兩次），合成同一欄
    allm = allm.T.groupby(level=0).mean().T
    allm = allm.resample("1s").mean().round(1)
    allm.index.name = "time"
    allm = allm[sorted(allm.columns, key=lambda c: (not c[3:4].isdigit(), c))]
    allm.to_csv(out)
    return len(allm), list(allm.columns)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("day", nargs="?", help="日期 YYYYMMDD（或 YYYYMMDD_HH 更精確）")
    p.add_argument("--data-dir", default="data")
    p.add_argument("--other-dir", action="append", default=[], help="另一臺電腦複製過來的資料夾，可重複")
    p.add_argument("--files", nargs="*", default=[], help="直接指定要合併的 merged_*.csv")
    p.add_argument("-o", "--out")
    a = p.parse_args(argv)
    dirs = [Path(a.data_dir)] + [Path(x) for x in a.other_dir]
    if a.files:
        files = [Path(f) for f in a.files]
        tag = a.day or "custom"
    elif a.day:
        files = collect(dirs, a.day)
        tag = a.day
    else:
        p.error("請給日期，或用 --files 指定檔案")
    if not files:
        print(f"找不到 {a.day} 的 merged_*.csv / merged_ecg_*.csv（找過：{[str(d) for d in dirs]}）")
        return 1
    out = Path(a.out) if a.out else Path(a.data_dir) / f"merged_all_{tag}.csv"
    n, cols = combine(files, out)
    print(f"合併 {len(files)} 個檔：")
    for f in files:
        print(f"   {f}")
    print(f"→ {out}（{n} 列，欄位 {cols}）")
    return 0 if n else 1


if __name__ == "__main__":
    sys.exit(main())

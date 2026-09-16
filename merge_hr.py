"""事後把多個 data/hr_*.csv 合併成每秒一列、每顆一欄的寬表。

用法：
    uv run python merge_hr.py data/hr_*_20260917_080000.csv -o data/merged.csv
    uv run python merge_hr.py --session 20260917_080000       # 自動抓 data/ 下同一個 session 的檔
"""
import argparse
import sys
from pathlib import Path

from polar_hr_logger import merge_csvs


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("files", nargs="*", help="hr_*.csv 檔案")
    p.add_argument("--session", help="session 代碼（檔名尾巴的 YYYYMMDD_HHMMSS）")
    p.add_argument("--data-dir", default="data")
    p.add_argument("-o", "--out", help="輸出檔名（預設 data/merged_<session>.csv）")
    a = p.parse_args(argv)

    files = [Path(f) for f in a.files]
    if a.session:
        files += sorted(Path(a.data_dir).glob(f"hr_*_{a.session}.csv"))
    if not files:
        p.error("沒有輸入檔案")
    out = Path(a.out) if a.out else Path(a.data_dir) / f"merged_{a.session or 'all'}.csv"
    n = merge_csvs(files, out)
    print(f"合併 {len(files)} 個檔 → {out}（{n} 列）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

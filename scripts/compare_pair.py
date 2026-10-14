"""比對同一人同時戴的貼片（ecgrr）與手環（ppi / hr）：HRV、逐拍差、每秒心率一致性，並畫圖。

用法：uv run python scripts/compare_pair.py <資料夾> <session> <貼片編號> <手環編號>
例：  uv run python scripts/compare_pair.py data/hrv_cmp2 20261014_101500 E2605-05 9P
輸出：<資料夾>/compare_<session>.png，並在畫面印出比對表。
"""
import sys
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hrv_summary import artifact_mask  # noqa: E402


def hrv(x):
    x = np.asarray(x, float)
    dif = np.diff(x)
    return {"拍數": len(x), "平均HR": 60000 / x.mean(), "SDNN": np.std(x, ddof=1),
            "RMSSD": np.sqrt(np.mean(dif ** 2)), "pNN50": 100 * np.mean(np.abs(dif) > 50)}


def main(d, s, ecg, pol):
    d = Path(d)
    rr = pd.read_csv(d / f"ecgrr_{ecg}_{s}.csv", parse_dates=["time"])
    pp = pd.read_csv(d / f"ppi_{pol}_{s}.csv", parse_dates=["beat_time"])
    summ = pd.read_csv(d / f"ecg_summary_{s}.csv")
    ma = pd.read_csv(d / f"merged_all_{s}.csv", parse_dates=["time"]).set_index("time")
    print("貼片品質：", summ.to_dict("records")[0])

    t0, t1 = pp.beat_time.min(), pp.beat_time.max()
    rr_c = rr[(rr.time >= t0) & (rr.time <= t1)]
    g = rr_c[rr_c.bad == 0]
    v = pp[pp.blocker == 0].copy()
    v["clean"] = ~artifact_mask(v.ppi_ms.to_numpy())
    vc = v[v.clean]
    print(f"\n共同時段 {t0:%H:%M:%S}–{t1:%H:%M:%S}（{(t1 - t0).total_seconds() / 60:.1f} 分）")
    print(f"手環 PPI {len(pp)} 拍：裝置標有效 {len(v)}（{100 * len(v) / len(pp):.0f}%），再清理後 {len(vc)}（{100 * len(vc) / len(pp):.0f}%）")
    tab = pd.DataFrame({"貼片 ECG": hrv(g.rr_ms), "手環 PPI": hrv(vc.ppi_ms)}).T
    print(tab.round(1).to_string())

    m = pd.merge_asof(vc.sort_values("beat_time"), g.sort_values("time"), left_on="beat_time", right_on="time",
                      direction="nearest", tolerance=pd.Timedelta("3s")).dropna(subset=["rr_ms"])
    dd = m.ppi_ms - m.rr_ms
    print(f"\n逐拍對照（{len(m)} 對）：|PPI−RR| 中位數 {dd.abs().median():.0f} ms，≤50 ms 佔 {(dd.abs() <= 50).mean() * 100:.0f}%")

    hc = [c for c in ma.columns if c == f"hr_{pol}"]
    ec = [c for c in ma.columns if c == f"hr_{ecg}"]
    if hc and ec:
        mm = ma[[hc[0], ec[0]]].dropna()
        diff = mm[hc[0]] - mm[ec[0]]
        r = np.corrcoef(mm[hc[0]], mm[ec[0]])[0, 1] if len(mm) > 3 else np.nan
        print(f"標準每秒心率（{len(mm)} 秒）：r={r:.2f}，偏差 {diff.mean():+.1f} bpm，|誤差|≤5 佔 {(diff.abs() <= 5).mean() * 100:.0f}%")
    # 用 PPI 算的每秒心率
    ph = (60000 / vc.set_index("beat_time").ppi_ms).resample("1s").mean().interpolate(limit=5)
    eh = (60000 / g.set_index("time").rr_ms).resample("1s").mean().interpolate(limit=3)
    j = pd.concat([ph.rename("p"), eh.rename("e")], axis=1).dropna()
    if len(j) > 10:
        dj = j.p - j.e
        print(f"PPI 換算每秒心率（{len(j)} 秒）：r={np.corrcoef(j.p, j.e)[0, 1]:.2f}，偏差 {dj.mean():+.1f} bpm，|誤差|≤5 佔 {(dj.abs() <= 5).mean() * 100:.0f}%")

    fig, ax = plt.subplots(2, 1, figsize=(12, 6.5), sharex=True)
    ax[0].plot(rr_c.time, rr_c.rr_ms, ".", ms=3, color="#275", label="patch ECG RR")
    ax[0].plot(vc.beat_time, vc.ppi_ms, ".", ms=4, color="#c33", label="Polar PPI used")
    rej = pp[~pp.index.isin(vc.index)]
    ax[0].plot(rej.beat_time, rej.ppi_ms, "x", ms=4, color="#f9a", alpha=.7, label="Polar PPI rejected")
    ax[0].set_ylabel("interval (ms)"); ax[0].legend(fontsize=8, ncol=3); ax[0].set_ylim(300, 1500)
    ax[1].plot(eh.index, eh.values, color="#275", label="patch HR from RR")
    ax[1].plot(ph.index, ph.values, color="#c33", label="Polar HR from PPI")
    if hc:
        ax[1].plot(ma.index, ma[hc[0]], color="#c33", alpha=.35, lw=1, label="Polar standard HR")
    ax[1].set_ylabel("bpm"); ax[1].legend(fontsize=8); ax[1].set_xlabel("time")
    plt.tight_layout()
    out = d / f"compare_{s}.png"
    plt.savefig(out, dpi=110)
    print(f"\n圖：{out}")


if __name__ == "__main__":
    main(*sys.argv[1:5])

import csv
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

import ecg_process as ep
from trible_logger import INDEX_FIELDS, patch_id


def synth_ecg(seconds: float, fs: float, bpm: float, invert: bool, seed: int = 0) -> np.ndarray:
    """合成心電：窄的 R 波 + 較寬較矮的 T 波 + 雜訊，量化成 uint8。回傳 [n,3]。"""
    rng = np.random.default_rng(seed)
    n = int(seconds * fs)
    t = np.arange(n) / fs
    x = np.zeros(n)
    rr = 60.0 / bpm
    beat = 0.5
    while beat < seconds - 0.5:
        x += 40 * np.exp(-((t - beat) / 0.012) ** 2)            # R
        x -= 8 * np.exp(-((t - beat - 0.03) / 0.015) ** 2)      # S
        x += 9 * np.exp(-((t - beat - 0.25) / 0.06) ** 2)       # T
        beat += rr + rng.normal(0, 0.02)
    x += rng.normal(0, 1.0, n)
    if invert:
        x = -x
    ch1 = np.clip(np.round(x + 120), 0, 255)
    out = np.stack([ch1, np.clip(np.round(x * 0.7 + 120), 0, 255), np.clip(np.round(x * 0.5 + 120), 0, 255)], axis=1)
    return out.astype(np.uint8)


def write_recording(tmp: Path, label: str, session: str, samples: np.ndarray, fs_true: float,
                    t_start: float, jitter: float = 0.03, seed: int = 1) -> None:
    """模擬 logger 寫出的 .bin 與 _index.csv：每包 36 點，到達時間 = 真實時間 + 隨機延遲。"""
    rng = np.random.default_rng(seed)
    raw = samples.tobytes()
    (tmp / f"ecg_{label}_{session}.bin").write_bytes(raw)
    with open(tmp / f"ecg_{label}_{session}_index.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(INDEX_FIELDS)
        for off in range(0, len(raw), 108):
            n_end = (off + 108) // 3
            t = t_start + n_end / fs_true + abs(rng.normal(0, jitter))
            w.writerow([datetime.fromtimestamp(t).isoformat(timespec="milliseconds"),
                        f"{t - t_start:.4f}", 1, off, min(108, len(raw) - off)])


def test_patch_id():
    assert patch_id("TriBLE 2512-03") == "2512-03"


@pytest.mark.parametrize("invert", [False, True])
def test_polarity_and_hr(tmp_path, invert):
    fs_true = 1013.0          # 故意不是 1000：模擬貼片時脈誤差
    samples = synth_ecg(60, fs_true, bpm=72, invert=invert)
    write_recording(tmp_path, "T1", "20260101_000000", samples, fs_true, t_start=1_800_000_000.0)
    r = ep.process_one(tmp_path / "ecg_T1_20260101_000000.bin", tmp_path / "ecg_T1_20260101_000000_index.csv")
    s = r["summary"]
    assert s["polarity"] == ("inverted→flipped" if invert else "normal")
    assert s["polarity_conf"] > 0.3
    assert abs(s["fs_hz"] - fs_true) < 3            # 從封包到達時間估回實際取樣率
    assert abs(s["mean_hr"] - 72) < 1.5             # 若誤用名目 1000 Hz 會得到 ~71.1
    assert s["bad_rr_pct"] < 5
    assert 65 <= s["beats"] <= 75


def test_process_session_outputs(tmp_path):
    for i, (label, inv) in enumerate([("A", False), ("B", True)]):
        write_recording(tmp_path, label, "20260101_000000", synth_ecg(40, 1000.0, 60 + 20 * i, inv, seed=i),
                        1000.0, t_start=1_800_000_000.0, seed=i)
    merged = ep.process_session(tmp_path, "20260101_000000")
    assert list(merged.columns) == ["hr_A", "hr_B"]
    assert abs(merged["hr_A"].mean() - 60) < 2 and abs(merged["hr_B"].mean() - 80) < 2
    for name in ["ecgrr_A", "ecghr_B", "merged_ecg", "ecg_summary"]:
        assert list(tmp_path.glob(f"{name}_*20260101_000000.csv")) or list(tmp_path.glob(f"{name}_20260101_000000.csv"))


def test_gap_between_segments_is_marked_bad(tmp_path):
    fs = 1000.0
    a, b = synth_ecg(30, fs, 70, False, seed=3), synth_ecg(30, fs, 70, False, seed=4)
    samples = np.concatenate([a, b])
    write_recording(tmp_path, "G", "20260101_000000", samples, fs, t_start=1_800_000_000.0)
    # 把後半段改成第 2 個連線段，並晚 20 秒到達（模擬斷線重連）
    idx_path = tmp_path / "ecg_G_20260101_000000_index.csv"
    rows = list(csv.reader(open(idx_path, encoding="utf-8")))
    half = len(a) * 3
    for row in rows[1:]:
        if int(row[3]) >= half:
            row[2] = "2"
            t = datetime.fromisoformat(row[0]).timestamp() + 20
            row[0] = datetime.fromtimestamp(t).isoformat(timespec="milliseconds")
    csv.writer(open(idx_path, "w", newline="", encoding="utf-8")).writerows(rows)
    r = ep.process_one(tmp_path / "ecg_G_20260101_000000.bin", idx_path)
    assert r["summary"]["segments"] == 2
    rr = r["rr"]
    assert rr.loc[rr["rr_ms"] > 5000, "bad"].all()      # 跨 20 秒缺口的那一拍必須標為異常
    assert abs(r["summary"]["mean_hr"] - 70) < 2


def test_merge_all_side_by_side(tmp_path):
    import record_all
    s = "20260101_000000"
    (tmp_path / f"merged_{s}.csv").write_text(
        "time,hr_2P,hr_3P\n2026-01-01 00:00:00,70.0,80.0\n2026-01-01 00:00:01,71.0,\n", encoding="utf-8")
    (tmp_path / f"merged_ecg_{s}.csv").write_text(
        "time,hr_E2512-03\n2026-01-01 00:00:01,72.5\n2026-01-01 00:00:02,73.0\n", encoding="utf-8")
    assert record_all.merge_all(tmp_path, s) == 3
    rows = list(csv.DictReader(open(tmp_path / f"merged_all_{s}.csv", encoding="utf-8")))
    assert list(rows[0].keys()) == ["time", "hr_2P", "hr_3P", "hr_E2512-03"]
    assert rows[1]["hr_2P"] == "71.0" and rows[1]["hr_E2512-03"] == "72.5" and rows[1]["hr_3P"] == ""
    assert record_all.merge_all(tmp_path, "nothing") == 0


def test_device_table_and_expected(tmp_path):
    import json
    import polar_hr_logger as phl
    import record_all
    p = tmp_path / "devices.json"
    json.dump({
        "0C2DC632": "2P",
        "0C2D7633": {"label": "1P", "skip": True},
        "2512-03": {"label": "E2512-03", "type": "ecg"},
        "2605-02": {"label": "E2605-02", "type": "ecg", "skip": True},
        "2603-09": {"type": "ecg"},
    }, open(p, "w", encoding="utf-8"))
    table = phl.load_device_table(p)
    assert table["0C2DC632"] == {"label": "2P", "type": "polar", "skip": False, "id": "0C2DC632"}
    assert table["2603-09"]["label"] == "2603-09" and table["2603-09"]["type"] == "ecg"
    ecg, polar = record_all.expected_from_table(table)
    assert ecg == ["2603-09", "2512-03"] and polar == ["0C2DC632"]


def test_expected_list_keeps_searching_for_missing(tmp_path):
    """名單裡有一顆永遠連不上的手環（模擬未開機）：其他顆照常錄，它的檔案只有表頭。"""
    import asyncio as aio
    import record_all
    args = record_all.parse_args(["--polar", "sim:A", "sim:Z@fail=99", "--duration", "6",
                                  "--status-interval", "2", "--out", str(tmp_path)])
    assert aio.run(record_all.main_async(args)) == 0
    a = list(tmp_path.glob("hr_sim_A_*.csv"))[0].read_text(encoding="utf-8").strip().splitlines()
    z = list(tmp_path.glob("hr_sim_Z_*.csv"))[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(a) >= 4 and len(z) == 1

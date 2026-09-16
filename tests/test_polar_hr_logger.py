import asyncio
import csv
from pathlib import Path

import pytest

import polar_hr_logger as phl
from sim_polar import encode_hr, parse_sim_id


# ---------------------------------------------------------------- parse_hr

def test_parse_hr_uint8_no_rr():
    # flags=0x06：8-bit HR、接觸支援且偵測到；真機插電時實際送出的封包
    assert phl.parse_hr(bytes.fromhex("0600")) == (0, [], 3)
    assert phl.parse_hr(bytes([0x06, 72])) == (72, [], 3)


def test_parse_hr_uint16():
    assert phl.parse_hr(bytes([0x01, 0x2C, 0x01])) == (300, [], 0)


def test_parse_hr_with_rr():
    rr1 = int(round(0.800 * 1024)); rr2 = int(round(0.812 * 1024))
    data = bytes([0x16, 75]) + rr1.to_bytes(2, "little") + rr2.to_bytes(2, "little")
    hr, rr, contact = phl.parse_hr(data)
    assert hr == 75 and contact == 3
    assert [round(x) for x in rr] == [800, 812]


def test_parse_hr_skips_energy_expended():
    data = bytes([0x1E, 60, 0x10, 0x00]) + (1024).to_bytes(2, "little")
    hr, rr, _ = phl.parse_hr(data)
    assert hr == 60 and rr == [1000.0]


def test_parse_hr_rejects_short():
    with pytest.raises(ValueError):
        phl.parse_hr(b"\x06")


def test_encode_roundtrip():
    hr, rr, contact = phl.parse_hr(encode_hr(88, [700.0, 710.5]))
    assert hr == 88 and contact == 3
    assert [round(x, 1) for x in rr] == [pytest.approx(700.0, abs=1), pytest.approx(710.5, abs=1)]


def test_parse_sim_id():
    assert parse_sim_id("sim:A") == ("sim:A", {})
    assert parse_sim_id("sim:B@drop=20,fail=2") == ("sim:B", {"drop": 20.0, "fail": 2.0})


# ---------------------------------------------------------------- merge_csvs

def _write_csv(path: Path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=phl.CSV_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in phl.CSV_FIELDS})


def test_merge_csvs_wide_table(tmp_path):
    a = tmp_path / "hr_A_x.csv"
    b = tmp_path / "hr_B_x.csv"
    _write_csv(a, [
        {"pc_time": "2026-09-16T23:00:00.100", "device": "A", "hr_bpm": 60},
        {"pc_time": "2026-09-16T23:00:00.900", "device": "A", "hr_bpm": 62},   # 同一秒兩筆 → 平均 61
        {"pc_time": "2026-09-16T23:00:02.000", "device": "A", "hr_bpm": 0},    # 0 視為缺值
    ])
    _write_csv(b, [
        {"pc_time": "2026-09-16T23:00:01.000", "device": "B", "hr_bpm": 70},
    ])
    out = tmp_path / "merged.csv"
    n = phl.merge_csvs([a, b], out)
    assert n == 3
    rows = list(csv.DictReader(open(out, encoding="utf-8")))
    assert [r["time"] for r in rows] == ["2026-09-16 23:00:00", "2026-09-16 23:00:01", "2026-09-16 23:00:02"]
    assert rows[0]["hr_A"] == "61.0" and rows[0]["hr_B"] == ""
    assert rows[1]["hr_A"] == "" and rows[1]["hr_B"] == "70.0"
    assert rows[2]["hr_A"] == ""


def test_merge_csvs_empty(tmp_path):
    a = tmp_path / "hr_A_x.csv"
    _write_csv(a, [])
    assert phl.merge_csvs([a], tmp_path / "m.csv") == 0


# ---------------------------------------------------------------- 端到端（模擬裝置）

def test_end_to_end_four_sim_devices(tmp_path):
    args = phl.parse_args([
        "--duration", "12", "--stagger", "0.2", "--status-interval", "5",
        "--out", str(tmp_path),
        "sim:A", "sim:B@drop=4", "sim:C@fail=1", "sim:D@drop=3,fail=1",
    ])
    assert asyncio.run(phl.main_async(args)) == 0
    files = sorted(tmp_path.glob("hr_*.csv"))
    assert [f.name.split("_")[2] for f in files] == ["A", "B", "C", "D"]
    for f in files:
        rows = list(csv.DictReader(open(f, encoding="utf-8")))
        assert len(rows) >= 3, f.name
        assert rows[0]["device"].startswith("sim:") and "@" not in rows[0]["device"]
        assert all(r["rr_ms"] for r in rows)
    merged = list(tmp_path.glob("merged_*.csv"))
    assert len(merged) == 1
    header = open(merged[0], encoding="utf-8").readline().strip().split(",")
    assert header == ["time", "hr_sim:A", "hr_sim:B", "hr_sim:C", "hr_sim:D"]

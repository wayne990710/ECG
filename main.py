import re
import numpy as np
import pandas as pd
from scipy.signal import butter, filtfilt, find_peaks


# ---------- 貼片（TriAnswer）：原始 ECG 波形 ----------

def detect_frequency(file_path):
    match = re.search(r'(\d+(?:\.\d+)?)\s*Hz', file_path, re.IGNORECASE)
    if match is None:
        raise ValueError(f"檔名裡找不到取樣頻率（例如 333Hz）：{file_path}")
    return float(match.group(1))


def detect_start_time(file_path):
    # 檔名裡的 14 位數字，例如 20260810140006 = 2026/08/10 14:00:06
    match = re.search(r'(\d{14})', file_path)
    if match is None:
        raise ValueError(f"檔名裡找不到開始時間（14 位數字）：{file_path}")
    return pd.to_datetime(match.group(1), format='%Y%m%d%H%M%S')


def super_filter(data, frequency, low, high):
    b, a = butter(3, [low, high], fs=frequency, btype='band')
    return filtfilt(b, a, data)


def auto_height(data_filtered, frequency):
    distance = int(0.3 * frequency)
    _, props = find_peaks(data_filtered, height=0.01, distance=distance)
    return 0.3 * np.percentile(props['peak_heights'], 90)


def find_r_peaks(data_filtered, frequency, height):
    distance = int(0.3 * frequency)
    return find_peaks(data_filtered, height=height, distance=distance)[0]


def mark_bad_rr(rr_ms, peaks, data, frequency,
                rr_low=300, rr_high=2000, local_tol=0.3, window=31):
    bad = np.zeros(len(rr_ms), dtype=bool)
    bad |= (rr_ms < rr_low) | (rr_ms > rr_high)
    for _ in range(3):
        s = pd.Series(np.where(bad, np.nan, rr_ms))
        local_med = s.rolling(window, center=True, min_periods=3).median()
        local_med = local_med.bfill().ffill().to_numpy()
        bad |= np.abs(rr_ms - local_med) > local_tol * local_med
    sat_level = np.max(data) * 0.98
    for i in range(len(rr_ms)):
        seg = data[peaks[i]:peaks[i + 1] + 1]
        if np.any(seg == 0) or np.any(seg >= sat_level):
            bad[i] = True
    return bad


def load_patch(file_path):
    """讀貼片的原始 ECG，回傳每秒一筆的心率表：欄位 time, hr_patch"""
    frequency = detect_frequency(file_path)
    start = detect_start_time(file_path)
    data = np.array(pd.read_csv(file_path, header=None))[:, 0].astype(float)

    y = super_filter(data, frequency, 15, 35)
    height = auto_height(y, frequency)
    peaks = find_r_peaks(y, frequency, height)

    rr_ms = np.diff(peaks) / frequency * 1000
    bad = mark_bad_rr(rr_ms, peaks, data, frequency)

    # 每一拍的瞬時心率，時間點放在該拍結束的 R 波
    beat_time = start + pd.to_timedelta(peaks[1:] / frequency, unit='s')
    hr_beat = np.where(bad, np.nan, 60000 / rr_ms)
    beats = pd.Series(hr_beat, index=beat_time)

    # 重新取樣成每秒一筆（該秒內各拍的平均），沒有心跳的秒用線性內插補，最多補 3 秒
    hr_1s = beats.resample('1s').mean()
    hr_1s = hr_1s.interpolate(limit=3)
    out = hr_1s.rename('hr_patch').rename_axis('time').reset_index()

    good = ~bad
    rr_good = rr_ms[good]
    adjacent_ok = good[:-1] & good[1:]
    stats = {
        'rr_good': rr_good,
        'sdnn': np.std(rr_good, ddof=1),
        'rmssd': np.sqrt(np.mean(np.square(np.diff(rr_ms)[adjacent_ok]))),
    }

    print(f"[貼片] {file_path}")
    print(f"       開始 {start}，長度 {len(data) / frequency / 60:.1f} 分鐘，"
          f"{frequency:.0f} Hz，心跳 {len(peaks)} 下，剔除異常 RR {bad.sum()} 筆"
          f"（{bad.mean() * 100:.1f}%）")
    
    return out, stats


# ---------- 手環（Polar Flow 匯出）：每秒心率 ----------

def load_polar(file_path):
    """讀 Polar Flow 匯出的 CSV，回傳每秒一筆的心率表：欄位 time, hr_polar"""
    head = pd.read_csv(file_path, nrows=1)
    start = pd.to_datetime(head['Date'][0] + ' ' + head['Start time'][0])

    body = pd.read_csv(file_path, skiprows=2)
    elapsed = pd.to_timedelta(body['Time'])
    hr = pd.to_numeric(body['HR (bpm)'], errors='coerce')
    hr = hr.replace(0, np.nan)   # Polar 讀不到心率時會寫 0

    out = pd.DataFrame({'time': start + elapsed, 'hr_polar': hr.to_numpy()})
    out = out.set_index('time').resample('1s').mean().reset_index()

    print(f"[手環] {file_path}")
    print(f"       開始 {start}，長度 {len(out) / 60:.1f} 分鐘，"
          f"缺值 {out['hr_polar'].isna().sum()} 秒")
    return out


# ---------- 對齊與比較 ----------

def best_lag(a, b, max_lag=60):
    """在 ±max_lag 秒內找讓兩序列相關最高的位移量。回傳 (lag, r)。"""
    a = (a - np.nanmean(a)) / np.nanstd(a)
    b = (b - np.nanmean(b)) / np.nanstd(b)
    best = (0, -np.inf)
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            x, y = a[lag:], b[:len(b) - lag]
        else:
            x, y = a[:lag], b[-lag:]
        m = ~np.isnan(x) & ~np.isnan(y)
        if m.sum() < 60:
            continue
        r = np.corrcoef(x[m], y[m])[0, 1]
        if r > best[1]:
            best = (lag, r)
    return best


def compare(patch_file, polar_file, auto_align=True, max_lag=60):
    patch, stats = load_patch(patch_file)
    polar = load_polar(polar_file)
    rr_good, sdnn, rmssd = stats['rr_good'], stats['sdnn'], stats['rmssd']

    merged = pd.merge(patch, polar, on='time', how='inner')
    if len(merged) == 0:
        raise ValueError("兩個檔案的時間沒有重疊，請確認檔名裡的日期時間")
    print(f"\n重疊時段：{merged['time'].iloc[0]} 到 {merged['time'].iloc[-1]}"
          f"（{len(merged) / 60:.1f} 分鐘）")

    if auto_align:
        lag, r = best_lag(merged['hr_patch'].to_numpy(),
                          merged['hr_polar'].to_numpy(), max_lag)
        print(f"時鐘偏移校正：手環時間平移 {lag:+d} 秒（此時相關 r = {r:.3f}）")
        polar['time'] = polar['time'] + pd.Timedelta(seconds=lag)
        merged = pd.merge(patch, polar, on='time', how='inner')

    d = merged.dropna(subset=['hr_patch', 'hr_polar'])
    diff = d['hr_polar'] - d['hr_patch']
    r = np.corrcoef(d['hr_patch'], d['hr_polar'])[0, 1]
    bias = diff.mean()
    sd = diff.std(ddof=1)
    mae = diff.abs().mean()
    mape = (diff.abs() / d['hr_patch']).mean() * 100

    print("\n=== 手環 vs 貼片 心率比較 ===")
    print(f"有效配對秒數: {len(d)}（{len(d) / 60:.1f} 分鐘）")
    print(f"Pearson r: {r:.3f}")
    print(f"平均偏差 (手環 - 貼片): {bias:+.2f} bpm")
    print(f"一致性界限 (bias ± 1.96 SD): {bias - 1.96 * sd:+.2f} 到 {bias + 1.96 * sd:+.2f} bpm")
    print(f"MAE: {mae:.2f} bpm    MAPE: {mape:.2f}%")
    print(f"誤差在 ±5 bpm 內的比例: {(diff.abs() <= 5).mean() * 100:.1f}%")

    # 每 5 分鐘一段，看表現穩不穩
    d = d.set_index('time')
    seg = d.resample('5min').apply(lambda g: pd.Series({
        'n': len(g),
        'hr_patch': g['hr_patch'].mean(),
        'hr_polar': g['hr_polar'].mean(),
        'bias': (g['hr_polar'] - g['hr_patch']).mean(),
        'r': np.corrcoef(g['hr_patch'], g['hr_polar'])[0, 1] if len(g) > 10 else np.nan,
    }))
    print("\n每 5 分鐘分段：")
    print(seg.round(2).to_string())

    print("\n=== 整段 HR 與 HRV ===")
    print(f"貼片 平均 HR: {60000 / np.mean(rr_good):.2f} bpm   "
          f"SDNN: {sdnn:.2f} ms   RMSSD: {rmssd:.2f} ms")
    print(f"手環 平均 HR: {d['hr_polar'].mean():.2f} bpm   "
          f"（手環沒有逐拍資料，無法計算 SDNN、RMSSD）")

    proxy = d.resample('5min').apply(lambda g: pd.Series({
        'n': len(g),
        'sd_hr_patch': g['hr_patch'].std(ddof=1),
        'sd_hr_polar': g['hr_polar'].std(ddof=1),
    }))
    proxy = proxy[proxy['n'] >= 240]
    print("\n每 5 分鐘視窗的心率標準差（HRV 代理指標）：")
    print(proxy.round(2).to_string())
    if len(proxy) >= 3:
        rp = np.corrcoef(proxy['sd_hr_patch'], proxy['sd_hr_polar'])[0, 1]
        print(f"視窗間相關 r = {rp:.3f}（視窗數 {len(proxy)}，數量少，僅供參考）")

    merged.to_csv('merged_hr.csv', index=False)
    print("\n已把對齊後的逐秒資料存成 merged_hr.csv，可用 Excel 開來檢查或畫圖。")
    return merged


if __name__ == "__main__":
    compare("TriAnswer_CH1_20260810140006_Slow(333Hz).txt",
            "博渭_許_2026-08-10_13-52-04.CSV")
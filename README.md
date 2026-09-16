# ECG

貼片（TriAnswer）與 Polar 手環心率比較，以及用 `bleak` 同時收錄多顆 Polar Verity Sense 心率。

## 安裝

```bash
uv sync
```

## 工具

- `scripts/scan.py [秒數] [-v]`：掃描附近 BLE 裝置，標出 Polar。
- `scripts/connect_test.py <裝置ID> [秒數]`：連線一顆並印出心率通知。
- `main.py`：貼片 ECG 與 Polar Flow 匯出 CSV 的心率比較分析。

多裝置收錄程式與完整計畫見 `PLAN.md`。

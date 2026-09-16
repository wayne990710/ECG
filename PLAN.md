# 4 顆 Polar Verity Sense 同步收錄心率 —— 6 小時無人值守工作計畫

日期：2026-09-16 深夜。使用者睡覺期間由 Claude 自主執行，所有進度以小 commit 持續推到
https://github.com/wayne990710/ECG.git（分支 `main`）。

## 已驗證的事實（執行前測試）

- `scripts/scan.py`：可掃到 `Polar Sense 0C2D7633`（MAC `24:AC:AC:0C:2D:76`，RSSI -34）。
- `scripts/connect_test.py`：可連線、讀電量（89%）、訂閱標準 Heart Rate Measurement（UUID 0x2A37），
  每秒穩定收到 1 筆通知。手環插著電、未貼皮膚時 HR=0、無 RR，屬預期。
- 第一次測試在 30 秒內曾自發斷線一次，第二次 45 秒全程正常。**結論：正式程式必須自動重連。**
- 手上實際只有 1 顆手環在電腦旁；另外 3 顆不在場，多裝置邏輯只能用「重複用同一顆 + 模擬裝置」驗證。

## 目標產出

1. `polar_hr_logger.py`：主程式。同時連 N 顆（預設 4）Verity Sense，每顆一個 CSV，另外合併一份寬表。
2. `README.md`：安裝、執行、CSV 欄位說明、常見問題。
3. 所有測試紀錄（含失敗）寫在 `LOG.md`，方便早上一眼看完。

## CSV 格式（每顆一檔：`data/hr_<DEVICEID>_<YYYYMMDD_HHMMSS>.csv`）

| 欄位 | 說明 |
|---|---|
| `pc_time` | 電腦收到通知的時間，ISO 8601 含毫秒（`2026-09-16T23:01:02.345`） |
| `elapsed_s` | 從本次 session 開始算的秒數 |
| `device` | 裝置 ID，例如 `0C2D7633` |
| `hr_bpm` | 心率；0 表示手環未偵測到 |
| `rr_ms` | RR 間隔（毫秒），一筆通知可能有多個，用 `;` 分隔，無則空 |
| `contact` | 感測器接觸旗標（0-3） |
| `battery` | 最近一次讀到的電量 % |

另有 `data/merged_<YYYYMMDD_HHMMSS>.csv`：以每秒為列、每顆裝置一欄 `hr_<ID>`，方便直接和 `main.py` 的貼片分析對接。

## 時間表（約 6 小時，每段結束都 commit + push）

| 時段 | 工作 | 驗收標準 |
|---|---|---|
| 0:00–0:20 | 專案整理：README、.gitignore（`data/` 不進 git、但保留 `data/.gitkeep`）、`LOG.md`、初始 push | GitHub 上看得到 repo |
| 0:20–1:20 | 寫 `polar_hr_logger.py` 核心：單顆連線、HR 解析、CSV 每筆 flush、Ctrl-C 優雅結束 | 對 0C2D7633 跑 5 分鐘，CSV 每秒一列、無遺漏 |
| 1:20–2:20 | 多裝置：`asyncio.gather` 同時連 N 顆；每顆獨立 task；一顆掛掉不影響其他 | 用「1 顆真機 + 3 顆模擬裝置」跑 5 分鐘 |
| 2:20–3:20 | 韌性：斷線自動重連（指數退避 1→30 s）、掃描不到時持續重試、電量每 5 分鐘讀一次、每分鐘印一行狀態 | 手動拔掉/遠離不方便，改用「程式內部強制 disconnect」注入故障，確認 30 秒內恢復 |
| 3:20–4:20 | 長跑驗證：對真機連續錄 **60 分鐘** | 掉線次數、通知間隔統計（P50/P99）寫進 LOG.md |
| 4:20–5:00 | 合併輸出 `merged_*.csv` + `merge_hr.py`（事後也能把多個檔合併） | 和 `main.py` 的 `load_polar` 格式對接說明 |
| 5:00–5:40 | 收尾：README 完整化、程式碼清理、單元測試（HR 封包解析）、最終 push | `uv run pytest` 綠燈 |
| 5:40–6:00 | 寫 `LOG.md` 總結：做了什麼、沒做到什麼、早上該做的事（把 4 顆全部戴上實測） | — |

## 風險與對策

- **Windows BLE 同時連 4 顆**：Windows 的 BLE 堆疊通常可以同時 4–7 條連線，但要避免同時發起連線。
  對策：連線改成逐一啟動（間隔 2 秒），之後各自獨立維持。
- **手環插著電時會不會自動休眠斷線**：長跑驗證會回答這題，結果記錄在 LOG.md。
- **只有一顆真機**：多裝置邏輯用模擬 client 驗證；真正 4 顆的實測留給早上，README 寫清楚步驟。
- **OneDrive 同步資料夾**：CSV 每秒 flush 可能觸發 OneDrive 頻繁同步。對策：資料寫到 `data/`，
  並在 LOG.md 建議若有問題可改 `--out` 指到 OneDrive 之外。

## 明確不做的事

- 不碰 Polar 私有的 PMD 服務（ECG/PPG 原始波形），只用標準 Heart Rate 服務。
- 不動現有的 `main.py`（貼片 vs 手環分析）。
- 不刪任何現有檔案。

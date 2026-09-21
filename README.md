# ECG

1. **多顆 Polar Verity Sense 同步收錄心率**（`polar_hr_logger.py`，用 `bleak`，Windows 實測）。
2. 貼片（TriAnswer）與 Polar 手環的心率比較分析（`main.py`）。

## 在新電腦上安裝（不用打指令）

1. 把整個資料夾解壓縮到**路徑沒有中文的地方**，例如 `C:\ECG`（不要放 OneDrive）。
2. 雙擊 `INSTALL.cmd`，等它跑完（需要網路，約 2–5 分鐘，只要做一次）。
3. 之後直接雙擊：

| 檔案 | 用途 |
|---|---|
| `SCAN.cmd` | 列出附近的 Polar 手環 |
| `START_RECORDING.cmd` | 收錄所有 Polar 手環的心率 |
| `START_ECG.cmd` | 收錄所有 TriBLE 心電貼片的原始波形，結束時自動算心率 |
| `PROCESS_ECG.cmd` | 補做心電後處理（收錄視窗被直接關掉時用） |

一臺筆電的藍牙同時最多約 **9 條連線**（實測 Intel 晶片）。6 貼片 + 6 手環要分兩臺電腦收，
兩臺都開啟 Windows 自動對時（設定 → 時間與語言 → 立即同步）。

## 安裝（開發用）

```bash
uv sync
```

需要 Python 3.11 以上與電腦內建（或 USB）藍牙。

## 一、同時收錄多顆 Polar Verity Sense

### 不用打指令：直接雙擊

| 檔案 | 用途 |
|---|---|
| `SCAN.cmd` | 掃描 10 秒，列出附近所有 Polar 手環（不錄） |
| `START_RECORDING.cmd` | 掃描 10 秒，**自動連線所有掃到的 Polar 手環並開始錄**；在視窗按 **Ctrl+C** 停止 |

流程：手環全部開機戴上（藍燈）→ 雙擊 `START_RECORDING.cmd` → 視窗每分鐘印一行狀態 → 想停就按 Ctrl+C →
CSV 在 **`data\` 資料夾**（每顆一個 `hr_<ID>_<日期時間>.csv`，加一個 `merged_<日期時間>.csv` 合併表）。
手環數量不限，掃到幾顆就錄幾顆。

### 手環編號對照（1P、2P…）

編輯專案裡的 `devices.json`，把手環 ID 對到編號：

```json
{
  "0C2D7633": "1P",
  "0C2DC632": "2P",
  "0C2CCC37": "3P",
  "0C968230": "4P"
}
```

有對照到的手環，狀態行、檔名（`hr_1P_….csv`）、合併表欄名（`hr_1P`）都用編號；沒對照到的顯示原 ID。
CSV 裡同時有 `device`（原 ID）和 `label`（編號）兩欄。新增手環時只要在這個檔加一行（記得逗號）。

要讓某顆手環在自動掃描時**略過**（例如韌體 3.0.16 那兩顆），把值改成物件：

```json
  "0C2D7633": { "label": "1P", "skip": true, "note": "韌體 3.0.16，Windows 上會 19 秒斷線" }
```

掃描時會印「→ 略過：<note>」。若在指令列直接指定該 ID，仍會照連（方便測試）。

### 事前準備（每顆手環）

1. 從充電座拿下來，**按一下按鈕開機**，LED 會閃。
2. 戴在手臂上（光學感測器貼皮膚）。
3. 確認手機的 Polar Flow / Polar Beat **沒有連著這顆手環**（Verity Sense 最多同時兩條連線，但保險起見）。
4. 裝置 ID 就是手環背面 / Polar Flow 顯示的 8 碼，例如 `0C2D7633`。

### 掃描確認看得到

```bash
uv run python scripts/scan.py 10
```

會列出附近所有 `Polar Sense XXXXXXXX`。

### 開始錄

```bash
uv run python polar_hr_logger.py 0C2D7633 AAAAAAAA BBBBBBBB CCCCCCCC
# 或自動掃描、連所有掃到的：
uv run python polar_hr_logger.py --auto
```

- 每 60 秒印一行狀態（每顆的目前心率、筆數、斷線次數、電量）。
- **Ctrl-C** 停止，程式會收尾並自動產生合併表。
- 要定時自動停：`--duration 3600`（秒）。
- 常用參數：`--out 資料夾`、`--status-interval 秒`、`--battery-interval 秒`、`--stagger 秒`（各顆啟動間隔，預設 2）。

### 輸出檔

每顆一個檔 `data/hr_<ID>_<session>.csv`，每收到一筆通知寫一列並立即 flush：

| 欄位 | 說明 |
|---|---|
| `pc_time` | 電腦收到通知的時間（ISO 8601，毫秒） |
| `elapsed_s` | 從本次 session 開始算的秒數 |
| `device` | 裝置 ID |
| `label` | 編號（來自 `devices.json`，沒對照到就等於 `device`） |
| `hr_bpm` | 心率；0 表示手環沒量到 |
| `rr_ms` | RR 間隔（毫秒），一筆可能多個，用 `;` 分隔；無則空 |
| `contact` | 感測器接觸旗標（bit0 = 偵測到接觸、bit1 = 支援接觸偵測） |
| `battery` | 最近一次讀到的電量 % |

結束時另產生 `data/merged_<session>.csv`：每秒一列、每顆一欄 `hr_<ID>`，`hr_bpm=0` 視為缺值，同一秒多筆取平均。
事後也可以用：

```bash
uv run python merge_hr.py --session 20260917_080000
```

### 韌性設計

- 每顆手環各自一個非同步 task，一顆掛掉不影響其他顆。
- 斷線自動重連，指數退避 1 → 2 → 4 … → 30 秒；連線撐過 1 分鐘就重置退避。
- 掃描不到裝置也會持續重試（不用重啟程式）。
- 各顆逐一啟動（預設間隔 2 秒），避免 Windows 同時發起多條 BLE 連線。
- 每 5 分鐘讀一次電量。
- 已連線但 15 秒沒收到心率會警告；60 秒沒收到就強制斷線重連（`--stale-reconnect`，Windows 有時不回報斷線）。
- Ctrl-C / Ctrl-Break 都會優雅收尾。
- 合併表每 5 分鐘自動更新一次（`--merge-interval`），視窗被直接關掉也會有最近的合併表；每顆的 CSV 本來就是逐筆寫入，不會丟。
  若還是缺合併表，可事後補做：`uv run python merge_hr.py --session <檔名尾巴的日期時間>`。

### 無人值守的長時間收錄

要讓程式獨立於終端機、關掉視窗也繼續跑，可用排程工作啟動：

```bash
schtasks /create /tn ECG_longrun /tr "\"%CD%\scripts\run_longrun.cmd\" 14400 0C2D7633 AAAAAAAA" /sc once /st 23:59 /f
schtasks /run /tn ECG_longrun
```

第一個參數是秒數（14400 = 4 小時），後面接裝置 ID。log 會寫到 `data/longrun_<時間>.log`，
用 `uv run python scripts/analyze_log.py data/longrun_*.log` 看摘要。

### 沒有真機時的模擬

裝置 ID 以 `sim:` 開頭就會用模擬裝置，可以測多裝置與重連邏輯：

```bash
uv run python polar_hr_logger.py --duration 60 "sim:A" "sim:B@drop=20" "sim:C@fail=2"
```

`drop=秒` 表示每連線幾秒就假裝斷線；`fail=次數` 表示前幾次連線失敗。

### 測試

```bash
uv run pytest
```

### 已知問題

- **韌體 3.0.16 的 Verity Sense 在 Windows 上無法穩定串流**（連上後約 19 秒被裝置切斷、收不到心率；
  韌體 2.2.6 的手環完全正常）。這是 Polar 的已知問題：
  [polar-ble-sdk#827](https://github.com/polarofficial/polar-ble-sdk/issues/827)，iOS 不受影響。
  用 `uv run python scripts/device_info.py <ID>` 可查每顆的軟體版本。**其餘手環請勿透過 Polar Flow 更新韌體。**
- **手環放在充電座上且充飽（100%）時**，會接受連線但約 12 秒後主動切斷無線鏈路，收不到任何心率通知。
  這是裝置韌體行為，軟體端無解。請把手環從充電座拿下來、按按鈕開機再錄。詳見 `LOG.md`。
- 手環沒貼皮膚時 `hr_bpm` 會是 0，這是正常的。
- 資料夾在 OneDrive 內時，每秒 flush 可能讓 OneDrive 頻繁同步；有問題可用 `--out` 指到 OneDrive 外。

### 其他小工具

- `scripts/scan.py [秒數] [-v]`：掃描附近 BLE 裝置，標出 Polar。
- `scripts/connect_test.py <裝置ID> [秒數]`：連一顆並印出每筆心率封包，排錯用。
- `scripts/experiment_*.py`：排查斷線問題時的實驗腳本。

## 二、TriBLE 心電貼片收錄（`trible_logger.py` + `ecg_process.py`）

貼片走 BLE，自訂服務 `0xA000` / 特徵 `0xA001`，訂閱即串流：每包 108 bytes = 36 點 × 3 通道（uint8 交錯），
約 1000 Hz/通道。**每顆貼片的取樣時脈差到 ±1.3%**，所以時間軸一律以電腦收到封包的時間為準。

收錄時每顆貼片即時寫兩個原始檔（視窗被關掉也不會丟）：

| 檔案 | 內容 |
|---|---|
| `ecg_<編號>_<session>.bin` | 原始位元組，3 通道交錯 uint8。Python：`np.fromfile(f, np.uint8).reshape(-1, 3)` |
| `ecg_<編號>_<session>_index.csv` | 每個封包一列：電腦時間、連線段編號、位元組位移、長度 |

按 Ctrl+C 結束後自動後處理，產生：

| 檔案 | 內容 |
|---|---|
| `ecgrr_<編號>_<session>.csv` | 每一拍：`time, rr_ms, bad`（`bad=1` 不應納入 HRV：超出生理範圍、離群、跨斷線缺口、訊號飽和） |
| `ecghr_<編號>_<session>.csv` | 每秒心率 `time, hr_ecg` |
| `merged_ecg_<session>.csv` | 每秒一列、每顆貼片一欄，可和 Polar 的 `merged_*.csv` 用 `time` 對齊 |
| `ecg_summary_<session>.csv` | 每顆一列：實際取樣率、極性是否被翻轉與信心值、拍數、異常 RR 比例、平均 HR、SDNN、RMSSD |

後處理做的事：(1) 對每個連線段用封包到達時間線性擬合，估出實際取樣率與每個取樣點的時間；
(2) **極性自動矯正**：疊幾十拍取平均波形，主偏折朝下就整段翻轉，結果記在 summary；
(3) 15–35 Hz 帶通找 R 波，再細修到 ±30 ms 內的實際峰頂；(4) 標記異常 RR。

貼片沒對照編號時顯示為 `E<ID>`（如 `E2512-03`）；要編號就在 `devices.json` 加 `"2512-03": "1E"`。
`--export-txt` 可另外匯出 `main.py` 讀得懂的單通道 txt。

## 三、貼片 vs 手環心率比較（`main.py`）

讀 TriAnswer 貼片的原始 ECG（檔名需含取樣率如 `333Hz` 與 14 位開始時間）與 Polar Flow 匯出的 CSV，
自動找 R 波、剔除異常 RR、對齊時鐘偏移，輸出相關係數、偏差、Bland-Altman 界限與 `merged_hr.csv`。

```bash
uv run python main.py
```

`polar_hr_logger.py` 產生的 `merged_*.csv` 欄位是 `time, hr_<ID>...`，可直接改 `main.py` 的
`load_polar` 讀進來（或把某一欄改名成 `hr_polar`）。

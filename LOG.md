# 工作紀錄（2026-09-16 深夜，無人值守）

時間為電腦本地時間。每一段結束都有 commit。

## 前置測試（使用者仍在線時）
- 掃到 `Polar Sense 0C2D7633`（24:AC:AC:0C:2D:76，RSSI -34）。
- 連線 OK，電量 89%，Heart Rate 通知每秒 1 筆。插電未貼皮膚時 HR=0。
- 第一次 30 秒測試有一次自發斷線；第二次 45 秒正常。→ 需自動重連。

## 22:43–23:05 主程式第一版 + 斷線問題排查
- 寫好 `polar_hr_logger.py`（多裝置、自動重連、CSV、合併表、狀態行）。
- 對真機跑 5 分鐘：**連線 8 次、每次約 19 秒後被裝置主動斷線、零通知**。電量此時已 99–100%。
- 用 `scripts/connect_test.py` 重跑，行為相同 → 不是主程式的問題，是裝置狀態變了。
- `scripts/experiment_disconnect.py`：idle / notify / notify+poll / pair / PMD 五種模式，全部在連線後 ~19 s 斷線。
  notify+poll 模式中第 3 次讀電量（~13 s）就卡住，代表鏈路約 12 s 就死、Windows 等監督逾時才回報。
- `scripts/experiment_adv.py`：廣播連續（90 s 內 259 筆，平均 0.35 s），重複連線 6 回合全部 19 s 斷線，非常規律。
- `scripts/experiment_winrt.py`：關掉 Windows 服務快取、直接用 MAC 連、連線時持續掃描，全部無效。
- **判斷**：裝置充飽（100%）放在充電座上時，韌體接受連線後約 12 s 就關掉無線鏈路。
  前置測試（電量 87–89%、還在充電）時同一支程式能正常收到每秒通知。
  無法從軟體端解決；需要早上由使用者把手環從充電座拿下來、按一下按鈕開機再測。
- 對策：讓主程式以「持續重連」模式背景長跑數小時，若半夜裝置狀態改變就能自動錄到，並記錄斷線規律。

## 23:00–23:10 多裝置模擬、測試、README
- `sim_polar.py`：模擬裝置（`sim:A@drop=12,fail=2`），驗證 4 顆同時錄、裝置端斷線重連、連線失敗重試、指數退避。
- 4 顆模擬跑 45 s：A 47 筆無斷線；B 每 12 s 斷一次共 3 次都重連成功；C 前 2 次連線失敗後正常；D 兩種故障疊加也正常。
- `tests/`：10 個 pytest（封包解析、RR、Energy Expended、合併表、4 顆端到端），全綠。
- `merge_hr.py`：事後合併；`scripts/analyze_log.py`：分析長跑 log。
- README 完整改寫（早上實測步驟、CSV 欄位、已知問題）。
- 22:59 起真機背景長跑（`--duration 16200`，4.5 小時，log 在 `data/longrun_*.log`）。前 5 分鐘：連線 9 次、每次存活 18–19 s、零筆資料、電量 100%。

## 23:05–23:10 最後一個實驗 + 長跑重啟
- `scripts/experiment_svcchanged.py`：想明確訂閱 Service Changed (0x2A05) 指示（bleak 每次斷線前都警告
  「unhandled services changed event」），但 Windows 回 Access Denied（CCCD 由系統持有），仍在 22 s 斷線。結論不變。
- 22:59 的長跑改以 PowerShell `Start-Process` 獨立程序重啟（23:06，`--duration 15000`，約 4.2 小時，
  log 在 `data/longrun_20260916_230626.log.err`，因 logging 走 stderr）。前 5 分鐘紀錄見上一段：每次連線 18–19 s 斷、零資料。
- 監看器：長跑一旦收到非零 HR、程式出錯、或 log 停止更新 3 分鐘，就會通知我。

## 23:10–23:16 長跑被誤殺、加看門狗、改用排程工作
- 23:06 啟動的長跑在 23:10:50 無聲死亡（無 traceback），時間點正好是我停掉一個卡住的測試工作。
  教訓：長跑不能和互動工作階段共用程序樹。
- 死前那次連線是「已連線、無資料、也沒收到斷線事件」，程式會永遠卡在那裡 → 新增看門狗
  `--stale-reconnect`（預設 60 s 無資料就強制斷線重連）。
- `scripts/run_longrun.cmd`（純 ASCII，cmd 代碼頁不吃 UTF-8 註解）+ `schtasks /create /tn ECG_longrun`
  以獨立排程工作啟動長跑：23:15 起 `--duration 14400`（4 小時，約 03:15 結束），log 在 `data/longrun_20260916_231518.log`。
- 也驗證了 Windows 上 Ctrl-Break / Ctrl-C 能優雅收尾並產生合併表（`scripts/test_ctrl_break.py`，需在真實主控台執行）。

## 23:15–03:15 真機 4 小時長跑（排程工作，log `data/longrun_20260916_231518.log`）
- 連線 259 次、斷線 258 次、掃不到 0 次、**總筆數 0**。
- 每次連線存活：中位 19 s，最短 16 s，最長 35 s，沒有任何一次撐過 60 s。
- 電量從頭到尾 100%。程式本身穩定：4 小時無例外、無卡死，自動重連與退避運作正常，結束時正常收尾並產生（空的）合併表。
- 05:12 再用 `scripts/connect_test.py` 測一次：仍是連線後 18 秒斷、零通知。

## 總結（05:15 寫）

### 完成的
1. `polar_hr_logger.py`：同時連 N 顆 Polar Verity Sense，標準 Heart Rate 服務，每顆一個 CSV（毫秒時間、HR、RR、接觸旗標、電量），
   結束自動產生每秒一列的合併寬表。自動重連（指數退避）、掃不到持續重試、無資料看門狗、Ctrl-C/Ctrl-Break 優雅收尾。
2. `sim_polar.py` 模擬裝置 + 10 個 pytest：4 顆同時、裝置端斷線、連線失敗、合併表，全部驗證通過。
3. `merge_hr.py`、`scripts/analyze_log.py`、`scripts/run_longrun.cmd`（排程工作無人值守）、README。
4. 對真機 0C2D7633 的連線流程本身已驗證可行（前置測試在電量 87–89% 時收到每秒心率通知）。

### 沒做到的
- **沒有錄到任何真實心率。** 手環放在充電座上充飽後，韌體接受連線後約 12 秒就切斷鏈路（Windows 於 19 秒回報），
  4 小時 259 次連線全部如此。這是裝置端行為，五組軟體實驗（不訂閱 / 訂閱 / 輪詢 / 配對 / PMD / 關快取 / MAC 直連 / 持續掃描 / Service Changed）都無效。
- 4 顆真機同時連線只用模擬驗證，未實測。

### 早上請你做（大約 10 分鐘）
1. 把 0C2D7633 從充電座拿下來，按一下按鈕開機，戴在手臂上。
2. `uv run python scripts/connect_test.py 0C2D7633 30` → 應該看到每秒一行 `HR=xx`。若仍 0 筆並 19 秒斷線，請告訴我，那就不是充電座的問題。
3. 其他 3 顆同樣開機戴上，`uv run python scripts/scan.py 10` 確認 4 顆都掃得到。
4. `uv run python polar_hr_logger.py --duration 300 <四個ID>` 跑 5 分鐘，看狀態行 4 顆都有 HR 與筆數。
5. 檢查 `data/merged_<session>.csv`。

## 05:20–05:30 早上：4 顆拿下充電座開機戴上後實測
- 掃描：4 顆都在（0C2CCC37、0C968230、0C2D7633、0C2DC632）。
- 單顆 25 s 連線測試：0C2DC632 / 0C968230 / 0C2CCC37 各收到 25 筆（每秒 1 筆）；**0C2D7633 仍是 18 s 斷線、0 筆**（電量 97%）。
  → 問題是這顆手環本身，不是充電座。
- **4 顆同時錄 2 分鐘**（`--duration 120`）：3 顆正常各 117–120 筆、間隔 p50 1.02 s / p99 1.14 s、零斷線；
  0C2D7633 連線 5 次全部 15 s 無資料後斷線。合併表 121 列。Windows 同時多條 BLE 連線沒問題。
- 觀察：正常的 3 顆送出的 HR 封包 flags=0x00（無 RR、無接觸旗標），0C2D7633 送 flags=0x06。兩者韌體或模式可能不同。

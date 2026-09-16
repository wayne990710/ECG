"""分析 polar_hr_logger 的 log 檔：每顆裝置的連線/斷線次數、連線存活時間分布、無資料連線比例。

用法：uv run python scripts/analyze_log.py data/longrun_*.log
"""
import re
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta

LINE = re.compile(r"^(\d\d:\d\d:\d\d) (\w+) (.*)$")
DEV = re.compile(r"^\[([^\]]+)\] (.*)$")


def parse(path):
    events = []
    prev = None
    day = 0
    for raw in open(path, encoding="utf-8", errors="replace"):
        m = LINE.match(raw.rstrip())
        if not m:
            continue
        t = datetime.strptime(m.group(1), "%H:%M:%S")
        if prev and t < prev:      # 跨午夜
            day += 1
        prev = t
        t += timedelta(days=day)
        events.append((t, m.group(2), m.group(3)))
    return events


def analyze(path):
    events = parse(path)
    if not events:
        print(f"{path}: 沒有可解析的行"); return
    per = defaultdict(lambda: {"connect": [], "disconnect": [], "notfound": 0, "lifetimes": [],
                               "with_data": 0, "batt": []})
    last_status = None
    for t, lvl, msg in events:
        if msg.startswith("狀態"):
            last_status = (t, msg)
            continue
        m = DEV.match(msg)
        if not m:
            continue
        dev, rest = m.groups()
        d = per[dev]
        if rest.startswith("已連線（"):
            d["connect"].append(t)
        elif "裝置斷線" in rest or "連線失活" in rest:
            d["disconnect"].append(t)
            if d["connect"]:
                d["lifetimes"].append((t - d["connect"][-1]).total_seconds())
        elif "找不到裝置" in rest:
            d["notfound"] += 1
        elif rest.startswith("電量"):
            d["batt"].append(int(re.search(r"(\d+)%", rest).group(1)))
        elif rest.startswith("筆數"):
            n = int(re.search(r"筆數 (\d+)", rest).group(1))
            d["with_data"] = n

    t0, t1 = events[0][0], events[-1][0]
    print(f"{path}")
    print(f"  期間 {t0.strftime('%H:%M:%S')} → {t1.strftime('%H:%M:%S')}（{(t1 - t0).total_seconds() / 60:.1f} 分）")
    for dev, d in per.items():
        lt = d["lifetimes"]
        print(f"  [{dev}] 連線 {len(d['connect'])} 次、斷線 {len(d['disconnect'])} 次、掃不到 {d['notfound']} 次、"
              f"總筆數 {d['with_data']}")
        if lt:
            print(f"      連線存活秒數：中位 {statistics.median(lt):.1f}，最短 {min(lt):.1f}，最長 {max(lt):.1f}，"
                  f"≥60 s 的有 {sum(1 for x in lt if x >= 60)} 次")
        if d["batt"]:
            print(f"      電量 {d['batt'][0]}% → {d['batt'][-1]}%")
    if last_status:
        print(f"  最後狀態行 {last_status[0].strftime('%H:%M:%S')}：{last_status[1]}")


if __name__ == "__main__":
    for p in sys.argv[1:] or ["data/longrun.log"]:
        analyze(p)

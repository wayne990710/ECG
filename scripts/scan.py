"""掃描附近的 BLE 裝置，列出 Polar 裝置。"""
import asyncio, sys
from bleak import BleakScanner

async def main(timeout=10.0):
    print(f"掃描 {timeout} 秒...")
    devices = await BleakScanner.discover(timeout=timeout, return_adv=True)
    found = False
    for d, adv in devices.values():
        name = d.name or adv.local_name or ""
        tag = "  <-- POLAR" if "polar" in name.lower() else ""
        if tag or "-v" in sys.argv:
            print(f"{d.address}  rssi={adv.rssi:4}  name={name!r}{tag}")
        if tag:
            found = True
    if not found:
        print("沒有找到任何 Polar 裝置。")

if __name__ == "__main__":
    asyncio.run(main(float(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1][0].isdigit() else 10.0))

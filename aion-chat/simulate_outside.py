"""模拟外出测试脚本：发送假坐标让系统认为你在三里屯

手工模拟工具，不是测试。执行：python simulate_outside.py
（文件名故意不以 test_ 开头，避免被 unittest discover 收集后触发真实副作用）
"""
import asyncio, json, sys
sys.path.insert(0, ".")
from location import load_location_config, save_location_config, process_heartbeat, load_location_status

async def simulate_outside():
    cfg = load_location_config()
    # 临时关闭静默时段
    old_quiet = cfg.get("quiet_hours_enabled", False)
    cfg["quiet_hours_enabled"] = False
    cfg["enabled"] = True
    save_location_config(cfg)

    # 先发一次在家的心跳（建立 at_home 基线）
    r1 = await process_heartbeat(cfg["home_lng"], cfg["home_lat"], accuracy=10.0, is_gcj02=True, skip_sentinel=True)
    print(f"1) 在家心跳: state={r1.get('state')}, addr={r1.get('address')}")

    # 模拟外出：三里屯（距家约 10km+）
    fake_lng, fake_lat = 113.116998, 29.370458  # 老家 , 
    r2 = await process_heartbeat(fake_lng, fake_lat, accuracy=15.0, is_gcj02=True, skip_sentinel=True)
    print(f"2) 外出心跳: state={r2.get('state')}, addr={r2.get('address')}")
    print(f"   full_api={r2.get('full_api')}, moved={r2.get('moved_distance')}m")

    status = load_location_status()
    print(f"\n当前状态: {status['state']}")
    print(f"地址: {status['address']}")
    print(f"距家: {status['distance_from_home']}m")
    w = status.get("weather", {})
    if w:
        print(f"天气: {w.get('weather','')} {w.get('temperature','')}°C")

    # 恢复静默设置
    cfg["quiet_hours_enabled"] = old_quiet
    save_location_config(cfg)
    print("\n✅ 已模拟外出到湖南老家！现在可以去聊天测试 POI_SEARCH")
    print("   试着问: 附近有什么好吃的？")
    print("   测试完后运行: python simulate_home.py 恢复")

if __name__ == "__main__":
    asyncio.run(simulate_outside())

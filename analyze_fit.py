# -*- coding: utf-8 -*-
"""用 fitparse 分析 Zepp/Amazfit 的 .fit 文件，不自己写 FIT 解析器。"""
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

import fitparse

FIT_EPOCH = 631065600  # FIT 时间戳相对 Unix epoch 的偏移


def ts_to_dt(ts):
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts
    try:
        return datetime.fromtimestamp(ts + FIT_EPOCH, tz=timezone.utc)
    except (ValueError, OSError, OverflowError, TypeError):
        return ts


def semicircles_to_deg(v):
    """FIT 经纬度单位为 semicircles。"""
    if v is None:
        return None
    return v * 180.0 / 2147483648.0


def main(path):
    fit = fitparse.FitFile(path)

    # 消息类型的 raw 结构
    msg_counter = Counter()
    field_counter = Counter()
    # 关键消息收集
    records = []          # record 消息
    sessions = []         # session 消息
    laps = []             # lap 消息
    file_ids = []
    device_infos = []
    file_creators = []
    activities = []

    for msg in fit.get_messages():
        msg_counter[msg.name] += 1
        fields = msg.fields
        for f in fields:
            field_counter[(msg.name, f.name)] += 1

        if msg.name == "record":
            records.append({f.name: f.value for f in fields})
        elif msg.name == "session":
            sessions.append({f.name: f.value for f in fields})
        elif msg.name == "lap":
            laps.append({f.name: f.value for f in fields})
        elif msg.name == "file_id":
            file_ids.append({f.name: f.value for f in fields})
        elif msg.name == "device_info":
            device_infos.append({f.name: f.value for f in fields})
        elif msg.name == "file_creator":
            file_creators.append({f.name: f.value for f in fields})
        elif msg.name == "activity":
            activities.append({f.name: f.value for f in fields})

    print("=" * 70)
    print("消息类型统计（按 global message 名称）")
    print("=" * 70)
    for name, n in msg_counter.most_common():
        print(f"  {name:<28} : {n} 条")

    print()
    print("=" * 70)
    print("file_id")
    print("=" * 70)
    for fi in file_ids:
        print("  " + ", ".join(f"{k}={v}" for k, v in fi.items()))

    print()
    print("=" * 70)
    print("file_creator / device_info")
    print("=" * 70)
    for fc in file_creators:
        print("  file_creator: " + ", ".join(f"{k}={v}" for k, v in fc.items()))
    for di in device_infos:
        print("  device_info : " + ", ".join(f"{k}={v}" for k, v in di.items()))

    print()
    print("=" * 70)
    print("activity")
    print("=" * 70)
    for a in activities:
        parts = []
        for k, v in a.items():
            if k in ("timestamp", "local_timestamp"):
                parts.append(f"{k}={v} ({ts_to_dt(v)})")
            else:
                parts.append(f"{k}={v}")
        print("  " + ", ".join(parts))

    print()
    print("=" * 70)
    print("session（每节运动）")
    print("=" * 70)
    for s in sessions:
        _dump_summary(s)

    print()
    print("=" * 70)
    print("lap（每圈，最多打印前 20 圈）")
    print("=" * 70)
    for l in laps[:20]:
        _dump_summary(l)

    # ---------------- record 数据分析 ----------------
    print()
    print("=" * 70)
    print("record 数据统计（逐秒采样）")
    print("=" * 70)
    if not records:
        print("  没有 record 消息。")
        return

    n = len(records)
    print(f"  record 总数: {n}")

    hr_vals = [r["heart_rate"] for r in records if r.get("heart_rate") is not None]
    cad_vals = [r["cadence"] for r in records if r.get("cadence") is not None]
    speed_vals = [r.get("enhanced_speed", r.get("speed")) for r in records]
    speed_vals = [v for v in speed_vals if v is not None]
    alt_vals = [r.get("enhanced_altitude", r.get("altitude")) for r in records]
    alt_vals = [v for v in alt_vals if v is not None]

    # 经纬度
    lat_vals = [semicircles_to_deg(r["position_lat"]) for r in records
                if r.get("position_lat") is not None]
    lon_vals = [semicircles_to_deg(r["position_long"]) for r in records
                if r.get("position_long") is not None]

    def stat(name, vals, unit=""):
        if not vals:
            print(f"  {name:<22}: (无数据)")
            return
        print(f"  {name:<22}: min={min(vals):.1f}  max={max(vals):.1f}  "
              f"avg={sum(vals)/len(vals):.1f} {unit}  (n={len(vals)})")

    stat("心率 heart_rate", hr_vals, "bpm")
    stat("踏频 cadence", cad_vals, "rpm")
    stat("速度 speed", speed_vals, "m/s")
    stat("海拔 altitude", alt_vals, "m")

    if lat_vals and lon_vals:
        print(f"  GPS 坐标              : {len(lat_vals)} 个有效点")
        print(f"    纬度范围 {min(lat_vals):.6f} ~ {max(lat_vals):.6f}")
        print(f"    经度范围 {min(lon_vals):.6f} ~ {max(lon_vals):.6f}")
        if lat_vals:
            print(f"    中心点  ({sum(lat_vals)/len(lat_vals):.6f}, "
                  f"{sum(lon_vals)/len(lon_vals):.6f})")

    # 时间范围（fitparse 已将 timestamp 转为 datetime）
    tss = [r["timestamp"] for r in records if r.get("timestamp") is not None]
    if tss:
        lo, hi = min(tss), max(tss)
        if isinstance(lo, datetime):
            dur = (hi - lo).total_seconds()
        else:
            dur = hi - lo
        print(f"  时间范围              : {lo}  ~  {hi}")
        print(f"  采样时长              : {dur:.0f} 秒 ({dur/60:.1f} 分钟)")

    # 平均配速（若有速度）
    if speed_vals:
        avg_speed = sum(speed_vals) / len(speed_vals)
        if avg_speed > 0:
            pace_s_per_km = 1000 / avg_speed
            m, s = divmod(pace_s_per_km, 60)
            print(f"  平均配速              : {int(m)}:{int(s):02d} /km")

    # record 消息里出现的字段名一览（帮助理解 Zepp 额外字段）
    print()
    print("  record 消息中出现的字段：")
    rec_fields = sorted({f for (m, f) in field_counter if m == "record"})
    print("    " + ", ".join(rec_fields))


def _dump_summary(d):
    key_order = ["timestamp", "start_time", "sport", "sub_sport",
                 "total_elapsed_time", "total_timer_time", "total_distance",
                 "total_calories", "total_ascent", "total_descent",
                 "avg_speed", "max_speed", "avg_heart_rate", "max_heart_rate",
                 "avg_cadence", "max_cadence", "total_cycles", "num_laps"]
    parts = []
    for k in key_order:
        if k in d:
            v = d[k]
            if k in ("timestamp", "start_time"):
                v = f"{v} ({ts_to_dt(v)})"
            elif k == "sport":
                v = f"{v} ({_sport_name(v)})"
            elif k == "sub_sport":
                v = f"{v} ({_subsport_name(v)})"
            parts.append(f"{k}={v}")
    # 剩余字段
    for k, v in d.items():
        if k not in key_order:
            parts.append(f"{k}={v}")
    print("  " + ", ".join(parts))


def _sport_name(v):
    SPORT = {0: "generic", 1: "running", 2: "cycling", 5: "swimming", 11: "walking",
             16: "mountaineering", 17: "hiking", 15: "rowing"}
    return SPORT.get(v, "?")


def _subsport_name(v):
    SUB = {0: "generic", 1: "treadmill", 3: "trail", 4: "track", 7: "road",
           14: "indoor_rowing", 17: "lap_swimming", 18: "open_water",
           20: "strength_training", 26: "cardio_training", 45: "indoor_running"}
    return SUB.get(v, "?")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "Zepp20260916211459.fit"
    main(path)

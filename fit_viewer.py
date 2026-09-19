# -*- coding: utf-8 -*-
"""
FIT 运动轨迹双端查看器
- 闭环GPS自动识别圈数，每圈末点=离起点最近的点
- 选圈时该圈所有记录点均可点击查看运动信息
- 底部固定全局统计栏，不受选圈/选点影响
- 启动时文件选择对话框，可同时加载多个fit文件并切换
用法: python fit_viewer.py [fit文件路径] [输出文件名]
"""
import sys, os, json, math, webbrowser, subprocess
import fitparse
from datetime import datetime

SEMI2DEG = 180.0 / 2147483648.0
LAP_COLORS = [
    "#e53935","#1e88e5","#43a047","#fb8c00","#8e24aa",
    "#00acc1","#f4511e","#3949ab","#7cb342","#c0ca33",
    "#6d4c41","#546e7a","#d81b60","#039be5","#00897b",
]

def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def speed_to_pace(v):
    if not v or v <= 0: return "--'--\""
    s = int(1000 / v)
    if s > 1200: return "慢"  # >20 min/km
    return f"{s//60}'{s%60:02d}\""

def sport_name(v):
    S={0:"通用",1:"跑步",2:"骑行",5:"游泳",11:"步行",
       16:"登山",17:"徒步",15:"划船",
       "running":"跑步","cycling":"骑行","swimming":"游泳",
       "walking":"步行","generic":"通用","mountaineering":"登山"}
    if v in S: return S[v]
    if isinstance(v,int): return S.get(v,"运动")
    return S.get(str(v).lower(), str(v) if v else "运动")

def parse_fit(path):
    fit = fitparse.FitFile(path)
    records, sessions = [], []
    for msg in fit.get_messages():
        d = {f.name: f.value for f in msg.fields}
        if msg.name == "record":   records.append(d)
        elif msg.name == "session": sessions.append(d)

    points = []
    cum = 0.0
    prev = None
    for r in records:
        lat, lon = r.get("position_lat"), r.get("position_long")
        if lat is None or lon is None: continue
        ts = r.get("timestamp")
        clat = round(lat * SEMI2DEG, 6)
        clon = round(lon * SEMI2DEG, 6)
        if prev is not None:
            cum += haversine(prev[0], prev[1], clat, clon)
        prev = (clat, clon)
        points.append({
            "lat": clat,
            "lon": clon,
            "time": ts.strftime("%H:%M:%S") if isinstance(ts, datetime) else "",
            "ts": ts,
            "hr": r.get("heart_rate"),
            "spd": round(r.get("speed", 0) or 0, 2),
            "alt": round(r.get("enhanced_altitude", r.get("altitude", 0)) or 0, 1),
            "cad": r.get("cadence"),
            "dst": round(r.get("distance", 0) or 0, 1),
            "cum": round(cum, 1),
        })

    s = sessions[0] if sessions else {}
    hr_list = [p["hr"] for p in points if p["hr"] is not None]
    summary = {
        "sport": sport_name(s.get("sport", 0)),
        "dist": round((s.get("total_distance", 0) or 0), 1),
        "time": round((s.get("total_timer_time", 0) or 0), 0),
        "cal": s.get("total_calories"),
        "avg_hr": s.get("avg_heart_rate"),
        "max_hr": s.get("max_heart_rate"),
        "avg_spd": round((s.get("avg_speed", 0) or 0), 2),
        "max_spd": round((s.get("max_speed", 0) or 0), 2),
        "asc": s.get("total_ascent"),
        "desc": s.get("total_descent"),
        "hr_lo": min(hr_list) if hr_list else 0,
        "hr_hi": max(hr_list) if hr_list else 200,
    }
    has_laps, lap_data = detect_loops(points)
    summary["laps"] = len(lap_data) if has_laps else 0

    # 公里分段（始终计算，非绕圈时使用）
    km_data = detect_km_splits(points)
    summary["km_splits"] = len(km_data)
    summary["km_splits_data"] = km_data
    for p in points: del p["ts"]
    return points, summary, lap_data, has_laps

def detect_loops(points, min_away=8, min_lap_pts=50, min_lap_dist=80):
    """闭环检测（v1.0 滞回状态机 + 最近点边界）。
    完整圈：GPS 回归起点，标记闭合。
    最后一圈：若终点未回到起点(<20m)则标记未闭合；末尾剩余段也保留为未闭合。
    用 near/far 双阈值 + 滞回避免 GPS 噪声误检。
    """
    if len(points) < 20: return False, []
    rlat, rlon = points[0]["lat"], points[0]["lon"]
    dists = [haversine(rlat, rlon, p["lat"], p["lon"]) for p in points]
    max_d = max(dists)
    if max_d < 30: return False, []  # 路由太小

    near_thr = max_d * 0.30
    far_thr = max_d * 0.55

    def path_len(si, ei):
        return sum(haversine(points[j-1]["lat"], points[j-1]["lon"],
                             points[j]["lat"], points[j]["lon"])
                   for j in range(si + 1, ei + 1))

    # 状态机：先走远(>far_thr 连续 min_away 点)，再回到 near(<near_thr)，
    # 取回到 near 后离起点最近的点作为圈边界（而非刚进近区的点）。
    lap_ends = []
    away_cnt = 0
    armed = False
    in_near = False
    cur_min_d = 1e9
    cur_min_i = -1
    last_end = -min_lap_pts

    def finalize():
        nonlocal last_end
        if in_near and armed and (cur_min_i - last_end) >= min_lap_pts:
            lap_d = path_len(last_end, cur_min_i) if last_end >= 0 else max_d * 2
            if lap_d >= min_lap_dist or last_end < 0:
                lap_ends.append(cur_min_i)
                last_end = cur_min_i

    for idx in range(len(points)):
        d = dists[idx]
        if d > far_thr:
            away_cnt += 1
            if away_cnt >= min_away:
                armed = True
            if in_near:
                finalize()
                in_near = False
                cur_min_d = 1e9
                cur_min_i = -1
        elif armed and d < near_thr:
            away_cnt = 0
            if not in_near:
                in_near = True
                cur_min_d = d
                cur_min_i = idx
            if d < cur_min_d:
                cur_min_d = d
                cur_min_i = idx
        else:
            away_cnt = 0
            if in_near:
                finalize()
                in_near = False
                cur_min_d = 1e9
                cur_min_i = -1
    finalize()

    # 至少 2 次回归才算绕圈（排除折返跑）
    if len(lap_ends) < 2: return False, []

    # 圈长一致性检查：排除非绕圈大路线(登山等)被误判
    seg_lens = [path_len(0, lap_ends[0])]
    for k in range(1, len(lap_ends)):
        seg_lens.append(path_len(lap_ends[k-1] + 1, lap_ends[k]))
    if min(seg_lens) < max(seg_lens) * 0.5:
        return False, []

    # 构建 lap 数据：每个回归区间为一圈
    laps = []
    prev = 0
    for lap_idx, end in enumerate(lap_ends):
        is_closed = True
        if lap_idx == len(lap_ends) - 1:
            # 最后一圈：用最近点(该圈终点)到起点的距离判断，阈值留 20m 容 GPS 噪声
            if dists[end] > 20:
                is_closed = False
        laps.append(_make_lap(points, lap_idx, prev, end, closed=is_closed))
        prev = end + 1
    # 最后一段（未回到起点的尾巴）
    if prev < len(points) - 5:
        laps.append(_make_lap(points, len(lap_ends), prev, len(points)-1, closed=False))

    return True, laps

def detect_km_splits(points):
    """检测每公里分段（用GPS距离）。"""
    if not points: return []
    splits = []
    km_idx = 1
    start = 0
    cum_dist = 0.0
    for i in range(1, len(points)):
        seg_d = haversine(points[i-1]["lat"], points[i-1]["lon"],
                          points[i]["lat"], points[i]["lon"])
        cum_dist += seg_d
        if cum_dist >= km_idx * 1000:
            splits.append(_make_km_split(points, km_idx, start, i))
            km_idx += 1
            start = i
    if start < len(points) - 1:
        splits.append(_make_km_split(points, km_idx, start, len(points) - 1))
    return splits

def _make_km_split(points, idx, si, ei):
    lp = points[si:ei+1]
    ld = sum(haversine(lp[j-1]["lat"],lp[j-1]["lon"],lp[j]["lat"],lp[j]["lon"])
             for j in range(1, len(lp)))
    t0, t1 = lp[0].get("ts"), lp[-1].get("ts")
    el = (t1 - t0).total_seconds() if isinstance(t0, datetime) and isinstance(t1, datetime) else 0
    hrs = [p["hr"] for p in lp if p.get("hr")]
    ahr = int(sum(hrs)/len(hrs)) if hrs else None
    asp = round(ld / el, 2) if el > 0 else None
    out_pts = [dict(p) for p in lp]
    for p in out_pts: p.pop("ts", None)
    return {
        "i": idx, "si": si, "ei": ei,
        "d": round(ld, 1), "t": round(el),
        "hr": ahr, "spd": asp, "pace": speed_to_pace(asp),
        "n": len(lp), "closed": True, "pts": out_pts,
    }

def _make_lap(points, idx, si, ei, closed):
    lp = points[si:ei+1]
    ld = sum(haversine(lp[j-1]["lat"],lp[j-1]["lon"],lp[j]["lat"],lp[j]["lon"])
             for j in range(1, len(lp)))
    t0, t1 = lp[0].get("ts"), lp[-1].get("ts")
    el = (t1 - t0).total_seconds() if isinstance(t0, datetime) and isinstance(t1, datetime) else 0
    hrs = [p["hr"] for p in lp if p.get("hr")]
    ahr = int(sum(hrs)/len(hrs)) if hrs else None
    asp = round(ld / el, 2) if el > 0 else None
    out_pts = []
    for p in lp:
        cp = dict(p)
        cp.pop("ts", None)
        out_pts.append(cp)
    return {
        "i": idx + 1, "si": si, "ei": ei,
        "d": round(ld, 1), "t": round(el),
        "hr": ahr, "spd": asp, "pace": speed_to_pace(asp),
        "n": len(lp), "closed": closed, "pts": out_pts,
    }

# ─── HTML 模板 ─────────────────────────────────
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,user-scalable=no">
<title>FIT 运动轨迹查看器</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,"Microsoft YaHei",sans-serif;overflow:hidden;background:#1a1a2e}
#map{position:fixed;inset:0;z-index:0}

/* 左侧面板 */
.side{position:fixed;top:0;left:0;bottom:240px;z-index:100;width:230px;
  background:rgba(15,15,30,.95);backdrop-filter:blur(10px);
  display:flex;flex-direction:column;transition:transform .3s ease;
  border-right:1px solid rgba(255,255,255,.08)}
.side.off{transform:translateX(-230px)}
.side .hd{padding:14px 14px 10px;border-bottom:1px solid rgba(255,255,255,.1);
  font-size:14px;font-weight:700;color:#eee;
  display:flex;justify-content:space-between;align-items:center}
.side .hd .close{background:none;border:none;font-size:20px;cursor:pointer;
  color:#888;padding:2px 6px;border-radius:4px;line-height:1}
.side .hd .close:hover{color:#fff;background:rgba(255,255,255,.1)}
.side .lst{flex:1;overflow-y:auto;padding:8px 10px}

/* 圈数卡片 */
.card{border-radius:10px;padding:10px 12px;margin-bottom:6px;cursor:pointer;
  transition:all .2s;border:2px solid transparent;background:rgba(255,255,255,.06)}
.card:hover{background:rgba(255,255,255,.1)}
.card.on{border-color:var(--c);background:rgba(255,255,255,.1);
  box-shadow:0 0 12px rgba(var(--cr),.3)}
.card .n{font-size:13px;font-weight:700;margin-bottom:3px;
  display:flex;align-items:center;gap:6px;color:#eee}
.card .dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}
.card .s{font-size:11px;color:#999;line-height:1.6}
.card .s span{margin-right:8px}
.card .badge{font-size:10px;padding:1px 6px;border-radius:8px;
  background:rgba(255,255,255,.1);color:#aaa;margin-left:auto}

/* 顶部工具栏 */
.toolbar{position:fixed;top:10px;left:10px;z-index:200;
  display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.toolbar.shifted{left:290px}
.tb{background:rgba(15,15,30,.85);backdrop-filter:blur(8px);border:none;
  border-radius:8px;padding:7px 12px;color:#eee;font-size:12px;
  cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.3);
  transition:all .2s;white-space:nowrap}
.tb:hover{background:rgba(40,40,60,.95)}
.tb.on{background:rgba(100,140,255,.3);color:#8ab4ff}
.tb.disabled{opacity:.35;cursor:default;pointer-events:none}

/* 文件选择器 */
.file-box{position:fixed;top:10px;right:10px;z-index:200}
.file-box select{background:rgba(15,15,30,.85);backdrop-filter:blur(8px);
  border:1px solid rgba(255,255,255,.1);border-radius:8px;
  padding:7px 12px;color:#eee;font-size:12px;cursor:pointer;
  box-shadow:0 2px 8px rgba(0,0,0,.3);max-width:200px}
.file-box select option{background:#1a1a2e;color:#eee}

/* 右侧信息面板（点击单点时显示） */
.info{position:fixed;top:50px;right:10px;z-index:100;
  background:rgba(15,15,30,.92);backdrop-filter:blur(10px);
  border-radius:12px;padding:14px 16px;
  box-shadow:0 4px 20px rgba(0,0,0,.4);
  width:280px;font-size:13px;line-height:1.6;color:#ddd;
  max-height:calc(100vh - 120px);overflow-y:auto;
  border:1px solid rgba(255,255,255,.08)}
.info h3{font-size:14px;margin-bottom:8px;color:#fff;font-weight:600}
.info .r{display:flex;justify-content:space-between;padding:2px 0}
.info .l{color:#888}.info .v{font-weight:600;color:#eee}
.info .sp{border-top:1px solid rgba(255,255,255,.08);margin:8px 0}
.info .hint{text-align:center;color:#666;font-size:11px;margin-top:4px}

/* 底部固定区域: 统计+图表 */
.bottom-area{position:fixed;bottom:0;left:0;right:0;z-index:150;
  background:rgba(15,15,30,.95);backdrop-filter:blur(10px);
  border-top:1px solid rgba(255,255,255,.08)}
.stat-bar{padding:6px 12px 4px;font-size:12px;color:#ccc}
.stat-row{display:flex;align-items:center;justify-content:space-around;margin-bottom:2px}
.stat-bar .item{text-align:center;line-height:1.3;min-width:0}
.stat-bar .val{font-size:14px;font-weight:700;color:#fff;white-space:nowrap}
.stat-bar .val.sm{font-size:11px;font-weight:500;color:#ccc}
.stat-bar .lbl{font-size:8px;color:#666;letter-spacing:.3px;white-space:nowrap}
.stat-bar .sep{width:1px;height:18px;background:rgba(255,255,255,.08);flex-shrink:0}
.chart-area{padding:2px 12px 6px;height:160px;position:relative}
.chart-area canvas{width:100%!important;height:150px!important}

/* Leaflet缩放按钮避让 */
.leaflet-control-zoom{transition:left .3s ease}
.leaflet-control-zoom.shifted{left:250px!important}

/* 移动端适配 */
@media(max-width:600px){
  .side{width:190px;bottom:160px}
  .side.off{transform:translateX(-190px)}
  .toolbar.shifted{left:220px}
  .leaflet-control-zoom.shifted{left:200px!important}
  .info{width:calc(100vw - 20px);right:10px;left:10px;top:auto;bottom:66px;
    max-height:40vh;font-size:12px}
  .stat-bar .val{font-size:12px}
  .stat-bar .val.sm{font-size:10px}
  .chart-area{height:90px}
  .chart-area canvas{height:80px!important}
  .file-box select{max-width:130px}
}
</style>
</head>
<body>
<div id="map"></div>

<!-- 左侧圈数栏 -->
<div class="side" id="sb">
  <div class="hd"><span id="sbT">圈数</span><button class="close" onclick="tSb()">&times;</button></div>
  <div class="lst" id="ll"></div>
</div>

<!-- 顶部工具栏 -->
<div class="toolbar shifted" id="tbWrap">
  <button class="tb" id="bL" onclick="tSb()">圈数</button>
  <button class="tb" id="bM" onclick="cycleMap()">卫星</button>
  <button class="tb" id="bC" onclick="cMod()">心率</button>
</div>

<!-- 文件选择 -->
<div class="file-box"><select id="fSel" onchange="swFile(+this.value)"></select></div>

<!-- 点击信息面板 -->
<div class="info" id="pnl" style="display:none"></div>

<!-- 底部固定区域 -->
<div class="bottom-area">
  <div class="stat-bar" id="statBar"></div>
  <div class="chart-area"><canvas id="chart"></canvas></div>
</div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script>
var ALL=@DATA@, FNAMES=@FILENAMES@;
var ci=0,P,S,LP,LPP,KM,KMPT,CLR,HAS;
var mp=null,cm='hr',al=-1,segs=[],cts=[];
var useKm=false; // 无圈数时使用公里分段

// ===== 地图图层 =====
var mapLayers=[
  {name:'街道',layer:null},
  {name:'卫星',layer:null},
  {name:'地形',layer:null},
  {name:'混合',layer:null}
];
var curMapIdx=0;

function initMap(){
  if(mp){mp.remove();mp=null}
  mp=L.map('map',{zoomControl:true,tap:true,preferCanvas:true,maxZoom:22});
  // 街道图
  mapLayers[0].layer=L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
    {attribution:'© OpenStreetMap',maxNativeZoom:19,maxZoom:22});
  // 卫星图 (Esri WorldImagery, 免费无需API key)
  mapLayers[1].layer=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    {attribution:'© Esri WorldImagery',maxNativeZoom:18,maxZoom:22});
  // 地形图 (OpenTopoMap)
  mapLayers[2].layer=L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',
    {attribution:'© OpenTopoMap',maxNativeZoom:17,maxZoom:22});
  // 混合图 (Esri卫星+标签)
  mapLayers[3].layer=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
    {attribution:'© Esri',maxNativeZoom:19,maxZoom:22,opacity:0.7});

  // 默认卫星图
  mapLayers[1].layer.addTo(mp);
  document.getElementById('bM').textContent='卫星';
  curMapIdx=1;
  mp.setView([0,0],13);
  // 缩放按钮默认右移（避让侧边栏）
  var zoomCtrl=document.querySelector('.leaflet-control-zoom');
  if(zoomCtrl) zoomCtrl.classList.add('shifted');
  // 点击地图空白处关闭信息面板
  mp.on('click',function(){document.getElementById('pnl').style.display='none'});
  // 鼠标离开地图容器时清除悬停高亮
  document.getElementById('map').addEventListener('mouseleave',function(){hideHoverPoint()});
}

function cycleMap(){
  mp.removeLayer(mapLayers[curMapIdx].layer);
  curMapIdx=(curMapIdx+1)%mapLayers.length;
  if(curMapIdx===3){
    // 混合图：卫星底图+标签叠加
    mapLayers[1].layer.addTo(mp);
    mapLayers[3].layer.addTo(mp);
  } else {
    mapLayers[curMapIdx].layer.addTo(mp);
  }
  var btn=document.getElementById('bM');
  btn.textContent=mapLayers[curMapIdx].name;
}

function loadFile(i){
  ci=i;var d=ALL[i];
  P=d.pts;S=d.sm;LP=d.laps;LPP=d.lap_pts;KM=d.km;KMPT=d.km_pts;CLR=d.clr;HAS=d.has;
  al=-1;useKm=false;
  document.getElementById('pnl').style.display='none';
  // 按钮：有圈数显示"圈数"，无圈数显示"公里"
  var bL=document.getElementById('bL');
  bL.style.display='';
  bL.classList.remove('disabled');
  if(!HAS||!LP.length){
    useKm=true;
    bL.textContent='公里';
    if(!KM||!KM.length) bL.classList.add('disabled');
  } else {
    useKm=false;
    bL.textContent='圈数';
  }
  if(!mp)initMap();
  buildSb();draw();addCts();buildStat();
  document.getElementById('fSel').value=i;
}

// ===== 颜色 =====
function gr(v,a,b){var r=b>a?(v-a)/(b-a):.5,R=r<.5?~~(r*510):255,G=r<.5?255:~~((1-r)*510);return'rgb('+R+','+G+',30)'}
function hc(h){return gr(h||S.hr_lo,S.hr_lo,S.hr_hi)}
function sc(s){var ss=P.map(function(p){return p.spd}).filter(function(v){return v>0});return ss.length?gr(s,Math.min.apply(null,ss),Math.max.apply(null,ss)):'#888'}
function ac(a){var aa=P.map(function(p){return p.alt});return aa.length?gr(a,Math.min.apply(null,aa),Math.max.apply(null,aa)):'#888'}
function pc(p){return cm==='spd'?sc(p.spd):cm==='alt'?ac(p.alt):hc(p.hr)}

// ===== 绘制轨迹 =====
function draw(){
  segs.forEach(function(s){mp.removeLayer(s)});segs=[];
  var v;
  if(al>=0){v=useKm?KMPT[al]:LPP[al]}
  else{v=P}
  if(!v||!v.length)return;
  for(var i=0;i<v.length-1;i++){
    var c=HAS&&al>=0?CLR[al%CLR.length]:pc(v[i]);
    segs.push(L.polyline([[v[i].lat,v[i].lon],[v[i+1].lat,v[i+1].lon]],
      {color:c,weight:4,opacity:.85}).addTo(mp));
  }
  mp.fitBounds(L.latLngBounds(v.map(function(p){return[p.lat,p.lon]})),{padding:[50,60,80,60]});
  segs.push(L.circleMarker([v[0].lat,v[0].lon],
    {radius:7,fillColor:'#00e676',fillOpacity:1,weight:2,color:'#fff'})
    .addTo(mp).bindTooltip('起点',{direction:'top',offset:[0,-8]}));
  if(HAS&&al>=0&&!LP[al].closed){
    segs.push(L.circleMarker([v[v.length-1].lat,v[v.length-1].lon],
      {radius:7,fillColor:'#ff1744',fillOpacity:1,weight:2,color:'#fff'})
      .addTo(mp).bindTooltip('终点(未闭合)',{direction:'top',offset:[0,-8]}));
  }
}

// ===== 可点击层 =====
function addCts(){
  cts.forEach(function(m){mp.removeLayer(m)});cts=[];
  var v;
  if(al>=0){v=useKm?KMPT[al]:LPP[al];if(!v)return}
  else{v=P}
  var base=al>=0?(useKm?KM[al].si:LP[al].si):0;
  v.forEach(function(p,i){
    var m=L.circleMarker([p.lat,p.lon],
      {radius:8,color:'transparent',fillColor:'transparent',weight:0,fillOpacity:0,interactive:true}
    ).addTo(mp);
    var idx=base+i;
    m.on('click',function(e){L.DomEvent.stopPropagation(e);showP(p,idx)});
    m.on('touchstart',function(){showP(p,idx)});
    m.on('mouseover',function(){showHoverPoint(idx)});
    cts.push(m);
  });
}

// ===== 点击信息面板 =====
function showP(p,idx){
  var pn=document.getElementById('pnl');
  var paceSec=p.spd>0?Math.floor(1000/p.spd):0;
  var pace=paceSec>0?(paceSec>1200?'慢':Math.floor(paceSec/60)+"'"+String(paceSec%60).padStart(2,'0')+'"'):'--\'--"';
  var ln=0;
  if(HAS)for(var j=0;j<LP.length;j++)if(idx>=LP[j].si&&idx<=LP[j].ei){ln=j+1;break}
  var lapInfo=ln?(useKm?('第'+ln+'公里'):('第'+ln+'圈')):'';
  pn.innerHTML=
    '<h3>#'+(idx+1)+(ln?' '+lapInfo+' ':'')+'</h3>'+
    '<div class="r"><span class="l">时间</span><span class="v">'+(p.time||'--')+'</span></div>'+
    '<div class="sp"></div>'+
    '<div class="r"><span class="l">心率</span><span class="v">'+(p.hr||'--')+' bpm</span></div>'+
    '<div class="r"><span class="l">速度</span><span class="v">'+(p.spd*3.6).toFixed(1)+' km/h</span></div>'+
    '<div class="r"><span class="l">配速</span><span class="v">'+pace+'/km</span></div>'+
    '<div class="r"><span class="l">海拔</span><span class="v">'+p.alt+' m</span></div>'+
    '<div class="sp"></div>'+
    '<div class="r"><span class="l">距离</span><span class="v">'+(p.cum/1000).toFixed(2)+' km</span></div>'+
    '<div class="r"><span class="l">踏频</span><span class="v">'+(p.cad||'--')+' spm</span></div>';
  pn.style.display='block';
}

// ===== 底部统计栏（固定，不受选圈/选点影响）=====
function buildStat(){
  var d=(S.dist/1000).toFixed(2);
  var apSec=S.avg_spd>0?Math.floor(1000/S.avg_spd):0;
  var ap=apSec>0?(apSec>1200?'慢':Math.floor(apSec/60)+"'"+String(apSec%60).padStart(2,'0')+'"'):'--\'--"';
  var bpSec=S.max_spd>0?Math.floor(1000/S.max_spd):0;
  var bp=bpSec>0?(bpSec>1200?'慢':Math.floor(bpSec/60)+"'"+String(bpSec%60).padStart(2,'0')+'"'):'--\'--"';
  var t=S.time;
  var tStr=t>=3600?(~~(t/3600)+':'+String(~~((t%3600)/60)).padStart(2,'0')+':'+String(~~(t%60)).padStart(2,'0'))
    :(~~(t/60)+':'+String(~~(t%60)).padStart(2,'0'));
  // 计算扩展统计
  var altVals=[],cadVals=[],hrVals=[];
  for(var i=0;i<P.length;i++){
    if(P[i].alt!=null) altVals.push(P[i].alt);
    if(P[i].cad) cadVals.push(P[i].cad);
    if(P[i].hr) hrVals.push(P[i].hr);
  }
  var minAlt=altVals.length?Math.min.apply(null,altVals).toFixed(0):'--';
  var maxAlt=altVals.length?Math.max.apply(null,altVals).toFixed(0):'--';
  var avgCad=cadVals.length?Math.round(cadVals.reduce(function(a,b){return a+b},0)/cadVals.length):'--';
  var maxCad=cadVals.length?Math.max.apply(null,cadVals):'--';
  var minHr=hrVals.length?Math.min.apply(null,hrVals):'--';
  var steps=0,avgStep=0;
  if(cadVals.length&&d>0){
    // 估算步数: cad(rpm) * time(min) * 2
    steps=Math.round(cadVals.reduce(function(a,b){return a+b},0)/cadVals.length*(t/60)*2);
    avgStep=steps>0?Math.round(d*100000/steps):0; // cm
  }
  var asc=S.asc||'--',desc=S.desc||'--',cal=S.cal||'--';
  document.getElementById('statBar').innerHTML=
    // 第一行
    '<div class="stat-row">'+
    '<div class="item"><div class="val">'+d+'</div><div class="lbl">距离</div></div>'+
    '<div class="sep"></div>'+
    '<div class="item"><div class="val">'+tStr+'</div><div class="lbl">用时</div></div>'+
    '<div class="sep"></div>'+
    '<div class="item"><div class="val">'+ap+'</div><div class="lbl">平均配速</div></div>'+
    '<div class="sep"></div>'+
    '<div class="item"><div class="val">'+bp+'</div><div class="lbl">最佳配速</div></div>'+
    '<div class="sep"></div>'+
    '<div class="item"><div class="val">'+(S.avg_hr||'--')+'</div><div class="lbl">平均心率</div></div>'+
    '<div class="sep"></div>'+
    '<div class="item"><div class="val">'+(S.max_hr||'--')+'</div><div class="lbl">最高心率</div></div>'+
    '<div class="sep"></div>'+
    '<div class="item"><div class="val">'+minHr+'</div><div class="lbl">最低心率</div></div>'+
    '</div>'+
    // 第二行
    '<div class="stat-row">'+
    '<div class="item"><div class="val sm">'+avgCad+'</div><div class="lbl">平均步频</div></div>'+
    '<div class="sep"></div>'+
    '<div class="item"><div class="val sm">'+maxCad+'</div><div class="lbl">最高步频</div></div>'+
    '<div class="sep"></div>'+
    '<div class="item"><div class="val sm">'+avgStep+'</div><div class="lbl">平均步幅cm</div></div>'+
    '<div class="sep"></div>'+
    '<div class="item"><div class="val sm">'+(steps||'--')+'</div><div class="lbl">步数</div></div>'+
    '<div class="sep"></div>'+
    '<div class="item"><div class="val sm">'+maxAlt+'m</div><div class="lbl">最高海拔</div></div>'+
    '<div class="sep"></div>'+
    '<div class="item"><div class="val sm">'+minAlt+'m</div><div class="lbl">最低海拔</div></div>'+
    '<div class="sep"></div>'+
    '<div class="item"><div class="val sm">'+asc+'m</div><div class="lbl">累计上升</div></div>'+
    '<div class="sep"></div>'+
    '<div class="item"><div class="val sm">'+desc+'m</div><div class="lbl">累计下降</div></div>'+
    '<div class="sep"></div>'+
    '<div class="item"><div class="val sm">'+cal+'</div><div class="lbl">消耗kcal</div></div>'+
    '</div>';
  // 绘制图表
  drawChart();
}

// ===== 圈数/公里栏 =====
function buildSb(){
  var btn=document.getElementById('bL'),sb=document.getElementById('sb');
  if(btn.classList.contains('disabled')){sb.style.display='none';return}
  sb.style.display='';

  var data, label, allLabel;
  if(useKm){
    data=KM; label='公里'; allLabel=S.sport+' · 公里分段';
  } else {
    data=LP; label='圈'; allLabel=S.sport+' · 圈数';
  }
  if(!data||!data.length){sb.style.display='none';return}

  var closedCnt=data.filter(function(l){return l.closed}).length;
  var h='<div class="card on" onclick="sL(-1)" style="--c:#666">'+
    '<div class="n">全部<span class="badge">'+data.length+label+(useKm?'':' / '+closedCnt+'闭合')+'</span></div>'+
    '<div class="s"><span>'+(S.dist/1000).toFixed(2)+'km</span></div></div>';

  data.forEach(function(l,i){
    var c=CLR[i%CLR.length];
    var tStr=l.t>=60?(~~(l.t/60)+'分'+l.t%60+'秒'):(l.t+'秒');
    var tag=(useKm||l.closed)?'':'<span class="badge" style="color:#ff9100">未闭合</span>';
    var name=useKm?('第'+l.i+'公里'):('第'+l.i+'圈'+tag);
    h+='<div class="card" id="c'+i+'" onclick="sL('+i+')" style="--c:'+c+'">'+
      '<div class="n"><span class="dot" style="background:'+c+'"></span>'+name+'</div>'+
      '<div class="s"><span>'+l.d+'m</span><span>'+tStr+'</span><span>'+l.pace+'/km</span></div>'+
      '<div class="s"><span>心率 '+(l.hr||'--')+'</span></div></div>';
  });
  document.getElementById('ll').innerHTML=h;
  document.getElementById('sbT').textContent=allLabel;
}

function sL(i){
  al=i;
  hideHoverPoint();
  document.querySelectorAll('.card').forEach(function(c){c.classList.remove('on')});
  if(i===-1)document.querySelector('.card').classList.add('on');
  else document.getElementById('c'+i).classList.add('on');
  draw();addCts();document.getElementById('pnl').style.display='none';
}
function tSb(){
  var bL=document.getElementById('bL');
  if(bL.classList.contains('disabled'))return;
  var sb=document.getElementById('sb');
  var isOpen=!sb.classList.contains('off');
  if(isOpen){
    sb.classList.add('off');
    document.getElementById('tbWrap').classList.remove('shifted');
    document.querySelector('.leaflet-control-zoom').classList.remove('shifted');
  } else {
    sb.classList.remove('off');
    document.getElementById('tbWrap').classList.add('shifted');
    document.querySelector('.leaflet-control-zoom').classList.add('shifted');
  }
}

// ===== 颜色模式 =====
function cMod(){
  cm=['hr','spd','alt'][(['hr','spd','alt'].indexOf(cm)+1)%3];
  document.getElementById('bC').textContent={hr:'心率',spd:'速度',alt:'海拔'}[cm];
  if(al===-1){draw();addCts()}
}

// ===== 图表 =====
var chartObj=null;
var hoverMk=null;    // 图表悬停时地图上的高亮点
var hoverIdx=-1;     // 当前悬停的原始点索引

function paceStr(spd){
  if(!spd||spd<=0) return "--'--\"";
  var s=Math.round(1000/spd);
  if(s>1200) return "慢";
  return Math.floor(s/60)+"'"+(s%60<10?'0':'')+(s%60)+'"';
}

function drawChart(){
  if(chartObj){chartObj.destroy();chartObj=null}
  if(hoverMk){mp.removeLayer(hoverMk);hoverMk=null}
  hoverIdx=-1;
  var cv=document.getElementById('chart');
  if(!cv||typeof Chart==='undefined')return;
  // 采样: 最多200个点
  var step=Math.max(1,Math.floor(P.length/200));
  var labels=[],hrData=[],altData=[],paceData=[];
  for(var i=0;i<P.length;i+=step){
    labels.push(i);
    hrData.push(P[i].hr||null);
    altData.push(P[i].alt||null);
    paceData.push(P[i].spd>0?Math.round(1000/P[i].spd/60):null); // min/km
  }
  chartObj=new Chart(cv,{
    type:'line',
    data:{
      labels:labels,
      datasets:[
        {label:'配速',data:paceData,borderColor:'#ff6b6b',borderWidth:1.5,
         pointRadius:0,tension:.3,yAxisID:'y',spanGaps:true},
        {label:'心率',data:hrData,borderColor:'#ffd93d',borderWidth:1.5,
         pointRadius:0,tension:.3,yAxisID:'y1',spanGaps:true},
        {label:'海拔',data:altData,borderColor:'#6bcb77',borderWidth:1.5,
         pointRadius:0,tension:.3,yAxisID:'y2',spanGaps:true}
      ]
    },
    options:{
      responsive:true,maintainAspectRatio:false,
      plugins:{
        legend:{display:true,labels:{color:'#aaa',font:{size:10},boxWidth:12,padding:6},position:'top'},
        tooltip:{
          callbacks:{
            title:function(items){
              var idx=labels[items[0].dataIndex];
              return (P[idx].time||'')+'  #'+(idx+1);
            },
            label:function(ctx){
              var p=P[labels[ctx.dataIndex]];
              if(ctx.datasetIndex===0) return '配速: '+paceStr(p.spd)+'/km';
              if(ctx.datasetIndex===1) return '心率: '+(p.hr||'--')+' bpm';
              return '海拔: '+(p.alt!=null?p.alt:'--')+' m';
            }
          }
        }
      },
      interaction:{intersect:false,mode:'index'},
      onHover:function(evt,items){
        if(items&&items.length){
          showHoverPoint(labels[items[0].index]);
        } else {
          hideHoverPoint();
        }
      },
      scales:{
        x:{display:false},
        y:{display:false,reverse:true,min:0},
        y1:{display:false,reverse:true,min:40},
        y2:{display:false}
      }
    }
  });
}

// ===== 图表悬停 → 地图/信息框联动 =====
function showHoverPoint(idx){
  if(idx===hoverIdx) return;
  hoverIdx=idx;
  var p=P[idx];
  if(!p||!mp) return;
  if(!hoverMk){
    hoverMk=L.circleMarker([p.lat,p.lon],
      {radius:9,color:'#ffeb3b',weight:3,fillColor:'#ffeb3b',fillOpacity:0.9})
      .addTo(mp);
  } else {
    hoverMk.setLatLng([p.lat,p.lon]);
  }
  showP(p,idx);
}

function hideHoverPoint(){
  if(hoverMk){mp.removeLayer(hoverMk);hoverMk=null}
  hoverIdx=-1;
}

// ===== 文件切换 =====
function swFile(i){loadFile(i)}

// ===== 初始化 =====
(function(){
  var sel=document.getElementById('fSel');
  for(var i=0;i<FNAMES.length;i++){
    var opt=document.createElement('option');
    opt.value=i;opt.textContent=FNAMES[i];
    sel.appendChild(opt);
  }
  initMap();loadFile(0);
})();
</script>
</body>
</html>"""

def gen_html(all_file_data, out):
    datasets = []
    names = []
    for fn, pts, sm, laps, has in all_file_data:
        clean_laps = []
        for l in laps:
            cl = dict(l)
            cl.pop("pts", None)
            clean_laps.append(cl)
        km_data = sm.get("km_splits_data", [])
        clean_km = []
        for l in km_data:
            cl = dict(l)
            cl.pop("pts", None)
            clean_km.append(cl)
        datasets.append({
            "pts": pts, "sm": sm, "laps": clean_laps,
            "lap_pts": [l.get("pts", []) for l in laps],
            "km": clean_km,
            "km_pts": [l.get("pts", []) for l in km_data],
            "clr": LAP_COLORS[:max(len(laps), len(km_data))], "has": has
        })
        names.append(fn)
    html = HTML_TEMPLATE.replace("@DATA@", json.dumps(datasets, ensure_ascii=False), 1)
    html = html.replace("@FILENAMES@", json.dumps(names, ensure_ascii=False), 1)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    return out

def find_fits(folder):
    """返回文件夹下所有 .fit 文件(按名称排序)。"""
    try:
        return sorted(os.path.join(folder, f) for f in os.listdir(folder)
                      if f.lower().endswith(".fit"))
    except OSError:
        return []

def pick_files():
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        folder = filedialog.askdirectory(title="选择包含 FIT 文件的文件夹")
        root.destroy()
        if folder:
            return find_fits(folder)
    except: pass
    return []

def open_browser(path):
    url = "file:///" + path.replace("\\", "/")
    try: webbrowser.open(url)
    except Exception:
        dirpath = os.path.dirname(path)
        fname = os.path.basename(path)
        print(f"Starting server at http://localhost:8765/{fname}")
        subprocess.Popen([sys.executable, "-m", "http.server", "8765", "-d", dirpath])
        webbrowser.open(f"http://localhost:8765/{fname}")

def open_window(path):
    """用独立窗口打开HTML。优先pywebview，回退浏览器。"""
    try:
        import webview
        webview.create_window(
            "FIT 运动轨迹查看器", path,
            width=1200, height=800,
            min_size=(800, 500)
        )
        webview.start()
        return True
    except ImportError:
        pass
    except Exception:
        pass
    # pywebview 不可用，回退浏览器
    open_browser(path)
    return False

def main():
    fit_paths = []
    for arg in sys.argv[1:]:
        if os.path.isdir(arg):
            fit_paths.extend(find_fits(arg))
        elif os.path.isfile(arg):
            fit_paths.append(arg)
    if not fit_paths: fit_paths = pick_files()
    if not fit_paths:
        # 回退：扫描脚本所在目录及其子目录
        base = os.path.dirname(os.path.abspath(__file__))
        for root, _dirs, files in os.walk(base):
            for f in files:
                if f.lower().endswith(".fit"):
                    fit_paths.append(os.path.join(root, f))
        fit_paths.sort()
        if not fit_paths:
            print("No FIT files selected."); sys.exit(1)

    out = os.path.join(os.path.dirname(os.path.abspath(fit_paths[0])), "fit_route.html")
    all_data = []
    for fp in fit_paths:
        print(f"Parsing: {fp}")
        try:
            pts, sm, laps, has = parse_fit(fp)
            if pts:
                all_data.append((os.path.basename(fp), pts, sm, laps, has))
                print(f"  Points: {len(pts)}, Laps: {len(laps) if has else 0}")
        except Exception as e:
            print(f"  Error: {e}")
    if not all_data:
        print("Error: No valid FIT files!"); sys.exit(1)
    gen_html(all_data, out)
    print(f"\nGenerated: {out} ({len(all_data)} files)")
    open_window(out)

if __name__ == "__main__":
    main()

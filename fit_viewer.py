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
    for r in records:
        lat, lon = r.get("position_lat"), r.get("position_long")
        if lat is None or lon is None: continue
        ts = r.get("timestamp")
        points.append({
            "lat": round(lat * SEMI2DEG, 6),
            "lon": round(lon * SEMI2DEG, 6),
            "time": ts.strftime("%H:%M:%S") if isinstance(ts, datetime) else "",
            "ts": ts,
            "hr": r.get("heart_rate"),
            "spd": round(r.get("speed", 0) or 0, 2),
            "alt": round(r.get("enhanced_altitude", r.get("altitude", 0)) or 0, 1),
            "cad": r.get("cadence"),
            "dst": round(r.get("distance", 0) or 0, 1),
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

def detect_loops(points, min_away=8, min_lap_pts=200, min_lap_dist=300):
    """闭环检测。每圈末点 = 离该圈起点最近的点。
    严格条件：
    1. 路由必须集中：距离标准差/最大距离 < 0.35（距离分布紧凑）
    2. 必须真正远离起点再回来（最远 > 80m）
    3. 回归点必须足够近（< 最远距离的25%）
    """
    if len(points) < 30: return False, []
    rlat, rlon = points[0]["lat"], points[0]["lon"]
    all_dists = [haversine(rlat, rlon, p["lat"], p["lon"]) for p in points]
    max_d = max(all_dists)
    if max_d < 80: return False, []

    # 离散度检查：绕圈路由的距离分布应该紧凑（std/max 小）
    avg_d = sum(all_dists) / len(all_dists)
    variance = sum((d - avg_d) ** 2 for d in all_dists) / len(all_dists)
    std_d = variance ** 0.5
    cv = std_d / max_d if max_d > 0 else 1
    if cv > 0.35: return False, []  # 距离分布太分散，不是绕圈

    far_thr = max_d * 0.50
    near_thr = max_d * 0.25

    lap_ends = []
    away_cnt = 0
    away_seen = False
    prev_start = 0
    min_dist = 1e9
    min_idx = 0

    for idx in range(len(points)):
        lsp = points[prev_start]
        d = haversine(lsp["lat"], lsp["lon"], points[idx]["lat"], points[idx]["lon"])

        if not away_seen:
            if d > far_thr:
                away_cnt += 1
                if away_cnt >= min_away:
                    away_seen = True
                    min_dist = 1e9
                    min_idx = idx
        else:
            if d < min_dist:
                min_dist = d
                min_idx = idx
            if d < near_thr and (idx - prev_start) >= min_lap_pts:
                lap_d = sum(
                    haversine(points[j-1]["lat"], points[j-1]["lon"],
                              points[j]["lat"], points[j]["lon"])
                    for j in range(prev_start + 1, min_idx + 1)
                ) if min_idx > prev_start else 0
                if lap_d >= min_lap_dist or prev_start == 0:
                    lap_ends.append(min_idx)
                    prev_start = min_idx
                away_seen = False
                away_cnt = 0

    if len(lap_ends) < 2: return False, []
    laps = []
    prev = 0
    for lap_idx, end in enumerate(lap_ends):
        laps.append(_make_lap(points, lap_idx, prev, end, closed=True))
        prev = end
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
.side{position:fixed;top:0;left:0;bottom:56px;z-index:100;width:230px;
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
.toolbar.shifted{left:250px}
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
.chart-area{padding:2px 12px 6px;height:80px;position:relative}
.chart-area canvas{width:100%!important;height:70px!important}

/* Leaflet缩放按钮避让 */
.leaflet-control-zoom{transition:left .3s ease}
.leaflet-control-zoom.shifted{left:250px!important}

/* 移动端适配 */
@media(max-width:600px){
  .side{width:190px}
  .side.off{transform:translateX(-190px)}
  .toolbar.shifted{left:200px}
  .leaflet-control-zoom.shifted{left:200px!important}
  .info{width:calc(100vw - 20px);right:10px;left:10px;top:auto;bottom:66px;
    max-height:40vh;font-size:12px}
  .stat-bar .val{font-size:12px}
  .stat-bar .val.sm{font-size:10px}
  .chart-area{height:60px}
  .chart-area canvas{height:50px!important}
  .file-box select{max-width:130px}
}
</style>
</head>
<body>
<div id="map"></div>

<!-- 左侧圈数栏 -->
<div class="side off" id="sb">
  <div class="hd"><span id="sbT">圈数</span><button class="close" onclick="tSb()">&times;</button></div>
  <div class="lst" id="ll"></div>
</div>

<!-- 顶部工具栏 -->
<div class="toolbar shifted" id="tbWrap">
  <button class="tb" id="bL" onclick="tSb()" style="display:none">圈数</button>
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
  mp=L.map('map',{zoomControl:true,tap:true,preferCanvas:true});
  // 街道图
  mapLayers[0].layer=L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
    {attribution:'© OpenStreetMap',maxZoom:19});
  // 卫星图 (Esri WorldImagery, 免费无需API key)
  mapLayers[1].layer=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    {attribution:'© Esri WorldImagery',maxZoom:18});
  // 地形图 (OpenTopoMap)
  mapLayers[2].layer=L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',
    {attribution:'© OpenTopoMap',maxZoom:17});
  // 混合图 (Esri卫星+标签)
  mapLayers[3].layer=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
    {attribution:'© Esri',maxZoom:19,opacity:0.7});

  // 默认卫星图
  mapLayers[1].layer.addTo(mp);
  document.getElementById('bM').textContent='卫星';
  document.getElementById('bM').classList.add('on');
  curMapIdx=1;
  mp.setView([0,0],13);
  // 点击地图空白处关闭信息面板
  mp.on('click',function(){document.getElementById('pnl').style.display='none'});
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
  btn.classList.toggle('on',curMapIdx!==0);
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
  v.forEach(function(p,i){
    var m=L.circleMarker([p.lat,p.lon],
      {radius:8,color:'transparent',fillColor:'transparent',weight:0,fillOpacity:0,interactive:true}
    ).addTo(mp);
    m.on('click',function(e){L.DomEvent.stopPropagation(e);showP(p,i)});
    m.on('touchstart',function(){showP(p,i)});
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
  var lapInfo=ln?(useKm?('第'+ln+'公里'):(LP[ln-1].closed?'<span style="color:#00e676">闭合</span>':'<span style="color:#ff9100">未闭合</span>')):'';
  pn.innerHTML=
    '<h3>#'+(idx+1)+(ln?' '+lapInfo+' ':'')+'</h3>'+
    '<div class="r"><span class="l">时间</span><span class="v">'+(p.time||'--')+'</span></div>'+
    '<div class="sp"></div>'+
    '<div class="r"><span class="l">心率</span><span class="v">'+(p.hr||'--')+' bpm</span></div>'+
    '<div class="r"><span class="l">速度</span><span class="v">'+(p.spd*3.6).toFixed(1)+' km/h</span></div>'+
    '<div class="r"><span class="l">配速</span><span class="v">'+pace+'/km</span></div>'+
    '<div class="r"><span class="l">海拔</span><span class="v">'+p.alt+' m</span></div>'+
    '<div class="sp"></div>'+
    '<div class="r"><span class="l">距离</span><span class="v">'+(p.dst/1000).toFixed(2)+' km</span></div>'+
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
  document.getElementById('bC').classList.toggle('on',cm!=='hr');
  if(al===-1){draw();addCts()}
}

// ===== 图表 =====
var chartObj=null;
function drawChart(){
  if(chartObj){chartObj.destroy();chartObj=null}
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
      plugins:{legend:{display:true,labels:{color:'#aaa',font:{size:10},
        boxWidth:12,padding:6},position:'top'}},
      interaction:{intersect:false,mode:'index'},
      scales:{
        x:{display:false},
        y:{display:false,reverse:true,min:0},
        y1:{display:false,reverse:true,min:40},
        y2:{display:false}
      }
    }
  });
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

def pick_files():
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        paths = filedialog.askopenfilenames(
            title="选择 FIT 文件(可多选)",
            filetypes=[("FIT 文件", "*.fit *.FIT"), ("所有文件", "*.*")])
        root.destroy()
        if paths: return list(paths)
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
        if os.path.isfile(arg): fit_paths.append(arg)
    if not fit_paths: fit_paths = pick_files()
    if not fit_paths:
        d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Zepp20260916211459.fit")
        if os.path.isfile(d): fit_paths = [d]
        else: print("No file selected."); sys.exit(1)

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

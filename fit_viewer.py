# -*- coding: utf-8 -*-
"""
FIT 运动轨迹双端查看器
- 闭环GPS自动识别圈数（最后一圈不闭合也保留）
- 选圈时该圈所有记录点均可点击查看运动信息
- 启动时文件选择对话框，可同时加载多个fit文件并切换
- 生成独立HTML（数据内嵌），桌面/移动浏览器直接打开
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
    if not v or v <= 0: return "--:--"
    s = int(1000 / v)
    return f"{s//60}:{s%60:02d}"

def sport_name(v):
    return {0:"通用",1:"跑步",2:"骑行",5:"游泳",11:"步行",
            16:"登山",17:"徒步",15:"划船"}.get(v, f"运动({v})")

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
    for p in points: del p["ts"]
    return points, summary, lap_data, has_laps

def detect_loops(points, min_away=8, min_lap_pts=50, min_lap_dist=80):
    """闭环检测。
    完整圈：GPS回归起点。最后一圈不闭合也保留（标记为 incomplete）。
    """
    if len(points) < 20: return False, []
    rlat, rlon = points[0]["lat"], points[0]["lon"]
    dists = [haversine(rlat, rlon, p["lat"], p["lon"]) for p in points]
    max_d = max(dists)
    if max_d < 30: return False, []

    near_thr = max_d * 0.30
    far_thr  = max_d * 0.55

    # 检测回归事件
    lap_ends = []
    away_cnt, gap, away_seen = 0, 0, False
    last_end = -min_lap_pts
    for idx, p in enumerate(points):
        d = dists[idx]
        if away_seen: gap += 1
        if d > far_thr:
            away_cnt += 1
            if away_cnt >= min_away: away_seen = True
        if away_seen and d < near_thr and gap >= 2 and (idx - last_end) >= min_lap_pts:
            lap_d = sum(
                haversine(points[j-1]["lat"], points[j-1]["lon"],
                          points[j]["lat"], points[j]["lon"])
                for j in range(max(last_end + 1, 0), idx + 1)
            ) if last_end >= 0 else max_d * 2
            if lap_d >= min_lap_dist or last_end < 0:
                lap_ends.append(idx)
                last_end = idx
            away_seen = False; away_cnt = 0; gap = 0

    # 构建 lap 数据
    laps = []
    prev = 0
    snap_lat, snap_lon = points[0]["lat"], points[0]["lon"]

    if lap_ends:
        for lap_idx, end in enumerate(lap_ends):
            lap, snap_lat, snap_lon = _make_lap(
                points, lap_idx, prev, end, closed=True,
                snap_lat=snap_lat, snap_lon=snap_lon)
            laps.append(lap)
            prev = end + 1
        if prev < len(points):
            lap, _, _ = _make_lap(points, len(lap_ends), prev, len(points)-1, closed=False)
            laps.append(lap)
    else:
        return False, []
    return True, laps

def _make_lap(points, idx, si, ei, closed, snap_lat=None, snap_lon=None):
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
    if closed:
        # 起点吸附到上一圈的终点，保持圈间连续
        if snap_lat is not None and out_pts:
            out_pts[0]["lat"] = round(snap_lat, 6)
            out_pts[0]["lon"] = round(snap_lon, 6)
        # 末点吸附到该圈的起点，形成闭合
        first_lat = out_pts[0]["lat"]
        first_lon = out_pts[0]["lon"]
        if out_pts:
            out_pts[-1]["lat"] = first_lat
            out_pts[-1]["lon"] = first_lon
        new_snap_lat = first_lat
        new_snap_lon = first_lon
    else:
        new_snap_lat = snap_lat
        new_snap_lon = snap_lon

    return {
        "i": idx + 1, "si": si, "ei": ei,
        "d": round(ld, 1), "t": round(el),
        "hr": ahr, "spd": asp, "pace": speed_to_pace(asp),
        "n": len(lp), "closed": closed, "pts": out_pts,
    }, new_snap_lat, new_snap_lon

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
.side{position:fixed;top:0;left:0;bottom:0;z-index:100;width:230px;
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
.tb{background:rgba(15,15,30,.85);backdrop-filter:blur(8px);border:none;
  border-radius:8px;padding:7px 12px;color:#eee;font-size:12px;
  cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.3);
  transition:all .2s;white-space:nowrap}
.tb:hover{background:rgba(40,40,60,.95)}
.tb.on{background:rgba(100,140,255,.3);color:#8ab4ff}

/* 文件选择器 */
.file-box{position:fixed;top:10px;right:10px;z-index:200}
.file-box select{background:rgba(15,15,30,.85);backdrop-filter:blur(8px);
  border:1px solid rgba(255,255,255,.1);border-radius:8px;
  padding:7px 12px;color:#eee;font-size:12px;cursor:pointer;
  box-shadow:0 2px 8px rgba(0,0,0,.3);max-width:200px}
.file-box select option{background:#1a1a2e;color:#eee}

/* 右侧信息面板 */
.info{position:fixed;top:50px;right:10px;z-index:100;
  background:rgba(15,15,30,.92);backdrop-filter:blur(10px);
  border-radius:12px;padding:14px 16px;
  box-shadow:0 4px 20px rgba(0,0,0,.4);
  width:280px;font-size:13px;line-height:1.6;color:#ddd;
  max-height:calc(100vh - 70px);overflow-y:auto;
  border:1px solid rgba(255,255,255,.08)}
.info h3{font-size:14px;margin-bottom:8px;color:#fff;font-weight:600}
.info .r{display:flex;justify-content:space-between;padding:2px 0}
.info .l{color:#888}.info .v{font-weight:600;color:#eee}
.info .sp{border-top:1px solid rgba(255,255,255,.08);margin:8px 0}
.info .hint{text-align:center;color:#666;font-size:11px;margin-top:4px}

/* 移动端适配 */
@media(max-width:600px){
  .side{width:200px}
  .side.off{transform:translateX(-200px)}
  .info{width:calc(100vw - 20px);right:10px;left:10px;top:auto;bottom:10px;
    max-height:45vh;font-size:12px}
  .file-box select{max-width:140px}
}
</style>
</head>
<body>
<div id="map"></div>

<!-- 左侧圈数栏 -->
<div class="side" id="sb" style="display:none">
  <div class="hd"><span id="sbT">圈数</span><button class="close" onclick="tSb()">&times;</button></div>
  <div class="lst" id="ll"></div>
</div>

<!-- 顶部工具栏 -->
<div class="toolbar">
  <button class="tb" id="bL" onclick="tSb()" style="display:none">圈数</button>
  <button class="tb" id="bS" onclick="tP()">概要</button>
  <button class="tb" id="bC" onclick="cMod()">心率</button>
</div>

<!-- 文件选择 -->
<div class="file-box"><select id="fSel" onchange="swFile(+this.value)"></select></div>

<!-- 信息面板 -->
<div class="info" id="pnl" style="display:none"></div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
// ===== 数据 =====
var ALL = @DATA@;
var FNAMES = @FILENAMES@;

var ci=0, P, S, LP, LPP, CLR, HAS;
var mp=null, cm='hr', al=-1;
var segs=[], cts=[];

// ===== 地图 =====
function initMap(){
  if(mp){mp.remove();mp=null}
  mp=L.map('map',{zoomControl:true,tap:true,preferCanvas:true});
  mp.addLayer(L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
    {attribution:'© OSM',maxZoom:19}));
  mp.setView([0,0],13);
}

function loadFile(i){
  ci=i; var d=ALL[i];
  P=d.pts; S=d.sm; LP=d.laps; LPP=d.lap_pts; CLR=d.clr; HAS=d.has;
  al=-1;
  document.getElementById('pnl').style.display='none';
  if(!mp)initMap();
  buildSb(); draw(); addCts();
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
  // 选圈时用该圈独立点数据（末点已吸附起点），全部时用全局点
  var v=al>=0?LPP[al]:P;
  if(!v||!v.length)return;
  for(var i=0;i<v.length-1;i++){
    var c=HAS&&al>=0?CLR[al%CLR.length]:pc(v[i]);
    segs.push(L.polyline([[v[i].lat,v[i].lon],[v[i+1].lat,v[i+1].lon]],
      {color:c,weight:4,opacity:.85}).addTo(mp));
  }
  mp.fitBounds(L.latLngBounds(v.map(function(p){return[p.lat,p.lon]})),{padding:[50,50]});
  // 起点
  segs.push(L.circleMarker([v[0].lat,v[0].lon],
    {radius:7,fillColor:'#00e676',fillOpacity:1,weight:2,color:'#fff'})
    .addTo(mp).bindTooltip('起点',{direction:'top',offset:[0,-8]}));
  // 终点（闭合圈时终点≈起点，不闭合圈时显示为独立标记）
  if(!HAS||al<0||LP[al].closed){
    // 闭合圈：终点和起点重合，只显示起点标记
  } else {
    segs.push(L.circleMarker([v[v.length-1].lat,v[v.length-1].lon],
      {radius:7,fillColor:'#ff1744',fillOpacity:1,weight:2,color:'#fff'})
      .addTo(mp).bindTooltip('终点（未闭合）',{direction:'top',offset:[0,-8]}));
  }
}

// ===== 可点击层 =====
function addCts(){
  cts.forEach(function(m){mp.removeLayer(m)});cts=[];
  // 选圈时用该圈独立点数据
  var v, ref;
  if(al>=0){
    v=LPP[al]; ref=P;  // ref 用于找回原始索引
    if(!v)return;
    v.forEach(function(p){
      // 在全局 P 中找最近的点索引
      var best=0,bestD=1e9;
      for(var k=LP[al].si;k<=LP[al].ei&&k<P.length;k++){
        var dd=Math.abs(P[k].lat-p.lat)+Math.abs(P[k].lon-p.lon);
        if(dd<bestD){bestD=dd;best=k}
      }
      var m=L.circleMarker([p.lat,p.lon],
        {radius:8,color:'transparent',fillColor:'transparent',weight:0,fillOpacity:0,interactive:true}
      ).addTo(mp);
      m.on('click',function(e){L.DomEvent.stopPropagation(e);showP(p,best)});
      m.on('touchstart',function(){showP(p,best)});
      cts.push(m);
    });
  } else {
    v=P;
    v.forEach(function(p,i){
      var m=L.circleMarker([p.lat,p.lon],
        {radius:8,color:'transparent',fillColor:'transparent',weight:0,fillOpacity:0,interactive:true}
      ).addTo(mp);
      m.on('click',function(e){L.DomEvent.stopPropagation(e);showP(p,i)});
      m.on('touchstart',function(){showP(p,i)});
      cts.push(m);
    });
  }
}

// ===== 信息面板 =====
function showP(p,idx){
  var pn=document.getElementById('pnl');
  var pace=p.spd>0?(Math.floor(1000/p.spd)+':'+String(~~(1000/p.spd)%60).padStart(2,'0')):'--:--';
  var ln=0;
  if(HAS)for(var j=0;j<LP.length;j++)if(idx>=LP[j].si&&idx<=LP[j].ei){ln=j+1;break}
  var lapInfo=ln?(LP[ln-1].closed?'<span style="color:#00e676">闭合圈</span>':'<span style="color:#ff9100">不闭合圈</span>'):'';
  pn.innerHTML=
    '<h3>#'+(idx+1)+(ln?' · 第'+ln+'圈 '+lapInfo:'')+'</h3>'+
    '<div class="r"><span class="l">时间</span><span class="v">'+(p.time||'--')+'</span></div>'+
    '<div class="sp"></div>'+
    '<div class="r"><span class="l">心率</span><span class="v">'+(p.hr||'--')+' bpm</span></div>'+
    '<div class="r"><span class="l">速度</span><span class="v">'+(p.spd*3.6).toFixed(1)+' km/h</span></div>'+
    '<div class="r"><span class="l">配速</span><span class="v">'+pace+' /km</span></div>'+
    '<div class="r"><span class="l">海拔</span><span class="v">'+p.alt+' m</span></div>'+
    '<div class="sp"></div>'+
    '<div class="r"><span class="l">距离</span><span class="v">'+(p.dst/1000).toFixed(2)+' km</span></div>'+
    '<div class="r"><span class="l">踏频</span><span class="v">'+(p.cad||'--')+' spm</span></div>';
  pn.style.display='block';
}

// ===== 圈数栏 =====
function buildSb(){
  var btn=document.getElementById('bL'),sb=document.getElementById('sb');
  if(!HAS||!LP.length){btn.style.display='none';sb.style.display='none';return}
  btn.style.display='';sb.style.display='';sb.classList.remove('off');

  var closedCnt=LP.filter(function(l){return l.closed}).length;
  var h='<div class="card on" onclick="sL(-1)" style="--c:#666">'+
    '<div class="n">全部<span class="badge">'+LP.length+'圈 / '+closedCnt+'闭合</span></div>'+
    '<div class="s"><span>'+(S.dist/1000).toFixed(2)+'km</span></div></div>';

  LP.forEach(function(l,i){
    var c=CLR[i%CLR.length];
    var tStr=l.t>=60?(~~(l.t/60)+'分'+l.t%60+'秒'):(l.t+'秒');
    var tag=l.closed?'':'<span class="badge" style="color:#ff9100">未闭合</span>';
    h+='<div class="card" id="c'+i+'" onclick="sL('+i+')" style="--c:'+c+'">'+
      '<div class="n"><span class="dot" style="background:'+c+'"></span>第'+l.i+'圈'+tag+'</div>'+
      '<div class="s"><span>'+l.d+'m</span><span>'+tStr+'</span><span>'+l.pace+'/km</span></div>'+
      '<div class="s"><span>心率 '+(l.hr||'--')+'</span></div></div>';
  });
  document.getElementById('ll').innerHTML=h;
  document.getElementById('sbT').textContent=S.sport+' · 圈数';
}

function sL(i){
  al=i;
  document.querySelectorAll('.card').forEach(function(c){c.classList.remove('on')});
  if(i===-1)document.querySelector('.card').classList.add('on');
  else document.getElementById('c'+i).classList.add('on');
  draw();addCts();document.getElementById('pnl').style.display='none';
}
function tSb(){document.getElementById('sb').classList.toggle('off')}

// ===== 概要 =====
function tP(){var p=document.getElementById('pnl');if(p.style.display==='none')showSum();else p.style.display='none'}
function showSum(){
  var p=document.getElementById('pnl');
  var d=(S.dist/1000).toFixed(2);
  var ap=S.avg_spd>0?(Math.floor(1000/S.avg_spd)+':'+String(~~(1000/S.avg_spd)%60).padStart(2,'0')):'--:--';
  var li=HAS?'<div class="r"><span class="l">圈数</span><span class="v">'+LP.length+' 圈</span></div>':'';
  p.innerHTML=
    '<h3>运动概要 · '+S.sport+'</h3>'+
    '<div class="r"><span class="l">总距离</span><span class="v">'+d+' km</span></div>'+
    '<div class="r"><span class="l">总时间</span><span class="v">'+
      (S.time>=3600?~~(S.time/3600)+'时':'')+~~((S.time%3600)/60)+'分'+~~(S.time%60)+'秒</span></div>'+
    '<div class="r"><span class="l">卡路里</span><span class="v">'+(S.cal||'--')+' kcal</span></div>'+
    '<div class="sp"></div>'+
    '<div class="r"><span class="l">平均配速</span><span class="v">'+ap+' /km</span></div>'+
    '<div class="r"><span class="l">平均心率</span><span class="v">'+(S.avg_hr||'--')+' bpm</span></div>'+
    '<div class="r"><span class="l">最高心率</span><span class="v">'+(S.max_hr||'--')+' bpm</span></div>'+
    '<div class="sp"></div>'+
    '<div class="r"><span class="l">爬升/下降</span><span class="v">'+(S.asc||'--')+'m / '+(S.desc||'--')+'m</span></div>'+
    li+'<div class="sp"></div>'+
    '<div class="hint">点击轨迹任意位置查看运动数据</div>';
  p.style.display='block';
}

// ===== 颜色模式 =====
function cMod(){
  cm=['hr','spd','alt'][(['hr','spd','alt'].indexOf(cm)+1)%3];
  document.getElementById('bC').textContent={hr:'心率',spd:'速度',alt:'海拔'}[cm];
  document.getElementById('bC').classList.toggle('on',cm!=='hr');
  if(al===-1){draw();addCts()}
}

// ===== 文件切换 =====
function swFile(i){loadFile(i)}

// ===== 初始化 =====
initMap();
loadFile(0);
</script>
</body>
</html>"""

def gen_html(all_file_data, out):
    datasets = []
    names = []
    for fn, pts, sm, laps, has in all_file_data:
        # 把 lap 内嵌的 pts 取出来放入 lap 数据，同时从 lap 中移除（减小体积）
        clean_laps = []
        for l in laps:
            cl = dict(l)
            cl.pop("pts", None)  # pts 在 lap 里但不放入全局 laps JSON
            clean_laps.append(cl)
        datasets.append({
            "pts": pts, "sm": sm, "laps": clean_laps,
            "lap_pts": [l.get("pts", []) for l in laps],  # 每圈的轨迹点
            "clr": LAP_COLORS[:len(laps)], "has": has
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
            title="选择 FIT 文件（可多选）",
            filetypes=[("FIT 文件", "*.fit *.FIT"), ("所有文件", "*.*")])
        root.destroy()
        if paths: return list(paths)
    except: pass
    return []

def open_browser(path):
    url = "file:///" + path.replace("\\", "/")
    try:
        webbrowser.open(url)
    except Exception:
        # 回退：用 python http server
        dirpath = os.path.dirname(path)
        fname = os.path.basename(path)
        print(f"Starting local server at http://localhost:8765/{fname}")
        subprocess.Popen([sys.executable, "-m", "http.server", "8765", "-d", dirpath])
        webbrowser.open(f"http://localhost:8765/{fname}")

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
    open_browser(out)

if __name__ == "__main__":
    main()

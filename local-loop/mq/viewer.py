#!/usr/bin/env python3
"""Quick-and-dirty local viewer for the GTFS-RT predictions.

Taps the local ZeroMQ feeds (read-only, no broker creds):
  - positions  : :5563  topic 'bustech'  (raw RealtimeEnvelope forwarded by the mq bridge)
  - predictions : :5568  topic 'time'     (GTFS-RT TripUpdates from the predictions engine)

Enriches stop_ids / route_ids with names + coordinates from the GTFS bundle (OBA_GTFS_ZIP) and serves
a Leaflet map at http://localhost:8090 — vehicle markers (live position) + a predictions side panel;
click a vehicle to plot its upcoming named stops. OpenStreetMap tiles by default; set MAPBOX_TOKEN for Mapbox.

Note: positions come from the raw feed on :5563, so markers appear only when the mq bridge is running.
"""
import csv
import io
import json
import os
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import zmq

try:
    from google.transit import gtfs_realtime_pb2 as gtfsrt
except Exception:
    gtfsrt = None

POS_PORT = os.environ.get("IE_INPUT_PORT", "5563")
POS_TOPIC = os.environ.get("IE_INPUT_TOPIC", "bustech")
PRED_PORT = os.environ.get("OBA_Q_OUT", "5568")
PRED_TOPIC = "time"
HTTP_PORT = int(os.environ.get("VIEWER_PORT", "8090"))
MAPBOX_TOKEN = os.environ.get("MAPBOX_TOKEN", "").strip()
GTFS_ZIP = os.environ.get("OBA_GTFS_ZIP", "").strip()
STALE_S = 240

_lock = threading.Lock()
_pos = {}    # vehicleId -> {lat, lon, dsc, bearing, ts}
_pred = {}   # vehicleId -> {route, trip, stops:[{stop,arr,dep}], ts}

STOPS = {}   # bare stop_id  -> {name, lat, lon}
ROUTES = {}  # bare route_id -> {short, long}


def load_gtfs():
    if not GTFS_ZIP or not os.path.exists(GTFS_ZIP):
        print("[viewer] no GTFS zip (set OBA_GTFS_ZIP) — showing raw stop/route ids", flush=True)
        return
    try:
        with zipfile.ZipFile(GTFS_ZIP) as z:
            with z.open("stops.txt") as f:
                for r in csv.DictReader(io.TextIOWrapper(f, "utf-8-sig")):
                    sid = (r.get("stop_id") or "").strip()
                    try:
                        STOPS[sid] = {"name": (r.get("stop_name") or "").strip(),
                                      "lat": float(r["stop_lat"]), "lon": float(r["stop_lon"])}
                    except (KeyError, ValueError, TypeError):
                        pass
            with z.open("routes.txt") as f:
                for r in csv.DictReader(io.TextIOWrapper(f, "utf-8-sig")):
                    rid = (r.get("route_id") or "").strip()
                    if rid:
                        ROUTES[rid] = {"short": (r.get("route_short_name") or "").strip(),
                                       "long": (r.get("route_long_name") or "").strip()}
        print("[viewer] GTFS loaded: %d stops, %d routes from %s"
              % (len(STOPS), len(ROUTES), os.path.basename(GTFS_ZIP)), flush=True)
    except Exception as e:
        print("[viewer] GTFS load failed (%s) — showing raw ids" % e, flush=True)


def _bare(x):
    # GTFS-RT ids are agency-prefixed ("MTA NYCT_401689" -> "401689", "MTA NYCT_M15" -> "M15").
    return x.split("_", 1)[1] if "_" in x else x


def stop_info(sid):
    return STOPS.get(sid) or STOPS.get(_bare(sid)) or STOPS.get(sid.split("_")[-1])


def route_info(rid):
    return ROUTES.get(rid) or ROUTES.get(_bare(rid)) or ROUTES.get(rid.split("_")[-1])


def _norm_ms(t):
    if not t:
        return 0
    return int(t) if t > 1_000_000_000_000 else int(t) * 1000   # GTFS-RT secs -> ms


def positions_loop():
    ctx = zmq.Context.instance()
    s = ctx.socket(zmq.SUB)
    s.connect("tcp://localhost:%s" % POS_PORT)
    s.setsockopt(zmq.SUBSCRIBE, POS_TOPIC.encode())
    s.setsockopt(zmq.RCVTIMEO, 1000)
    while True:
        try:
            body = s.recv_multipart()[-1]
        except zmq.Again:
            continue
        except Exception:
            time.sleep(0.5)
            continue
        try:
            ccr = json.loads(body)["RealtimeEnvelope"]["CcLocationReport"]
            v = ccr["vehicle"]
            vid = "%s_%s" % (v.get("agencydesignator"), v.get("vehicle-id"))
            with _lock:
                _pos[vid] = {"lat": ccr["latitude"] / 1e6, "lon": ccr["longitude"] / 1e6,
                             "dsc": ccr.get("destSignCode"),
                             "bearing": (ccr.get("direction") or {}).get("deg"),
                             "ts": time.time()}
        except Exception:
            pass


def predictions_loop():
    if gtfsrt is None:
        print("[viewer] gtfs-realtime-bindings missing; predictions disabled "
              "(pip install --user gtfs-realtime-bindings)", flush=True)
        return
    ctx = zmq.Context.instance()
    s = ctx.socket(zmq.SUB)
    s.connect("tcp://localhost:%s" % PRED_PORT)
    s.setsockopt(zmq.SUBSCRIBE, PRED_TOPIC.encode())
    s.setsockopt(zmq.RCVTIMEO, 1000)
    while True:
        try:
            body = s.recv_multipart()[-1]
        except zmq.Again:
            continue
        except Exception:
            time.sleep(0.5)
            continue
        try:
            fm = gtfsrt.FeedMessage()
            fm.ParseFromString(body)
            for e in fm.entity:
                if not e.HasField("trip_update"):
                    continue
                tu = e.trip_update
                vid = tu.vehicle.id or tu.trip.trip_id
                stops = []
                for stu in tu.stop_time_update:
                    stops.append({"stop": stu.stop_id,
                                  "arr": _norm_ms(stu.arrival.time if stu.HasField("arrival") else 0),
                                  "dep": _norm_ms(stu.departure.time if stu.HasField("departure") else 0)})
                with _lock:
                    _pred[vid] = {"route": tu.trip.route_id, "trip": tu.trip.trip_id,
                                  "stops": stops, "ts": time.time()}
        except Exception:
            pass


def snapshot():
    now = time.time()
    with _lock:
        for d in (_pos, _pred):
            for k in [k for k, v in d.items() if now - v["ts"] > STALE_S]:
                del d[k]
        ids = set(_pos) | set(_pred)
        out = []
        for vid in ids:
            p = _pos.get(vid)
            pr = _pred.get(vid)
            rid = (pr or {}).get("route") or ""
            ri = route_info(rid) if rid else None
            route = (ri or {}).get("short") or (rid.split("_")[-1] if rid else None) \
                or ("DSC " + str(p["dsc"]) if p else "")
            stops = []
            for s in (pr or {}).get("stops", []):
                si = stop_info(s["stop"])
                stops.append({"stop": s["stop"], "arr": s["arr"], "dep": s["dep"],
                              "name": (si or {}).get("name") or s["stop"].split("_")[-1],
                              "lat": (si or {}).get("lat"), "lon": (si or {}).get("lon")})
            out.append({
                "id": vid, "route": route, "routeLong": (ri or {}).get("long", ""),
                "lat": p["lat"] if p else None, "lon": p["lon"] if p else None,
                "bearing": p.get("bearing") if p else None,
                "trip": (pr or {}).get("trip", ""), "stops": stops,
                "posAge": round(now - p["ts"], 1) if p else None,
                "predAge": round(now - pr["ts"], 1) if pr else None,
            })
    out.sort(key=lambda r: (r["route"], r["id"]))
    return {"now": int(now * 1000), "vehicles": out,
            "counts": {"positions": len(_pos), "predictions": len(_pred),
                       "stops": len(STOPS), "routes": len(ROUTES)}}


PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><title>OBA-NYC GTFS-RT viewer</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
 html,body{margin:0;height:100%;font:13px/1.4 system-ui,sans-serif;background:#111;color:#eee}
 #wrap{display:flex;height:100%}#map{flex:1}#side{width:360px;overflow:auto;background:#181818;padding:8px}
 h1{font-size:14px;margin:4px 4px 2px}#stat{color:#9ad;margin:0 4px 8px;font-size:12px}
 .v{border:1px solid #333;border-radius:6px;padding:6px 8px;margin:0 0 6px;cursor:pointer}
 .v:hover{border-color:#777}.v.sel{border-color:#9cf;background:#1d2733}
 .v .r{font-weight:700}.v .rl{color:#9bb;font-weight:400;font-size:11px}.v .id{color:#888;font-size:11px;float:right}
 .stp{display:flex;justify-content:space-between;color:#cdd;font-size:11px;gap:8px}
 .stp .nm{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.stp .t{color:#7e7;white-space:nowrap}
 .badge{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px;vertical-align:middle}
 .muted{color:#888}
</style></head><body><div id="wrap">
<div id="map"></div>
<div id="side"><h1>GTFS-RT predictions</h1><div id="stat">connecting…</div><div id="list"></div></div>
</div><script>
var TILE_URL=__TILE_URL__, TILE_ATTR=__TILE_ATTR__, TILE_OPTS=__TILE_OPTS__;
var map=L.map('map',{zoomControl:true}).setView([40.78,-73.96],12);
L.tileLayer(TILE_URL,Object.assign({attribution:TILE_ATTR,maxZoom:19},TILE_OPTS)).addTo(map);
var markers={}, last={}, selected=null;
var stopLayer=L.layerGroup().addTo(map);
function color(r){var p=['#ff5252','#40c4ff','#ffd740','#69f0ae','#e040fb','#ffab40','#18ffff','#b388ff','#ff8a80','#84ffff'];
 var h=0;r=r||'';for(var i=0;i<r.length;i++)h=(h*31+r.charCodeAt(i))>>>0;return p[h%p.length];}
function cd(ms,now){if(!ms)return '';var s=Math.round((ms-now)/1000);if(s<0)return s>-60?'due':Math.round(-s/60)+'m ago';
 return s<60?s+'s':Math.floor(s/60)+'m'+(s%60?' '+(s%60)+'s':'');}
function clock(ms){if(!ms)return '';var d=new Date(ms);return d.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});}
function drawStops(v){stopLayer.clearLayers();var pts=[];
 v.stops.forEach(function(s,i){ if(s.lat==null)return; pts.push([s.lat,s.lon]);
   stopLayer.addLayer(L.circleMarker([s.lat,s.lon],{radius:i===0?6:4,weight:1,color:'#fff',
     fillColor:i===0?'#fff':'#9cf',fillOpacity:0.9}).bindTooltip(
     '<b>'+s.name+'</b><br>'+clock(s.arr)+(i===0?' (next)':''),{direction:'top'}));});
 if(pts.length>1)stopLayer.addLayer(L.polyline(pts,{color:'#9cf',weight:2,opacity:0.6,dashArray:'4'}));}
function popup(v,now){var s=v.stops.slice(0,8).map(function(x){return x.name+' &middot; '+(cd(x.arr,now)||clock(x.arr));}).join('<br>');
 return '<b>'+(v.route||'?')+'</b> '+(v.routeLong||'')+'<br><span style="color:#888">'+v.id+'</span><br>'+(s||'<i>no prediction</i>');}
function select(id){selected=id;var v=last[id];if(!v)return;
 if(markers[id]){map.setView(markers[id].getLatLng(),14);markers[id].openPopup();}
 drawStops(v);render();}
function row(v,now){
 var stops=v.stops.slice(0,5).map(function(s){return '<div class="stp"><span class="nm">'+s.name+'</span><span class="t">'+(cd(s.arr,now)||clock(s.arr))+'</span></div>';}).join('');
 if(!stops)stops='<div class="muted">no prediction yet</div>';
 return '<div class="v'+(v.id===selected?' sel':'')+'" data-id="'+v.id+'"><div><span class="id">'+v.id+'</span>'+
  '<span class="badge" style="background:'+color(v.route)+'"></span><span class="r">'+(v.route||'?')+'</span> '+
  '<span class="rl">'+(v.routeLong||'')+'</span></div>'+stops+'</div>';}
function render(){var now=Date.now(),list=document.getElementById('list');
 var vs=Object.keys(last).map(function(k){return last[k];}).sort(function(a,b){return (a.route+a.id).localeCompare(b.route+b.id);});
 list.innerHTML=vs.map(function(v){return row(v,now);}).join('')||'<div class="muted">waiting for data… is the bridge + loop running?</div>';
 Array.prototype.forEach.call(list.querySelectorAll('.v'),function(el){el.onclick=function(){select(el.getAttribute('data-id'));};});}
function tick(){fetch('/state').then(function(r){return r.json();}).then(function(d){
 var now=d.now;document.getElementById('stat').textContent=
   d.vehicles.length+' vehicles · '+d.counts.predictions+' predicted · '+d.counts.stops+' stops, '+d.counts.routes+' routes loaded';
 last={};d.vehicles.forEach(function(v){last[v.id]=v;});
 var seen={};
 d.vehicles.forEach(function(v){ if(v.lat==null)return; seen[v.id]=1; var c=color(v.route);
   if(markers[v.id]){markers[v.id].setLatLng([v.lat,v.lon]).setPopupContent(popup(v,now));}
   else{markers[v.id]=L.circleMarker([v.lat,v.lon],{radius:7,color:'#000',weight:1,fillColor:c,fillOpacity:0.9})
     .addTo(map).bindPopup(popup(v,now)).on('click',(function(id){return function(){select(id);};})(v.id));}});
 Object.keys(markers).forEach(function(id){if(!seen[id]){map.removeLayer(markers[id]);delete markers[id];}});
 if(selected&&last[selected])drawStops(last[selected]);
 render();
}).catch(function(e){document.getElementById('stat').textContent='error: '+e;});}
tick();setInterval(tick,2500);
</script></body></html>"""


def page_html():
    if MAPBOX_TOKEN:
        url = ("https://api.mapbox.com/styles/v1/mapbox/dark-v11/tiles/256/{z}/{x}/{y}@2x"
               "?access_token=" + MAPBOX_TOKEN)
        attr = "&copy; Mapbox &copy; OpenStreetMap"
        opts = "{tileSize:256}"
    else:
        url = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        attr = "&copy; OpenStreetMap contributors"
        opts = "{}"
    return (PAGE.replace("__TILE_URL__", json.dumps(url))
                .replace("__TILE_ATTR__", json.dumps(attr))
                .replace("__TILE_OPTS__", opts))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/state"):
            self._send(200, "application/json", json.dumps(snapshot()).encode())
        elif self.path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", page_html().encode())
        else:
            self._send(404, "text/plain", b"not found")


def main():
    load_gtfs()
    threading.Thread(target=positions_loop, daemon=True).start()
    threading.Thread(target=predictions_loop, daemon=True).start()
    tiles = "Mapbox" if MAPBOX_TOKEN else "OpenStreetMap (set MAPBOX_TOKEN for Mapbox)"
    print("[viewer] positions tcp://localhost:%s/%s  predictions tcp://localhost:%s/%s  tiles=%s"
          % (POS_PORT, POS_TOPIC, PRED_PORT, PRED_TOPIC, tiles), flush=True)
    print("[viewer] open  http://localhost:%d" % HTTP_PORT, flush=True)
    ThreadingHTTPServer(("127.0.0.1", HTTP_PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()

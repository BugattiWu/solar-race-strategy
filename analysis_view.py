"""Aligned canvas profiles sharing distance, cursor and map position."""
import json
import numpy as np
import pandas as pd
import streamlit as st
from benchmark import sample_weather


def analysis_html(data,units):
    battery=data['battery'].copy()
    # Older replay histories lack irradiance. Recover it using saved location/time,
    # never using a single current-time weather slice for the whole route.
    if 'irradiance_w_m2' not in battery: battery['irradiance_w_m2']=np.nan
    for i in battery.index[battery.irradiance_w_m2.isna()]:
        try:
            stamp=pd.Timestamp(battery.at[i,'time']).timestamp()
            battery.at[i,'irradiance_w_m2']=max(0,sample_weather(data['weather'],float(battery.at[i,'end_km'])*1000,stamp)[0])
        except (ValueError,KeyError): pass
    def rows(frame): return json.loads(frame.to_json(orient='values'))
    targets=data['targets']
    payload=dict(route=rows(data['route'][['distance_m','lat','lon','elevation_m']]),battery=rows(battery[['end_km','battery_wh','irradiance_w_m2']]),targets=rows(targets[['start_km','end_km','target_kmh']]) if not targets.empty else [],capacity=data['capacity'],progress=data['progress'],first=data['start_km']*1000,last=data['end_km']*1000,df=units.distance_factor,ef=units.elevation_factor,du=units.distance,su=units.speed,eu=units.elevation)
    return TEMPLATE.replace('__DATA__',json.dumps(payload,allow_nan=False).replace('<','\\u003c'))


def render_analysis(data,units):
    st.subheader('Full race analysis · aligned profiles')
    st.iframe(analysis_html(data,units),height=1160,alt='Aligned battery, target speed, elevation and predicted solar irradiance graphs with a shared cursor and route map')
    st.caption('Hover any graph: one vertical cursor aligns all four profiles and the orange map marker. Battery uses Wh and %. Solar irradiance (W/m², not accumulated irradiation) is sampled at predicted encounter times and includes modeled clouds and solar incidence for a horizontal array. No extra cosine or cloud multiplier is applied. Hourly weather cannot resolve short cloud transients; array curvature, road pitch and shading are not yet modeled.')
    st.caption('Historical runs use reanalysis, not forecasts available on race day. Solar history is retained across replanning. CSV fields retain explicitly named km, km/h and Wh units.')
    st.download_button('Download full battery and solar data',data['battery'].to_csv(index=False),'full-race-battery-solar.csv','text/csv')
    st.download_button('Download full target profile',data['targets'].to_csv(index=False),'full-race-targets.csv','text/csv')


TEMPLATE='''<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"><script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script><style>
body{margin:0;background:#131c25;color:#dce7f0;font:13px system-ui}canvas{display:block;width:100%;height:180px;touch-action:none}#map{height:330px}#readout{min-height:42px;padding:10px;font-variant-numeric:tabular-nums} .leaflet-tile-pane{filter:invert(1) hue-rotate(180deg) brightness(.75)}
</style></head><body><div id="readout">Hover a profile to align the cursor across all graphs and the map.</div><canvas id="battery" aria-label="Battery energy and SOC"></canvas><canvas id="speed" aria-label="Target speed"></canvas><canvas id="elevation" aria-label="Elevation"></canvas><canvas id="solar" aria-label="Predicted solar irradiance at encounter time"></canvas><div id="map"></div><script>
const d=__DATA__; let cursor=null,map=null,marker=null; const LFT=70,RGT=70,H=180;
const configs=[['battery','Battery · Wh / %','#00c4aa',d.battery.map(p=>[p[0]*1000,p[1]]),0,d.capacity],['speed','Target speed · '+d.su,'#efad45',d.targets.flatMap(p=>[[p[0]*1000,p[2]*d.df],[p[1]*1000,p[2]*d.df]]),0,null],['elevation','Elevation · '+d.eu,'#9dbdd6',d.route.map(p=>[p[0],p[3]*d.ef]),null,null],['solar','Predicted solar irradiance · W/m²','#edda67',d.battery.map(p=>[p[0]*1000,p[2]]),0,null]];
function at(points,x){if(!points.length)return null;let lo=0,hi=points.length-1;while(lo<hi){let mid=Math.floor((lo+hi)/2);if(points[mid][0]<x)lo=mid+1;else hi=mid;}let b=points[lo],a=points[Math.max(0,lo-1)];if(a[1]===null||b[1]===null)return null;let f=b[0]===a[0]?1:Math.max(0,Math.min(1,(x-a[0])/(b[0]-a[0])));return a[1]+(b[1]-a[1])*f;}
function routeLocation(x){return [at(d.route.map(p=>[p[0],p[1]]),x),at(d.route.map(p=>[p[0],p[2]]),x)];}
function draw(){for(const [id,title,color,pts,min,max] of configs){const c=document.getElementById(id),w=c.clientWidth,ctx=c.getContext('2d'),ratio=window.devicePixelRatio||1;c.width=w*ratio;c.height=H*ratio;ctx.setTransform(ratio,0,0,ratio,0,0);let vals=pts.filter(p=>p[1]!==null).map(p=>p[1]);let low=min===null?Math.min(...vals)-5:min,high=max===null?Math.max(low+1,...vals)*1.05:max;if(high<=low)high=low+1;const x=v=>LFT+(w-LFT-RGT)*(v-d.first)/(d.last-d.first),y=v=>H-30-(H-64)*(v-low)/(high-low);ctx.fillStyle='#dce7f0';ctx.font='13px system-ui';ctx.fillText(title,LFT,18);ctx.font='11px system-ui';for(let i=0;i<=3;i++){let val=low+(high-low)*i/3;ctx.strokeStyle='#344452';ctx.beginPath();ctx.moveTo(LFT,y(val));ctx.lineTo(w-RGT,y(val));ctx.stroke();ctx.fillStyle='#bdcedc';ctx.fillText(val.toFixed(0),5,y(val)+4);if(id==='battery')ctx.fillText((100*val/d.capacity).toFixed(0)+'%',w-RGT+9,y(val)+4);}for(let i=0;i<5;i++){let v=d.first+(d.last-d.first)*i/4;ctx.fillText((v/1000*d.df).toFixed(1),x(v)-12,H-10);}ctx.fillText(d.du,w-30,H-10);ctx.save();ctx.beginPath();ctx.rect(LFT,28,w-LFT-RGT,H-58);ctx.clip();ctx.strokeStyle=color;ctx.lineWidth=1.7;ctx.beginPath();let active=false;for(const p of pts){if(p[1]===null){active=false;continue;}if(active)ctx.lineTo(x(p[0]),y(p[1]));else ctx.moveTo(x(p[0]),y(p[1]));active=true;}ctx.stroke();ctx.setLineDash([3,4]);ctx.strokeStyle='#00c4aa';ctx.beginPath();ctx.moveTo(x(d.progress),28);ctx.lineTo(x(d.progress),H-30);ctx.stroke();ctx.setLineDash([]);if(cursor!==null){ctx.strokeStyle='#ff9850';ctx.beginPath();ctx.moveTo(x(cursor),28);ctx.lineTo(x(cursor),H-30);ctx.stroke();}ctx.restore();}}
function inspect(x){cursor=Math.max(d.first,Math.min(d.last,x));const values=configs.map(c=>at(c[3],cursor)),v=(n,digits=1)=>n===null?'unknown':n.toFixed(digits);document.getElementById('readout').textContent=`${(cursor/1000*d.df).toFixed(2)} ${d.du} · ${v(values[0],0)} Wh / ${v(values[0]===null?null:100*values[0]/d.capacity)}% · ${v(values[1])} ${d.su} · ${v(values[2])} ${d.eu} · ${v(values[3],0)} W/m²`;if(marker){marker.setLatLng(routeLocation(cursor));if(!map.hasLayer(marker))marker.addTo(map);}draw();}
for(const [id] of configs){let c=document.getElementById(id);c.addEventListener('pointermove',e=>{let r=c.getBoundingClientRect();inspect(d.first+(e.clientX-r.left-LFT)/(r.width-LFT-RGT)*(d.last-d.first));});c.addEventListener('pointerleave',()=>{cursor=null;if(marker)map.removeLayer(marker);draw();});}
if(typeof L!=='undefined'){map=L.map('map',{scrollWheelZoom:true});L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{attribution:'© OpenStreetMap contributors',maxZoom:19}).addTo(map);let line=L.polyline(d.route.map(p=>[p[1],p[2]]),{color:'#d99735',weight:3}).addTo(map);map.fitBounds(line.getBounds());L.circleMarker(routeLocation(d.progress),{radius:6,color:'white',fillColor:'#00c4aa',fillOpacity:1}).addTo(map);marker=L.circleMarker(routeLocation(d.progress),{radius:6,color:'white',fillColor:'#ff9850',fillOpacity:1});map.on('mousemove',e=>{let p=d.route.reduce((a,b)=>Math.hypot(b[1]-e.latlng.lat,(b[2]-e.latlng.lng)*Math.cos(e.latlng.lat*Math.PI/180))<Math.hypot(a[1]-e.latlng.lat,(a[2]-e.latlng.lng)*Math.cos(e.latlng.lat*Math.PI/180))?b:a);inspect(p[0]);});}else document.getElementById('map').textContent='Map library unavailable; graphs remain usable.';
window.addEventListener('resize',draw);draw();</script></body></html>'''

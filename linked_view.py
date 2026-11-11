"""Linked map/elevation browser view with local hover synchronization."""
import json
import numpy as np
from replay import cloud_samples


def road_grade(route, distance):
    index = int(np.clip(np.searchsorted(route.distance_m,distance,side='right')-1,0,len(route)-2))
    a,b = route.iloc[index],route.iloc[index+1]
    return float(100*(b.elevation_m-a.elevation_m)/(b.distance_m-a.distance_m))


def linked_html(route, progress, weather, timestamp, clouds=True, wind=False, theme="dark", controls=None, imperial=False):
    records = route[['distance_m','lat','lon','elevation_m']].values.tolist()
    samples = cloud_samples(route,weather,timestamp) if clouds or wind else []
    payload = json.dumps(dict(route=records,progress=progress,samples=samples,clouds=clouds,wind=wind,theme=theme,controls=controls or [])).replace('<','\\u003c')
    html = TEMPLATE.replace('__DATA__',payload)
    if imperial:
        html = html.replace('(d/1000).toFixed(1)','(d/1609.344).toFixed(1)').replace('(hovered/1000).toFixed(2)','(hovered/1609.344).toFixed(2)')
        html = html.replace("e.toFixed(0)+' m'","(e*3.280839895).toFixed(0)+' ft'").replace("ctx.fillText('km'","ctx.fillText('mi'")
        html = html.replace('Cursor at ${','Cursor at ${').replace('} km · elevation ${p[3].toFixed(1)} m','} mi · elevation ${(p[3]*3.280839895).toFixed(1)} ft')
        html = html.replace('${sample.wind_m_s.toFixed(1)} m/s','${(sample.wind_m_s*2.236936292).toFixed(1)} mph').replace('km ${control.route_km.toFixed(2)}','mi ${(control.route_km/1.609344).toFixed(2)}')
    return html


TEMPLATE = '''<!DOCTYPE html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
body{margin:0;font:14px system-ui;color:#22313e;background:white}#map{height:380px;border-radius:10px}#elevation{width:100%;height:190px;display:block;touch-action:none}#readout{padding:8px;min-height:20px;font-variant-numeric:tabular-nums}.wind{color:#0878c9;font-size:25px;text-shadow:0 0 2px white}.wind span{display:block;transform-origin:50% 50%}.legend{padding:6px;font-size:12px;color:#4f5f6f}.error{padding:12px;color:#a22}
body.dark{background:#131c25;color:#dce7f0}body.dark .legend{color:#bbc8d5}body.dark .leaflet-tile-pane{filter:invert(100%) hue-rotate(180deg) brightness(.75) contrast(.95)}body.dark #map{background:#15202b}body.dark .wind{color:#43bcff;text-shadow:0 0 2px black}
</style></head><body><div id="map"></div><div class="legend">Teal dot = car · orange dot = cursor · wind arrows point toward flow direction</div><canvas id="elevation" aria-label="Elevation profile; move the cursor to inspect corresponding map location"></canvas><div id="readout"></div>
<script>
const data=__DATA__, points=data.route;
if(data.theme==='dark')document.body.classList.add('dark');
let map, hoverMarker, hovered=null;
const canvas=document.getElementById('elevation'),ctx=canvas.getContext('2d');
const first=points[0][0],last=points[points.length-1][0];
const low=Math.min(...points.map(p=>p[3]))-10,high=Math.max(...points.map(p=>p[3]))+10;
function locate(distance){let lo=0,hi=points.length-1;while(lo<hi){const mid=Math.floor((lo+hi)/2);if(points[mid][0]<distance)lo=mid+1;else hi=mid;}return Math.max(0,Math.min(points.length-2,lo-1));}
function at(distance){const i=locate(distance),a=points[i],b=points[i+1],f=Math.max(0,Math.min(1,(distance-a[0])/(b[0]-a[0])));return [distance,a[1]+f*(b[1]-a[1]),a[2]+f*(b[2]-a[2]),a[3]+f*(b[3]-a[3])];}
function grade(distance){const i=locate(distance);return 100*(points[i+1][3]-points[i][3])/(points[i+1][0]-points[i][0]);}
function draw(){const w=canvas.clientWidth,h=190,dpr=window.devicePixelRatio||1;canvas.width=w*dpr;canvas.height=h*dpr;ctx.setTransform(dpr,0,0,dpr,0,0);const x=d=>42+(w-58)*(d-first)/(last-first),y=e=>h-30-(h-50)*(e-low)/(high-low);ctx.clearRect(0,0,w,h);ctx.strokeStyle=data.theme==='dark'?'#354350':'#dce4eb';ctx.fillStyle=data.theme==='dark'?'#bdcedc':'#526273';ctx.font='11px system-ui';for(let i=0;i<4;i++){const e=low+(high-low)*i/3;ctx.beginPath();ctx.moveTo(42,y(e));ctx.lineTo(w-16,y(e));ctx.stroke();ctx.fillText(e.toFixed(0)+' m',0,y(e)+3);}ctx.beginPath();points.forEach((p,i)=>i?ctx.lineTo(x(p[0]),y(p[3])):ctx.moveTo(x(p[0]),y(p[3])));ctx.strokeStyle=data.theme==='dark'?'#9dbdd6':'#406b8c';ctx.lineWidth=2;ctx.stroke();for(let i=0;i<5;i++){const d=first+(last-first)*i/4;ctx.fillText((d/1000).toFixed(1),Math.min(w-34,x(d)-10),h-9);}ctx.fillText('km',w-19,h-9);const markers=[[data.progress,'#00a898']];if(hovered!==null)markers.push([hovered,'#ef8e28']);for(const [distance,color] of markers){const p=at(distance);ctx.beginPath();ctx.arc(x(distance),y(p[3]),6,0,Math.PI*2);ctx.fillStyle=color;ctx.fill();ctx.strokeStyle='white';ctx.stroke();}}
function inspect(distance){hovered=Math.max(first,Math.min(last,distance));const p=at(hovered);document.getElementById('readout').textContent=`Cursor at ${(hovered/1000).toFixed(2)} km · elevation ${p[3].toFixed(1)} m · grade ${grade(hovered).toFixed(2)}%`;if(map){hoverMarker.setLatLng([p[1],p[2]]);if(!map.hasLayer(hoverMarker))hoverMarker.addTo(map);}draw();}
canvas.addEventListener('pointermove',event=>{const box=canvas.getBoundingClientRect();inspect(first+(event.clientX-box.left-42)/(box.width-58)*(last-first));});
canvas.addEventListener('pointerleave',()=>{hovered=null;if(map)map.removeLayer(hoverMarker);document.getElementById('readout').textContent=`Current road grade ${grade(data.progress).toFixed(2)}%`;draw();});
window.addEventListener('resize',draw);
if(typeof L==='undefined'){document.getElementById('map').innerHTML='<div class="error">Map library could not load. Check the internet connection.</div>';}else{
map=L.map('map',{scrollWheelZoom:true});L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{attribution:'© OpenStreetMap contributors',maxZoom:19}).addTo(map);
const line=L.polyline(points.map(p=>[p[1],p[2]]),{color:'#ca8b28',weight:3}).addTo(map);map.fitBounds(line.getBounds(),{padding:[15,15]});
for(const sample of data.samples){if(data.clouds)L.circle([sample.lat,sample.lon],{radius:10000,stroke:false,fillColor:data.theme==='dark'?'#d5e2ed':'#61768c',fillOpacity:.08+.65*sample.cloud_percent/100}).bindTooltip(`${sample.cloud_percent.toFixed(0)}% cloud cover`).addTo(map);
if(data.wind){const from=(sample.wind_toward_deg+180)%360;L.marker([sample.lat,sample.lon],{icon:L.divIcon({className:'wind',html:`<span style="transform:rotate(${sample.wind_toward_deg}deg)">↑</span>`,iconSize:[26,30],iconAnchor:[13,15]})}).bindTooltip(`${sample.wind_m_s.toFixed(1)} m/s · from ${from.toFixed(0)}°`).addTo(map);}}
for(const control of data.controls){if(control.route_km*1000>last)continue;L.circleMarker([control.lat,control.lon],{radius:5,color:control.kind==='stop'?'#ec4e43':'#efaa35',fillOpacity:.9}).bindTooltip(`${control.kind==='stop'?'Stop candidate':'Signal candidate'} · km ${control.route_km.toFixed(2)} · approach unverified`).addTo(map);}
const car=at(data.progress);L.circleMarker([car[1],car[2]],{radius:7,color:'white',weight:2,fillColor:'#00a898',fillOpacity:1}).bindTooltip('Current car position').addTo(map);
hoverMarker=L.circleMarker([car[1],car[2]],{radius:7,color:'white',weight:2,fillColor:'#ef8e28',fillOpacity:1});
map.on('mousemove',event=>{let nearest=points[0],best=Infinity;for(const p of points){const delta=(p[1]-event.latlng.lat)**2+((p[2]-event.latlng.lng)*Math.cos(event.latlng.lat*Math.PI/180))**2;if(delta<best){best=delta;nearest=p;}}inspect(nearest[0]);});
}
document.getElementById('readout').textContent=`Current road grade ${grade(data.progress).toFixed(2)}%`;
draw();
</script></body></html>'''

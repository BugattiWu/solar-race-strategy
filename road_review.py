"""Conservative geometric matching for reviewing OSM corridor data.

Matches are candidates, not verified regulatory instructions. Intersection
approaches, conditional limits and parallel roads need review.
"""
from collections import defaultdict
import math
import re
import numpy as np
import pandas as pd


def parse_speed(value):
    match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(mph|km/h|kph)?\s*',value or '',re.I)
    if not match:
        return None
    return float(match[1])*(1.609344 if (match[2] or '').lower()=='mph' else 1)


def review_roads(route,payload):
    latitude = float(route.lat.mean())
    scale = math.cos(math.radians(latitude))
    def xy(lat,lon):
        return np.array([lon*111195*scale,lat*111195])
    route_xy = np.column_stack((route.lon*111195*scale,route.lat*111195))
    route_edges = np.diff(route_xy,axis=0)
    distances = route.distance_m.to_numpy()
    segments = []
    grid = defaultdict(list)
    for element in payload['elements']:
        if element['type']!='way':
            continue
        geometry=element.get('geometry',[])
        for first,last in zip(geometry,geometry[1:]):
            a,b=xy(first['lat'],first['lon']),xy(last['lat'],last['lon'])
            if np.linalg.norm(b-a)<.1:
                continue
            index=len(segments)
            segments.append((a,b,element))
            lower=np.floor(np.minimum(a,b)/100).astype(int)
            upper=np.floor(np.maximum(a,b)/100).astype(int)
            for x in range(lower[0],upper[0]+1):
                for y in range(lower[1],upper[1]+1):
                    grid[x,y].append(index)
    rows=[]
    for i,edge in enumerate(route_edges):
        point=(route_xy[i]+route_xy[i+1])/2
        cell=np.floor(point/100).astype(int)
        candidates=set()
        for x in range(cell[0]-1,cell[0]+2):
            for y in range(cell[1]-1,cell[1]+2):
                candidates.update(grid[x,y])
        matched=[]
        for index in candidates:
            a,b,element=segments[index]
            way_edge=b-a
            fraction=np.clip(np.dot(point-a,way_edge)/np.dot(way_edge,way_edge),0,1)
            offset=float(np.linalg.norm(point-a-fraction*way_edge))
            alignment=float(np.dot(edge,way_edge)/(np.linalg.norm(edge)*np.linalg.norm(way_edge)))
            if offset<=25 and abs(alignment)>=math.cos(math.radians(25)):
                matched.append((offset+10*(1-abs(alignment)),offset,alignment,element))
        row=dict(start_km=distances[i]/1000,end_km=distances[i+1]/1000,speed_kmh=None,raw_limit='',osm_way=None,road='',status='unknown',source_url='',edited_utc='')
        if matched:
            matched.sort(key=lambda entry:entry[0])
            _,offset,alignment,element=matched[0]
            tags=element['tags']
            raw=tags.get('maxspeed:forward' if alignment>0 else 'maxspeed:backward',tags.get('maxspeed',''))
            conditional=any('conditional' in key for key in tags if key.startswith('maxspeed'))
            ambiguous=any(item[3]['id']!=element['id'] and item[0]-matched[0][0]<5 for item in matched[1:])
            status='candidate'
            if conditional:
                status='conditional — review'
            elif ambiguous:
                status='ambiguous match — review'
            elif parse_speed(raw) is None:
                status='unparsed limit — review'
            row.update(speed_kmh=parse_speed(raw),raw_limit=raw,osm_way=element['id'],road=tags.get('name',tags.get('ref','')),status=status,
                       source_url=f"https://www.openstreetmap.org/way/{element['id']}",edited_utc=element.get('timestamp',''))
        rows.append(row)
    limits=pd.DataFrame(rows)
    # Merge consecutive samples with identical provenance for a readable review table.
    groups=(limits[['osm_way','raw_limit','status']].fillna('').ne(limits[['osm_way','raw_limit','status']].fillna('').shift()).any(axis=1)).cumsum()
    merged=[]
    for _,group in limits.groupby(groups):
        row=group.iloc[0].to_dict()
        row['end_km']=group.end_km.iloc[-1]
        merged.append(row)
    controls=[]
    lengths_squared=np.sum(route_edges**2,axis=1)
    for element in payload['elements']:
        if element['type']!='node':
            continue
        point=xy(element['lat'],element['lon'])
        fraction=np.clip(np.sum((point-route_xy[:-1])*route_edges,axis=1)/lengths_squared,0,1)
        offsets=np.linalg.norm(point-route_xy[:-1]-route_edges*fraction[:,None],axis=1)
        index=int(np.argmin(offsets))
        if offsets[index]>30:
            continue
        tags=element.get('tags',{})
        controls.append(dict(route_km=float((distances[index]+fraction[index]*(distances[index+1]-distances[index]))/1000),
                             kind=tags.get('highway'),direction=tags.get('direction',tags.get('traffic_signals:direction','unspecified')),
                             offset_m=round(float(offsets[index]),1),lat=element['lat'],lon=element['lon'],
                             status='candidate — approach needs review',source_url=f"https://www.openstreetmap.org/node/{element['id']}",edited_utc=element.get('timestamp','')))
    features=pd.DataFrame(controls,columns=['route_km','kind','direction','offset_m','lat','lon','status','source_url','edited_utc'])
    if not features.empty:
        features=features.sort_values('route_km')
    return pd.DataFrame(merged),features


if __name__=='__main__':
    import json
    from pathlib import Path
    from benchmark import load_gpx
    root=Path(__file__).resolve().parent
    payload=json.loads((root/'road_data_osm.json').read_text())
    limits,controls=review_roads(load_gpx(root/'Seg1B.gpx'),payload)
    limits.to_csv(root/'speed_limits_review.csv',index=False)
    controls.to_csv(root/'traffic_controls_review.csv',index=False)
    known=limits[limits.speed_kmh.notna()]
    print(f'Numeric-limit candidate coverage: {(known.end_km-known.start_km).sum():.1f} km')
    print(controls.kind.value_counts().to_dict() if not controls.empty else {})

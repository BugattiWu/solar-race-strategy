"""Download review data in cached 50 km route sections from public Overpass."""
from datetime import datetime, timezone
from pathlib import Path
import json
import requests
from benchmark import load_gpx


def fetch(endpoint='https://overpass-api.de/api/interpreter'):
    root=Path(__file__).resolve().parent
    route=load_gpx(root/'Seg1B.gpx')
    cache=root/'road_cache'
    cache.mkdir(exist_ok=True)
    elements={}
    failed=[]
    consecutive_failures=0
    sections=list(range(0,len(route)-1,500))
    for number,begin in enumerate(sections):
        file=cache/f'section_{number:02d}.json'
        if file.exists():
            payload=json.loads(file.read_text())
        else:
            points=route.iloc[begin:min(begin+501,len(route))]
            bounds=f'{points.lat.min()-.002},{points.lon.min()-.002},{points.lat.max()+.002},{points.lon.max()+.002}'
            query=f'[out:json][timeout:25];(way["highway"][~"^maxspeed"~"."]({bounds});node["highway"~"^(traffic_signals|stop)$"]({bounds}););out meta geom;'
            try:
                response=requests.get(endpoint,params={'data':query},headers={'User-Agent':'SolarRaceStrategyResearch/0.1 (local route study)'},timeout=35)
                response.raise_for_status()
                payload=response.json()
                if payload.get('remark'):
                    raise ValueError(payload['remark'])
                file.write_text(json.dumps(payload))
            except (requests.RequestException,ValueError) as error:
                failed.append(number)
                consecutive_failures+=1
                print(f'Section {number} unavailable: {error}',flush=True)
                if consecutive_failures>=2:
                    failed.extend(range(number+1,len(sections)))
                    break
                continue
        consecutive_failures=0
        for feature in payload['elements']:
            elements[feature['type'],feature['id']]=feature
        print(f'Section {number+1}/{len(sections)} loaded',flush=True)
    result=dict(elements=list(elements.values()),retrieved_utc=datetime.now(timezone.utc).isoformat(),
                source=endpoint,missing_sections=failed,complete=not failed)
    (root/'road_data_osm.json').write_text(json.dumps(result))
    print(f'Saved {len(elements)} features. Missing 50 km sections: {failed}',flush=True)

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--endpoint',default='https://overpass-api.de/api/interpreter')
    fetch(parser.parse_args().endpoint)

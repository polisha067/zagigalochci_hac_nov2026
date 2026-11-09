"""Package the reviewed observation DB, without copying auth or runtime databases."""
import hashlib
import json
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[2]

def build(database):
    connection = sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro', uri=True)
    try:
        cities = [dict(zip(('id','name','region','latitude','longitude'), row)) for row in connection.execute(
            'SELECT oktmo,name,region,latitude,longitude FROM city_observations WHERE source_id=? ORDER BY oktmo', ('source-01',))]
        # Preserve null rows: missing observations must never become synthetic connections.
        rows = [list(row) for row in connection.execute(
            'SELECT from_oktmo,to_oktmo,mode,distance_km,estimated_hours,missing_reason FROM connectivity_observations WHERE source_id=? ORDER BY from_oktmo,to_oktmo,mode', ('source-01',))]
        metadata = json.loads(connection.execute('SELECT metadata_json FROM observation_sources WHERE id=?', ('source-01',)).fetchone()[0])
    finally:
        connection.close()
    return {'id':'russia-connectivity-2026-v1','source':{'id':'source-01','url':'https://tochno.st/datasets/intercity_connectivity','roles':['connectivity_observation'],'metadata':metadata},
            'columns':['origin','destination','mode','distance_km','estimated_hours','missing_reason'], 'settlements':cities,'connections':rows}

if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--database', type=Path, default=ROOT/'data/network-observations.sqlite3')
    args=parser.parse_args();data=build(args.database)
    target=Path(__file__).with_name('connectivity.json');target.write_text(json.dumps(data,ensure_ascii=False,separators=(',',':'))+'\n')
    print({'settlements':len(data['settlements']),'observations':len(data['connections']),'sha256':hashlib.sha256(target.read_bytes()).hexdigest()})

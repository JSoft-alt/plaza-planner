"""Search complete regional OSM extracts locally; never depend on public query slots.

Reuse road PBFs, download only missing covering regions, and cache area features.
One reader at a time bounds memory and coalesces simultaneous repeated searches.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from threading import Lock
import time
from urllib.parse import urlsplit

import osmium
import requests
from shapely import from_wkb
from shapely.geometry import Point, Polygon, box, mapping, shape
from shapely.ops import transform, unary_union
from shapely.strtree import STRtree

import store
import estimates

CATALOG = 'https://download.geofabrik.de/index-v1.json'
USER_AGENT = 'PlazaPlanner/1.0 (+https://github.com/JSoft-alt/plaza-planner)'
_reader_lock = Lock()
_source_lock = Lock()


def check(cancelled):
    if cancelled():
        raise ValueError('搜索已取消；已下载的本机地图会保留。')


def valid_region(feature):
    url = urlsplit(feature.get('properties', {}).get('urls', {}).get('pbf', ''))
    return url.scheme == 'https' and url.hostname == 'download.geofabrik.de' and url.path.endswith('.osm.pbf')


def select_regions(catalog, required):
    """Prefer small adjoining extracts over one multi-GB parent region."""
    remaining = required
    selected = []
    candidates = sorted([(shape(f['geometry']).area, str(f['properties']['id']), f)
                         for f in catalog['features'] if valid_region(f) and shape(f['geometry']).intersects(required)],
                        key=lambda entry: (entry[0], entry[1]))
    for _, _, feature in candidates:
        polygon = shape(feature['geometry'])
        if remaining.is_empty:
            break
        if polygon.intersection(remaining).area <= 1e-12:
            continue
        selected.append(feature)
        remaining = remaining.difference(polygon)
        if len(selected) > 8:
            raise ValueError('本次搜索跨越过多地图区域，请缩小搜索范围。')
    if not remaining.is_empty and remaining.area > 1e-12:
        raise ValueError('没有覆盖完整搜索范围的区域地图，请调整范围后重试。')
    return selected


def catalog(activity):
    path = store.DATA/'routing'/'regions.json'
    if path.exists():
        saved = json.loads(path.read_text(encoding='utf-8'))
        if time.time()-path.stat().st_mtime < 30*86400:
            return saved
    activity(phase='local_catalog', provider='Geofabrik')
    try:
        response = requests.get(CATALOG, timeout=(5,30), headers={'User-Agent':USER_AGENT})
        response.raise_for_status()
        result = response.json()
        if not isinstance(result.get('features'),list):
            raise ValueError('区域地图目录无效。')
        store.atomic_json(path,result)
        return result
    except requests.RequestException:
        if path.exists():
            return json.loads(path.read_text(encoding='utf-8'))
        raise ValueError('无法下载区域地图目录，请联网后重试；本机应用仍可访问。') from None


def source_file(region, activity, cancelled):
    while not _source_lock.acquire(timeout=.2):check(cancelled)
    try:return _source_file(region,activity,cancelled)
    finally:_source_lock.release()


def _source_file(region, activity, cancelled):
    props = region['properties']
    # Only this app's own caches are read. Do not touch the older deployment.
    for root in (store.DATA/'routing',store.DATA/'osm-extracts'):
        for manifest in root.glob('*/ready.json'):
            try:saved = json.loads(manifest.read_text(encoding='utf-8'))
            except (OSError,ValueError):continue
            pbf = manifest.parent/'map.osm.pbf'
            if saved.get('region_id') == props['id'] and pbf.is_file():
                return pbf
    directory = store.DATA/'osm-extracts'/hashlib.sha256(props['id'].encode()).hexdigest()[:20]
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    pbf = directory/'map.osm.pbf'
    partial = directory/'map.osm.pbf.part'
    url = props['urls']['pbf']
    if not valid_region(region):
        raise ValueError('区域地图下载地址无效。')
    check(cancelled)
    activity(phase='local_download',provider=props['id'],bytes=0,total_bytes=None)
    with requests.get(url,stream=True,timeout=(5,30),headers={'User-Agent':USER_AGENT}) as response:
        response.raise_for_status()
        total = int(response.headers.get('Content-Length') or 0)
        maximum = int(os.environ.get('OSRM_MAX_EXTRACT_MB','2048'))*1024**2
        if maximum and total > maximum:
            raise ValueError('本区域地图超过下载上限，请缩小范围或调整 OSRM_MAX_EXTRACT_MB。')
        if total and shutil.disk_usage(directory).free < total*3+512*1024**2:
            raise ValueError('磁盘空间不足，请腾出空间后重试；已有历史不会删除。')
        count = 0; reported = 0; started = time.monotonic()
        with partial.open('wb') as output:
            for chunk in response.iter_content(1024*1024):
                check(cancelled)
                count += len(chunk)
                if maximum and count > maximum:
                    raise ValueError('区域地图超过下载上限。')
                if time.monotonic()-started > 3600:
                    raise ValueError('区域地图下载超时，可重试；已有地图保留。')
                output.write(chunk)
                if time.monotonic()-reported >= .5:
                    activity(phase='local_download',provider=props['id'],bytes=count,total_bytes=total or None,
                             eta_seconds=estimates.download_eta(count,total,time.monotonic()-started),eta_scope='下载')
                    reported = time.monotonic()
        if not count or (total and count != total):
            raise ValueError('区域地图下载不完整，请重试。')
    check(cancelled)
    partial.replace(pbf)
    store.atomic_json(directory/'ready.json',dict(region_id=props['id'],source=url,downloaded_at=time.time()))
    return pbf


def read_features(pbf, bounds, config, activity, cancelled):
    """Read relevant ways directly; assemble only tagged multipolygon relations.

    AreaManager assembles millions of unrelated closed ways before iterator
    filters run. A direct way geometry avoids that work and the second full
    business/building area pass. Relation member geometry stays exact.
    """
    import discovery
    offices=config.discovery_kind=='offices'
    keys=('office','healthcare','amenity','building','landuse') if offices else ('shop','amenity','sport','healthcare','beauty','cuisine','building','landuse')
    envelope=box(*bounds);factory=osmium.geom.WKBFactory()
    def relevant(tags):
        if offices:
            return bool(tags.get('office') or tags.get('healthcare') or tags.get('amenity') in ('hospital','clinic','dentist','doctors') or
                        tags.get('building','no') not in ('no','roof','construction','ruins') or tags.get('landuse')=='commercial')
        return bool(tags.get('shop') or tags.get('sport') or tags.get('beauty') or tags.get('cuisine') or
                    tags.get('healthcare')=='acupuncture' or tags.get('amenity') in
                    ('restaurant','cafe','fast_food','pharmacy','car_wash','food_court','ice_cream','bar','pub','library','community_centre','arts_centre','music_school','language_school','training','bank') or
                    tags.get('building') in ('retail','shopping_centre','shopping_center') or tags.get('landuse')=='retail')
    def office_business(tags):
        return bool(tags.get('office') or tags.get('healthcare') or tags.get('amenity') in ('hospital','clinic','dentist','doctors'))
    activity(phase='local_read',elements=0)
    relations=[];member_ids=set()
    for obj in osmium.FileProcessor(str(pbf),osmium.osm.RELATION).with_filter(osmium.filter.KeyFilter(*keys)):
        check(cancelled)
        if obj.tags.get('type')!='multipolygon' or not relevant(obj.tags):continue
        members=[(m.ref,m.role) for m in obj.members if m.type=='w' and m.role in ('','outer','inner')]
        relations.append((obj.id,dict(obj.tags),obj.version,members));member_ids.update(ref for ref,_ in members)
    members={};result={};seen=0;reported=time.monotonic()
    primary_keys=('office','healthcare','amenity') if offices else keys
    processor=osmium.FileProcessor(str(pbf)).with_locations('flex_mem').with_filter(osmium.filter.KeyFilter(*primary_keys))
    snapshot=processor.header.get('osmosis_replication_timestamp')
    for obj in processor:
        seen+=1
        if seen%1024==0:
            check(cancelled)
            if time.monotonic()-reported>=1:
                activity(phase='local_read',elements=seen);reported=time.monotonic()
        if obj.is_relation():continue
        member=obj.is_way() and obj.id in member_ids
        if not member and not (office_business(obj.tags) if offices else relevant(obj.tags)):continue
        if obj.is_node():
            if not obj.location.valid() or not (bounds[0]<=obj.lon<=bounds[2] and bounds[1]<=obj.lat<=bounds[3]):continue
            geom=Point(obj.lon,obj.lat);typ='node'
        elif obj.is_way():
            try:line=from_wkb(factory.create_linestring(obj))
            except (RuntimeError,osmium.InvalidLocationError):continue
            if member:members[obj.id]=list(line.coords)
            if not (office_business(obj.tags) if offices else relevant(obj.tags)):continue
            geom=Polygon(line.coords) if obj.nodes.is_closed() and len(obj.nodes)>=4 else line
            typ='way'
        else:continue
        if geom.is_empty or not geom.is_valid or not envelope.intersects(geom):continue
        result[f'{typ}:{obj.id}']=dict(type=typ,id=obj.id,tags=dict(obj.tags),version=obj.version,geojson=mapping(geom))
    del processor
    missing=member_ids-members.keys()
    if missing:
        # Only untagged relation members need this cheap reference pass. Never
        # infer a multipolygon from just the tagged members or fill its holes.
        activity(phase='local_relations',elements=len(missing))
        for obj in (osmium.FileProcessor(str(pbf)).with_locations('flex_mem')
                    .with_filter(osmium.filter.EntityFilter(osmium.osm.WAY))
                    .with_filter(osmium.filter.IdFilter(missing))):
            check(cancelled)
            try:members[obj.id]=list(from_wkb(factory.create_linestring(obj)).coords)
            except (RuntimeError,osmium.InvalidLocationError):continue
    for pid,tags,version,refs in relations:
        check(cancelled)
        if any(ref not in members for ref,_ in refs):continue
        item=dict(type='relation',id=pid,tags=tags,version=version,members=[dict(type='way',role=role or 'outer',geometry=[dict(lon=x,lat=y) for x,y in members[ref]]) for ref,role in refs])
        geom=discovery.geometry(item)
        if geom is not None and not geom.is_empty and geom.is_valid and envelope.intersects(geom):
            item.pop('members');item['geojson']=mapping(geom);result[f'relation:{pid}']=item
    if offices:
        # Dense cities have hundreds of thousands of residential footprints.
        # Match them against real business points before retaining/serializing;
        # don't turn a 10-mile medical search into a housing inventory.
        points=[discovery.geometry(item).representative_point() for item in result.values() if office_business(item['tags'])]
        tree=STRtree(points)
        def occupied(geom):
            return geom.is_valid and any(geom.covers(points[int(i)]) for i in tree.query(geom))
        result={pid:item for pid,item in result.items() if office_business(item['tags']) or occupied(discovery.geometry(item))}
        activity(phase='local_buildings',businesses=len(points),elements=0)
        seen=0;reported=time.monotonic()
        for obj in (osmium.FileProcessor(str(pbf)).with_locations('flex_mem')
                    .with_filter(osmium.filter.EntityFilter(osmium.osm.WAY))
                    .with_filter(osmium.filter.KeyFilter('building','landuse'))):
            seen+=1
            if seen%4096==0:
                check(cancelled)
                if time.monotonic()-reported>=1:
                    activity(phase='local_buildings',businesses=len(points),elements=seen);reported=time.monotonic()
            if not relevant(obj.tags) or not obj.nodes.is_closed() or len(obj.nodes)<4:continue
            try:geom=Polygon(from_wkb(factory.create_linestring(obj)).coords)
            except (RuntimeError,osmium.InvalidLocationError):continue
            if not occupied(geom):continue
            pid=f'way:{obj.id}'
            result[pid]=dict(type='way',id=obj.id,tags=dict(obj.tags),version=obj.version,geojson=mapping(geom))
    check(cancelled)
    if len(result)>100000:raise ValueError('本区域地点过多，请缩小范围。')
    activity(phase='local_filter',elements=len(result))
    return list(result.values()),snapshot


def compact_source(pbf,bounds,activity,cancelled):
    """Use osmium's native spatial extraction before Python geometry work."""
    binary=shutil.which('osmium')
    if not binary:return pbf
    fingerprint=hashlib.sha256(store.dump(dict(path=str(pbf.resolve()),size=pbf.stat().st_size,
        modified=pbf.stat().st_mtime_ns,bounds=list(bounds))).encode()).hexdigest()
    directory=store.DATA/'local-extract-cache';directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    target=directory/(fingerprint+'.osm.pbf')
    if target.exists():return target
    partial=directory/(fingerprint+'.part.osm.pbf');started=time.monotonic()
    activity(phase='local_clip',elements=0)
    with (directory/(fingerprint+'.log')).open('wb') as log:
        process=subprocess.Popen([binary,'extract','--bbox',','.join(map(str,bounds)),'--strategy','smart',
            '--output-header=osmosis_replication_timestamp!','--overwrite','--output',str(partial),str(pbf)],stdout=log,stderr=log)
        try:
            while process.poll() is None:
                check(cancelled)
                if time.monotonic()-started>300:raise ValueError('本机地图裁剪超时，请缩小范围后重试。')
                time.sleep(.1)
            if process.returncode:raise ValueError('本机地图裁剪失败，请检查磁盘及 local-extract-cache 日志。')
            check(cancelled);partial.replace(target)
        finally:
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.wait()
            partial.unlink(missing_ok=True)
    # Cache is disposable, never user history. Bound disk growth across areas.
    for old in sorted(directory.glob('*.osm.pbf'),key=lambda p:p.stat().st_mtime,reverse=True)[12:]:
        old.unlink(missing_ok=True);old.with_name(old.name.replace('.osm.pbf','.log')).unlink(missing_ok=True)
    return target


def extract(pbf, bounds, config, rules, activity, cancelled):
    stat = pbf.stat()
    common=dict(path=str(pbf.resolve()),size=stat.st_size,modified=stat.st_mtime_ns,bounds=list(bounds),kind=config.discovery_kind)
    fingerprint = hashlib.sha256(store.dump(dict(version=3,**common)).encode()).hexdigest()
    cache = store.DATA/'local-map-cache'/(fingerprint+'.json')
    available=cache
    if available.exists():
        try:
            saved = json.loads(available.read_text(encoding='utf-8'))
            if isinstance(saved['elements'],list):
                activity(phase='local_cache',provider=pbf.parent.name,elements=len(saved['elements']))
                return saved['elements'], saved.get('snapshot')
        except (OSError,ValueError,KeyError,TypeError):pass
    key=f'local:{config.discovery_kind}:{pbf.stat().st_size}'
    expected=estimates.expected(key);started=time.monotonic()
    def report(**event):
        left=expected-(time.monotonic()-started) if expected else None
        activity(**event,eta_seconds=round(left) if left and left>0 else None,eta_scope='地图处理')
    source=compact_source(pbf,bounds,report,cancelled)
    result,snapshot = read_features(source,bounds,config,report,cancelled)
    estimates.record(key,time.monotonic()-started)
    store.atomic_json(cache,dict(elements=result,snapshot=snapshot,bounds=list(bounds),kind=config.discovery_kind,version=3))
    for old in sorted(cache.parent.glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)[64:]:
        old.unlink(missing_ok=True)
    return result,snapshot


def search(config, rules, activity, cancelled, on_results=None):
    activity(phase='local_wait',elements=0)
    while not _reader_lock.acquire(timeout=.2):
        check(cancelled)
    try:
        check(cancelled)
        area, _, inverse = config.area()
        required = transform(inverse,area)
        bounds = transform(inverse,area.buffer(500)).bounds
        regions = select_regions(catalog(activity), required)
        regions.sort(key=lambda region: -shape(region['geometry']).intersection(required).area)
        elements = {}; sources=[]
        for index,region in enumerate(regions):
            check(cancelled)
            activity(phase='local_prepare',provider=region['properties']['id'],done=index,total=len(regions))
            pbf = source_file(region,activity,cancelled)
            def region_activity(**event):
                activity(**(event | {'provider':region['properties']['id']}))
            found,snapshot = extract(pbf,bounds,config,rules,region_activity,cancelled)
            sources.append(dict(id=region['properties']['id'],snapshot=snapshot))
            for item in found:
                key=f"{item['type']}:{item['id']}"
                if key not in elements or item.get('version',0) >= elements[key].get('version',0):
                    elements[key]=item
            if on_results:
                on_results(list(elements.values()),index+1,len(regions))
        return list(elements.values()),dict(mode='local-osm',regions=sources)
    finally:
        _reader_lock.release()

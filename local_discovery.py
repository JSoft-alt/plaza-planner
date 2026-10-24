"""Search complete regional OSM extracts locally; never depend on public query slots.

Reuse road PBFs, download only missing covering regions, and cache area features.
One reader at a time bounds memory and coalesces simultaneous repeated searches.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
from threading import Lock
import time
from urllib.parse import urlsplit

import osmium
import requests
from shapely import from_wkb
from shapely.geometry import Point, box, mapping, shape
from shapely.ops import transform, unary_union
from shapely.strtree import STRtree

import store

CATALOG = 'https://download.geofabrik.de/index-v1.json'
USER_AGENT = 'PlazaPlanner/1.0 (+https://github.com/JSoft-alt/plaza-planner)'
_reader_lock = Lock()


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
                    activity(phase='local_download',provider=props['id'],bytes=count,total_bytes=total or None)
                    reported = time.monotonic()
        if not count or (total and count != total):
            raise ValueError('区域地图下载不完整，请重试。')
    check(cancelled)
    partial.replace(pbf)
    store.atomic_json(directory/'ready.json',dict(region_id=props['id'],source=url,downloaded_at=time.time()))
    return pbf


def extract(pbf, bounds, config, rules, activity, cancelled):
    stat = pbf.stat()
    common=dict(path=str(pbf.resolve()),size=stat.st_size,modified=stat.st_mtime_ns,bounds=list(bounds),kind=config.discovery_kind)
    fingerprint = hashlib.sha256(store.dump(dict(version=2,**common)).encode()).hexdigest()
    cache = store.DATA/'local-map-cache'/(fingerprint+'.json')
    legacy = cache.with_name(hashlib.sha256(store.dump(dict(version=1,**common,rules=rules)).encode()).hexdigest()+'.json')
    available=cache if cache.exists() else legacy
    if available.exists():
        try:
            saved = json.loads(available.read_text(encoding='utf-8'))
            if isinstance(saved['elements'],list):
                activity(phase='local_cache',provider=pbf.parent.name,elements=len(saved['elements']))
                return saved['elements'], saved.get('snapshot')
        except (OSError,ValueError,KeyError,TypeError):pass
    offices = config.discovery_kind == 'offices'
    keys = ('office','healthcare','amenity') if offices else ('shop','amenity','sport','healthcare','beauty','cuisine')
    envelope = box(*bounds)
    factory = osmium.geom.WKBFactory()
    businesses = []
    seen = 0; reported = time.monotonic()
    activity(phase='local_companies',provider=pbf.parent.name,elements=0)
    processor = (osmium.FileProcessor(str(pbf)).with_locations('flex_mem')
                 .with_areas(osmium.filter.KeyFilter(*keys))
                 .with_filter(osmium.filter.KeyFilter(*keys)))
    snapshot = processor.header.get('osmosis_replication_timestamp')

    def decode(obj):
        if obj.is_node():
            if not obj.location.valid():return None
            return Point(obj.lon,obj.lat),'node',obj.id
        if obj.is_way():
            if obj.nodes.is_closed():return None
            return from_wkb(factory.create_linestring(obj)),'way',obj.id
        if obj.is_area():
            return from_wkb(factory.create_multipolygon(obj)),('way' if obj.from_way() else 'relation'),obj.orig_id()
        return None

    for obj in processor:
        seen += 1
        if seen % 1024 == 0:
            check(cancelled)
            if time.monotonic()-reported >= 1:
                activity(phase='local_companies',provider=pbf.parent.name,elements=seen)
                reported=time.monotonic()
        if obj.is_relation():continue
        if offices and not (obj.tags.get('office') or obj.tags.get('healthcare') or obj.tags.get('amenity') in ('hospital','clinic','dentist','doctors')):
            continue
        if not offices and not (obj.tags.get('shop') or obj.tags.get('sport') or obj.tags.get('beauty') or obj.tags.get('cuisine') or obj.tags.get('healthcare')=='acupuncture' or obj.tags.get('amenity') in ('restaurant','cafe','fast_food','pharmacy','car_wash')):
            continue
        if obj.is_node():
            if not obj.location.valid() or not (bounds[0] <= obj.lon <= bounds[2] and bounds[1] <= obj.lat <= bounds[3]):
                continue
        if obj.is_way() and obj.nodes.is_closed():continue
        tags=dict(obj.tags)
        try:decoded=decode(obj)
        except (RuntimeError,osmium.InvalidLocationError):continue
        if decoded is None:continue
        geom,typ,pid=decoded
        if geom.is_empty or not geom.is_valid or not envelope.intersects(geom):continue
        businesses.append((dict(type=typ,id=pid,tags=tags,version=obj.version,geojson=mapping(geom)),geom))
    del processor
    check(cancelled)
    kept={f"{item['type']}:{item['id']}":item for item,_ in businesses}
    if not businesses:
        store.atomic_json(cache,dict(elements=[],snapshot=snapshot))
        return [],snapshot
    points=[geom.representative_point() for _,geom in businesses]
    tree=STRtree(points)
    # A second C++-filtered pass emits only polygons. Do not retain a million
    # unrelated houses: keep a footprint only if it contains a mapped business.
    activity(phase='local_buildings',provider=pbf.parent.name,elements=0,businesses=len(businesses))
    processor=(osmium.FileProcessor(str(pbf)).with_locations('flex_mem')
        .with_areas(osmium.filter.KeyFilter('building','landuse','shop'))
        .with_filter(osmium.filter.EntityFilter(osmium.osm.AREA))
        .with_filter(osmium.filter.KeyFilter('building','landuse','shop')))
    seen=0;reported=time.monotonic()
    for obj in processor:
        seen+=1
        if seen % 4096==0:
            check(cancelled)
            if time.monotonic()-reported>=1:
                activity(phase='local_buildings',provider=pbf.parent.name,elements=seen,businesses=len(businesses))
                reported=time.monotonic()
        building=obj.tags.get('building','no')
        landuse=obj.tags.get('landuse','')
        mall=obj.tags.get('shop','')=='mall' or building in ('shopping_centre','shopping_center')
        if offices:
            if building in ('no','roof','construction','ruins') and landuse!='commercial':continue
        elif not (mall or building=='retail' or landuse=='retail'):continue
        try:geom=from_wkb(factory.create_multipolygon(obj))
        except (RuntimeError,osmium.InvalidLocationError):continue
        candidates=tree.query(geom)
        if not len(candidates):continue
        if not geom.is_valid or not any(geom.covers(points[int(i)]) for i in candidates):continue
        typ='way' if obj.from_way() else 'relation';pid=obj.orig_id()
        item=dict(type=typ,id=pid,tags=dict(obj.tags),version=obj.version,geojson=mapping(geom))
        kept[f"{typ}:{pid}"]=item
    del processor
    check(cancelled)
    activity(phase='local_filter',elements=seen,businesses=len(businesses),outlines=len(kept))
    result = list(kept.values())
    if len(result) > 100000:
        raise ValueError('本区域地点过多，请缩小范围。')
    store.atomic_json(cache,dict(elements=result,snapshot=snapshot))
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

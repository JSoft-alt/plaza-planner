"""Background regional OSRM provisioning. No public routing demo or paid API.

Only Geofabrik regional extracts are downloaded; native OSRM binaries prepare
one complete graph per selected region, with versioned caches and one worker.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import atexit
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
from threading import Event, RLock
import time
from urllib.parse import urlsplit

import requests
from shapely.geometry import MultiPoint, shape

import store

CATALOG = 'https://download.geofabrik.de/index-v1.json'
PHASES = {'queued':'等待准备路网','checking':'检查已有路网','catalog':'选择覆盖本批地点的路网',
          'download':'下载区域道路数据','extract':'提取驾驶道路','partition':'划分驾驶路网',
          'customize':'计算道路权重','starting':'启动区域路线服务','ready':'路网已就绪','failed':'路网准备失败','cancelled':'路网准备已停止'}


def enabled():
    return os.environ.get('OSRM_AUTO_DOWNLOAD', '1').lower() not in ('0','false','no')


def select_region(catalog, points):
    required = MultiPoint(points)
    choices = []
    for feature in catalog.get('features', []):
        props = feature.get('properties', {})
        url = props.get('urls', {}).get('pbf', '')
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.hostname != 'download.geofabrik.de' or not parsed.path.endswith('.osm.pbf'):
            continue
        polygon = shape(feature['geometry'])
        if polygon.covers(required):
            choices.append((polygon.area, str(props['id']), props))
    if not choices:
        raise ValueError('没有覆盖整批地点的可下载路网；请检查地点是否能在同一区域内驾车连通。')
    return min(choices, key=lambda entry: (entry[0],entry[1]))[2]


def profile_path():
    configured = os.environ.get('OSRM_CAR_PROFILE')
    candidates = [configured] if configured else ['/opt/car.lua','/opt/homebrew/share/osrm/profiles/car.lua',
                  '/usr/local/share/osrm/profiles/car.lua','/usr/share/osrm/profiles/car.lua']
    for value in candidates:
        if value and Path(value).is_file():
            return Path(value)
    raise ValueError('未找到 OSRM 驾驶配置 car.lua。请按部署文档安装 OSRM 或设置 OSRM_CAR_PROFILE。')


class Manager:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip('/')
        self.root = store.DATA / 'routing'
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.jobs = {}
        self.engines = {}
        self.lock = RLock()
        self.stopping = Event()
        self.pool = ThreadPoolExecutor(max_workers=1,thread_name_prefix='road-data')
        self.catalog = None

    def update(self, key, **changes):
        with self.lock:
            job = self.jobs[key]
            if changes.get('phase') != job.get('phase'):
                changes['phase_started_at'] = time.time()
            job.update(changes,updated_at=time.time())
            job['message'] = PHASES.get(job['phase'],job['phase'])
            # Public status has no coordinates, local paths, endpoint or raw logs.
            store.atomic_json(self.root/'last-status.json',self.public(job))

    @staticmethod
    def public(job):
        return {k:v for k,v in job.items() if k not in ('points','endpoint','process','future')}

    def request(self, points, retry=False):
        points = sorted(set((round(float(lng),7),round(float(lat),7)) for lng,lat in points))
        if not points or any(not -180<=p[0]<=180 or not -85<=p[1]<=85 for p in points):
            raise ValueError('路网准备收到无效坐标。')
        key = hashlib.sha256(store.dump(points).encode()).hexdigest()[:24]
        with self.lock:
            job = self.jobs.get(key)
            if job and job['phase']=='ready' and job['endpoint'] != self.base_url:
                entry = self.engines.get(job['endpoint'])
                if not entry or entry['process'].poll() is not None:
                    job = None  # Cached graph can restart after process exit/eviction.
            if job is None and self.catalog is not None:
                # A large new-area build must not queue trips covered by a
                # different engine that is already running.
                wanted=MultiPoint(points)
                for endpoint,engine in self.engines.items():
                    region=next((f for f in self.catalog['features'] if f['properties']['id']==engine['region']),None)
                    if engine['process'].poll() is None and region and shape(region['geometry']).covers(wanted):
                        self.jobs[key]=dict(job_id=key,phase='ready',message=PHASES['ready'],endpoint=endpoint,
                                            region=engine['region'],started_at=time.time(),updated_at=time.time(),
                                            road_data=json.loads((engine['directory']/'ready.json').read_text(encoding='utf-8')))
                        return dict(self.jobs[key])
                base_region=os.environ.get('OSRM_BASE_REGION','')
                region=next((f for f in self.catalog['features'] if f['properties']['id']==base_region),None)
                if region and shape(region['geometry']).covers(wanted) and self.base_covers(points):
                    self.jobs[key]=dict(job_id=key,phase='ready',message=PHASES['ready'],endpoint=self.base_url,
                                        region=base_region,started_at=time.time(),updated_at=time.time())
                    return dict(self.jobs[key])
            if job is None or (job['phase'] in ('failed','cancelled') and retry):
                if sum(j['phase'] not in ('ready','failed','cancelled') for j in self.jobs.values())>=4:
                    raise ValueError('已有多个路网任务，请等待当前准备完成。')
                self.jobs[key] = dict(job_id=key,phase='queued',message=PHASES['queued'],points=points,
                                      started_at=time.time(),updated_at=time.time(),phase_started_at=time.time())
                self.pool.submit(self.prepare,key,points)
            return dict(self.jobs[key])

    def status(self, key=None):
        with self.lock:
            if key:
                job=self.jobs.get(key)
            else:
                active=[j for j in self.jobs.values() if j['phase'] not in ('ready','failed','cancelled')]
                job=min(active,key=lambda j:j['started_at']) if active else max(self.jobs.values(),key=lambda j:j['started_at'],default=None)
            return self.public(job) if job else dict(phase='idle',message='新区域将在搜索或计算时自动准备路网。')

    def cancelled(self, key):
        return self.stopping.is_set() or self.jobs[key].get('cancel_requested',False)

    def cancel(self, key):
        with self.lock:
            if key in self.jobs and self.jobs[key]['phase'] not in ('ready','failed','cancelled'):
                self.jobs[key]['cancel_requested']=True
                return self.public(self.jobs[key])
        return self.status(key)

    def get_catalog(self):
        if self.catalog is not None:
            return self.catalog
        path = self.root/'regions.json'
        cached = None
        if path.exists():
            try:
                cached=json.loads(path.read_text(encoding='utf-8'))
                if time.time()-path.stat().st_mtime<30*86400:
                    self.catalog=cached
                    return cached
            except (ValueError,OSError):
                pass
        try:
            response=requests.get(CATALOG,timeout=(5,30));response.raise_for_status()
            catalog=response.json()
            if not isinstance(catalog.get('features'),list):
                raise ValueError('区域下载目录无效。')
            store.atomic_json(path,catalog)
            self.catalog=catalog
        except (requests.RequestException,ValueError):
            if cached is None:
                raise ValueError('暂时无法取得区域路网目录，请联网后重试。')
            self.catalog=cached
        return self.catalog

    def base_covers(self, points):
        """A declared region checks area coverage, not distance from an entrance."""
        region_id=os.environ.get('OSRM_BASE_REGION','')
        if region_id:
            feature=next((f for f in self.get_catalog()['features'] if f['properties']['id']==region_id),None)
            if not feature or not shape(feature['geometry']).covers(MultiPoint(points)):
                return False
            probes=points[:1]
        else:
            probes=points
        try:
            for lng,lat in probes:
                response=requests.get(f'{self.base_url}/nearest/v1/driving/{lng},{lat}',params={'number':1},timeout=(1,2))
                response.raise_for_status();result=response.json()
                waypoint=result.get('waypoints',[{}])[0]
                if result.get('code')!='Ok' or (not region_id and waypoint.get('distance',float('inf'))>100):
                    return False
            return True
        except (requests.RequestException,ValueError,IndexError,TypeError):
            return False

    def command(self, key, args, phase, directory, timeout=7200):
        self.update(key,phase=phase)
        with (directory/(phase+'.log')).open('wb') as log:
            process=subprocess.Popen(args,cwd=directory,stdout=log,stderr=subprocess.STDOUT)
            started=time.monotonic()
            try:
                while process.poll() is None:
                    if self.stopping.wait(.5) or self.cancelled(key) or time.monotonic()-started>timeout:
                        raise ValueError('路网准备已停止或超时；已下载的数据会保留，可重试。')
                if process.returncode:
                    raise ValueError(f'{PHASES[phase]}失败。请检查内存/磁盘和 data/routing 中的准备日志后重试。')
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:process.wait(timeout=5)
                    except subprocess.TimeoutExpired:process.kill();process.wait()

    def download(self, key, url, directory):
        target=directory/'map.osm.pbf'
        if target.exists():return target
        self.update(key,phase='download',download_bytes=0,total_bytes=None)
        partial=directory/'map.osm.pbf.part'
        try:
            with requests.get(url,stream=True,timeout=(5,30)) as response:
                response.raise_for_status()
                total=int(response.headers.get('Content-Length') or 0)
                maximum=int(os.environ.get('OSRM_MAX_EXTRACT_MB','2048'))*1024**2
                if total and maximum and total>maximum:
                    raise ValueError('覆盖整批地点的路网超过本机自动下载大小上限；请在足够内存/磁盘的自托管机器上提高 OSRM_MAX_EXTRACT_MB 后重试。不会购买云资源。')
                if total and shutil.disk_usage(directory).free<total*10+512*1024**2:
                    raise ValueError('此区域的路网需要更多磁盘空间。请腾出空间或在容量更大的自托管机器上运行；不会购买云资源。')
                size=0;last=0;started=time.monotonic()
                with partial.open('wb') as output:
                    for chunk in response.iter_content(1024*1024):
                        if self.cancelled(key) or time.monotonic()-started>3600:
                            raise ValueError('道路数据下载已停止或超时，请联网后重试。')
                        size+=len(chunk)
                        if maximum and size>maximum:raise ValueError('区域道路文件超过 OSRM_MAX_EXTRACT_MB 上限，请在适合的自托管机器上调整后重试。')
                        output.write(chunk)
                        if time.monotonic()-last>.5:
                            self.update(key,phase='download',download_bytes=size,total_bytes=total or None)
                            last=time.monotonic()
                if not size or (total and size!=total):raise ValueError('道路数据下载不完整，请重试。')
            partial.replace(target)
            self.update(key,phase='download',download_bytes=size,total_bytes=size)
            return target
        except requests.RequestException as exc:
            raise ValueError('区域道路数据下载失败，请检查网络后重试；不会使用公共路线演示服务器。') from exc

    def start_engine(self, key, directory, region):
        with self.lock:
            for endpoint,engine in self.engines.items():
                if engine['directory']==directory and engine['process'].poll() is None:
                    return endpoint
        if self.cancelled(key):raise ValueError('路网准备已停止。')
        self.update(key,phase='starting')
        # A private dynamically allocated port avoids touching the old service.
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
        endpoint=f'http://127.0.0.1:{port}'
        log=(directory/'server.log').open('ab')
        process=subprocess.Popen(['osrm-routed','--algorithm','mld','--ip','127.0.0.1','--port',str(port),
                                  '--threads','2','--mmap=true','map.osrm'],cwd=directory,stdout=log,stderr=subprocess.STDOUT)
        log.close()
        try:
            for _ in range(120):
                if process.poll() is not None:raise ValueError('区域路线服务启动失败，请检查路网版本及 server.log。')
                try:
                    with socket.create_connection(('127.0.0.1',port),timeout=.5):break
                except OSError:
                    if self.stopping.wait(.5):raise ValueError('应用正在关闭，已停止启动路网。')
            else:raise ValueError('区域路线服务启动超时，请重试。')
        except Exception:
            process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait()
            raise
        with self.lock:
            # Keep existing graphs/processes available to in-flight requests.
            self.engines[endpoint]=dict(process=process,directory=directory,region=region,users=0,used_at=time.monotonic())
            self.trim(endpoint)
        return endpoint

    def trim(self, keep=None):
        alive=[(url,e) for url,e in self.engines.items() if e['process'].poll() is None]
        for url,engine in sorted(alive,key=lambda pair:pair[1]['used_at']):
            if len(alive)<=2:break
            if url==keep or engine['users']:continue
            process=engine['process'];process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait()
            alive=[pair for pair in alive if pair[0]!=url]

    @contextmanager
    def using(self, endpoint):
        with self.lock:
            engine=self.engines.get(endpoint)
            if engine:
                if engine['process'].poll() is not None:raise ValueError('已缓存路网需要重新启动，请重试计算。')
                engine['users']+=1;engine['used_at']=time.monotonic()
        try:yield
        finally:
            with self.lock:
                if engine:engine['users']-=1
                self.trim(endpoint)

    def prepare(self, key, points):
        try:
            if self.cancelled(key):raise ValueError('路网准备已停止。')
            self.update(key,phase='checking')
            if self.base_covers(points):
                self.update(key,phase='ready',endpoint=self.base_url,region=os.environ.get('OSRM_BASE_REGION') or '已有本地路网')
                return
            if self.cancelled(key):raise ValueError('路网准备已停止。')
            self.update(key,phase='catalog')
            region=select_region(self.get_catalog(),points)
            self.update(key,phase='catalog',region=region.get('name') or region['id'],region_id=region['id'])
            binaries=['osrm-extract','osrm-partition','osrm-customize','osrm-routed']
            if any(not shutil.which(name) for name in binaries):
                raise ValueError('自动准备新路网需要安装 OSRM，或使用仓库内自动路网 Docker 镜像；当前搜索结果已保存。')
            profile=profile_path()
            version=subprocess.check_output(['osrm-routed','--version'],text=True,timeout=5).strip()
            fingerprint=hashlib.sha256((region['id']+version+hashlib.sha256(profile.read_bytes()).hexdigest()).encode()).hexdigest()[:20]
            directory=self.root/fingerprint;directory.mkdir(exist_ok=True,mode=0o700)
            manifest=directory/'ready.json'
            if not manifest.exists():
                self.download(key,region['urls']['pbf'],directory)
                if self.cancelled(key):raise ValueError('路网准备已停止。')
                self.command(key,['osrm-extract','--threads','2','-p',str(profile),'map.osm.pbf'],'extract',directory)
                self.command(key,['osrm-partition','--threads','2','map.osrm'],'partition',directory)
                self.command(key,['osrm-customize','--threads','2','map.osrm'],'customize',directory)
                store.atomic_json(manifest,dict(region_id=region['id'],source=region['urls']['pbf'],version=version,prepared_at=time.time()))
            endpoint=self.start_engine(key,directory,region['id'])
            self.update(key,phase='ready',endpoint=endpoint,region=region.get('name') or region['id'],road_data=json.loads(manifest.read_text(encoding='utf-8')))
        except Exception as exc:
            import logging
            logging.getLogger(__name__).exception('Regional OSRM preparation failed')
            self.update(key,phase='cancelled' if self.cancelled(key) else 'failed',error=str(exc) if isinstance(exc,ValueError) else '路网准备失败，请检查网络、磁盘与本机日志后重试。')

    def close(self):
        self.stopping.set()
        self.pool.shutdown(wait=True,cancel_futures=True)
        for engine in self.engines.values():
            process=engine['process']
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.wait()


_manager=None
_manager_lock=RLock()

def manager():
    global _manager
    import routing
    with _manager_lock:
        if _manager is None:_manager=Manager(routing.OSRM)
        return _manager


def request(points, retry=False):
    import routing
    if not enabled():return dict(phase='ready',endpoint=routing.OSRM,message='使用已配置的路网')
    return manager().request(points,retry=retry)


def status():
    return manager().status() if enabled() else dict(phase='disabled',message='使用已配置的固定路网')


def shutdown():
    global _manager
    with _manager_lock:
        if _manager is not None:_manager.close();_manager=None

atexit.register(shutdown)

@contextmanager
def using(endpoint):
    if _manager is None:
        yield
    else:
        with _manager.using(endpoint):yield

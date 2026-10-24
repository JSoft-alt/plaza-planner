"""Real app processes: Unicode paths/history, tzdata, instance locks and restart.

No public APIs or OSRM downloads. Runs on macOS/Linux and native Windows CI.
"""
import json
import hashlib
import importlib.util
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import Mock, patch

import store
from models import SearchInput

ROOT = Path(__file__).resolve().parents[1]


class Portability(unittest.TestCase):
    def native_launcher(self):
        spec=importlib.util.spec_from_file_location('planner_native_launcher',ROOT/'scripts'/'run-native.py')
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_native_launcher_reuses_dependencies_and_preserves_spaced_paths(self):
        launcher=self.native_launcher()
        with tempfile.TemporaryDirectory(prefix='native launch spaces ') as folder:
            root=Path(folder)/'公司 app';root.mkdir()
            requirements=root/'requirements.txt';requirements.write_bytes(b'fastapi\n')
            environment=root/'.venv-windows'
            python=environment/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
            python.parent.mkdir(parents=True);python.touch()
            (environment/'planner-requirements.sha256').write_text(hashlib.sha256(requirements.read_bytes()).hexdigest(),encoding='ascii')
            process=Mock();process.wait.return_value=0
            with patch.object(launcher,'ROOT',root),patch.object(sys,'argv',['run-native.py','--no-browser']),\
                 patch.object(launcher.subprocess,'Popen',return_value=process) as start,patch.object(launcher.subprocess,'run') as install:
                self.assertEqual(launcher.main(),0)
                install.assert_not_called()
                command=start.call_args.args[0]
                self.assertEqual(command[0],str(python))
                self.assertEqual(start.call_args.kwargs['cwd'],root)
                self.assertEqual(start.call_args.kwargs['env']['PYTHONUTF8'],'1')
                self.assertNotIn('shell',start.call_args.kwargs)

    def test_failed_dependency_install_does_not_launch_or_mark_environment_ready(self):
        launcher=self.native_launcher()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'requirements.txt').write_bytes(b'fastapi\n')
            environment=root/'.venv-windows'
            python=environment/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
            python.parent.mkdir(parents=True);python.touch()
            history=root/'data'/'keep.sqlite3';history.parent.mkdir();history.write_bytes(b'untouched')
            with patch.object(launcher,'ROOT',root),patch.object(sys,'argv',['run-native.py','--no-browser']),\
                 patch.object(launcher.subprocess,'run',side_effect=subprocess.CalledProcessError(1,'pip')),\
                 patch.object(launcher.subprocess,'Popen') as start:
                with self.assertRaises(subprocess.CalledProcessError):launcher.main()
                start.assert_not_called()
                self.assertFalse((environment/'planner-requirements.sha256').exists())
                self.assertEqual(history.read_bytes(),b'untouched')

    def test_chinese_discovery_cache_roundtrip(self):
        import discovery
        from test_discovery_services import response
        features=[dict(type='node',id=1,lat=40.75,lon=-73.98,tags={'office':'lawyer','name':'律师事务所'})]
        with tempfile.TemporaryDirectory() as folder,patch.object(store,'DATA',Path(folder)/'中文 cache'),\
             patch.object(discovery,'_blocked_until',0),patch.object(discovery.requests,'post',return_value=response(200,{'elements':features})) as post:
            first=discovery.fetch_tile((40.75,-73.98,40.751,-73.979),discovery_kind='offices')
            second=discovery.fetch_tile((40.75,-73.98,40.751,-73.979),discovery_kind='offices')
            self.assertEqual(first,features)
            self.assertEqual(second,features)
            self.assertEqual(post.call_count,1)

    def test_native_process_unicode_history_lock_and_restart(self):
        with tempfile.TemporaryDirectory(prefix='planner spaces ') as folder:
            data = Path(folder) / '医院与公司 数据'
            source = Path(folder) / '中文 app source'
            source.mkdir()
            for path in [*ROOT.glob('*.py'), *ROOT.glob('*_rules.json')]:
                shutil.copy2(path,source/path.name)
            (source/'static').mkdir()
            # Windows Notepad may prepend a UTF-8 BOM. Check the first key and
            # Chinese value through the real app, without touching local .env.
            style='https://example.invalid/中文/style.json'
            (source/'.env').write_text('MAP_STYLE_URL='+style+'\n',encoding='utf-8-sig')
            with patch.object(store, 'DATA', data):
                store.init()
                config = SearchInput(origin=dict(lat=40.758,lng=-73.9855,label='公开测试起点',source='pin',confirmed=True),
                                     discovery_kind='offices',min_companies=2,batch_size=8)
                scan = store.insert_scan(config.model_dump(mode='json'))
                store.update_scan(scan['id'], status='complete', plazas=[dict(id='way:1',name='牙科与律师办公楼',
                    lat=40.759,lng=-73.985,address='测试街道',rank=1,company_count=2,entity_kind='building')])
                store.auto_export(scan['id'])
                archived=dict(name='旧定时搜索',config=config.model_dump(mode='json'),cron='0 9 * * *',timezone='America/New_York',enabled=True)
                with store.db() as conn:
                    conn.execute('INSERT INTO schedules VALUES (?,?,?,?,?,?)',('archived',store.dump(archived),1,1,None,None))
            env = os.environ | dict(PLANNER_DATA_DIR=str(data),OSRM_AUTO_DOWNLOAD='0',PYTHONTZPATH='',PYTHONUTF8='0')
            with (Path(folder)/'server.log').open('wb') as log:
                def start():
                    with socket.socket() as listener:
                        listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
                    process=subprocess.Popen([sys.executable,'-m','uvicorn','app:app','--host','127.0.0.1',
                                              '--port',str(port),'--workers','1'],cwd=source,env=env,stdout=log,stderr=log)
                    return process,f'http://127.0.0.1:{port}'

                def api(url,path,body=None,token=None):
                    request=urllib.request.Request(url+path,data=json.dumps(body).encode('utf-8') if body is not None else None,
                        headers={'Content-Type':'application/json','x-planner-csrf':token or ''})
                    with urllib.request.urlopen(request,timeout=3) as response:
                        return json.load(response)

                def wait_ready(process,url):
                    for _ in range(100):
                        self.assertIsNone(process.poll(),'App exited; inspect temporary server.log')
                        try:return api(url,'/api/bootstrap')
                        except (OSError,ValueError):time.sleep(.1)
                    self.fail('App startup timed out')

                def stop(process):
                    if process.poll() is None:
                        process.terminate()
                        try:process.wait(timeout=15)
                        except subprocess.TimeoutExpired:process.kill();process.wait()

                first,url=start()
                try:
                    bootstrap=wait_ready(first,url)
                    self.assertEqual(bootstrap['map_style'],style)
                    saved=api(url,f"/api/scans/{scan['id']}")
                    self.assertEqual(saved['plazas'][0]['name'],'牙科与律师办公楼')
                    self.assertEqual(api(url,f"/api/scans/{scan['id']}/export")['config']['discovery_kind'],'offices')
                    second,_=start()
                    try:
                        self.assertNotEqual(second.wait(timeout=15),0,'Duplicate instance must fail instead of sharing the history database')
                        self.assertEqual(api(url,'/api/bootstrap')['csrf'],bootstrap['csrf'])
                    finally:stop(second)
                finally:stop(first)
                restarted,url=start()
                try:
                    wait_ready(restarted,url)
                    self.assertEqual(api(url,'/api/history')['total'],1)
                    schedules=api(url,'/api/schedules')['items']
                    self.assertEqual(schedules[0]['name'],'旧定时搜索')
                    self.assertFalse(schedules[0]['enabled']);self.assertIsNone(schedules[0]['next_run'])
                finally:stop(restarted)


if __name__ == '__main__':
    unittest.main()

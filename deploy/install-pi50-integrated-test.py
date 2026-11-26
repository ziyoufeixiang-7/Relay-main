"""One enrolled Pi50 local-test switch. Run as root only after compilation succeeds."""
import hashlib,json,os,shutil,subprocess,time,urllib.request
from pathlib import Path

BUILD=Path('/home/pi/relay-integrated-build')
ROOT=Path('/opt/relay-integrated-test')
ORIGINAL=Path('/opt/RelayProtection')
META=json.loads((BUILD/'deployment.json').read_text())
IDENT='20261126-'+META['backend_commit'][:7]+'-'+META['frontend_commit'][:7]
RELEASE=ROOT/'releases'/IDENT
BACKUP=ROOT/'backups'/IDENT
NETWORK_DROP=Path('/etc/systemd/system/relay-mobile-network-test.service.d/50-integrated-source.conf')
USERCTL=['runuser','-u','pi','--','env','XDG_RUNTIME_DIR=/run/user/1000','DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus','systemctl','--user']

def run(*args,check=True,env=None):
    return subprocess.run(args,check=check,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,env=env).stdout.strip()
def user(*args,check=True):return run(*USERCTL,*args,check=check)
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def health():
    with urllib.request.urlopen('http://127.0.0.1:8181/health',timeout=3) as r:return json.load(r)
def wait_health():
    for _ in range(30):
        try:
            h=health()
            if h['upstreamOnline'] and h['active'] is False and h['phase']=='local':return h
        except Exception:pass
        time.sleep(1)
    raise RuntimeError('New backend has not confirmed idle within 30 seconds')

if os.geteuid()!=0:raise SystemExit('Run as root')
assert META['target']=='192.168.1.50' and META['build_mode']=='6phase-test'
assert (BUILD/'source-commit.txt').read_text().strip()==META['backend_commit']
assert 'BUILD_OK' in (BUILD/'compile.log').read_text()
assert digest(BUILD/'frontend/index.html')==META['frontend_sha256']
# No WS connection is opened to the original single-client backend.
recent=run('journalctl','-u','relay-protection.service','-n','30','--no-pager')
loads=[line for line in recent.splitlines() if '[WS TX]' in line and '"type":"load"' in line]
if not loads or '"active":false' not in loads[-1]:
    raise RuntimeError('Original backend idle state is not confirmed; no switch performed')
BACKUP.mkdir(parents=True,exist_ok=False)
state={key:run('systemctl',action,'relay-protection.service',check=False) for key,action in [('engine_active','is-active'),('engine_enabled','is-enabled')]}
state.update(gateway_active=user('is-active','relay-ui-mirror-test.service',check=False),gateway_enabled=user('is-enabled','relay-ui-mirror-test.service',check=False),network_active=run('systemctl','is-active','relay-mobile-network-test.service',check=False),network_drop_existed=NETWORK_DROP.exists(),original_html_sha_existed=(ORIGINAL/'index.html.sha256').exists())
state['protected']={str(p):digest(p) for p in [ORIGINAL/'SysEngine.bin',*sorted((ORIGINAL/'config').glob('*.json'))]}
(BACKUP/'state.json').write_text(json.dumps(state,indent=2))
if NETWORK_DROP.exists():shutil.copy2(NETWORK_DROP,BACKUP/'network-drop.conf')
run('tar','-C','/opt','-czf',str(BACKUP/'original.tar.gz'),'RelayProtection')
for name in ['index.html','index.html.sha256']:
    if (ORIGINAL/name).exists():shutil.copy2(ORIGINAL/name,BACKUP/name)
RELEASE.parent.mkdir(parents=True,exist_ok=True)
shutil.copytree(BUILD/'deploy/build/SysEngine.dist',RELEASE)
for p in (ORIGINAL/'config').glob('*.json'):shutil.copy2(p,RELEASE/'config'/p.name)
for name in ['index.html','index.html.sha256']:shutil.copy2(BUILD/'frontend'/name,RELEASE/name)
shutil.copytree(BUILD/'RelayProtection/services/mobile_network',RELEASE/'network')
META['backend_sha256']=digest(RELEASE/'SysEngine.bin')
META['release']=str(RELEASE)
META['backup']=str(BACKUP)
(RELEASE/'build-info.json').write_text(json.dumps(META,indent=2))
(RELEASE/'version.json').write_text(json.dumps({'version':'0.0.143-local-test','build_mode':'6phase-test','git_commit':META['backend_commit'],'frontend_git_commit':META['frontend_commit'],'local_test':True,'sha256':META['frontend_sha256']},indent=2))
env=os.environ.copy();env['RELAY_TEST_DEVICE_CONFIG']='/etc/relay-mobile-test/device.json'
print(run(str(RELEASE/'SysEngine.bin'),'--self-check',env=env),flush=True)

rollback=f'''#!/usr/bin/env python3
import json,subprocess,shutil
from pathlib import Path
b=Path({str(BACKUP)!r}); s=json.loads((b/'state.json').read_text())
def run(*args): subprocess.run(args,check=False)
u={USERCTL!r}
run('systemctl','disable','--now','relay-integrated-test.service')
p=Path({str(NETWORK_DROP)!r})
if s['network_drop_existed']:shutil.copy2(b/'network-drop.conf',p)
else:p.unlink(missing_ok=True)
run('systemctl','daemon-reload')
shutil.copy2(b/'index.html','/opt/RelayProtection/index.html')
if s['original_html_sha_existed']:shutil.copy2(b/'index.html.sha256','/opt/RelayProtection/index.html.sha256')
else:Path('/opt/RelayProtection/index.html.sha256').unlink(missing_ok=True)
if s['engine_enabled']=='enabled':run('systemctl','enable','relay-protection.service')
if s['engine_active']=='active':run('systemctl','start','relay-protection.service')
if s['gateway_enabled']=='enabled':run(*u,'enable','relay-ui-mirror-test.service')
if s['gateway_active']=='active':run(*u,'start','relay-ui-mirror-test.service')
print('Original services and HTML restored; refresh the local page.')
'''
(BACKUP/'rollback.py').write_text(rollback)
try:
    user('disable','--now','relay-ui-mirror-test.service')
    run('systemctl','disable','--now','relay-protection.service')
    pending=ROOT/'current.next';pending.unlink(missing_ok=True);pending.symlink_to(RELEASE);os.replace(pending,ROOT/'current')
    shutil.copy2(BUILD/'deploy/relay-integrated-test.service','/etc/systemd/system/relay-integrated-test.service')
    NETWORK_DROP.parent.mkdir(parents=True,exist_ok=True)
    NETWORK_DROP.write_text('[Service]\nExecStart=\nExecStart=/usr/bin/python3 '+str(RELEASE/'network/network_manager.py')+'\n')
    run('systemctl','daemon-reload')
    # The network helper restores saved profiles on startup. Defer its restart
    # to preserve the current Wi-Fi; the next normal boot uses the migrated path.
    run('systemctl','enable','--now','relay-integrated-test.service')
    result=wait_health()
    # Update the file kiosk's existing entry only after the backend is healthy.
    for name in ['index.html','index.html.sha256']:
        tmp=ORIGINAL/(name+'.integrated-next');shutil.copy2(RELEASE/name,tmp);os.replace(tmp,ORIGINAL/name)
    assert digest(ORIGINAL/'index.html')==META['frontend_sha256']
    for p,value in state['protected'].items():assert digest(Path(p))==value,p
    (BACKUP/'deployment.json').write_text(json.dumps(META,indent=2))
    print(json.dumps({'deployed':META,'health':result},indent=2),flush=True)
except BaseException:
    print(run('journalctl','-u','relay-integrated-test.service','-n','30','--no-pager',check=False),flush=True)
    run('python3',str(BACKUP/'rollback.py'),check=False)
    raise

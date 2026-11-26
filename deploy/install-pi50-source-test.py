"""One enrolled Pi50 source-mode local-test switch. Native compilation is not required."""
import hashlib,json,os,shutil,subprocess,time,urllib.request,uuid
from pathlib import Path

BUILD=Path('/home/pi/relay-integrated-build')
ROOT=Path('/opt/relay-integrated-test')
ORIGINAL=Path('/opt/RelayProtection')
META=json.loads((BUILD/'deployment.json').read_text())
IDENT='20261126-'+META['backend_commit'][:7]+'-'+META['frontend_commit'][:7]+'-'+uuid.uuid4().hex[:6]
RELEASE=ROOT/'releases'/IDENT
BACKUP=ROOT/'backups'/IDENT
NETWORK_DROP=Path('/etc/systemd/system/relay-mobile-network-test.service.d/50-integrated-source.conf')
USERCTL=['runuser','-u','pi','--','env','XDG_RUNTIME_DIR=/run/user/1000','DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus','systemctl','--user']

def run(*args,check=True,env=None):
    return subprocess.run(args,check=check,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,env=env).stdout.strip()
def user(*args,check=True):return run(*USERCTL,*args,check=check)
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def health():
    with urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=3) as r:return json.load(r)
def wait_health():
    for _ in range(30):
        try:
            h=health()
            if h['upstreamOnline'] and h['active'] is False and h['phase']=='local':return h
        except Exception:pass
        time.sleep(1)
    raise RuntimeError('New backend has not confirmed idle within 30 seconds')

if os.geteuid()!=0:raise SystemExit('Run as root')
assert META['target']=='192.168.1.50' and META['build_mode'] in ('6phase','6phase-test')
assert (BUILD/'source-commit.txt').read_text().strip()==META['backend_commit']
assert (BUILD/'RelayProtection/comms/mobile_control/mirror-modules.json').is_file()
assert (BUILD/'RelayProtection/SysEngine.py').is_file()
assert not (BUILD/'RelayProtection/comms/AdminAuth.py').exists()
assert digest(BUILD/'frontend/index.html')==META['frontend_sha256']
PREVIOUS=(ROOT/'current').resolve() if (ROOT/'current').exists() else None
UNIT=Path('/etc/systemd/system/relay-integrated-test.service')
if PREVIOUS:
    h=health()
    if not (h.get('upstreamOnline') and h.get('active') is False and h.get('phase')=='local'):
        raise RuntimeError('Current integrated backend is not confirmed idle; no switch performed')
else:
    recent=run('journalctl','-u','relay-protection.service','-n','30','--no-pager')
    loads=[line for line in recent.splitlines() if '[WS TX]' in line and '"type":"load"' in line]
    if not loads or '"active":false' not in loads[-1]:
        raise RuntimeError('Original backend idle state is not confirmed; no switch performed')
BACKUP.mkdir(parents=True,exist_ok=False)
state={key:run('systemctl',action,'relay-protection.service',check=False) for key,action in [('engine_active','is-active'),('engine_enabled','is-enabled')]}
state.update(gateway_active=user('is-active','relay-ui-mirror-test.service',check=False),gateway_enabled=user('is-enabled','relay-ui-mirror-test.service',check=False),network_active=run('systemctl','is-active','relay-mobile-network-test.service',check=False),network_drop_existed=NETWORK_DROP.exists(),original_html_sha_existed=(ORIGINAL/'index.html.sha256').exists())
state.update(previous_release=str(PREVIOUS) if PREVIOUS else None, integrated_active=run('systemctl','is-active','relay-integrated-test.service',check=False), integrated_enabled=run('systemctl','is-enabled','relay-integrated-test.service',check=False), unit_existed=UNIT.exists())
if UNIT.exists():shutil.copy2(UNIT,BACKUP/'integrated.service')
state['protected']={str(p):digest(p) for p in [ORIGINAL/'SysEngine.bin',*sorted((ORIGINAL/'config').glob('*.json'))]}
(BACKUP/'state.json').write_text(json.dumps(state,indent=2))
if NETWORK_DROP.exists():shutil.copy2(NETWORK_DROP,BACKUP/'network-drop.conf')
run('tar','-C','/opt','-czf',str(BACKUP/'original.tar.gz'),'RelayProtection')
for name in ['index.html','index.html.sha256']:
    if (ORIGINAL/name).exists():shutil.copy2(ORIGINAL/name,BACKUP/name)
RELEASE.parent.mkdir(parents=True,exist_ok=True)
RELEASE.mkdir()
shutil.copytree(BUILD/'RelayProtection',RELEASE/'app',ignore=shutil.ignore_patterns('__pycache__','*.pyc','tests','docs','*.bak'))
shutil.copytree(PREVIOUS/'runtime' if PREVIOUS else BUILD/'.venv',RELEASE/'runtime',symlinks=True,ignore=shutil.ignore_patterns('__pycache__'))
for p in ((PREVIOUS/'app/config') if PREVIOUS else ORIGINAL/'config').glob('*.json'):shutil.copy2(p,RELEASE/'app/config'/p.name)
for name in ['index.html','index.html.sha256']:shutil.copy2(BUILD/'frontend'/name,RELEASE/name)
shutil.copytree(BUILD/'RelayProtection/services/mobile_network',RELEASE/'network')
META['deployment_kind']='python-source'
META['backend_entry_sha256']=digest(RELEASE/'app/SysEngine.py')
META['release']=str(RELEASE)
META['backup']=str(BACKUP)
(RELEASE/'build-info.json').write_text(json.dumps(META,indent=2))
(RELEASE/'app/version.json').write_text(json.dumps({'version':'0.0.143-local-test','build_mode':META['build_mode'],'git_commit':META['backend_commit'],'frontend_git_commit':META['frontend_commit'],'local_test':True,'sha256':META['frontend_sha256']},indent=2))

rollback=f'''#!/usr/bin/env python3
import json,subprocess,shutil,os
from pathlib import Path
b=Path({str(BACKUP)!r}); s=json.loads((b/'state.json').read_text())
def run(*args): subprocess.run(args,check=False)
u={USERCTL!r}
run('systemctl','disable','--now','relay-integrated-test.service')
p=Path({str(NETWORK_DROP)!r})
if s['network_drop_existed']:shutil.copy2(b/'network-drop.conf',p)
else:p.unlink(missing_ok=True)
unit=Path('/etc/systemd/system/relay-integrated-test.service')
if s['unit_existed']:shutil.copy2(b/'integrated.service',unit)
else:unit.unlink(missing_ok=True)
current=Path('/opt/relay-integrated-test/current')
if s['previous_release']:
    pending=current.with_name('current.rollback');pending.unlink(missing_ok=True)
    pending.symlink_to(s['previous_release']);os.replace(pending,current)
else:current.unlink(missing_ok=True)
run('systemctl','daemon-reload')
if s['integrated_enabled']=='enabled':run('systemctl','enable','relay-integrated-test.service')
if s['integrated_active']=='active':run('systemctl','start','relay-integrated-test.service')
shutil.copy2(b/'index.html','/opt/RelayProtection/index.html')
if s['original_html_sha_existed']:shutil.copy2(b/'index.html.sha256','/opt/RelayProtection/index.html.sha256')
else:Path('/opt/RelayProtection/index.html.sha256').unlink(missing_ok=True)
if s['engine_enabled']=='enabled':run('systemctl','enable','relay-protection.service')
if s['engine_active']=='active':run('systemctl','start','relay-protection.service')
if s['gateway_enabled']=='enabled':run(*u,'enable','relay-ui-mirror-test.service')
if s['gateway_active']=='active':run(*u,'start','relay-ui-mirror-test.service')
print('Previous services, source release and HTML restored; refresh the local page.')
'''
(BACKUP/'rollback.py').write_text(rollback)
try:
    user('disable','--now','relay-ui-mirror-test.service')
    run('systemctl','disable','--now','relay-protection.service')
    run('systemctl','stop','relay-integrated-test.service')
    pending=ROOT/'current.next';pending.unlink(missing_ok=True);pending.symlink_to(RELEASE);os.replace(pending,ROOT/'current')
    shutil.copy2(BUILD/'relay-integrated-source-test.service','/etc/systemd/system/relay-integrated-test.service')
    NETWORK_DROP.parent.mkdir(parents=True,exist_ok=True)
    NETWORK_DROP.write_text('[Service]\nExecStart=\nExecStart=/usr/bin/python3 '+str(RELEASE/'network/network_manager.py')+'\n')
    run('systemctl','daemon-reload')
    # The network helper restores saved profiles on startup. Defer its restart
    # to preserve the current Wi-Fi; the next normal boot uses the migrated path.
    run('systemctl','enable','--now','relay-integrated-test.service')
    # Deploy both components before the final test stage, as requested.
    for name in ['index.html','index.html.sha256']:
        tmp=ORIGINAL/(name+'.integrated-next');shutil.copy2(RELEASE/name,tmp);os.replace(tmp,ORIGINAL/name)
    assert digest(ORIGINAL/'index.html')==META['frontend_sha256']
    for p,value in state['protected'].items():assert digest(Path(p))==value,p
    (BACKUP/'deployment.json').write_text(json.dumps(META,indent=2))
    print(json.dumps({'deployed':META,'testing':'pending'},indent=2),flush=True)
except BaseException:
    print(run('journalctl','-u','relay-integrated-test.service','-n','30','--no-pager',check=False),flush=True)
    run('python3',str(BACKUP/'rollback.py'),check=False)
    raise

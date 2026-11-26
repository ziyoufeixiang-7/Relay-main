"""Restart the existing pi user's kiosk with its original arguments and session environment."""
import os
import pwd
import signal
import shlex
import subprocess
import time
from pathlib import Path

account=pwd.getpwnam('pi')
if os.getuid() not in (0,account.pw_uid):
    raise SystemExit('Run as pi or its deployment administrator')
found=[]
for item in Path('/proc').iterdir():
    if not item.name.isdigit():continue
    try:
        status=(item/'status').read_text()
        uid=int(next(line for line in status.splitlines() if line.startswith('Uid:')).split()[1])
        if uid!=account.pw_uid:continue
        args=[x.decode() for x in (item/'cmdline').read_bytes().split(b'\0') if x]
        if len(args)==1 and ' --kiosk ' in args[0]:args=shlex.split(args[0])
        if args and 'chromium' in Path(args[0]).name and '--kiosk' in args and any(x.startswith('file:///opt/RelayProtection/index.html') for x in args):
            found.append((int(item.name),args,item))
    except (OSError,UnicodeDecodeError):pass
if len(found)!=1:raise SystemExit('Expected exactly one existing file kiosk; no process changed')
pid,args,process=found[0]
env=dict(x.decode().split('=',1) for x in (process/'environ').read_bytes().split(b'\0') if b'=' in x)
os.kill(pid,signal.SIGTERM)
for _ in range(100):
    if not process.exists():break
    time.sleep(.1)
else:raise SystemExit('Kiosk did not exit; no second process started')
def as_kiosk_user():
    if os.getuid()==0:
        os.initgroups(account.pw_name,account.pw_gid)
        os.setgid(account.pw_gid);os.setuid(account.pw_uid)
with (Path(account.pw_dir)/'relay-integrated-build/kiosk-restart.log').open('ab') as log:
    new=subprocess.Popen(args,env=env,cwd=account.pw_dir,preexec_fn=as_kiosk_user,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
print('Kiosk restarted with original arguments; pid='+str(new.pid))

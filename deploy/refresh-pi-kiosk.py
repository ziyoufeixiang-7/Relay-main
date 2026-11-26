"""Restart the existing pi user's kiosk with its original arguments and session environment."""
import os
import signal
import subprocess
import time
from pathlib import Path

if os.getuid()==0:
    raise SystemExit('Run as the existing kiosk user, not root')
found=[]
for item in Path('/proc').iterdir():
    if not item.name.isdigit():continue
    try:
        if item.stat().st_uid!=os.getuid():continue
        args=[x.decode() for x in (item/'cmdline').read_bytes().split(b'\0') if x]
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
with (Path.home()/'relay-integrated-build/kiosk-restart.log').open('ab') as log:
    new=subprocess.Popen(args,env=env,cwd=Path.home(),stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
print('Kiosk restarted with original arguments; pid='+str(new.pid))

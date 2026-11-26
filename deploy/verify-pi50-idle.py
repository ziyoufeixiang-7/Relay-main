"""Post-deployment Pi50 checks. Claims control only while confirmed idle; never starts output."""
import asyncio,json,time
from websockets.asyncio.client import connect

URL='ws://127.0.0.1:8080'
async def state(ws,predicate=lambda s:True,timeout=12):
    async with asyncio.timeout(timeout):
        while True:
            msg=json.loads(await ws.recv())
            if msg.get('type')=='gateway' and msg.get('event')=='state' and predicate(msg):return msg
async def action(ws,name,**fields):
    await ws.send(json.dumps({'gateway':True,'action':name,**fields}))
async def main():
    async with connect(URL,origin='null') as local:
        initial=await state(local)
        assert initial.get('integratedBackend') is True and initial.get('simulation') is False
        assert initial['active'] is False and initial['phase']=='local' and initial['owner']=='local'
        assert not initial.get('remoteEnabled'),'Existing remote authorization; leaving it unchanged'
        enabled=False
        try:
            await action(local,'remote_enable');enabled=True
            await state(local,lambda s:s.get('remoteEnabled'))
            async with connect(URL+'/remote',origin='http://127.0.0.1:8080') as phone:
                await state(phone)
                await action(phone,'claim')
                await state(phone,lambda s:bool(s.get('lease')))
                await action(local,'reclaim')
                reclaimed=await state(local,lambda s:s['owner']=='local' and not s.get('remoteEnabled') and s['phase']=='local')
                assert reclaimed['active'] is False
                await state(phone,lambda s:not s.get('lease') and s['owner']=='local')
                await action(local,'remote_enable')
                await state(local,lambda s:s.get('remoteEnabled'))
                await action(phone,'claim')
                await state(phone,lambda s:bool(s.get('lease')))
                started=time.monotonic()
                expired=await state(phone,lambda s:not s.get('lease') and s.get('reason')=='heartbeat_timeout',timeout=12)
                elapsed=time.monotonic()-started
                assert elapsed>=5 and expired['active'] is False and expired['owner']=='local'
                await phone.send(json.dumps({'cmd':'adminAuth','action':'identity'}))
                async with asyncio.timeout(5):
                    while True:
                        reply=json.loads(await phone.recv())
                        if reply.get('event')=='rejected':
                            assert reply.get('code')=='gateway_protocol_required'
                            break
                print(json.dumps({'result':'PASS','port':8080,'integratedBackend':True,'simulation':False,
                                  'claim_reclaim':'PASS','idle_heartbeat_expiry_seconds':round(elapsed,3),
                                  'raw_admin_auth_rejected':True,'output_start_commands_sent':0}))
        finally:
            if enabled:await action(local,'reclaim')

if __name__=='__main__':asyncio.run(main())

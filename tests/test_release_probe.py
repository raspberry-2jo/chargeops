import asyncio
import importlib.util
import json
from pathlib import Path
import pytest
from prometheus_client import CollectorRegistry

spec=importlib.util.spec_from_file_location('release_probe',Path(__file__).resolve().parents[1]/'apps/csms/release_probe.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)


@pytest.mark.parametrize('namespace,charger,reserved,hash_value,ok',[
    ('dev','CI-DEV-CANARY-01','','',True),('dev','CH-01','','',False),
    ('ops','CI-OPS-CANARY-001','true','abc123',True),('ops','','false','abc123',False),
    ('ops','CH-01','true','abc123',False),('ops','CI-OPS-CANARY-001','false','abc123',False),
    ('ops','CI-OPS-CANARY-001','true','',False),('default','CI-DEV-CANARY-01','','',False)])
def test_fixture_configuration_never_accepts_operational_ids(namespace,charger,reserved,hash_value,ok):
    env={'POD_NAMESPACE':namespace,'OCPP_TEST_CHARGER_ID':charger,'OCPP_FIXTURE_RESERVED':reserved,'CANDIDATE_POD_HASH':hash_value}
    if ok: assert p.configuration(env)[0]==namespace
    else:
        with pytest.raises(p.Unverified):p.configuration(env)


@pytest.mark.parametrize('fault',['none','boot_rejected','wrong_reply','callerror','timeout','wrong_pod','session_replaced','stale_heartbeat','stuck_session'])
def test_real_websocket_roundtrips_and_ops_session_checks(fault):
    from websockets.asyncio.server import serve
    from websockets.asyncio.client import connect
    async def scenario():
        state=p.State(CollectorRegistry())
        active=[None];sessions=[0];reads=[0];messages=[]
        async def server(socket):
            sessions[0]+=1
            active[0]=(str(sessions[0]),'csms-'+('wrong' if fault=='wrong_pod' else 'hash')+'-pod')
            try:
                async for raw in socket:
                    _,identity,action,_=json.loads(raw);messages.append(action)
                    if fault=='timeout':await socket.close();continue
                    body={'status':'Rejected' if fault=='boot_rejected' else 'Accepted'} if action=='BootNotification' else {'currentTime':'2026-10-26T00:00:00Z'}
                    reply=[4,identity,'InternalError','test',{}] if fault=='callerror' else [3,'wrong' if fault=='wrong_reply' else identity,body]
                    await socket.send(json.dumps(reply))
            finally:
                if fault!='stuck_session':active[0]=None
        def row(charger):
            reads[0]+=1
            if fault=='session_replaced' and reads[0]%2==0 and active[0]:return ('outsider','csms-hash-other')
            return active[0]
        async def no_wait(seconds):await asyncio.sleep(0)
        async with serve(server,'127.0.0.1',0) as listener:
            port=listener.sockets[0].getsockname()[1]
            def local_connect(url,**kwargs):return connect(f'ws://127.0.0.1:{port}/ocpp/test',**kwargs)
            coro=p.verify('CI-OPS-CANARY-001','ignored','hash',state,local_connect,
                read_session=row,read_heartbeat=lambda cid:0 if fault=='stale_heartbeat' else p.time.time()+1,
                sleep=no_wait,heartbeat_count=2)
            if fault=='none':
                await coro
                assert sessions[0]==2 and messages.count('BootNotification')==2 and messages.count('Heartbeat')==4
            else:
                with pytest.raises(Exception):await coro
        assert state.connection._value.get()==0
    asyncio.run(scenario())


def test_ops_existing_session_blocks_before_any_socket(monkeypatch):
    monkeypatch.setenv('POD_NAMESPACE','ops');monkeypatch.setenv('OCPP_TEST_CHARGER_ID','CI-OPS-CANARY-001')
    monkeypatch.setenv('OCPP_FIXTURE_RESERVED','true');monkeypatch.setenv('CANDIDATE_POD_HASH','abc')
    monkeypatch.setattr(p,'health',lambda host:None)
    monkeypatch.setattr(p,'db_read',lambda sql,params=():(1,) if sql=='SELECT 1' else ('virtual',))
    monkeypatch.setattr(p,'session_row',lambda charger:('existing','csms-stable-1'))
    with pytest.raises(p.Unverified,match='refusing takeover'):p.checks(p.State(CollectorRegistry()))


def test_timeout_is_counted_as_timeout_and_closes_test_connection():
    class Socket:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def send(self,payload):pass
        async def recv(self):raise asyncio.TimeoutError()
    state=p.State(CollectorRegistry())
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(p.verify('CI-DEV-CANARY-01','ignored','',state,lambda *a,**kw:Socket()))
    assert state.requests.labels('BootNotification','timeout')._value.get()==1
    assert state.connection._value.get()==0

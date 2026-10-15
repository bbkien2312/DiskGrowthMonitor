"""NON-ELEVATED transport fixture. Not an entry point in the shipped app.

Mocks the Admin check only in this test process. Never runs system checks,
maintenance, cleanup or service commands; native scanning stays under pytest.
"""
import argparse
import logging
import time
from pathlib import Path
from dgm import config,elevation

parser=argparse.ArgumentParser()
for key in ('home','ticket','digest','scenario'):parser.add_argument('--'+key,required=True)
args=parser.parse_args();config.ROOT=Path(args.home).resolve()
elevation.is_admin=lambda:True
original=elevation.dispatch
def fixture(action,payload,stop,notify):
    if args.scenario=='native':
        assert action=='scan' and payload['profile']['engine']=='native'
        assert all(Path(r).resolve().is_relative_to(config.ROOT.parent) for r in payload['profile']['roots'])
        return original(action,payload,stop,notify)
    logging.getLogger('dgm').debug('fixture DEBUG from worker')
    logging.getLogger('dgm').info('fixture INFO from worker')
    from dgm.logging_utils import audit
    audit('fixture-worker',value='only-test')
    notify('progress',{'count':20,'path':'test fixture only'})
    if args.scenario=='error':raise ValueError('fixture error, nothing executed')
    if args.scenario=='crash':
        import os
        os._exit(7)
    if args.scenario=='wait':
        notify('info',{'message':'fixture waiting'})
        stop.wait(10)
        return {'status':'cancelled' if stop.is_set() else 'complete','scan_id':None,'files':0,'seconds':0}
    return {'exit':0,'fixture':True,'home':str(config.home()),'userprofile':__import__('os').environ.get('USERPROFILE'),'engines':config.load()['engines']}
elevation.dispatch=fixture
raise SystemExit(elevation.worker_main(args.ticket,args.digest))

"""Bounded phase timings, shared by CLI/jobs and the GUI. No fake percentages."""
from __future__ import annotations
import logging
import time
import uuid

SCAN_PERCENT={'prepare':0,'collect':5,'read_source':25,'validate':30,'normalize':40,
              'hardlinks':55,'folders':65,'write':80,'verify':90,'done':100}


class Timeline:
    def __init__(self,operation,notify=None,ident=None,clock=time.perf_counter,finish_on_done=True):
        self.operation=operation;self.notify=notify or (lambda *_:None)
        self.ident=ident or uuid.uuid4().hex;self.clock=clock;self.started=clock()
        self.finish_on_done=finish_on_done
        self.stages=[];self.percent=0;self.state='running';self.ended=None

    def report(self):
        now=self.ended if self.ended is not None else self.clock()
        stages=[dict(r,seconds=max(0,(r.get('ended',now)-r['started']))) for r in self.stages]
        for row in stages:row.pop('started',None);row.pop('ended',None)
        return {'operation':self.operation,'operation_id':self.ident,'state':self.state,
                'seconds':max(0,now-self.started),'stages':stages}

    def __call__(self,event,data):
        if event!='progress' or not data.get('stage'):
            self.notify(event,data);return
        now=self.clock();key=data['stage'];message=data.get('message','')
        if key=='done':
            if self.finish_on_done:self.finish('complete',message=message);return
            if self.stages and self.stages[-1]['state']=='running':self.stages[-1].update(ended=now,state='complete')
            self.percent=max(self.percent,99)
            self.notify('progress',dict(data,percent=99,timing=self.report()));return
        if not self.stages or self.stages[-1]['key']!=key or self.stages[-1]['state']!='running':
            if self.stages and self.stages[-1]['state']=='running':self.stages[-1].update(ended=now,state='complete')
            self.stages.append({'key':key,'name':message,'started':now,'state':'running'})
            logging.getLogger('dgm').info('[%s] %s: %s',self.operation,self.ident[:8],message)
        else:self.stages[-1]['name']=message
        value=SCAN_PERCENT.get(key,data.get('percent',0)) if self.operation in ('scan','import') else data.get('percent',0)
        self.percent=max(self.percent,min(99,int(value)))
        self.notify('progress',dict(data,percent=self.percent,timing=self.report()))

    def finish(self,state='complete',message=None):
        if self.ended is not None:return self.report()
        self.ended=self.clock();self.state=state
        if self.stages and self.stages[-1]['state']=='running':self.stages[-1].update(ended=self.ended,state=state)
        if state=='complete':self.percent=100
        text=message or {'complete':'Đã hoàn thành công đoạn','cancelled':'Đã dừng an toàn','failed':'Lỗi ở công đoạn hiện tại'}[state]
        report=self.report()
        logging.getLogger('dgm').info('[%s] %s: %s (%.3fs)',self.operation,self.ident[:8],state,report['seconds'])
        self.notify('progress',{'percent':self.percent,'message':text,'stage':'done','timing':report})
        return report

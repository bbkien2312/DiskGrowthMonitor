"""Monotonic stage completion; unknown-length work holds at its stage boundary."""
import uuid


class TaskProgress:
    def __init__(self,task,notify,steps):
        self.task=task;self.notify=notify;self.steps=steps;self.index=0
        self.percent=0;self.completed=[];self.ident=uuid.uuid4().hex

    def emit(self,detail='',label=None):
        self.notify('task-progress',{'task':self.task,'id':self.ident,'percent':self.percent,
            'stage':label or self.steps[self.index][1],'detail':detail,'completed':list(self.completed),
            'basis':'Tiến độ theo các bước; bước chưa biết tổng giữ ở mốc hiện tại, số mục đã đọc vẫn cập nhật.'})

    def stage(self,key,detail=''):
        self.index=next(i for i,step in enumerate(self.steps) if step[0]==key)
        low=self.steps[self.index-1][2] if self.index else 0
        self.percent=max(self.percent,low);self.emit(detail)

    def advance(self,count,total=None,detail=''):
        low=self.steps[self.index-1][2] if self.index else 0
        high=self.steps[self.index][2]
        if total and total>0:
            self.percent=max(self.percent,min(high-1,low+int((high-low)*max(0,min(count,total))/total)))
        self.emit(detail)

    def complete(self,description=None):
        self.percent=max(self.percent,self.steps[self.index][2])
        text=description or self.steps[self.index][1]
        if text not in self.completed:self.completed.append(text)
        self.emit()

    def finish(self,description):
        self.percent=100
        if description not in self.completed:self.completed.append(description)
        self.emit(label=description)

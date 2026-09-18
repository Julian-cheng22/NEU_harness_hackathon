"""Isolated review fixture: original web app, controlled model timing, real HTTP."""
import os
os.environ['HARNESS_LLM']='local'
import sys,json,time,threading,socket,gc
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from harness.llm import LLMResponse
from harness import schema_card
from web import server as web
import uvicorn,httpx

class SlowFixture:
    name='scripted-review-only'
    def __init__(self):
        self.lock=threading.Lock();self.calls=0;self.active=0;self.max_active=0
        self.first=threading.Event();self.second=threading.Event();self.release=threading.Event()
    def chat(self,*args,**kwargs):
        with self.lock:
            self.calls+=1;self.active+=1;self.max_active=max(self.max_active,self.active)
            (self.first if self.calls==1 else self.second).set()
        try:
            self.release.wait(8)
            return LLMResponse(text='```sql\nSELECT COUNT(*) AS n FROM employees\n```')
        finally:
            with self.lock:self.active-=1

ddl=schema_card.render_raw_ddl()
def setup():
    web.RUNNER=web._Runner()
    fixture=SlowFixture();web.RUNNER._llm=fixture;web.RUNNER._ddl=ddl
    return fixture

report={}
fixture=setup()
req=web.AskRequest(question='How many employees?',arms=['baseline'])
first=web._run_stream(req)
next(first);assert fixture.first.wait(3)
first.close()
second=web._run_stream(req)
second_start=next(second)
fixture.second.wait(2)
report['generator_close']={'second_event':second_start,'max_concurrent_model_calls':fixture.max_active,'first_worker_still_active':fixture.active>0}
fixture.release.set()
list(second)
time.sleep(.1)

fixture=setup()
sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
port=sock.getsockname()[1]
srv=uvicorn.Server(uvicorn.Config(web.app,host='127.0.0.1',port=port,log_level='error',access_log=False))
thread=threading.Thread(target=lambda:srv.run(sockets=[sock]),daemon=True);thread.start()
for _ in range(100):
    if srv.started:break
    time.sleep(.05)
client=httpx.Client(base_url=f'http://127.0.0.1:{port}',timeout=12,trust_env=False)
payload={'question':'How many employees?','arms':['baseline']}
try:
    with client.stream('POST','/api/ask',json=payload) as response:
        for line in response.iter_lines():
            if 'prompting' in line:
                assert fixture.first.wait(3)
                break
    time.sleep(.5)
    # Keep responses bounded and release any workers even if a second request
    # is erroneously admitted. This fixture never calls a paid or local model.
    timer=threading.Timer(2,fixture.release.set);timer.start()
    second_response=client.post('/api/ask',json=payload).text
    report['http_disconnect']={'second_response':second_response,'max_concurrent_model_calls':fixture.max_active,'first_worker_continued_after_disconnect':fixture.first.is_set()}
    fixture.release.set();timer.join();time.sleep(2)
    report['http_disconnect']['active_model_calls_after_finish']=fixture.active
    third_response=client.post('/api/ask',json=payload).text
    report['http_disconnect']['request_after_worker_finished']=third_response
    gc.collect()
    report['http_disconnect']['request_after_garbage_collection']=client.post('/api/ask',json=payload).text
finally:
    fixture.release.set();client.close();srv.should_exit=True;thread.join(5);sock.close()
    gc.collect()
(Path(__file__).parent/'stream_check.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report,indent=2),flush=True)

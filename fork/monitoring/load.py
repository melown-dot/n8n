#!/usr/bin/env python3
"""Exactly100 LOCAL synthetic fixture requests,4 concurrency,25batches/~25s."""
import json,time,urllib.request,urllib.error,concurrent.futures
from pathlib import Path
p=Path(__file__).resolve().parent
state=json.loads((p.parent/'private/state.json').read_text())
normal=[x for x in state['workflows'] if not x['failing']]
failing=next(x for x in state['workflows'] if x['failing'])
start=time.time();counts={};durations=[]
def call(i):
 w=failing if i%5==0 else normal[i%len(normal)]
 t=time.time()
 try:
  with urllib.request.urlopen('http://127.0.0.1:15678/webhook/'+w['path'],timeout=10) as res:code=res.status
 except urllib.error.HTTPError as ex:code=ex.code
 assert code==(500 if w['failing'] else 200),(i,code)
 return code,time.time()-t
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
 for batch in range(25):
  target=start+batch
  if target>time.time():time.sleep(target-time.time())
  for code,duration in ex.map(call,range(batch*4,(batch+1)*4)):
   counts[code]=counts.get(code,0)+1;durations.append(duration)
assert counts=={200:80,500:20},counts
print(json.dumps({'start_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(start)),'end_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'duration_s':time.time()-start,'requests':100,'concurrency':4,'http_status_counts':counts,'request_latency_mean_s':sum(durations)/len(durations),'request_latency_max_s':max(durations)},indent=2))

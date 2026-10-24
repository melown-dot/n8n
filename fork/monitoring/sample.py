#!/usr/bin/env python3
"""Bounded Docker CPU/cgroup memory and per-process RSS observations."""
import argparse,subprocess,json,time,statistics
ap=argparse.ArgumentParser();ap.add_argument('label');ap.add_argument('--samples',type=int,default=8);args=ap.parse_args()
names=subprocess.check_output(['docker','ps','--format','{{.Names}}'],text=True).splitlines()
names=[x for x in names if x.startswith('n8n-monitoring-pilot-') or x in ['n8n2398-fork-test-n8n-1','n8n2398-fork-test-db-1']]
points={x:[] for x in names};start=time.time()
for _ in range(args.samples):
 lines=subprocess.check_output(['docker','stats','--no-stream','--format','{{json .}}',*names],text=True).splitlines()
 for line in lines:
  x=json.loads(line);rss=subprocess.check_output(['docker','top',x['Name'],'-eo','pid,rss'],text=True).splitlines()[1:]
  points[x['Name']].append({'cpu_percent':float(x['CPUPerc'].strip('%')),'rss_kib':sum(int(t.split()[1]) for t in rss),'cgroup_memory':x['MemUsage'],'block_io':x['BlockIO']})
 time.sleep(1)
summary={k:{'cpu_mean':statistics.mean(t['cpu_percent'] for t in v),'cpu_max':max(t['cpu_percent'] for t in v),'rss_mib_mean':statistics.mean(t['rss_kib'] for t in v)/1024,'rss_mib_max':max(t['rss_kib'] for t in v)/1024,'last_cgroup_memory':v[-1]['cgroup_memory'],'last_block_io':v[-1]['block_io']} for k,v in points.items()}
print(json.dumps({'label':args.label,'start_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(start)),'end_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'duration_s':time.time()-start,'samples':args.samples,'method':'docker stats no-stream; Docker top sum process RSS (shared pages may double count)','services':summary},indent=2))

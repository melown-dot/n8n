#!/usr/bin/env python3
"""Real synthetic source→ETL→authenticated API acceptance. Never prints secrets."""
from pathlib import Path
import base64,json,subprocess,urllib.request,urllib.error,time,hashlib
p=Path(__file__).resolve().parent
env=dict(x.split('=',1) for x in (p/'.env').read_text().splitlines())
stage=dict(x.split('=',1) for x in (p.parent/'.env').read_text().splitlines() if '=' in x)
def req(port,path,data=None,auth=None,method=None):
 r=urllib.request.Request(f'http://127.0.0.1:{port}{path}',data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json'},method=method)
 if auth:r.add_header('Authorization',auth)
 try:
  with urllib.request.urlopen(r,timeout=30) as res:
   body=res.read();return res.status,json.loads(body) if body else None
 except urllib.error.HTTPError as err:return err.code,None
def basic(password):return 'Basic '+base64.b64encode(('pilot:'+password).encode()).decode()
assert req(19090,'/api/v1/targets')[0]==401
assert req(13000,'/api/search')[0]==401
assert req(13001,'/api/analytics/metrics')[0]==401
assert req(13001,'/api/login',{'email':'owner@fork.test','password':'incorrect'})[0]==401
code,login=req(13001,'/api/login',{'email':'owner@fork.test','password':stage['OWNER_PASSWORD']});assert code==200
with urllib.request.urlopen('http://127.0.0.1:13001/vendor/purify.min.js',timeout=10) as response: asset=response.read()
assert b'DOMPurify 3.4.17' in asset[:200]
bearer='Bearer '+login['token']
code,sync=req(13001,'/api/sync/force',{},bearer);assert code==200,(code,sync)
code,metrics=req(13001,'/api/analytics/metrics',auth=bearer);assert code==200
code,system=req(13001,'/api/analytics/system',auth=bearer);assert code==200
code,target=req(19090,'/api/v1/targets',auth=basic(env['PROM_PASSWORD']));assert code==200 and target['data']['activeTargets'][0]['health']=='up'
code,metric=req(19090,'/api/v1/query?query=n8n_webhook_request_duration_seconds_count',auth=basic(env['PROM_PASSWORD']));assert code==200 and metric['data']['result']
code,dash=req(13000,'/api/search',auth=basic(env['GRAFANA_PASSWORD']));assert code==200
uids=sorted(x['uid'] for x in dash if x['type']=='dash-db');assert len(uids)==5
node=r'''const {pool}=require('/app/src/config/db'); const local=require('/app/src/config/localDb');const jwt=require('jsonwebtoken');
(async()=>{await local.ready; const t=performance.now();
const fs=require('node:fs');if(!fs.readFileSync('/app/public/vendor/purify.min.js').equals(fs.readFileSync('/app/node_modules/dompurify/dist/purify.min.js')))throw Error('served sanitizer differs from patched package');
const page_size=(await local.query('PRAGMA page_size')).rows[0].page_size;const max_pages=(await local.query('PRAGMA max_page_count')).rows[0].max_page_count;if(page_size!==4096||max_pages!==32768)throw Error('configured SQLite cap missing');
const source=(await pool.query('SELECT id,status FROM execution_entity WHERE "deletedAt" IS NULL ORDER BY id')).rows;
const query_ms=performance.now()-t;
const replica=(await local.query('SELECT id,status FROM execution_entity ORDER BY id')).rows;
if(JSON.stringify(source)!==JSON.stringify(replica))throw Error('source/replica IDs/status mismatch');
for(const q of ['UPDATE workflow_entity SET name=name WHERE false','SELECT data FROM credentials_entity LIMIT 0','SELECT nodes FROM workflow_entity LIMIT 0']){let denied=false;try{await pool.query(q)}catch(e){denied=['42501','25006'].includes(e.code)}if(!denied)throw Error('restricted grant unexpectedly permitted');}
const owner=(await pool.query('SELECT id,email FROM "user" WHERE "roleSlug"=\'global:owner\'')).rows[0];
for(const role of ['global:member','global:admin',null]){const token=jwt.sign({...owner,role},process.env.DASHBOARD_JWT_SECRET);const res=await fetch('http://localhost:3000/api/analytics/metrics',{headers:{Authorization:'Bearer '+token}});if(res.status!==403)throw Error('non-owner token allowed');}
console.log(JSON.stringify({sqlite_cap:{page_size,max_pages,max_bytes:page_size*max_pages},rows:source.length,errors:source.filter(x=>x.status==='error').length,source_query_ms:query_ms,exact_ids_status_equal:true,source_write_credential_workflow_denied:true,non_owner_tokens_denied:true}));
await pool.end();await new Promise(r=>local.close(r));})().catch(e=>{console.error(e.message);process.exit(1)})'''
r=subprocess.run(['docker','compose','-f',str(p/'compose.yaml'),'exec','-T','analytics','node','-e',node],text=True,capture_output=True)
if r.returncode:raise RuntimeError(r.stderr.strip())
proof=json.loads(r.stdout.splitlines()[-1]);assert metrics['summary']['total']==proof['rows'] and metrics['summary']['error']==proof['errors']
print(json.dumps({'timestamp_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'auth':'owner200/wrong401/anonymous401/non-owner403','dashboards':uids,'target':'up','webhook_metric':metric['data']['result'],'served_dompurify':{'version':'3.4.17','sha256':hashlib.sha256(asset).hexdigest()},'ledger':proof,'metrics':metrics['summary'],'pipeline':system['pipeline'],'latest_sync':max(system['runs']['history'],key=lambda r:r['started_at']),'storage':system['storage']},indent=2))

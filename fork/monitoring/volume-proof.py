#!/usr/bin/env python3
"""LOCAL synthetic backup→restore→same-immutable-image restart and SQLite cap proof."""
from pathlib import Path
import subprocess,hashlib,json,os
p=Path(__file__).resolve().parent
base='node:22-alpine@sha256:fe4f6d74e20370acb04ad889077d14d0f64a9c8ea6a42bc60cddf1e844699914'
compose=['docker','compose','-f',str(p/'compose.yaml')]
image=subprocess.check_output(['docker','inspect','n8n-monitoring-pilot-analytics-1','--format','{{.Image}}'],text=True).strip()
assert image.startswith('sha256:')
backup=p/'private/analytics-accepted-volume.tar'
clone='n8n-monitoring-pilot-restore-proof'
subprocess.run([*compose,'stop','analytics'],check=True,stdout=subprocess.DEVNULL)
try:
 with backup.open('wb') as out:
  subprocess.run(['docker','run','--rm','--network','none','--user','0','-v','n8n-monitoring-pilot_analytics:/data:ro','--entrypoint','tar',base,'-C','/data','-cf','-','.'],check=True,stdout=out)
 backup.chmod(0o600)
 subprocess.run(['docker','volume','create',clone],check=True,stdout=subprocess.DEVNULL)
 with backup.open('rb') as src:
  subprocess.run(['docker','run','-i','--rm','--network','none','--user','0','-v',clone+':/data','--entrypoint','tar',base,'-C','/data','-xf','-'],check=True,stdin=src)
 node=r'''const db=require('/app/src/config/localDb');(async()=>{await db.ready;
const rows=(await db.query('SELECT id,status FROM execution_entity ORDER BY id')).rows;
const page=(await db.query('PRAGMA page_size')).rows[0].page_size;
const cap=(await db.query('PRAGMA max_page_count')).rows[0].max_page_count;
if(process.env.CAP_EDGE==='true'){await db.execute('CREATE TABLE cap_probe(data BLOB)');let full=false;for(let i=0;i<30;i++){try{await db.execute('INSERT INTO cap_probe VALUES(zeroblob(65536))')}catch(e){if(e.code!=='SQLITE_FULL')throw e;full=true;break;}}if(!full)throw Error('cap not enforced');}
const integrity=(await db.query('PRAGMA integrity_check')).rows[0].integrity_check;if(integrity!=='ok')throw Error('integrity failed');
const pages=(await db.query('PRAGMA page_count')).rows[0].page_count;if(pages>cap)throw Error('page cap exceeded');
console.log(JSON.stringify({rows:rows.length,ledger_sha256:require('crypto').createHash('sha256').update(JSON.stringify(rows)).digest('hex'),page_size:page,max_pages:cap,page_count:pages,integrity,cap_edge:process.env.CAP_EDGE==='true'}));await db.closeAsync();})().catch(e=>{console.error(e.message);process.exit(1)})'''
 results=[]
 for cap,edge in [('134217728','false'),('134217728','false'),('1048576','true')]:
  r=subprocess.run(['docker','run','--rm','--network','none','--read-only','--tmpfs','/tmp:size=16m','--cap-drop','ALL','-v',clone+':/data','-e','ANALYTICS_MAX_DB_BYTES='+cap,'-e','CAP_EDGE='+edge,'-e','LOG_FILE=off','-e','LOG_LEVEL=error','--entrypoint','node',image,'-e',node],text=True,capture_output=True)
  if r.returncode:raise RuntimeError(r.stderr.strip())
  results.append(json.loads(r.stdout.splitlines()[-1]))
 assert len({x['ledger_sha256'] for x in results})==1
 assert len({x['rows'] for x in results})==1 and results[0]['rows']>=127
 assert all(x['integrity']=='ok' and x['page_size']==4096 for x in results)
 print(json.dumps({'image_id':image,'backup_sha256':hashlib.sha256(backup.read_bytes()).hexdigest(),'backup_bytes':backup.stat().st_size,'restored_same_image_restart_and_cap':results},indent=2))
finally:
 subprocess.run(['docker','volume','rm',clone],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 subprocess.run([*compose,'up','-d','analytics'],check=True,stdout=subprocess.DEVNULL)

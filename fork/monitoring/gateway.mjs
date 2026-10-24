import http from 'node:http';
import {timingSafeEqual} from 'node:crypto';
import {startMetricsServer} from './metrics-endpoint.mjs';
for (const [port, host, upstreamPort, auth] of [[3000,'grafana',3000,false],[9090,'prometheus',9090,true],[3001,'analytics',3000,false]]) {
 http.createServer((req,res)=>{
  if(auth) {
   const got=Buffer.from(req.headers.authorization||'');
   const want=Buffer.from('Basic '+Buffer.from('pilot:'+process.env.PROM_PASSWORD).toString('base64'));
   if(got.length!==want.length||!timingSafeEqual(got,want)){res.writeHead(401,{'WWW-Authenticate':'Basic realm="Monitoring pilot"'});res.end();return;}
   delete req.headers.authorization;
  }
  for (const key of Object.keys(req.headers)) {
   if (key === 'forwarded' || key === 'x-real-ip' || key.startsWith('x-forwarded-')) delete req.headers[key];
  }
  req.headers['x-forwarded-for'] = req.socket.remoteAddress;
  req.headers['x-forwarded-proto'] = 'http';
  const upstream=http.request({hostname:host,port:upstreamPort,path:req.url,method:req.method,headers:req.headers,timeout:15000},r=>{res.writeHead(r.statusCode,r.headers);r.pipe(res);});
  upstream.on('timeout',()=>upstream.destroy());upstream.on('error',()=>{if(!res.headersSent)res.writeHead(502);res.end();});req.pipe(upstream);
 }).listen(port,'0.0.0.0');
}

if (process.env.METRICS_NETWORK_CIDR) {
 startMetricsServer({file:process.env.METRICS_FILE_PATH||'/metrics/unified.prom',cidr:process.env.METRICS_NETWORK_CIDR,port:9109}).catch(()=>{});
}

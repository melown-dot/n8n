import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {timingSafeEqual} from 'node:crypto';
test('relay overwrites attacker proxy identity and requires Prometheus auth',()=>{
 const handlers=new Map();let sent;
 const http={createServer(fn){return {listen(port){handlers.set(port,fn)}}},request(options){sent=options;return {on(){},destroy(){}}}};
 const source=fs.readFileSync(new URL('./gateway.mjs',import.meta.url),'utf8').replace(/^import .*;\n/gm,'');
 vm.runInNewContext(source,{http,timingSafeEqual,Buffer,process:{env:{PROM_PASSWORD:'test-only'}}});
 const request={url:'/api/login',method:'POST',headers:{'x-forwarded-for':'198.51.100.5','x-forwarded-proto':'https','x-forwarded-host':'evil','x-real-ip':'evil',forwarded:'for=evil'},socket:{remoteAddress:'172.20.0.1'},pipe(){}};
 handlers.get(3001)(request,{});
 assert.equal(sent.hostname,'analytics');assert.equal(sent.headers['x-forwarded-for'],'172.20.0.1');assert.equal(sent.headers['x-forwarded-proto'],'http');
 for(const key of ['forwarded','x-real-ip','x-forwarded-host'])assert.equal(sent.headers[key],undefined);
 let status;
 handlers.get(9090)({headers:{}},{writeHead(code){status=code},end(){}});assert.equal(status,401);
});

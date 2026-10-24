import {test} from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import os from 'node:os';
import {execFile as execFileCallback} from 'node:child_process';
import {promisify} from 'node:util';
import {mkdir, mkdtemp, rename, rm, symlink, utimes, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {findInternalAddress, makeMetricsHandler, startMetricsServer} from './metrics-endpoint.mjs';

const execFile = promisify(execFileCallback);
const MAX_BYTES = 262144;

function listen(server, host = '127.0.0.1') {
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, host, () => resolve(server.address().port));
  });
}

function close(server) {
  return new Promise((resolve, reject) => server.close(error => (error ? reject(error) : resolve())));
}

function request(port, path, method = 'GET', host = '127.0.0.1') {
  return new Promise((resolve, reject) => {
    const request = http.request({host, port, path, method}, response => {
      const chunks = [];
      response.on('data', chunk => chunks.push(chunk));
      response.on('end', () =>
        resolve({
          status: response.statusCode,
          headers: response.headers,
          body: Buffer.concat(chunks),
        }),
      );
    });
    request.setTimeout(1000, () => request.destroy(new Error('request timeout')));
    request.on('error', reject);
    request.end();
  });
}

async function withTempDir(callback) {
  const directory = await mkdtemp(join(tmpdir(), 'metrics-endpoint-'));
  try {
    return await callback(directory);
  } finally {
    await rm(directory, {recursive: true, force: true});
  }
}

async function withHandler(file, now, callback) {
  const server = http.createServer(makeMetricsHandler(file, () => now));
  const port = await listen(server);
  try {
    return await callback(port);
  } finally {
    await close(server);
  }
}

const fixtureInterfaces = {
  isolated: [{address: '172.25.0.4', family: 'IPv4', internal: false}],
  other: [{address: '10.0.0.4', family: 'IPv4', internal: false}],
};

test('selects one non-loopback IPv4 address in the configured CIDR', () => {
  assert.equal(findInternalAddress('172.25.0.0/16', fixtureInterfaces), '172.25.0.4');
  assert.throws(
    () => findInternalAddress('172.25.0.0/16', {...fixtureInterfaces, second: fixtureInterfaces.isolated}),
    /ambiguous/,
  );
  assert.throws(() => findInternalAddress('192.0.2.0/24', fixtureInterfaces), /match/);
  assert.throws(() => findInternalAddress('0.0.0.0/0', fixtureInterfaces), /CIDR/);
  assert.throws(() => findInternalAddress('127.0.0.0/8', fixtureInterfaces), /loopback/);
  assert.throws(() => findInternalAddress('172.25.0.0/33', fixtureInterfaces), /CIDR/);
  assert.throws(() => findInternalAddress('172.25.0.0/01', fixtureInterfaces), /CIDR/);
  assert.throws(() => findInternalAddress('172.25/16', fixtureInterfaces), /CIDR/);
});

test('returns a fresh bounded snapshot including the exact size boundary', async () => {
  await withTempDir(async directory => {
    const file = join(directory, 'unified.prom');
    const now = Date.now();
    await writeFile(file, Buffer.alloc(MAX_BYTES, 0x78));
    await utimes(file, now / 1000, now / 1000);
    await withHandler(file, now, async port => {
      const response = await request(port, '/metrics');
      assert.equal(response.status, 200);
      assert.equal(response.body.length, MAX_BYTES);
      assert.equal(response.headers['content-length'], String(MAX_BYTES));
      assert.match(response.headers['content-type'], /^text\/plain; version=0\.0\.4; charset=utf-8$/);
      assert.equal(response.headers['cache-control'], 'no-store');
      assert.equal(response.headers['x-content-type-options'], 'nosniff');
    });
  });
});

test('rejects an oversize snapshot without exposing file errors', async () => {
  await withTempDir(async directory => {
    const file = join(directory, 'unified.prom');
    const now = Date.now();
    await writeFile(file, Buffer.alloc(MAX_BYTES + 1, 0x78));
    await utimes(file, now / 1000, now / 1000);
    await withHandler(file, now, async port => {
      const response = await request(port, '/metrics');
      assert.equal(response.status, 413);
      assert.equal(response.body.length, 0);
      assert.equal(response.headers['cache-control'], 'no-store');
      assert.equal(response.headers['x-content-type-options'], 'nosniff');
    });
  });
});

test('returns 503 for missing, stale, and future-dated snapshots', async () => {
  await withTempDir(async directory => {
    const missing = join(directory, 'missing.prom');
    await withHandler(missing, Date.now(), async port => {
      const response = await request(port, '/metrics');
      assert.equal(response.status, 503);
      assert.equal(response.body.length, 0);
    });

    const file = join(directory, 'unified.prom');
    await writeFile(file, 'metric 1\n');
    const now = Date.now();
    await utimes(file, (now - 181000) / 1000, (now - 181000) / 1000);
    await withHandler(file, now, async port => assert.equal((await request(port, '/metrics')).status, 503));
    await utimes(file, (now + 31000) / 1000, (now + 31000) / 1000);
    await withHandler(file, now, async port => assert.equal((await request(port, '/metrics')).status, 503));
  });
});

test('rejects symlinks, directories, and FIFOs without waiting for a writer', async () => {
  await withTempDir(async directory => {
    const now = Date.now();
    const target = join(directory, 'target.prom');
    const link = join(directory, 'link.prom');
    await writeFile(target, 'metric 1\n');
    await symlink(target, link);
    await withHandler(link, now, async port => assert.equal((await request(port, '/metrics')).status, 503));

    const subdirectory = join(directory, 'directory.prom');
    await mkdir(subdirectory);
    await withHandler(subdirectory, now, async port => assert.equal((await request(port, '/metrics')).status, 503));

    const fifo = join(directory, 'fifo.prom');
    try {
      await execFile('mkfifo', [fifo]);
    } catch (error) {
      if (error?.code === 'ENOENT' || error?.code === 1) return;
      throw error;
    }
    await withHandler(fifo, now, async port => assert.equal((await request(port, '/metrics')).status, 503));
  });
});

test('restricts path and method while applying safety headers to every response', async () => {
  await withTempDir(async directory => {
    const file = join(directory, 'unified.prom');
    const now = Date.now();
    await writeFile(file, 'metric 1\n');
    await utimes(file, now / 1000, now / 1000);
    await withHandler(file, now, async port => {
      const wrongPath = await request(port, '/other');
      assert.equal(wrongPath.status, 404);
      assert.equal(wrongPath.headers['cache-control'], 'no-store');
      assert.equal(wrongPath.headers['x-content-type-options'], 'nosniff');

      const query = await request(port, '/metrics?format=text');
      assert.equal(query.status, 404);

      const method = await request(port, '/metrics', 'POST');
      assert.equal(method.status, 405);
      assert.equal(method.headers.allow, 'GET');
      assert.equal(method.headers['cache-control'], 'no-store');
      assert.equal(method.headers['x-content-type-options'], 'nosniff');
    });
  });
});

test('observes atomic replacement by opening one bounded handle per request', async () => {
  await withTempDir(async directory => {
    const file = join(directory, 'unified.prom');
    const replacement = join(directory, 'replacement.prom');
    const now = Date.now();
    await writeFile(file, 'metric 1\n');
    await utimes(file, now / 1000, now / 1000);
    await withHandler(file, now, async port => {
      assert.equal((await request(port, '/metrics')).body.toString(), 'metric 1\n');
      await writeFile(replacement, 'metric 2\n');
      await utimes(replacement, now / 1000, now / 1000);
      await rename(replacement, file);
      assert.equal((await request(port, '/metrics')).body.toString(), 'metric 2\n');
    });
  });
});

test('startMetricsServer rejects invalid CIDR without creating a listener', async () => {
  await assert.rejects(
    startMetricsServer({file: '/does/not/matter', cidr: '0.0.0.0/0', port: 0}),
    /CIDR/,
  );
});

test('startMetricsServer reports bind failure while leaving the caller in control', async t => {
  const candidates = Object.values(os.networkInterfaces())
    .flatMap(entries => entries ?? [])
    .filter(entry => (entry.family === 'IPv4' || entry.family === 4) && !entry.internal && !entry.address.startsWith('127.'));
  if (!candidates.length) {
    t.skip('no non-loopback IPv4 interface available');
    return;
  }
  const address = candidates[0].address;
  const cidr = `${address}/32`;
  const held = http.createServer(() => {});
  await new Promise((resolve, reject) => {
    held.once('error', reject);
    held.listen(0, address, resolve);
  });
  try {
    await assert.rejects(startMetricsServer({file: '/does/not/matter', cidr, port: held.address().port}));
  } finally {
    await close(held);
  }
});


test('never selects the IPv4 wildcard address', () => {
 assert.throws(() => findInternalAddress('0.0.0.0/8', {eth0:[{address:'0.0.0.0',family:'IPv4',internal:false}]}));
});

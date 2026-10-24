import http from 'node:http';
import {open} from 'node:fs/promises';
import {constants as fsConstants} from 'node:fs';
import os from 'node:os';

const MAX_BYTES = 262144;
const MAX_READ_BYTES = MAX_BYTES + 1;
const STALE_MS = 180000;
const FUTURE_MS = 30000;
const CONTENT_TYPE = 'text/plain; version=0.0.4; charset=utf-8';

function parseIPv4(value) {
  if (typeof value !== 'string' || !/^(?:\d{1,3}\.){3}\d{1,3}$/.test(value)) return null;
  const parts = value.split('.');
  if (parts.some(part => (part.length > 1 && part.startsWith('0')) || Number(part) > 255)) return null;
  return parts.reduce((result, part) => (result * 256 + Number(part)) >>> 0, 0);
}

function parseCIDR(value) {
  if (typeof value !== 'string' || value.trim() !== value) throw new Error('invalid CIDR');
  const parts = value.split('/');
  if (parts.length !== 2 || !/^(?:0|[1-9]\d?)$/.test(parts[1])) throw new Error('invalid CIDR');
  const address = parseIPv4(parts[0]);
  const prefix = Number(parts[1]);
  if (address === null || prefix < 1 || prefix > 32) throw new Error('invalid CIDR');
  const mask = (0xffffffff << (32 - prefix)) >>> 0;
  const network = (address & mask) >>> 0;
  const broadcast = (network | (~mask >>> 0)) >>> 0;
  const loopbackStart = (127 << 24) >>> 0;
  const loopbackEnd = ((127 << 24) | 0xffffff) >>> 0;
  if (network <= loopbackEnd && broadcast >= loopbackStart) throw new Error('loopback CIDR');
  return {mask, network};
}

export function findInternalAddress(cidr, interfaces = os.networkInterfaces()) {
  const {mask, network} = parseCIDR(cidr);
  const matches = [];
  for (const entries of Object.values(interfaces ?? {})) {
    if (!Array.isArray(entries)) continue;
    for (const entry of entries) {
      if (!entry || entry.internal || (entry.family !== 'IPv4' && entry.family !== 4)) continue;
      const address = parseIPv4(entry.address);
      if (address === null || address === 0 || (address >>> 24) === 127) continue;
      if (((address & mask) >>> 0) === network) matches.push(entry.address);
    }
  }
  if (matches.length === 0) throw new Error('no interface matches CIDR');
  if (matches.length !== 1) throw new Error('ambiguous interface matches CIDR');
  return matches[0];
}

function safetyHeaders(response) {
  response.setHeader('Cache-Control', 'no-store');
  response.setHeader('X-Content-Type-Options', 'nosniff');
}

function status(response, code, headers = {}) {
  for (const [name, value] of Object.entries(headers)) response.setHeader(name, value);
  response.statusCode = code;
  response.end();
}

function nowMilliseconds(nowFn) {
  const value = Number(nowFn());
  if (!Number.isFinite(value)) throw new Error('invalid clock');
  return value < 100000000000 ? value * 1000 : value;
}

export function makeMetricsHandler(file, nowFn = Date.now) {
  return async (request, response) => {
    safetyHeaders(response);
    if (request.url !== '/metrics') {
      status(response, 404);
      return;
    }
    if (request.method !== 'GET') {
      status(response, 405, {Allow: 'GET'});
      return;
    }

    let handle;
    try {
      const flags = fsConstants.O_RDONLY | (fsConstants.O_NOFOLLOW ?? 0) | (fsConstants.O_NONBLOCK ?? 0);
      handle = await open(file, flags);
      const stat = await handle.stat();
      if (!stat.isFile()) {
        status(response, 503);
        return;
      }
      if (!Number.isFinite(stat.size) || stat.size > MAX_BYTES) {
        status(response, 413);
        return;
      }
      const now = nowMilliseconds(nowFn);
      if (!Number.isFinite(stat.mtimeMs) || stat.mtimeMs < now - STALE_MS || stat.mtimeMs > now + FUTURE_MS) {
        status(response, 503);
        return;
      }

      const buffer = Buffer.allocUnsafe(MAX_READ_BYTES);
      let bytesRead = 0;
      while (bytesRead < MAX_READ_BYTES) {
        const result = await handle.read(buffer, bytesRead, MAX_READ_BYTES - bytesRead, bytesRead);
        if (!result.bytesRead) break;
        bytesRead += result.bytesRead;
      }
      if (bytesRead > MAX_BYTES) {
        status(response, 413);
        return;
      }
      const body = buffer.subarray(0, bytesRead);
      response.statusCode = 200;
      response.setHeader('Content-Type', CONTENT_TYPE);
      response.setHeader('Content-Length', String(body.length));
      response.end(body);
    } catch {
      if (!response.writableEnded) status(response, 503);
    } finally {
      if (handle) await handle.close().catch(() => {});
    }
  };
}

export async function startMetricsServer({file, cidr, port = 9109, interfaces} = {}) {
  const address = findInternalAddress(cidr, interfaces);
  const server = http.createServer(makeMetricsHandler(file));
  await new Promise((resolve, reject) => {
    const onError = error => {
      server.close(() => {});
      reject(error);
    };
    server.on('error', onError);
    server.listen(port, address, resolve);
  });
  return server;
}

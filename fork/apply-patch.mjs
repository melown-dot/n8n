import { readFileSync, writeFileSync } from 'node:fs';

import { patchLicense } from './patch-license.mjs';

const root = '/usr/local/lib/node_modules/n8n';
const { version } = JSON.parse(readFileSync(`${root}/package.json`, 'utf8'));
if (version !== '2.39.8') throw new Error('This fork requires n8n 2.39.8.');
const target = `${root}/dist/license.js`;
const patched = patchLicense(readFileSync(target, 'utf8'));
writeFileSync(target, patched);

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

import { patchLicense } from '../patch-license.mjs';

const original = readFileSync(
	'/usr/local/lib/node_modules/n8n/dist/license.js',
	'utf8',
);

test('the exact official artifact can be patched', () => {
	const patched = patchLicense(original);
	assert.notEqual(patched, original);
	assert.ok(patched.includes('quota:maxTeamProjects'));
});

test('a changed upstream artifact is rejected', () => {
	assert.throws(() => patchLicense(`${original}\n`), /artifact hash/);
});

test('a second patch application is rejected', () => {
	assert.throws(() => patchLicense(patchLicense(original)), /artifact hash/);
});

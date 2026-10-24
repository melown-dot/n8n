import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { setTimeout as delay } from 'node:timers/promises';

const base = 'http://localhost:15678';
const mode = process.argv[2] ?? 'setup';
assert.ok(
	['setup', 'verify', 'restarted', 'rollback', 'restored', 'replay'].includes(
		mode,
	),
	'Unknown acceptance mode',
);
const results = [];
let cookie = '';

for (let attempt = 0; attempt < 30; attempt++) {
	try {
		const ready = await fetch(`${base}/healthz/readiness`, {
			signal: AbortSignal.timeout(2000),
		});
		if (ready.ok) break;
	} catch {}
	assert.ok(attempt < 29, 'The isolated stage did not become ready');
	await delay(1000);
}

/**
 * @param {string} route
 * @param {{ method?: string, body?: unknown, authenticated?: boolean, expected?: number[] }} [options]
 */
async function call(
	route,
	{ method = 'GET', body, authenticated = true, expected = [200, 201] } = {},
) {
	const response = await fetch(`${base}${route}`, {
		method,
		signal: AbortSignal.timeout(10000),
		headers: {
			'Content-Type': 'application/json',
			Origin: base,
			...(authenticated && cookie ? { Cookie: cookie } : {}),
		},
		...(body === undefined ? {} : { body: JSON.stringify(body) }),
	});
	const text = await response.text();
	assert.ok(
		expected.includes(response.status),
		`${method} ${route}: ${response.status} ${text.slice(0, 800)}`,
	);
	const cookies = response.headers.getSetCookie();
	if (cookies.length)
		cookie = cookies.map((value) => value.split(';')[0]).join('; ');
	const json = text ? JSON.parse(text) : {};
	return json.data ?? json;
}

await mkdir('private', { recursive: true, mode: 0o700 });
let state;
if (mode === 'setup') {
	assert.ok(
		process.env.OWNER_PASSWORD &&
			process.env.FIXTURE_A_SECRET &&
			process.env.FIXTURE_B_SECRET,
	);
	const initial = await call('/rest/settings');
	if (initial.userManagement.showSetupOnFirstLoad) {
		await call('/rest/owner/setup', {
			method: 'POST',
			body: {
				email: 'owner@fork.test',
				firstName: 'Fork',
				lastName: 'Owner',
				password: process.env.OWNER_PASSWORD,
			},
		});
	} else {
		await call('/rest/login', {
			method: 'POST',
			body: {
				emailOrLdapLoginId: 'owner@fork.test',
				password: process.env.OWNER_PASSWORD,
			},
		});
	}
	assert.ok(cookie, 'The fixture owner must have a session');
	try {
		state = JSON.parse(await readFile('private/state.json', 'utf8'));
	} catch (error) {
		if (error.code !== 'ENOENT') throw error;
	}
	state ??= { projects: [], credentials: [], workflows: [] };
	state.cookie = cookie;
	const projects = [];
	const existingProjects = await call('/rest/projects');
	for (const key of ['a', 'b']) {
		const name = `Fixture ${key.toUpperCase()}`;
		const project =
			existingProjects.find((item) => item.name === name) ??
			(await call('/rest/projects', { method: 'POST', body: { name } }));
		projects.push(project);
	}
	state.projects = projects;
	await writeFile('private/state.json', JSON.stringify(state), { mode: 0o600 });
	const existingCredentials = await call('/rest/credentials');
	const existingWorkflows = await call('/rest/workflows');
	for (const [index, key] of ['a', 'b'].entries()) {
		const credentialName = `Fixture ${key.toUpperCase()} HTTP`;
		const credentialMatches = existingCredentials.filter(
			(item) =>
				item.name === credentialName &&
				item.homeProject?.id === projects[index].id,
		);
		assert.ok(
			credentialMatches.length <= 1,
			'Fixture credentials must be unique',
		);
		const credential =
			state.credentials.find((item) => item.projectId === projects[index].id) ??
			credentialMatches[0] ??
			(await call('/rest/credentials', {
				method: 'POST',
				body: {
					name: `Fixture ${key.toUpperCase()} HTTP`,
					type: 'httpBasicAuth',
					projectId: projects[index].id,
					data: {
						user: key,
						password: process.env[`FIXTURE_${key.toUpperCase()}_SECRET`],
					},
				},
			}));
		if (!state.credentials.some((item) => item.id === credential.id))
			state.credentials.push({
				id: credential.id,
				name: credential.name,
				projectId: projects[index].id,
			});
		await writeFile('private/state.json', JSON.stringify(state), {
			mode: 0o600,
		});
		for (const failing of [false, ...(key === 'a' ? [true] : [])]) {
			const path = `fixture-${key}${failing ? '-fail' : ''}`;
			const workflowName = `Fixture ${key.toUpperCase()}${failing ? ' error' : ''}`;
			const workflowMatches = existingWorkflows.filter(
				(item) =>
					item.name === workflowName &&
					item.homeProject?.id === projects[index].id,
			);
			assert.ok(
				workflowMatches.length <= 1,
				'Fixture workflows must be unique',
			);
			const workflow =
				state.workflows.find((item) => item.path === path) ??
				workflowMatches[0] ??
				(await call('/rest/workflows', {
					method: 'POST',
					body: {
						name: `Fixture ${key.toUpperCase()}${failing ? ' error' : ''}`,
						projectId: projects[index].id,
						settings: { executionOrder: 'v1' },
						nodes: [
							{
								id: randomUUID(),
								name: 'Webhook',
								type: 'n8n-nodes-base.webhook',
								typeVersion: 2.1,
								position: [0, 0],
								webhookId: randomUUID(),
								parameters: {
									path,
									httpMethod: 'GET',
									responseMode: 'lastNode',
									options: {},
								},
							},
							{
								id: randomUUID(),
								name: 'Read fixture',
								type: 'n8n-nodes-base.httpRequest',
								typeVersion: 4.2,
								position: [240, 0],
								parameters: {
									url: `http://fixture:8080/${failing ? 'fail-' : ''}${key}`,
									authentication: 'genericCredentialType',
									genericAuthType: 'httpBasicAuth',
									options: {},
								},
								credentials: {
									httpBasicAuth: { id: credential.id, name: credential.name },
								},
							},
						],
						connections: {
							Webhook: {
								main: [[{ node: 'Read fixture', type: 'main', index: 0 }]],
							},
						},
					},
				}));
			if (!state.workflows.some((item) => item.id === workflow.id)) {
				state.workflows.push({
					id: workflow.id,
					name: workflow.name,
					projectId: projects[index].id,
					path,
					failing,
					key,
				});
				await writeFile('private/state.json', JSON.stringify(state), {
					mode: 0o600,
				});
			}
			const persisted = await call(`/rest/workflows/${workflow.id}`);
			if (!persisted.activeVersionId)
				await call(`/rest/workflows/${workflow.id}/activate`, {
					method: 'POST',
					body: { versionId: persisted.versionId },
				});
		}
	}
	await writeFile('private/state.json', JSON.stringify(state), { mode: 0o600 });
	results.push(
		'Created two projects, two credentials and three published fixture workflows',
	);
} else {
	state = JSON.parse(await readFile('private/state.json', 'utf8'));
	assert.ok(process.env.OWNER_PASSWORD, 'Set the fixture owner password');
	// Version upgrades can invalidate saved sessions. Authenticate normally on
	// every verification run instead of mistaking public settings for features.
	await call('/rest/login', {
		method: 'POST',
		body: {
			emailOrLdapLoginId: 'owner@fork.test',
			password: process.env.OWNER_PASSWORD,
		},
	});
	state.cookie = cookie;
	await writeFile('private/state.json', JSON.stringify(state), { mode: 0o600 });
}

const settings = await call('/rest/settings');
assert.equal(settings.settingsMode, 'authenticated');
assert.equal(settings.enterprise.sharing, mode !== 'rollback');
assert.equal(
	settings.enterprise.projects.team.limit,
	mode === 'rollback' ? 0 : -1,
);
assert.equal(settings.enterprise.saml, false);
assert.equal(settings.enterprise.ldap, false);
results.push(
	'Settings expose requested capabilities and preserve SSO defaults',
);

for (const project of state.projects) {
	assert.equal((await call(`/rest/projects/${project.id}`)).name, project.name);
}
for (const credential of state.credentials) {
	const persisted = await call(`/rest/credentials/${credential.id}`);
	assert.equal(persisted.id, credential.id);
	if (mode !== 'rollback')
		assert.ok(JSON.stringify(persisted).includes(credential.projectId));
}
for (const workflow of state.workflows) {
	const persisted = await call(`/rest/workflows/${workflow.id}`);
	assert.equal(persisted.id, workflow.id);
	if (mode !== 'rollback')
		assert.ok(JSON.stringify(persisted).includes(workflow.projectId));
	{
		const response = await fetch(`${base}/webhook/${workflow.path}`);
		assert.equal(response.status, workflow.failing ? 500 : 200);
		if (!workflow.failing)
			assert.deepEqual(await response.json(), {
				project: workflow.key,
				authenticated: true,
			});
	}
}
results.push(
	'Projects and project-owned workflows reload; webhooks use the matching credentials',
);

for (const route of [
	'/rest/projects',
	'/rest/credentials',
	'/rest/insights/by-workflow',
	'/api/v1/workflows',
]) {
	await call(route, { authenticated: false, expected: [401, 403] });
}
results.push('Protected routes still require authentication');

if (!state.apiKey) {
	const created = await call('/rest/api-keys', {
		method: 'POST',
		body: {
			label: 'Fork fixture',
			scopes: ['workflow:list', 'execution:list'],
			expiresAt: Math.floor(Date.now() / 1000) + 86400,
		},
	});
	assert.ok(created.rawApiKey);
	state.apiKey = created.rawApiKey;
	await writeFile('private/state.json', JSON.stringify(state), { mode: 0o600 });
}
const publicResponse = await fetch(`${base}/api/v1/workflows?limit=100`, {
	headers: { 'X-N8N-API-KEY': state.apiKey },
	signal: AbortSignal.timeout(10000),
});
assert.equal(publicResponse.status, 200);
const publicWorkflows = await publicResponse.json();
assert.ok(
	state.workflows.every((workflow) =>
		publicWorkflows.data.some((row) => row.id === workflow.id),
	),
);
results.push(
	'Public API authenticates a real fixture key and lists the saved workflows',
);

if (mode === 'rollback') {
	const sql = `SELECT json_build_object(
		'projects', (SELECT json_agg(id) FROM project WHERE type = 'team'),
		'credentials', (SELECT json_agg(json_build_object('id', "credentialsId", 'projectId', "projectId")) FROM shared_credentials),
		'workflows', (SELECT json_agg(json_build_object('id', "workflowId", 'projectId', "projectId")) FROM shared_workflow));`;
	const ownership = JSON.parse(
		execFileSync(
			'docker',
			[
				'compose',
				'exec',
				'-T',
				'db',
				'psql',
				'-X',
				'-qAt',
				'-U',
				'fork_test',
				'-d',
				'fork_test',
				'-c',
				sql,
			],
			{ encoding: 'utf8' },
		),
	);
	for (const project of state.projects)
		assert.ok(ownership.projects.includes(project.id));
	for (const item of state.credentials)
		assert.ok(
			ownership.credentials.some(
				(row) => row.id === item.id && row.projectId === item.projectId,
			),
		);
	for (const item of state.workflows)
		assert.ok(
			ownership.workflows.some(
				(row) => row.id === item.id && row.projectId === item.projectId,
			),
		);
	await call('/rest/insights/by-workflow', { expected: [403] });
	await call('/rest/insights/by-time', { expected: [403] });
	results.push(
		'Official image restores the original Insights and project creation limits',
	);
	await mkdir('test-results', { recursive: true });
	await writeFile(
		'test-results/rollback.json',
		JSON.stringify({ mode, results, ownership }, null, 2),
	);
	console.log(JSON.stringify({ mode, results }, null, 2));
	process.exit(0);
}

const firstPage = await call('/rest/executions?limit=100');
const executions = { ...firstPage, results: [...firstPage.results] };
const cursors = new Set();
let nextCursor = firstPage.nextCursor;
while (nextCursor) {
	assert.ok(
		cursors.size < 100,
		'Synthetic ledger exceeds the pagination bound',
	);
	assert.ok(!cursors.has(nextCursor), 'Execution cursor repeated');
	cursors.add(nextCursor);
	const page = await call(
		`/rest/executions?limit=100&cursor=${encodeURIComponent(nextCursor)}`,
	);
	assert.equal(
		page.count,
		executions.count,
		'Execution ledger changed during read',
	);
	executions.results.push(...page.results);
	nextCursor = page.nextCursor;
}
assert.equal(executions.results.length, executions.count);
assert.equal(
	new Set(executions.results.map((execution) => execution.id)).size,
	executions.count,
);
let summary, byWorkflow, byTime;
for (let attempt = 0; attempt < 90; attempt++) {
	summary = await call('/rest/insights/summary');
	byWorkflow = await call('/rest/insights/by-workflow');
	byTime = await call('/rest/insights/by-time');
	if (
		summary.total.value === executions.count &&
		state.workflows.every((workflow) =>
			byWorkflow.data?.some((row) => row.workflowId === workflow.id),
		)
	)
		break;
	await delay(1000);
}
for (const workflow of state.workflows) {
	const row = byWorkflow.data.find((item) => item.workflowId === workflow.id);
	assert.ok(row, `Missing Insights row for ${workflow.name}`);
	assert.equal(row.projectId, workflow.projectId);
	assert.ok(row.total >= 1);
	assert.equal(workflow.failing ? row.succeeded : row.failed, 0);
}
assert.equal(
	summary.total.value,
	byWorkflow.data.reduce((sum, row) => sum + row.total, 0),
);
assert.equal(
	summary.failed.value,
	byWorkflow.data.reduce((sum, row) => sum + row.failed, 0),
);
assert.equal(summary.total.value, executions.count);
assert.equal(
	summary.failed.value,
	executions.results.filter((execution) => execution.status === 'error').length,
);
assert.ok(Array.isArray(byTime) && byTime.length > 0);
assert.equal(
	byTime.reduce((sum, row) => sum + row.values.total, 0),
	executions.count,
);
assert.equal(
	byTime.reduce((sum, row) => sum + row.values.failed, 0),
	summary.failed.value,
);
const projectRows = await call(
	`/rest/insights/by-workflow?projectId=${state.projects[0].id}`,
);
assert.ok(
	projectRows.data.length > 0 &&
		projectRows.data.every((row) => row.projectId === state.projects[0].id),
);
const yesterday = new Date(Date.now() - 12 * 3600000).toISOString();
await call(`/rest/insights/by-time?startDate=${encodeURIComponent(yesterday)}`);
const tooOld = new Date(Date.now() - 366 * 86400000).toISOString();
await call(`/rest/insights/by-time?startDate=${encodeURIComponent(tooOld)}`, {
	expected: [403],
});
results.push(
	'Native Insights returns persisted fixture workflow and time aggregates',
);
await mkdir('test-results', { recursive: true });
await writeFile(
	`test-results/${mode}.json`,
	JSON.stringify({ mode, results, summary, byWorkflow, byTime }, null, 2),
);
console.log(JSON.stringify({ mode, results }, null, 2));

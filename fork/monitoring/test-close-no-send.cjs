'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');

const sourceFixture = path.resolve(
	__dirname,
	'fixtures/health-before-close.js.txt',
);

function readBaselineCode() {
	if (!process.env.HEALTH_FIXTURE)
		return fs.readFileSync(sourceFixture, 'utf8');
	const workflow = JSON.parse(
		fs.readFileSync(process.env.HEALTH_FIXTURE, 'utf8'),
	);
	const healthNode = workflow.nodes.find(
		(node) => node.name === 'Evaluate health without resends',
	);
	assert.ok(healthNode, 'The health evaluator fixture is required');
	const code = healthNode.parameters.jsCode;
	const wrapper = '\n\n\nconst snapshot=$input.first().json;';
	const end = code.indexOf(wrapper);
	return end > 0 ? code.slice(0, end) : code;
}

const baselineCode = readBaselineCode();

function evaluatorFrom(code) {
	return new Function(`${code}\nreturn evaluateHealth;`)();
}

const baselineEvaluator = evaluatorFrom(baselineCode);
const { patchHealth } = require('./close-no-send.cjs');
const patchedCode = patchHealth(baselineCode);
const patchedEvaluator = evaluatorFrom(patchedCode);
const now = Date.parse('2026-10-24T21:00:00.000Z');
const updatedAt = new Date(now - 60_000).toISOString();

function row(id, status, extra = {}) {
	return {
		id,
		event_key: `synthetic:${id}`,
		call_id: `call-${id}`,
		source_lead_id: `lead-${id}`,
		status,
		attempt_count: 0,
		crm_record_id: '',
		last_error: 'auditable closure reason',
		received_at: updatedAt,
		updated_at: updatedAt,
		delivered_at: null,
		createdAt: updatedAt,
		updatedAt,
		next_retry_at: null,
		original_event_json: JSON.stringify({
			type: 'call_result',
			call: { id: `call-${id}`, started_at: updatedAt },
		}),
		frozen_payload: null,
		claim_id: null,
		claimed_at: null,
		...extra,
	};
}

function inputFor(ledger) {
	return {
		now,
		ledger,
		archive: [],
		sources: ['backup5m', 'daily', 'history14d'].map((key) => ({
			key,
			last_success_at: updatedAt,
		})),
		previous: [],
		acks: [],
		ledgerComplete: true,
		archiveComplete: true,
		sourcesComplete: true,
		previousComplete: true,
		acksComplete: true,
		dispositions: [],
		qualifiedMembership: [],
	};
}

function evaluate(ledger) {
	return patchedEvaluator(inputFor(ledger));
}

function openIncident(result, key) {
	return result.incidents.find((incident) => incident.key === key);
}

test('valid zero-attempt closures are held without a health incident', () => {
	const result = evaluate([
		row(236, 'closed_no_send', {
			last_error:
				'source_qualification_changed: current source result changed; not sent',
		}),
		row(301, 'closed_no_send', {
			last_error: 'webhook_scenario_or_qualification_changed',
		}),
	]);

	assert.equal(result.report.status, 'ok');
	assert.equal(result.report.counts.closed_no_send, 2);
	assert.equal(result.report.counts.held_closed_no_send, 2);
	assert.equal(result.report.counts.action_required_manual_review, 0);
	assert.equal(result.report.open_incidents, 0);
	assert.deepEqual(result.incidents, []);
	assert.deepEqual(result.notifications, []);
});

test('attempted closed rows remain a critical invalid metadata incident', () => {
	const result = evaluate([
		row(329, 'closed_no_send', {
			attempt_count: 1,
			claim_id: 'prior-claim',
			claimed_at: updatedAt,
			frozen_payload: '{}',
			last_error: 'closure reason after an attempted send',
		}),
	]);

	const incident = openIncident(result, 'queue:invalid_metadata');
	assert.equal(result.report.status, 'attention');
	assert.equal(result.report.counts.held_closed_no_send, undefined);
	assert.equal(incident?.severity, 'critical');
	assert.equal(openIncident(result, 'queue:unknown_status'), undefined);
});

test('closed rows without a nonempty string reason remain critical', () => {
	for (const last_error of ['  ', null, 0]) {
		const result = evaluate([row(301, 'closed_no_send', { last_error })]);

		const incident = openIncident(result, 'queue:invalid_metadata');
		assert.equal(result.report.status, 'attention');
		assert.equal(result.report.counts.held_closed_no_send, undefined);
		assert.equal(incident?.severity, 'critical');
	}
});

test('ambiguous attempted row 329 stays manual review', () => {
	const result = evaluate([
		row(329, 'manual_review', {
			attempt_count: 1,
			claim_id: 'prior-claim',
			claimed_at: updatedAt,
			frozen_payload: '{}',
			last_error: 'Unconfirmed HTTP outcome; reconcile CRM before retry',
		}),
	]);

	const incident = openIncident(result, 'queue:manual_review');
	assert.equal(result.report.counts.action_required_manual_review, 1);
	assert.equal(incident?.severity, 'warning');
	assert.equal(openIncident(result, 'queue:unknown_status'), undefined);
});

test('delivered rows keep their existing healthy classification', () => {
	const delivered = row(313, 'delivered', {
		attempt_count: 1,
		claim_id: 'delivered-claim',
		claimed_at: updatedAt,
		crm_record_id: 'crm-record',
		delivered_at: updatedAt,
		last_error: '',
	});

	const before = baselineEvaluator(inputFor([delivered]));
	const after = evaluate([delivered]);
	assert.deepEqual(after, before);
	assert.equal(after.report.status, 'ok');
	assert.equal(after.report.counts.delivered, 1);
});

test('patch preserves unrelated manual-review and notification behavior', () => {
	const manual = row(329, 'manual_review', {
		attempt_count: 1,
		claim_id: 'prior-claim',
		claimed_at: updatedAt,
		frozen_payload: '{}',
		last_error: 'Unconfirmed HTTP outcome; reconcile CRM before retry',
	});
	const before = baselineEvaluator(inputFor([manual]));
	const after = evaluate([manual]);
	assert.deepEqual(after, before);
});

test('patch is deterministic and fails when the evaluator branch drifts', () => {
	assert.equal(patchHealth(baselineCode), patchedCode);
	assert.throws(() => patchHealth(null), /must be a string/);
	const driftedCode = baselineCode.replace(
		"else if(status==='failed') buckets.failed.push(id);",
		"else if(status==='failed') buckets.failed.push(id); // drift",
	);
	assert.throws(() => patchHealth(driftedCode), /expected exactly once/);
	const duplicateAnchor =
		"else if(status==='failed') buckets.failed.push(id);\n" +
		"    else if(status==='quarantined') buckets.quarantined.push(id);\n" +
		"    else if(!['delivered','historical_review'].includes(status)) buckets.unknown.push(id);";
	assert.throws(
		() => patchHealth(`${baselineCode}\n${duplicateAnchor}`),
		/expected exactly once; found 2/,
	);
});

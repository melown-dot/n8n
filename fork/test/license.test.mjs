import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';

const require = createRequire(import.meta.url);
const root = '/usr/local/lib/node_modules/n8n';
const { License } = require(`${root}/dist/license.js`);
const makeLicense = () => Object.create(License.prototype);

for (const feature of [
	'feat:insights:viewSummary',
	'feat:insights:viewDashboard',
	'feat:insights:viewHourlyData',
	'feat:sharing',
	'feat:projectRole:admin',
]) {
	test(`the fork provides ${feature} without a certificate`, () => {
		assert.equal(makeLicense().isLicensed(feature), true);
	});
}

test('the fork permits more than one team project', () => {
	assert.equal(makeLicense().getTeamProjectLimit(), -1);
});

test('the dashboard permits the standard year range', () => {
	assert.equal(makeLicense().getValue('quota:insights:maxHistoryDays'), 365);
});

test('unrelated and negative capabilities keep their original default', () => {
	const license = makeLicense();
	for (const feature of [
		'feat:apiDisabled',
		'feat:saml',
		'feat:ldap',
		'feat:oidc',
		'feat:aiAssistant',
		'feat:projectRole:editor',
		'feat:projectRole:viewer',
		'feat:multipleMainInstances',
		'feat:externalSecrets',
		'feat:unknownFutureFeature',
	]) {
		assert.equal(license.isLicensed(feature), false, feature);
	}
});

test('the real provider still controls other capabilities and quotas', () => {
	const license = makeLicense();
	license.manager = {
		hasFeatureEnabled: (feature) => feature === 'feat:variables',
		getFeatureValue: (feature) =>
			({ 'quota:insights:retention:maxAgeDays': 90, 'quota:users': 7 })[
				feature
			],
	};
	assert.equal(license.isLicensed('feat:variables'), true);
	assert.equal(license.isLicensed('feat:apiDisabled'), false);
	assert.equal(license.getValue('quota:users'), 7);
	assert.equal(license.getValue('quota:insights:retention:maxAgeDays'), 90);
	assert.equal(license.getValue('quota:unknownFutureQuota'), undefined);
});

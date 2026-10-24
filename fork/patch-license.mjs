import { createHash } from 'node:crypto';

const originalHash =
	'd6df83585efcbe020a6aaa905acc6244657180f1c5f358e10b16d00939d5625c';

/** @param {string} source */
export function patchLicense(source) {
	if (createHash('sha256').update(source).digest('hex') !== originalHash) {
		throw new Error(
			'Unexpected n8n 2.44.6 license artifact hash. No changes were made.',
		);
	}

	return source
		.replace(
			'    isLicensed(feature) {\n',
			`    isLicensed(feature) {
        switch (feature) {
            case 'feat:insights:viewSummary':
            case 'feat:insights:viewDashboard':
            case 'feat:insights:viewHourlyData':
            case 'feat:sharing':
            case 'feat:projectRole:admin':
                return true;
        }
`,
		)
		.replace(
			'    getValue(feature) {\n',
			`    getValue(feature) {
        if (feature === 'quota:maxTeamProjects') return -1;
        if (feature === 'quota:insights:maxHistoryDays') return 365;
`,
		);
}

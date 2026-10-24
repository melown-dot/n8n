import { createHash } from 'node:crypto';

const originalHash =
	'906bd7bb065087858df8a7bcb7302d6fcaeae38de6fbc30ba6a9bc29a2764779';

/** @param {string} source */
export function patchLicense(source) {
	if (createHash('sha256').update(source).digest('hex') !== originalHash) {
		throw new Error(
			'Unexpected n8n 2.39.8 license artifact hash. No changes were made.',
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

'use strict';

const branchAnchor =
	"else if(status==='failed') buckets.failed.push(id);\n" +
	"    else if(status==='quarantined') buckets.quarantined.push(id);\n" +
	"    else if(!['delivered','historical_review'].includes(status)) buckets.unknown.push(id);";
const branchReplacement =
	"else if(status==='failed') buckets.failed.push(id);\n" +
	"    else if(status==='quarantined') buckets.quarantined.push(id);\n" +
	"    else if(status==='closed_no_send'){\n" +
	"      const reason=typeof row.last_error==='string'?row.last_error.trim():'';\n" +
	'      if(noPriorAttempt(row)&&reason.length>0)counts.held_closed_no_send=(counts.held_closed_no_send||0)+1;\n' +
	'      else buckets.invalid.push(id);\n' +
	'    }\n' +
	"    else if(!['delivered','historical_review'].includes(status)) buckets.unknown.push(id);";

function patchHealth(code) {
	if (typeof code !== 'string')
		throw new TypeError('Health code must be a string');
	const matches = code.split(branchAnchor).length - 1;
	if (matches !== 1)
		throw new Error(
			`Health evaluator branch anchor expected exactly once; found ${matches}`,
		);
	return code.replace(branchAnchor, branchReplacement);
}

module.exports = { patchHealth };

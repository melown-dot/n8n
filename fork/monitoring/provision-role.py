#!/usr/bin/env python3
"""Provision restricted Analytics role. LOCAL default; production parameters explicit.
Secret only in environment or private .env, SQL travels over stdin; never printed.
"""
from pathlib import Path
import os,re,subprocess
p=Path(__file__).resolve().parent
private=dict(line.split('=',1) for line in (p/'.env').read_text().splitlines() if '=' in line) if (p/'.env').exists() else {}
secret=os.environ.get('ANALYTICS_DB_PASSWORD',private.get('ANALYTICS_DB_PASSWORD',''))
container=os.environ.get('SOURCE_DB_CONTAINER','n8n2398-fork-test-db-1')
database=os.environ.get('SOURCE_DB_DATABASE','fork_test')
admin=os.environ.get('SOURCE_DB_ADMIN_USER','fork_test')
role=os.environ.get('ANALYTICS_DB_ROLE','monitoring_readonly')
assert secret and '\x00' not in secret
assert all(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',s) for s in [database,admin,role])
assert re.fullmatch(r'[A-Za-z0-9_.-]+',container)
columns={
'execution_entity':'id workflowId status startedAt stoppedAt deletedAt mode createdAt waitTill finished retryOf retrySuccessId jsonSizeBytes binaryDataSizeBytes workflowVersionId',
'execution_data':'executionId data',
'workflow_entity':'id name active isArchived parentFolderId createdAt updatedAt triggerCount description',
'workflow_history':'versionId workflowId authors name autosaved createdAt',
'credentials_entity':'id name type isManaged createdAt updatedAt',
'user':'id email password firstName lastName disabled mfaEnabled roleSlug',
'project':'id name type',
'project_relation':'projectId userId role',
'shared_workflow':'workflowId projectId role userId',
'workflow_statistics':'workflowId name count latestEvent',
'folder':'id name parentFolderId projectId createdAt updatedAt',
 'tag_entity':'id name',
 'workflows_tags':'workflowId tagId',
 'workflow_dependency':'id workflowId workflowVersionId publishedVersionId dependencyType dependencyKey dependencyInfo createdAt',
 'execution_metadata':'id executionId key value',
}
esc=lambda s:s.replace("'","''")
queries=[]
for table,cols in columns.items():
 names=','.join("'"+c+"'" for c in cols.split())
 queries.append(f"""SELECT string_agg(quote_ident(column_name),',') INTO cols FROM information_schema.columns WHERE table_schema='public' AND table_name='{table}' AND column_name IN ({names});
 IF cols IS NOT NULL THEN EXECUTE format('GRANT SELECT (%s) ON TABLE %I TO {role}',cols,'{table}'); END IF;""")
sql=f"""DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN CREATE ROLE {role} LOGIN; END IF; END $$;
ALTER ROLE {role} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION CONNECTION LIMIT 3 PASSWORD '{esc(secret)}';
ALTER ROLE {role} SET default_transaction_read_only=on;
ALTER ROLE {role} SET statement_timeout='5s';
ALTER ROLE {role} SET idle_in_transaction_session_timeout='5s';
GRANT CONNECT ON DATABASE {database} TO {role};
GRANT USAGE ON SCHEMA public TO {role};
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {role};
DO $$ DECLARE cols text; BEGIN
{chr(10).join(queries)}
END $$;
"""
result=subprocess.run(['docker','exec','-i',container,'psql','-U',admin,'-d',database,'-v','ON_ERROR_STOP=1'],input=sql,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
if result.returncode:raise SystemExit('Role provisioning failed (SQL diagnostics suppressed to protect credentials); check database identifiers/permissions privately')
print('Analytics read-only role provisioned; 3 connections, 5s timeouts, explicit column grants; no source writes')

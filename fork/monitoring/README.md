# Isolated n8n monitoring deployment

Environment: the accepted pilot uses synthetic local n8n2.44.6/PostgreSQL18.6. Production lifecycle, snapshots, networking and installation belong to the parent task. This directory contains no production data or credentials. Source and decision/check/resource evidence are in the existing `outputs/SUMMARY.md` monitoring section.

## Close a reviewed delivery without sending it

The terminal ledger status `closed_no_send` records an approved decision to skip delivery. Keep the original events and the prior reason. Use this status only when `attempt_count` is zero and the row has no claim, frozen payload, CRM receipt, or delivery time. Keep a nonempty reason in `last_error`.

`close-no-send.cjs` adds this status to the existing health evaluator. It stops if the expected code branch has changed. Invalid closures remain critical health incidents. The sender still selects only `received` and `retrying` rows. The collector keeps the existing event key, so closure does not permit a new copy of the same call.

Run the portable evaluator tests from the repository root:

```sh
pnpm exec node --test fork/monitoring/test-close-no-send.cjs
```

Publish the compatible health evaluator before changing a ledger status. Use exact row versions and preserve every field outside status, reason, and update times. A rollback must restore the held rows before removing support from the health evaluator. Never reopen an attempted row to perform this operation.

## Build and source verification

`analytics-source.tar.gz` is the original Git archive of Analytics revision `c8d00ee98c96769949b04599cc3f4bf7fc8e2067`; its SHA is verified before extraction. The two reviewed patches update dependencies and enforce the optional owner-only/live-source policy plus a bounded PG pool. The Dockerfile pins the Node base and replaces the browser's vendored sanitizer from the patched module after copying the original source. Official dashboard JSONs are copied unchanged from Observability revision `cc18e2296a09aec9bc1cd6b5c0462a08d8a6660f`; alert provisioning is excluded.

```sh
cd /path/to/monitoring
shasum -a 256 -c recipe.sha256
PLATFORM=linux/amd64 ANALYTICS_BUILD_TAG=n8n-analytics-pilot:c8d00ee98c96-patched ./build.sh
```

Use `PLATFORM=linux/arm64` for the local pilot. No floating image is deployed. Local tested image IDs and final checks are in SUMMARY. A rebuild on another host must repeat runtime acceptance; image IDs may differ by build metadata.

## Private configuration

Create a private `.env` with fresh local deployment secrets; never copy the synthetic n8n owner or database passwords to production.

```sh
umask 077
python3 - <<'PY'
from pathlib import Path
import secrets
p=Path('.env')
assert not p.exists(), 'Preserve existing secrets and volume authentication'
p.write_text('\n'.join(k+'='+secrets.token_hex(32) for k in ['GRAFANA_PASSWORD','PROM_PASSWORD','ANALYTICS_DB_PASSWORD','DASHBOARD_JWT_SECRET'])+'\n')
p.chmod(0o600)
PY
```

Append environment values appropriate to the host (examples are identifiers, not credentials):

```text
SOURCE_DB_CONTAINER=<existing-postgres-container>
SOURCE_DB_ADMIN_USER=<existing-db-owner>
SOURCE_DB_HOST=<postgres-network-alias>
SOURCE_DB_DATABASE=<existing-n8n-database>
ANALYTICS_DB_ROLE=monitoring_readonly
MONITORING_NETWORK=<dedicated-internal-docker-network>
N8N_EDITOR_BASE_URL=https://n8n.melown.ru
MONITORING_RESTART_POLICY=unless-stopped
ANALYTICS_IMAGE=sha256:<verified-local-image-id>
```

Set runtime `ANALYTICS_IMAGE` to the exact local image ID printed/read back after the reviewed build. Compose requires it and uses pull_policy never; the build tag is only a build/transfer handle. Verify architecture against the target host before startup. Compose reads `.env`; `provision-role.py` reads private secrets from that file and connection identifiers from the environment, so load identifiers privately before provisioning. Do not print/source untrusted files. The deployment owner may run a short Python wrapper that parses `.env` and supplies `os.environ` for the script; passwords travel only over Docker stdin, not process arguments.

```sh
python3 - <<'PY'
import os,subprocess
from pathlib import Path
config=dict(line.split('=',1) for line in Path('.env').read_text().splitlines() if line and not line.startswith('#') and '=' in line)
subprocess.run(['python3','provision-role.py'],env={**os.environ,**config},check=True)
PY

docker compose config --quiet
docker compose up -d
```

Prerequisites owned by the deployment operator: full tested DB/files snapshot, verified isolated migration, dedicated internal network with existing n8n+Postgres attached, public n8n `/metrics` denied/authenticated before enabling metrics. Do not replace existing networks or remove n8n's existing egress. Metrics flags required by dashboards: `N8N_METRICS`, `N8N_METRICS_INCLUDE_WEBHOOK_METRICS`, `N8N_METRICS_INCLUDE_FORM_METRICS`, `N8N_METRICS_INCLUDE_WORKFLOW_INFO`, `N8N_METRICS_INCLUDE_POLL_TRIGGER_METRICS`, `N8N_METRICS_INCLUDE_SCHEDULER_METRICS`, `N8N_METRICS_INCLUDE_SYSTEM_TASK_METRICS`, all `true`. Update `prometheus.yml` target if the n8n network alias differs from `n8n:5678`.

## Acceptance and access

Backends have no published ports. Loopback URLs: Grafana `http://127.0.0.1:13000` (user `pilot`, private `GRAFANA_PASSWORD`), Prometheus `http://127.0.0.1:19090` (Basic user `pilot`, private `PROM_PASSWORD`), Analytics `http://127.0.0.1:13001` (existing n8n owner login). Access a remote deployment through SSH local port forwarding. No public monitoring route is configured. The proxy replaces caller forwarding headers before trust-proxy-aware applications see them.

Local synthetic acceptance only: `python3 prove.py`; it reads the adjacent synthetic owner's private state and must not be pointed at production. `python3 load.py` intentionally creates exactly100 synthetic executions (80success/20error),4concurrency/~25seconds; never run it against production. `python3 sample.py <label> --samples 8` measures Docker CPU, cgroup memory and summed process RSS; shared RSS pages may be counted multiple times. Production acceptance must use read-only DB/source cutoffs and owner login on the actual host, with no synthetic workload or data extraction to local.

Independent targeted checks: `node --test test-gateway.mjs`. The image includes `test/owner-policy.test.js` (run with temporary writable `/tmp`); upstream `npm test` covers207 tests. Exact audited package0 advisories is not a whole-image/OS CVE guarantee. Verify the actual served `/vendor/purify.min.js` is3.4.17 with SHA256 `cd37898f9782adf54820c413191b5691803f0f137c799d6edcfd38523b4c2c31` and equals the patched module bytes.

## Limits and rollback

Service caps total1152MiB and1.1CPU: Prom256MiB/.25CPU; Grafana512MiB/.25CPU with GOMAXPROCS2/GOMEMLIMIT256MiB and256pids; Analytics320MiB/.5CPU; relay64MiB/.1CPU. Default stage restart policy is `no`; production can explicitly choose `unless-stopped`. There is no n8n dependency on monitoring, so monitoring stop/failure cannot stop the n8n container, though shared CPU/disk/DB capacity can still contend.

Interactive production UI acceptance found that Grafana13.2.4 can hit a320MiB cap during login and dashboard discovery. The process had repeated cgroup memory-limit events and API timeouts without an OOM kill. Raising only the Grafana cap to512MiB restored authenticated APIs in0.10–0.28seconds without a restart. Keep GOMEMLIMIT256MiB and the CPU cap unchanged. Check cgroup memory events and API latency under actual browser use; an idle-only sample does not prove UI capacity.

Prom retention24hours/128MB excludes head/WAL overhead. Analytics writer also enforces SQLite max_page_count32768 with page_size4096 before migrations on every boot: the main database cannot grow past128MiB, and an existing larger replica fails startup. The cloned-volume edge test hit SQLITE_FULL at a reduced1MiB cap without changing the accepted ledger. WAL is bounded by128MiB kernel fsize, its SHM index is finite; only dashboard.sqlite/-wal/-shm are runtime data writers, with structured logs and raw debug export disabled. No automatic backup/VACUUM creates extra copies. At the cap ETL fails visibly and retains accepted history; an operator must archive/replace/raise capacity after review. Per-file fsize caps are256MiB(Prom) and128MiB(SQLite/Grafana); Docker logs2x5MiB/service live separately. Named Docker volumes have no general filesystem quota, but this application writer has the stated SQLite/WAL bounds. Analytics preserves aggregate execution history until the capacity gate; error input/stack retained1day, metadata values disabled, source payload max256KiB per fetched trace, batches bounded200/20errors. PG pool2/rolelimit3/querytimeout5s. Native login reads password hashes; error grouping/profiling reads execution payloads. Encrypted credential data and workflow definitions are denied. Do not describe this access as metadata-only. Owner-only policy denies members/admins/unknown source roles and source outage; owner MFA is unsupported and denied. AI/alerts have no keys/channels/rules and the backend network has no external egress.

Backup/restore acceptance for the local synthetic volume is reproducible with `python3 volume-proof.py`; it stops only local Analytics, restores a separate volume with the same immutable image, verifies identical ID/status ledger and integrity across two starts, then exercises a reduced SQLite cap and removes the clone. The private accepted backup is retained for rollback; never run this local fixture against production. A first installation has no older trusted Analytics image/schema to roll back to: rollback means stop/remove the new service and preserve its volume; future upgrades require an image-matched volume snapshot and tested restore before switching.

Stop without deleting history: `docker compose down` (no `-v`). Preserve named volumes and `.env` for restart. Role rollback: use the operator's PostgreSQL connection to `REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM monitoring_readonly`, revoke schema/database rights, then drop the dedicated role after disconnecting Analytics. Remove only the dedicated network attachments through the deployment owner. Source n8n DB data is never changed by monitoring rollback. Disk thresholds, long-horizon retention/backup and multi-host capacity remain operational responsibilities; the bounded synthetic sample is not a production load guarantee.

## Unified host and delivery dashboard

`unified_collector.py` collects a single aggregate snapshot. Use Python 3.10 or newer. It uses only the standard library. It reads Linux `/proc`, root filesystem capacity, and Docker GET requests. It uses the existing operator Docker authority. Do not mount the Docker socket into the gateway. Docker authority is root-equivalent.

The collector calls `unified-chain.sql` through a local PostgreSQL socket. The dedicated role can read only specified count, status, timestamp and Insights columns. It cannot read payloads, phones, credential data or user accounts. `unified-role.sql` is an operator-only first-install transaction. It rejects every existing role with the same name. Do not rerun it blindly or alter PostgreSQL authentication to make it work. The current host uses local/loopback trust and non-loopback SCRAM. The role has no password and cannot use non-loopback SCRAM authentication.

Run once per minute with `nice -n19`, an outer `timeout --kill-after=2s 25s`, and the collector's nonblocking file lock. Use absolute paths in cron. Set the metrics directory to0755, the snapshot to0644, and the cache to0600. Store no secrets in this directory. The collector retains the last successful component values on a source failure. It also sets component success to0 and retains the old success timestamp. The dashboard hides failed or stale component values. The entire snapshot becomes stale after180seconds. Empty latency samples are NaN, not0.

The optional internal endpoint contract is `metrics-contract.yml`. Bind it only to the verified isolated-network interface. The gateway has two networks; binding to all interfaces would also expose metrics to its ingress network. Mount the snapshot directory read-only, not an individual file. This preserves atomic rename visibility. Do not publish9109. Do not expose the snapshot through the public proxy routes. The production endpoint binds to172.25.0.0/16. Prometheus scrapes it once per minute. The deploy crontab runs one bounded collector command per minute with the marker `# melown-unified-metrics`. No public9109port is published.

The provisioned dashboard is `dashboards/melown-operations.json`, UID `melown-operations`. It refreshes once per minute. The existing Prometheus retention remains24hours. New host and Docker graph history starts at activation. Today counts use Europe/Moscow. Delivery delay is queue `received_at` to confirmed `delivered_at`, not call start to CRM. A CRM receipt is ledger evidence; the collector does not query Plex to verify each record again. Manual-review entries require reconciliation before any retry. No external notifications are configured.

Checks: from `fork/`, run `python3 -m unittest monitoring.test_unified_collector`. Run `promtool check metrics` against a fixture snapshot. Create `test-unified-chain.sql` only in a separate empty fixture database. Run the query with the dedicated role there. Check empty data, cross-midnight delivery, invalid delay, missing health, hourly/raw Insights aggregation, role collision and denied payload/credential/write access. Do not run this fixture in production.

Capacity evidence: one synthetic/local Docker pass0.218seconds; the production readonly aggregate query took29.069milliseconds with10307buffer hits and0physical reads. These are samples, not scale guarantees. Archive last-event and ledger aggregation read history without new indexes. Recheck query cost as history grows. Statement timeout2seconds, connection limit1, Docker64container/2MiBresponse limits and the process deadline bound each pass.

Deployment acceptance must compare host and source values with the dashboard, validate every PromQL expression, exercise stale/failure semantics, verify internal-only reachability and no public9109port, measure actual overhead, and confirm n8n settings and scraping unchanged. Rollback removes only the dedicated cron marker, listener, scrape job and dashboard. Revoke the metrics role grants and drop the role after disconnecting it. Preserve required snapshots and operational evidence.

Production acceptance on24October2026 verified all29panel query contracts, live source counts and browser access through the existing private SSH tunnel. The first production collector pass took0.5seconds and reached32700KiB maximum process RSS. The collector exits after each pass. The source backup freshness budget is22minutes:10minuteprioritythreshold+5minutetick+4minuteexecution+2minutecollector/scrapephase+margin. This avoids warnings during the permitted source schedule. Native n8n scraping stays10seconds. Grafana, Analytics, n8n, PostgreSQL and Prometheus processes stay running; Prometheus uses SIGHUP for config reload. Only the gateway was recreated to add read-only mounts.

Private access from the operator Mac: forward127.0.0.1:23000 to server127.0.0.1:13000 with SSH. Open `http://localhost:23000/d/melown-operations`. The tunnel must remain connected. This local address does not mean Grafana runs on the Mac. Existing Grafana authentication remains enabled.

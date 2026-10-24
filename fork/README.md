# n8n 2.44.6: native Insights and personal projects

This directory builds a distribution fork of n8n 2.44.6. It reuses the official
runtime, frontend and dependencies. It changes two methods in the compiled
`License` class. The Docker build checks the original file hash before it writes
the change. A different version, changed artifact or second application fails.

Upstream source revision: `b58d9e89d60e9a3485bb511c5091d8be0d9511e4`.

Official base digest:
`sha256:d02752c6ab446ae9db8deec2f9cc887c378f77cdcad31650814df936b7386dfb`.

| Change | Value |
|---|---|
| Insights summary, dashboard and hourly data | Enabled |
| Insights date range | Up to 365 days |
| Team project quota | Unlimited |
| Project admin capability | Enabled, as the native creation route requires it |
| Sharing capability | Enabled for project-owned workflows and credentials |

Authentication, request scopes and project permission checks still run. All
other capability and quota queries use the original license provider. The
change does not alter the plan name, retention settings, workflow schedules or
execution limits. A longer date range does not restore deleted history.

The original license files still apply. This technical change does not grant
additional rights to separately licensed code.

## Build and check

Run these commands in `fork/`. Use Node.js 24 or later, pnpm 10.18.3 and Docker
with Compose. The small package here has its own lockfile. It does not require a
monorepo install or a rebuild of unchanged upstream packages.

```sh
pnpm install --ignore-scripts
pnpm lint
pnpm typecheck
pnpm build
pnpm test:patch
pnpm test:runtime
```

The artifact tests use the pinned official image. The runtime tests load the
real class from the fork image. Both test containers have no network access.
Before this change, seven requested-capability assertions fail on the official
image; the two unrelated-behavior assertions pass.

## Isolated local stand

This stand uses test data only. It binds the UI to `127.0.0.1:15678`. PostgreSQL
has no host port. n8n has only an internal Docker network. A fixed TCP relay
exposes the UI and supports WebSocket connections. The relay cannot select a
different upstream. The fixture server checks a different Basic Auth credential
for each project.

Create test secrets once. The command refuses to overwrite an existing file.
It does not print the values.

```sh
node --input-type=module <<'JS'
import { randomBytes } from 'node:crypto';
import { writeFileSync } from 'node:fs';
const keys = ['POSTGRES_PASSWORD', 'N8N_ENCRYPTION_KEY', 'OWNER_PASSWORD',
  'FIXTURE_A_SECRET', 'FIXTURE_B_SECRET'];
const lines = keys.map(key => `${key}=F!8${randomBytes(36).toString('base64url')}`);
writeFileSync('.env', `${lines.join('\n')}\n`, { mode: 0o600, flag: 'wx' });
JS
docker compose up -d
node --env-file=.env acceptance.mjs setup
```

The test owner is `owner@fork.test`. Its password is in the local `.env` file.
The file, browser state and API key stay out of Git. The fixture API key expires
after one day. An acceptance run after expiry needs a new test key.

Open <http://localhost:15678>. `setup` creates two named fixture projects, two
project-owned credentials and three published workflows. Two workflows return
the authenticated fixture result. One workflow produces an expected HTTP 500.
The check waits for native Insights compaction and compares workflow, time and
summary counters with the saved executions. It also checks project filtering,
date-range limits, anonymous access and an authenticated public API request.

```sh
node --env-file=.env acceptance.mjs verify
docker compose restart n8n
node --env-file=.env acceptance.mjs restarted
```

`setup` reads the native project, credential and workflow lists before creating
missing fixtures. It can recover when local progress state was lost. It refuses
ambiguous duplicate fixture names within a project. Keep the `.env` encryption
key and the Compose data volumes together.

## Test rollback

Use the same test database and data volume. Do not use these commands on a
production deployment.

```sh
N8N_FORK_IMAGE=docker.n8n.io/n8nio/n8n:2.44.6@sha256:d02752c6ab446ae9db8deec2f9cc887c378f77cdcad31650814df936b7386dfb \
  docker compose up -d --no-deps --force-recreate n8n
node --env-file=.env acceptance.mjs rollback
docker compose up -d --no-deps --force-recreate n8n
node --env-file=.env acceptance.mjs restored
```

The official image restores its original feature limits. The checks also read
the existing project-owned data, execute its webhooks and check the public API.
The fork must pass again after it is restored.

Use `docker compose stop` to stop this stand and preserve its test data. Do not
use a global Docker cleanup command.

## Scope and limits

- The acceptance data is synthetic. It does not prove delivery to a business
  system or acceptance of production workflows.
- This is an owner-only organization workflow. Multi-user role combinations
  are outside the tested scope.
- The normal single-owner project UI path is the target of this fork.
  Concurrent multi-user/project administration is outside this acceptance scope.
- The full upstream monorepo suite is not run. The original app artifacts stay
  unchanged except for the checked `License` methods. The local package checks,
  actual artifact tests, native API and browser acceptance cover this change.
- The internal network blocks native background catalog requests. Their network
  errors in this test stand do not indicate a workflow execution failure.

Production deployment is a separate step. This task does not replace or restart
a production service.

## Updating the fork

Current supported base: stable 2.44.6, verified against a persisted synthetic
2.39.8 stand. Keep the Compose project name `n8n2398-fork-test` when updating
that stand: it identifies the existing volumes, not the installed version.

This is a maintained image overlay, not a permanent guarantee for every future
release. To update, inspect upstream changes to the feature provider and its
project/Insights consumers, pin the new official digest, verify the new original
`dist/license.js` hash, update the version guard and build/test image references,
then run artifact/runtime and native acceptance checks. Unreviewed versions and
changed artifacts deliberately fail the build. Replacing this image with an
official image restores official feature limits. Automatic updates are disabled.

Before a version upgrade, take a consistent PostgreSQL backup and preserve the
matching n8n data volume and encryption key. Test restore into a separate DB.
Test the new image against the restored stand first, including project and
credential ownership, webhook credential use, execution/Insights agreement and
restart persistence. After database migrations, rollback means restoring the
matching pre-upgrade database/data snapshot and old image; do not assume an old
image can safely reuse a database migrated by a newer n8n.

The patch adds native Insights/hourly views with a 365-day query range and
unlimited team-project quota. Native sharing and project-admin capability are
required by project-owned workflows/credentials. It does not enable SSO, external
secrets, editor/viewer project roles, multi-main, AI credits or source control.
Execution/Insights storage retention remains the original configuration; a
365-day date picker does not promise that 365 days of data exist.

Optional isolated monitoring deployment: see [monitoring](monitoring/README.md).

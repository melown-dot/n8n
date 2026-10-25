# GitHub Actions policy for the distribution fork

This repository inherits upstream n8n workflows for release management,
Blacksmith runners, Azure benchmarks, Docker publishing, security mirroring,
upstream branch maintenance, and full-monorepo coverage. Those services and
credentials do not belong to the personal distribution fork.

## Automatic schedules

The inherited upstream cron triggers were removed from 23 workflows, including
benchmark provisioning/teardown, E2E coverage/performance, publishing pipelines,
and automated branch, PR, and GHCR cleanup. This prevents automatic upstream
infrastructure work when the fork's custom branch is its default branch.

Existing `workflow_dispatch`, `workflow_call`, and PR event handlers remain in
place when present. The Azure benchmark and destroy jobs, E2E coverage entry
jobs, and GHCR cleanup additionally require the upstream repository identity
so accidental manual invocations in this fork cannot execute those operations.

These workflows are not broken unit tests, and configuring Azure credentials
or Slack tokens just to make their scheduled runs pass is not appropriate.

## Checks that belong to this fork

`.github/workflows/fork-ci.yml` runs when `fork/**` changes or the CI workflow
changes. It uses a GitHub-hosted runner, a pinned Node.js setup action, the
project's pinned pnpm, and read-only token permissions. It runs:

- Portable monitoring unit tests without HTTP calls or credentials.
- Biome linting for the fork's source files.
- TypeScript check of the JavaScript sources.

It does not build, publish, deploy, or destroy any cloud resource. The fork
Docker image patch and runtime tests are documented in `fork/README.md`.
Those need the pinned upstream Docker image and remain deliberate local
validation steps, not prerequisites hidden behind unavailable Azure services.

Before enabling any inherited nightly workflow for this repository, review its
runner availability, required secrets, license constraints, mutation effects,
and costs. Re-enable its schedule only after explicitly deciding it is part of
the fork's maintenance strategy.

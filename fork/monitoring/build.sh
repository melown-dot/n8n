#!/bin/sh
set -eu
cd "$(dirname "$0")"
: "${PLATFORM:?Set PLATFORM=linux/amd64 or linux/arm64 explicitly}"
shasum -a 256 -c analytics-source.sha256
build_tmp=$(mktemp -d)
trap 'rm -rf "$build_tmp"' EXIT INT TERM
tar -xzf analytics-source.tar.gz -C "$build_tmp"
patch -d "$build_tmp" -p1 < analytics-dependencies.patch
patch -d "$build_tmp" -p1 < analytics-owner-only.patch
docker build --platform "${PLATFORM}" -f "$PWD/Analytics.Dockerfile" --build-arg GIT_SHA=c8d00ee98c96769949b04599cc3f4bf7fc8e2067+dependency-patch --build-arg APP_VERSION=2.0.0 -t "${ANALYTICS_BUILD_TAG:-n8n-analytics-pilot:c8d00ee98c96-patched}" "$build_tmp"

docker image inspect "${ANALYTICS_BUILD_TAG:-n8n-analytics-pilot:c8d00ee98c96-patched}" --format '{{.Architecture}} {{.Id}}'

FROM docker.n8n.io/n8nio/n8n:2.39.8@sha256:b73045abaddb40cb4024e86eea1b1f69093501a7339f685a4cd486b7743d23ae

USER root
COPY patch-license.mjs apply-patch.mjs /tmp/n8n-personal-fork/
RUN node /tmp/n8n-personal-fork/apply-patch.mjs \
    && node --check /usr/local/lib/node_modules/n8n/dist/license.js \
    && rm -r /tmp/n8n-personal-fork
USER node

LABEL org.opencontainers.image.source="https://github.com/melown-dot/n8n" \
      org.opencontainers.image.version="2.39.8-personal.1" \
      org.opencontainers.image.description="Native Insights and personal project organization"

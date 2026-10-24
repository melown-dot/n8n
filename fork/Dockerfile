FROM docker.n8n.io/n8nio/n8n:2.44.6@sha256:d02752c6ab446ae9db8deec2f9cc887c378f77cdcad31650814df936b7386dfb

USER root
COPY patch-license.mjs apply-patch.mjs /tmp/n8n-personal-fork/
RUN node /tmp/n8n-personal-fork/apply-patch.mjs \
    && node --check /usr/local/lib/node_modules/n8n/dist/license.js \
    && rm -r /tmp/n8n-personal-fork
USER node

LABEL org.opencontainers.image.source="https://github.com/melown-dot/n8n" \
      org.opencontainers.image.version="2.44.6-personal.1" \
      org.opencontainers.image.description="Native Insights and personal project organization"

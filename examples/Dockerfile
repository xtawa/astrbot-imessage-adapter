ARG ASTRBOT_IMAGE
FROM node:22-bookworm-slim AS node_runtime
FROM ${ASTRBOT_IMAGE}
COPY --from=node_runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node_runtime /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm

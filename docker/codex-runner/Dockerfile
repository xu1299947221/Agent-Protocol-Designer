FROM node:22-bookworm-slim

ARG CODEX_VERSION=0.110.0

ENV NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_FUND=false \
    NPM_CONFIG_AUDIT=false \
    HOME=/home/node

RUN npm install -g "@openai/codex@${CODEX_VERSION}" \
    && npm cache clean --force

WORKDIR /workspace/project
USER node

CMD ["sh", "-lc", "codex --version && echo 'APD Codex runner is ready. Set command in APD Docker 沙箱.'"]

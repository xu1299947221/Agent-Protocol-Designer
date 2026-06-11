FROM node:22-bookworm-slim

ARG CLAUDE_CODE_VERSION=2.1.162

ENV NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_FUND=false \
    NPM_CONFIG_AUDIT=false \
    HOME=/home/node

RUN npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
    && npm cache clean --force

WORKDIR /workspace/project
USER node

CMD ["sh", "-lc", "claude --version && echo 'APD Claude runner is ready. Set command in APD Docker 沙箱.'"]

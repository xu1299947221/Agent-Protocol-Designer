# APD Docker Sandbox Runner Images

APD 的 Docker 沙箱可以运行任意你填写的镜像和命令。这里提供两个基础 runner 镜像：

- `apd-codex-runner:latest`：预装 `@openai/codex`
- `apd-claude-runner:latest`：预装 `@anthropic-ai/claude-code`

## 构建

```bash
cd /home/data/api/agent-protocol-designer
./scripts/build_sandbox_images.sh all
```

只构建 Codex：

```bash
./scripts/build_sandbox_images.sh codex
```

只构建 Claude Code：

```bash
./scripts/build_sandbox_images.sh claude
```

可覆盖版本和镜像名：

```bash
CODEX_VERSION=0.110.0 CODEX_IMAGE=apd-codex-runner:latest ./scripts/build_sandbox_images.sh codex
CLAUDE_CODE_VERSION=2.1.162 CLAUDE_IMAGE=apd-claude-runner:latest ./scripts/build_sandbox_images.sh claude
```

## 在 APD Web UI 使用

打开 `Docker 沙箱` 抽屉：

Codex：

```text
执行器：codex
Docker 镜像：apd-codex-runner:latest
容器内执行命令：选择 codex 后页面会自动填入 `codex exec ... "$(cat /workspace/TASK.md)"`
```

Claude Code：

```text
执行器：claude
Docker 镜像：apd-claude-runner:latest
容器内执行命令：选择 claude 后页面会自动填入 `claude --print ... "$(cat /workspace/TASK.md)"`
```

真正执行工程任务时，容器命令会读取 `/workspace/TASK.md`。认证信息建议通过 APD 的 LLM 配置或专门的只读配置目录传入，不要挂载宿主机整个 home。

# GitHub 与本地 RSS 发布

GitHub 写入授权现已恢复，可选择公开 Pages 或本地网页。上线见 [QUICKSTART.md](QUICKSTART.md)；本地启动、收集与 ChatGPT 分析导入见 [QUICKSTART.md](QUICKSTART.md) 和 [LOCAL.md](LOCAL.md)。


## GitHub Actions 与 Pages

在仓库 Settings → Pages 将 Source 设为 GitHub Actions。工作流的 `push` 构建保存的分析；首次无收集记录时，hosted 模式的 `push` 也初始化候选。默认 hosted 模式的每日计划和手动运行收集 arXiv 候选，同时构建已有精选。ChatGPT 负责分析与写回，完整任务指令见 [HOSTED_ANALYSIS.md](HOSTED_ANALYSIS.md)。

默认无需 OpenAI API key。若以后选择 API 模式，设置 Actions Variable `DIGEST_MODE=api`，另行配置可用的 `OPENAI_API_KEY` secret；不要把 key 写入仓库。API 与 hosted 分析不要同时处理同一队列。

Actions 的状态保存需要工作流 `contents: write`，部署需要 `pages: write` 和 `id-token: write`。ChatGPT 连接的权限与 Actions token 是两条独立路径；当前仅确认 ChatGPT 写入已成功，云端工作流须实际运行检查。状态提交不 force push，遇并发更新应重读状态再合并。

首次 Pages 部署已成功，每日分析尚未启用。对外订阅应以 Pages deploy 的成功结果为准。

## 本机订阅

`qradar serve` 在 `127.0.0.1` 上运行，仅接受本机连接。默认网页地址为 `http://127.0.0.1:8765/`，RSS 地址为 `http://127.0.0.1:8765/feed.xml`。它提供的是订阅源，RSS 阅读器按自己的频率拉取，并不是推送通知。

如果 8765 端口被占用，改用 `qradar serve --port 8766`，订阅地址也随之改为 `http://127.0.0.1:8766/feed.xml`。启动脚本支持同样的参数。阅读器与服务运行在同一台电脑时才能使用该地址。

## 给别人订阅

本地可以生成标准 RSS 文件；公开订阅需要一个对订阅者可访问的服务器地址。若选择静态托管，只需用实际公开网址构建，再上传整个输出目录：

```bash
qradar build --state data/state.json --output publish-public --site-url https://YOUR-PUBLIC-HOST.example/quantum-radar
```

`site-url` 用于 RSS 自身地址和网页链接；构建命令不会自动开通服务器或上传文件。这个步骤可在以后选择托管服务后再做。当前没有公开网址，也没有对外发布的 RSS。

## 分析和定时更新

默认不用 API key：网页收集候选，导出分析请求，ChatGPT 阅读并生成 JSON，用户再导入本地平台。导入时检查论文 ID、版本、字段和来源，未处理候选继续保留。

当前没有配置自动抓取或自动分析。ChatGPT 网页任务不能直接更新你电脑上的本地目录；本地网页也不能直接调用你在 ChatGPT 中的对话额度。若以后需要无人值守的本地更新，应先选定能在电脑上运行的分析方式，再验证调度与写入，不要把手动导入宣称为全自动。

已有的可选 `qradar run` 是 API 客户端，仍需有效 key；它不是本地版的启动条件。`qradar collect`、`qradar import-analysis`、`qradar build` 和 `qradar serve` 都无需 key。

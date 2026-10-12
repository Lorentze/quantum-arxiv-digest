# GitHub 上线：无需个人 API key

目标仓库：[Lorentze/quantum-arxiv-digest](https://github.com/Lorentze/quantum-arxiv-digest)。2026-10-12 已验证连接可以真实写入仓库，之前的 403 已解决。代码已上传，首次 Pages 构建与部署已成功；ChatGPT 每日分析任务已启用，从 2026-10-13 上午开始。

## 1. 启用 Pages

打开 [Settings → Pages](https://github.com/Lorentze/quantum-arxiv-digest/settings/pages)。在 **Build and deployment → Source** 选择 **GitHub Actions**。

## 2. 检查第一次发布

打开 [Actions → Daily research digest](https://github.com/Lorentze/quantum-arxiv-digest/actions/workflows/digest.yml)。若首次提交因 Pages 尚未启用失败，启用后点击 **Re-run jobs**。也可 **Run workflow → main**，同时测试 arXiv 抓取与候选队列保存。

默认 `DIGEST_MODE=hosted`，无需设置 Secrets，也无需 `OPENAI_API_KEY`。hosted 模式首次无收集记录时，`push` 会初始化候选队列；后续 `push` 只构建已保存分析。计划和手动任务在 hosted 模式下收集候选。正式存档已有三篇本次 ChatGPT 会话分析的论文，均说明只读了官方摘要；五篇独立 demo 不计入正式存档。

检查 build 和 deploy 都成功，以 deploy 返回的网址为准。成功后的预期地址：

- 网站：https://lorentze.github.io/quantum-arxiv-digest/
- RSS：https://lorentze.github.io/quantum-arxiv-digest/feed.xml

这是公开 RSS，其他人和云端阅读器也可以订阅。发布成功前可能是 404；不能用提交成功代替部署验证。

## 3. 验证 ChatGPT 分析

工作流计划每天北京时间 09:17 抓取候选，但它本身不会在 hosted 模式生成技术分析。先手动运行工作流，确认 `data/state.json` 中有 `pending`，再按 [HOSTED_ANALYSIS.md](HOSTED_ANALYSIS.md) 分析少量真实候选并安全写回。

当前已验证一次抓取、候选分析、导入写回与 Pages 再发布，并已创建每日 ChatGPT 分析任务：北京时间上午约 10:30，实际启动可浮动；首次安排在 2026-10-13。任务每轮最多处理 30 篇，剩余保留。不把未经分析的候选当成 AI 摘要。无需重复创建该任务，可在 ChatGPT Scheduled 查看运行结果。

## 本地版

仍可解压本地包并双击 `Start_Quantum_Radar.command`（Mac）或 `Start_Quantum_Radar.bat`（Windows）。需要 Python 3.11+，不要求 API key。详见 [LOCAL.md](LOCAL.md)。本地 `127.0.0.1` 订阅仅供同一电脑使用，不是上面的公开订阅地址。

# Quantum arXiv Digest · 量子论文雷达

面向 Guoding Liu 研究方向的网页与本地阅读平台：收集 arXiv 新论文，保存中文技术分析，在浏览器中按主题检索，并生成 RSS。

**本地流程不需要 GitHub，也不需要 OpenAI API key。** 本地程序负责抓取、归档、网页和 RSS；你把它导出的分析请求交给 ChatGPT，再把 ChatGPT 返回的分析 JSON 导入网页。没有 API key 时，这一步需要手动完成，目前没有启用每日自动分析任务。

## GitHub 网页与公开 RSS

GitHub 仓库写入授权已在 2026-10-12 实际验证。当前代码支持 GitHub Pages 发布，**首次 Pages 部署已成功，ChatGPT 每日分析任务尚未创建**。GitHub Actions 默认只收集候选，不要求个人 OpenAI API key。

1. 在 [仓库 Pages 设置](https://github.com/Lorentze/quantum-arxiv-digest/settings/pages) 的 **Build and deployment → Source** 选择 **GitHub Actions**。
2. 打开 [Daily research digest 工作流](https://github.com/Lorentze/quantum-arxiv-digest/actions/workflows/digest.yml)，运行一次；若首次 push 因 Pages 未启用而失败，启用后重跑失败任务即可。
3. 确认 Pages 发布成功后，网站为 `https://lorentze.github.io/quantum-arxiv-digest/`，全部精选 RSS 为 `https://lorentze.github.io/quantum-arxiv-digest/feed.xml`。在发布成功前，这些是预期网址。
4. 按 [ChatGPT 分析说明](docs/HOSTED_ANALYSIS.md) 验证真实候选分析、导入和写回，再配置每日分析任务。仅抓取完成不会生成新的 AI 摘要。

正式存档已有三篇本次会话的真实分析；它们可随首次 Pages 部署发布，不代表每日分析已启动。详细上线步骤见 [QUICKSTART.md](docs/QUICKSTART.md)。本地功能也保留如下。

## 本地启动与使用

要求 **Python 3.11 或更新版本**。核心功能仅用 Python 标准库，无需安装第三方包。

1. 解压项目。在 Mac 上双击 `Start_Quantum_Radar.command`，Windows 上双击 `Start_Quantum_Radar.bat`。也可在项目目录打开终端，运行：

   ```bash
   PYTHONPATH=src python3 -m qradar serve --port 8765 --open
   ```

2. 浏览器打开 `http://127.0.0.1:8765/`。正式存档预置三篇本次 ChatGPT 会话整理的真实论文摘要，标明证据范围；它们不是每日自动运行的结果。`Preview.html` 另有五篇独立阅读示例。
3. 在网页点击 **收集候选**。首次默认回看最近七天；关键词匹配的论文进入待分析队列。
4. 点击 **导出待分析材料**，把下载的 `qradar-analysis-request.txt` 上传到 ChatGPT，让它按文件里的要求输出分析 JSON。
5. 将 JSON 保存为文件，回到网页点击 **导入 ChatGPT 分析**。程序先校验批次；符合研究相关性阈值的摘要进入正式存档、网页和 RSS，其余记录为已评估。

详细步骤和常见问题见 [本地使用说明](docs/LOCAL.md)。`--open` 只负责打开浏览器；终端窗口需保持运行。停止服务可在终端按 `Ctrl+C`，下次启动会读取原来的存档。

## RSS 怎么订阅

正式存档的全部精选 RSS：

```text
http://127.0.0.1:8765/feed.xml
```

网页还提供已有主题的独立 RSS，例如 `feeds/logical_qec.xml` 和 `feeds/magic_stabilizer.xml`。

**本地运行照样能生成 RSS。** 同一台电脑上的桌面 RSS 阅读器可以订阅这个地址，但检查更新时需要本地服务和电脑正在运行。Feedly、Inoreader 等由云端抓取的服务，以及另一台设备，无法访问你电脑的 `127.0.0.1`。如果以后希望手机、云端阅读器或其他人订阅，需要把生成的静态网页与 feed 文件放到可访问的服务器；不必使用 GitHub。

RSS 只包含已经分析并选入的正式条目。单纯收集候选不会产生新的技术摘要。五篇独立预览不自动计入正式订阅。

## 研究范围与技术摘要

在 `config/profile.json` 修改研究主题和问题：

| 主题 | 关注的问题 |
|---|---|
| Benchmarking / certification | SPAM-robust gate certification、randomized benchmarking、噪声与控制假设 |
| Logical QEC | syndrome learning、logical fidelity、decoupling、浅层编码 |
| Random circuits / designs | design order、depth、locality、ancillas、magic 成本 |
| Magic / stabilizer | 受限操作、资源 separation、量子优势 |
| Adaptive circuits | mid-circuit measurement、feedforward、态制备的资源 tradeoffs |
| Quantum learning / information access | shadows、data hiding、side information、量子数据的学习与认证 |

每篇技术摘要包含：**问题与 setting、主要结果、方法、必要假设、限制、与你研究的具体联系，以及后续问题**。默认中文，保留英文术语和参数；明确标注只读了 abstract，还是审阅了部分正文。技术解读不等于独立验证证明。

候选按基础 arXiv ID 去重，已处理论文不会反复分析。首次回看可能产生较多候选，未处理的候选保留在队列中。当前不自动追踪已处理论文的新版本；待分析候选再次抓到时会更新元数据。

## 文件与可调参数

| 路径或配置 | 用途 |
|---|---|
| `config/profile.json` | 个性化研究主题、关键词、研究问题和排除方向 |
| `data/state.json` | 正式摘要存档、待分析队列、去重与评估记录；建议备份 |
| `local-public/` | 本地服务生成的网页、公开摘要 JSON 和 RSS；可重新生成 |
| `examples/demo-state.json` | 五篇真实论文的独立阅读示例 |
| `source.lookback_days` / `source.max_catchup_days` | 正常回看天数与自动补抓上限 |
| `source.max_results` | 元数据完整性上限，超限明确报错 |
| `screening.min_keyword_score` | 进入候选队列的关键词阈值 |
| `screening.min_ai_score` | 已分析条目进入精选 feed 的相关性阈值 |
| `screening.max_analyses_per_run` | 每批分析数量上限，默认 30 |

修改 profile 影响后续候选，不会自动重分析已处理条目。随包仅包含适合用于筛选的研究偏好，不包含原始 CV、申请材料或邮箱。向 ChatGPT 上传分析请求前，也可先查看文件内容。

## 命令行与可选扩展

安装为命令行工具后，可使用 `qradar serve`、`collect`、`build`、`import-analysis` 等命令：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
qradar serve --port 8765 --open
```

可选 API 分析流程需要从 PDF 提取正文时，可安装 `python -m pip install '.[fulltext]'`。无此依赖时，程序仍可使用 arXiv HTML 或 abstract，并标记实际取得的证据范围。

如果以后有可用的模型 API key，`qradar run` 可启用程序内的 AI 调用。当前本地使用无需配置仓库、Pages 或 Secrets。有关外部发布见 [DEPLOYMENT.md](docs/DEPLOYMENT.md)。

代码采用 [MIT License](LICENSE)，论文与源站内容遵循各自许可。

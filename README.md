# Quantum Paper Digest

量子信息论文的阅读与分析平台。首页与 RSS 展示论文原始英文标题、英文 abstract 和原文链接；点击标题进入独立的中文 AI 技术报告，说明论文的问题、贡献、核心方法、适用假设和局限。

- [阅读平台](https://lorentze.github.io/quantum-arxiv-digest/)
- [订阅 RSS](https://lorentze.github.io/quantum-arxiv-digest/feed.xml)

## 内容与更新

研究方向覆盖 benchmarking、logical QEC、random circuits、magic/stabilizer、adaptive resources、quantum learning 和 quantum information access。来源为 arXiv，以及 Nature、Science、Nature Physics、PRX、PRL、PRX Quantum、Nature Communications 和 npj Quantum Information。每篇显示来源，支持来源筛选及来源 RSS。

北京时间每天 09:00 启动 ChatGPT 分析，Actions 提前在 08:17 收集两类候选。SciRate 和 19:00 的任务已取消。实际发布还需要阅读、分析和部署时间。

默认 hosted 模式不需要个人 OpenAI API key。Actions 收集，ChatGPT 阅读官方材料并写回。期刊来源失败当轮跳过并记录，不影响其他来源；全文受限时只依据公开摘要，明确标注范围。推送只有节选时不冒充完整原始 abstract。正式期刊报告增加“为何适合该期刊”，讨论新意、技术突破和物理意义，明确属于阅读判断。

详细来源和局限见 [JOURNAL_SOURCES.md](docs/JOURNAL_SOURCES.md)，完整每日任务指令见 [HOSTED_ANALYSIS.md](docs/HOSTED_ANALYSIS.md)。

## 阅读与 RSS

报告页保留所读论文版本和分析来源范围。中文内容解释研究问题、任务与访问模型、主要结果、核心技术、必要假设、局限、研究联系和后续问题。全文节选分析不表示证明已经独立核验。原文英文 abstract 保持独立，不用中文 AI 文本冒充原始摘要。

RSS 主链接是本站 `articles/<arxiv-id>.html`，原文链接放在条目正文中。已有 arXiv GUID 保持不变；期刊独立条目使用 `urn:doi:<DOI>`。预印本与正式版本匹配后合并来源，更新报告不会制造重复条目。已有阅读器可能缓存旧条目的 arXiv 主链接，需要刷新订阅缓存后读取新链接。

## 本地使用

Python 3.11 或更新版本；核心流程只使用标准库：

```bash
PYTHONPATH=src python3 -m qradar serve --port 8765 --open
```

Mac / Windows 启动脚本也可使用。首页的本地工具区可收集、导出和导入 ChatGPT 分析。详情见 [LOCAL.md](docs/LOCAL.md)。本机 RSS 为 `http://127.0.0.1:8765/feed.xml`；其他设备或云端阅读器应订阅上方公开 RSS。

安装 CLI：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install '.[fulltext]'
qradar collect --profile config/profile.json --state data/state.json --output public --site-url https://lorentze.github.io/quantum-arxiv-digest/
qradar build --state data/state.json --output public --site-url https://lorentze.github.io/quantum-arxiv-digest/
```

`config/profile.json` 配置研究筛选；`data/state.json` 保留正式报告、待分析队列和处理记录。默认每批最多处理 30 篇，未处理候选继续保留。已分析的论文不自动跟踪新版本。部署说明见 [DEPLOYMENT.md](docs/DEPLOYMENT.md)，验证说明见 [VALIDATION.md](docs/VALIDATION.md)。

代码采用 [MIT License](LICENSE)，论文与源站内容遵循各自许可。


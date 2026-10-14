# 正式发表来源

arXiv 之外监测 Nature、Science、Nature Physics、Physical Review X (PRX)、Physical Review Letters (PRL)、PRX Quantum、Nature Communications 和 npj Quantum Information。八种期刊均已实测官方 RSS 能返回有效 XML。

| 来源 | 官方 RSS |
| --- | --- |
| Nature | https://www.nature.com/nature.rss |
| Science | https://feeds.science.org/rss/science.xml 和 science-aop.xml |
| Nature Physics | https://www.nature.com/nphys.rss |
| PRX | https://feeds.aps.org/rss/recent/prx.xml |
| PRL | https://feeds.aps.org/rss/recent/prl.xml |
| PRX Quantum | https://feeds.aps.org/rss/recent/prxquantum.xml |
| Nature Communications | https://www.nature.com/ncomms.rss |
| npj Quantum Information | https://www.nature.com/npjqi.rss |

RSS 是滚动窗口，可能只保留少数条目。每日期刊收集还使用 Crossref 的期刊 ISSN 查询近期 published-online 记录。出版日期只使用实际 online/print/publication 日期，不用 deposited/indexed 时间；日期只有日精度时保留日精度。默认重叠查询 3 天，用稳定 DOI 去重。元数据登记延迟、推送截断或来源故障仍可能造成遗漏，不能保证当日完整覆盖。

Science 的新闻和其他杂志记录通过 DOI 与标题类型筛除；其部分论文正文页面目前返回 403，仍可以分析公开 DOI 元数据中的摘要。完整官方 abstract、Crossref abstract 与推送节选分别标注；摘要缺失时不会把标题当作摘要，也不会直接发表没有分析的条目。正文访问受限时不绕过付费墙或机器人验证。

每个来源独立收集，一次请求失败即跳过，记录实际状态与警告；不循环尝试，也不影响 arXiv 或其他来源。需长期禁用的期刊可在 source.journals.sources 中将 enabled 设为 false。SciRate 已关闭，没有晚间计划。

初筛和 AI 相关性门槛对期刊与 arXiv 一致。匹配 DOI、arXiv ID 或一致标题与兼容作者后，把正式发表信息并入原条目；不同 DOI 或不同作者的同名论文不会被标题合并。若更完整摘要之后到达，初筛会重新检查之前因元数据不足而未选中的记录。

论文报告必须增加 publication_significance，分析具体新意、突破的困难、物理意义、证据强度与期刊读者范围。判断基于所读论文，而非已知编辑理由；只有摘要时明确限制，不靠期刊声望推断结果重要性。已分析预印本的正式发表会通过 publication_reviews_pending 补充此部分，保持原报告及 RSS GUID。

"""Command-line entry points. Live, offline-build and preview are distinct."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .analysis import AnalysisError, ConfigurationError
from .pipeline import (StateError, atomic_json, collect_update, import_analysis_update,
                       read_json, run_update, validate_profile)
from .sources import SourceError, fetch_recent


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Personalized quantum-information arXiv digest")
    sub = root.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Fetch, analyze and publish a live update; API key required")
    run.add_argument("--profile", default="config/profile.json")
    run.add_argument("--state", default="data/state.json")
    run.add_argument("--output", default="public")
    run.add_argument("--site-url", required=True)
    run.add_argument("--lookback-days", type=int)
    run.add_argument("--source", choices=("arxiv", "scirate"), default="arxiv")
    collect = sub.add_parser("collect", help="Fetch and queue candidates without an AI key; publish only saved analyses")
    collect.add_argument("--profile", default="config/profile.json")
    collect.add_argument("--state", default="data/state.json")
    collect.add_argument("--output", default="public")
    collect.add_argument("--site-url", required=True)
    collect.add_argument("--lookback-days", type=int)
    collect.add_argument("--source", choices=("arxiv", "scirate"), default="arxiv",
                         help="arxiv: research-topic candidates; scirate: quant-ph range=3 with >10 total scites")
    imported = sub.add_parser("import-analysis", help="Validate and import ChatGPT or other external analyses without an API key")
    imported.add_argument("--batch", required=True)
    imported.add_argument("--profile", default="config/profile.json")
    imported.add_argument("--state", default="data/state.json")
    imported.add_argument("--output", default="public")
    imported.add_argument("--site-url", required=True)
    local = sub.add_parser("serve", help="Open a local webpage with candidate collection, analysis import and local RSS; no API key")
    local.add_argument("--profile", default="config/profile.json")
    local.add_argument("--state", default="data/state.json")
    local.add_argument("--output", default="local-public")
    local.add_argument("--port", type=int, default=8765)
    local.add_argument("--open", action="store_true", dest="open_browser", help="Open the local page in your browser")
    build = sub.add_parser("build", help="Render saved production state without network or AI")
    build.add_argument("--state", default="data/state.json")
    build.add_argument("--output", default="public")
    build.add_argument("--site-url", required=True)
    demo = sub.add_parser("demo", help="Render a labeled real-paper preview without network or AI API")
    demo.add_argument("--example", default="examples/demo-state.json")
    demo.add_argument("--output", default="demo-public")
    demo.add_argument("--site-url", default="http://localhost:8000")
    fetch = sub.add_parser("fetch", help="Fetch official arXiv metadata only; no AI analysis")
    fetch.add_argument("--profile", default="config/profile.json")
    fetch.add_argument("--output", required=True)
    fetch.add_argument("--lookback-days", type=int)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "run":
            result = run_update(args.profile, args.state, args.output, args.site_url, lookback_days=args.lookback_days, source=args.source)
            print(json.dumps(result["last_run"], ensure_ascii=False))
        elif args.command == "collect":
            result = collect_update(args.profile, args.state, args.output, args.site_url, lookback_days=args.lookback_days, source=args.source)
            print(json.dumps(result["last_run"], ensure_ascii=False))
        elif args.command == "import-analysis":
            result = import_analysis_update(args.batch, args.profile, args.state, args.output, args.site_url)
            print(json.dumps(result["last_analysis_batch"], ensure_ascii=False))
        elif args.command == "serve":
            from .local import serve
            serve(args.profile, args.state, args.output, args.port, open_browser=args.open_browser)
        elif args.command in {"build", "demo"}:
            from .render import build_site
            result = read_json(args.example if args.command == "demo" else args.state)
            if args.command == "demo":
                result["is_demo"] = True
            elif result.get("is_demo"):
                raise StateError("Use qradar demo to render a demo archive")
            build_site(result, Path(args.output), args.site_url)
            print(f"Built {len(result.get('papers', []))} papers in {args.output}; " + ("preview only" if args.command == "demo" else "saved archive"))
        else:
            profile = read_json(args.profile)
            validate_profile(profile)
            papers = fetch_recent(profile["source"]["categories"],
                                  args.lookback_days if args.lookback_days is not None else profile["source"].get("lookback_days", 7),
                                  profile["source"].get("max_results", 2500))
            atomic_json(args.output, papers)
            print(f"Fetched {len(papers)} official arXiv metadata records; no AI analysis")
        return 0
    except (ConfigurationError, AnalysisError, SourceError, StateError, OSError, ValueError) as exc:
        print(f"qradar: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

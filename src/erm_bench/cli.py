"""Command line: run benchmarks without the chat, e.g. over the SGX test set.

  erm-bench run "City Developments" --year 2025 --until export
  erm-bench resume <run_id> --until findings
  erm-bench show <run_id>
  erm-bench run "City Developments" --dry-run      # fake client: what a real run would use
"""

from __future__ import annotations

import argparse
import sys
import textwrap

from dotenv import load_dotenv

from .pipeline import Pipeline
from .schemas import Request
from .standard_setup import StandardSetup
from .views import Reply, findings_replies, scope_replies

STAGES = ["scope", "findings", "export"]


def print_replies(replies: list[Reply], width: int = 32) -> None:
    for r in replies:
        print(r.text, "\n")
        if r.table:
            headers = list(r.table[0])
            print(" | ".join(h[:width] for h in headers))
            for row in r.table:
                print(" | ".join(textwrap.shorten(str(row.get(h, "")), width) for h in headers))
            print()
        if r.file:
            print(f"File: {r.file}\n")


def advance(p: Pipeline, until: str) -> None:
    """Run stages up to `until`, accepting the AI's recommended peers at checkpoint 1.
    For unattended runs (the test set); the chat is where consultants make these calls."""
    target = STAGES.index(until)
    if p.store.status in ("created", "researching"):
        p.research()
    if target >= 1 and p.store.status == "awaiting_scope":
        p.lock_scope()
    if target >= 1 and p.store.status == "analysing":
        p.analyse()
    if target >= 2 and p.store.status in ("awaiting_findings", "exported"):
        print(f"Workbook: {p.export()}")


def show(p: Pipeline) -> None:
    status = p.store.status
    print(f"Run {p.store.run_id}: {status}\n")
    if status == "awaiting_scope":
        print_replies(scope_replies(p))
    elif status in ("awaiting_findings", "exported"):
        print_replies(findings_replies(p))


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="erm-bench")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="start a new benchmark")
    run.add_argument("client")
    run.add_argument("--year", type=int)
    run.add_argument("--peers", type=int)
    run.add_argument("--bucket", choices=["large", "mid", "small"])
    run.add_argument("--sector")
    run.add_argument("--until", choices=STAGES, help="default: scope (export with --dry-run)")
    run.add_argument("--dry-run", action="store_true",
                     help="run every stage with a fake Claude client and print the calls, tokens "
                          "and cost a real run would use; nothing is sent to the API")

    resume = sub.add_parser("resume", help="continue a run")
    resume.add_argument("run_id")
    resume.add_argument("--until", choices=STAGES, default="findings")

    show_cmd = sub.add_parser("show", help="print a run's current checkpoint")
    show_cmd.add_argument("run_id")

    serve = sub.add_parser("serve", help="run the web API (and the built frontend, if any)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    args = parser.parse_args(argv)
    if args.command == "serve":
        import uvicorn

        from .api import create_app

        uvicorn.run(create_app(), host=args.host, port=args.port)
        return 0
    progress = lambda m: print(f"… {m}", file=sys.stderr)  # noqa: E731

    if args.command == "run":
        setup = StandardSetup.load()
        request = Request(client_name=args.client, reporting_year=args.year, sector=args.sector,
                          market_cap_bucket=args.bucket,
                          peer_count=args.peers or setup.method["peers"]["default_count"])
        if args.dry_run:
            from .dryrun import dry_run

            return dry_run(request, args.until or "export", advance)
        p = Pipeline.start(request, setup, progress=progress)
        print(f"Run {p.store.run_id}", file=sys.stderr)
        advance(p, args.until or "scope")
    elif args.command == "resume":
        p = Pipeline.resume(args.run_id, progress=progress)
        advance(p, args.until)
    else:
        p = Pipeline.resume(args.run_id)
    show(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Command line: `examinator synth | selftest | run | status | serve | compare`."""
from __future__ import annotations

import argparse
import getpass
import sys
import tempfile
from pathlib import Path

from .core import batch, overrides
from .core.defs import Exam, load_exam
from .core.runner import Runner
from .core.workflow import question_status, total
from .core.workspace import Workspace

DEFAULT_URL = 'http://localhost:8241/v1'
DEFAULT_MODEL = 'google/gemma-4-26b-a4b-qat'


def open_model(args, root: Path):
    """The scripted model for `--fake`, else the loopback model if it answers, else None."""
    if getattr(args, 'fake', False):
        from . import synth
        return synth.fake_model(root)
    from .model.client import Client, probe
    if probe(args.model_url, args.model) is None:
        return None
    return Client(args.model_url, args.model)


def status_table(exam: Exam, ws: Workspace) -> str:
    cols = [(q, part) for q in exam.questions.values() for part in q.result]
    lines = ["\t".join(['submission', *[q.column(p) for q, p in cols], 'total'])]
    for sub in ws.store.submissions():
        by_q = {qid: question_status(q, ws.store.for_question(sub.id, qid), code_hash=exam.code_hash)
                for qid, q in exam.questions.items()}
        cells = []
        for q, part in cols:
            st = by_q[q.id][part]
            shown = '-' if st.value is None else str(st.value)
            cells.append(f"{shown} ({st.state})" if not st.counts else shown)
        points, complete = total(exam, by_q)
        lines.append("\t".join([sub.id, *cells, f"{points:g}{'' if complete else '+?'}"]))
    return "\n".join(lines)


def compare_models(args, exam: Exam, ws: Workspace) -> int:
    import time
    from . import compare
    from .model.client import probe
    entrants = [compare.parse_entrant(m, args.model_url, 'stage') for m in args.model]
    entrants += [compare.parse_entrant(m, args.model_url, 'ocr', args.ocr_prompt or compare.OCR_PROMPT)
                 for m in args.ocr]
    clients = {}
    for e in entrants:
        if probe(e.url, e.name) is None:
            print(f"model not available: {e.name} at {e.url}", file=sys.stderr)
            return 1
    if args.fake:
        from . import synth
        fake = compare.Entrant('scripted', '')
        entrants.append(fake)
        clients[fake] = synth.fake_model(ws.root)
    if not entrants:
        print("name at least one model: --model NAME[@URL] or --ocr NAME[@URL]", file=sys.stderr)
        return 1
    subs = args.submission or [s.id for s in ws.store.submissions()][:args.limit]
    out_dir = Path(args.out) if args.out else ws.root / 'exports' / time.strftime('compare-%Y%m%d-%H%M%S')
    results = compare.run(exam, ws, entrants, submissions=subs, questions=args.question or list(exam.questions),
                          make_client=lambda e: clients.get(e) or compare.client_for(e),
                          progress=print)
    path = compare.write(exam, ws, results, out_dir, images=not args.no_images)
    for row in compare.summary(results, compare.stored(ws, subs, args.question or list(exam.questions))):
        print("  ".join(f"{k}={v}" for k, v in row.items()))
    print(f"report: {path}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog='examinator')
    sub = ap.add_subparsers(dest='cmd', required=True)

    def common(p, workspace=True):
        p.add_argument('exam', help="path to the exam package")
        if workspace:
            p.add_argument('--workspace', '-w', required=True)
        p.add_argument('--model-url', default=DEFAULT_URL)
        p.add_argument('--model', default=DEFAULT_MODEL)
        p.add_argument('--fake', action='store_true', help="use the workspace's scripted model (synthetic workspaces)")

    p = sub.add_parser('synth', help="create a synthetic workspace from the exam's fixtures")
    p.add_argument('exam')
    p.add_argument('workspace')

    p = sub.add_parser('selftest', help="run the exam's fixtures end to end with the scripted model")
    p.add_argument('exam')
    p.add_argument('workspace', nargs='?', help="where to build the synthetic workspace (default: a temp dir)")

    p = sub.add_parser('run', help="run stages for submissions")
    common(p)
    p.add_argument('--submission', '-s', action='append', help="default: all")
    p.add_argument('--question', '-q', action='append', help="default: all")
    p.add_argument('--stage', action='append', help="default: all")
    p.add_argument('--missing-only', action='store_true')
    p.add_argument('--include-protected', action='store_true')
    p.add_argument('--parallel', type=int, default=2)
    p.add_argument('--dry-run', action='store_true', help="print the plan and stop")

    p = sub.add_parser('status', help="print the grade grid")
    common(p)

    p = sub.add_parser('compare', help="transcribe the same answers with several models; writes a report, not the store")
    p.add_argument('exam')
    p.add_argument('--workspace', '-w', required=True)
    p.add_argument('--model-url', default=DEFAULT_URL, help="endpoint for models given without @URL")
    p.add_argument('--model', action='append', default=[], metavar='NAME[@URL]',
                   help="a model that runs the exam's transcript stage as is (repeatable)")
    p.add_argument('--ocr', action='append', default=[], metavar='NAME[@URL][::PROMPT]',
                   help="a dedicated OCR model: one answer box per call, short prompt (repeatable)")
    p.add_argument('--ocr-prompt', default=None, help='the prompt for --ocr models (default "OCR:")')
    p.add_argument('--fake', action='store_true', help="add the workspace's scripted model (synthetic workspaces)")
    p.add_argument('--submission', '-s', action='append', help="default: the first --limit submissions")
    p.add_argument('--question', '-q', action='append', help="default: all")
    p.add_argument('--limit', type=int, default=5, help="how many submissions when none are named (default 5)")
    p.add_argument('--no-images', action='store_true', help="leave the scans out of the report")
    p.add_argument('--out', default=None, help="report folder (default: WORKSPACE/exports/compare-TIME)")

    p = sub.add_parser('serve', help="run the app")
    common(p)
    p.add_argument('--port', type=int, default=8580)
    p.add_argument('--author', default=None, help="name recorded on manual decisions (default: your login)")
    p.add_argument('--public-url', default=None, help="public URL when behind an authenticating proxy")

    args = ap.parse_args(argv)
    exam = load_exam(args.exam)

    if args.cmd == 'synth':
        from . import synth
        root = synth.generate(exam, args.workspace, synth.load_cases(exam))
        print(f"synthetic workspace: {root}")
        return 0

    if args.cmd == 'selftest':
        from . import synth
        root = Path(args.workspace) if args.workspace else Path(tempfile.mkdtemp(prefix='examinator-selftest-')) / 'ws'
        problems = synth.selftest(exam, root)
        print(f"workspace: {root}")
        for line in problems:
            print("FAIL", line)
        print("ok" if not problems else f"{len(problems)} mismatches")
        return 1 if problems else 0

    ws = Workspace(args.workspace, exam)
    ws.sync_roster()
    for s in overrides.refresh(exam, ws.store):
        print(f"override not applied: {s.path}: {s.reason}", file=sys.stderr)
    if args.cmd == 'compare':
        return compare_models(args, exam, ws)
    model = open_model(args, ws.root)

    if args.cmd == 'status':
        print(status_table(exam, ws))
        return 0

    if args.cmd == 'serve':
        import uvicorn
        from .web.app import create_app
        runner = Runner(exam, ws, model=model)
        app = create_app(exam, ws, runner, author=args.author or getpass.getuser(),
                         public_url=args.public_url)
        print(f"http://127.0.0.1:{args.port}/" + ("" if model else "   (model unavailable: model stages are off)"))
        try:
            uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')
        finally:
            runner.close()
        return 0

    runner = Runner(exam, ws, model=model)
    try:
        sel = batch.Selection(
            tuple(args.submission or [s.id for s in ws.store.submissions()]),
            tuple(args.question or exam.questions),
            tuple(args.stage or batch.stage_order(exam)),
            missing_only=args.missing_only, include_protected=args.include_protected)
        plan = batch.plan(exam, ws.store, sel, model_available=model is not None)
        for action in batch.ACTIONS:
            print(f"{action}: {plan.count(action)}")
        if args.dry_run:
            for item in plan.items:
                print(f"  {item.submission} {item.question} {item.stage}: {item.action} {item.reason}")
            return 0
        outcomes = batch.BatchRun(runner, plan, parallel=args.parallel).run()
        for o in outcomes:
            if not o.ok:
                print(f"  {o.submission} {o.question} {o.stage}: {o.status} {o.detail}")
        print(status_table(exam, ws))
    finally:
        runner.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())

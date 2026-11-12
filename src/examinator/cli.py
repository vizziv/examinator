"""Command line: `examinator synth | selftest | intake | run | status | serve | compare`."""
from __future__ import annotations

import argparse
import getpass
import sys
import tempfile
from pathlib import Path

from .core import batch, focus, grid, overrides
from .core.defs import Exam, load_exam
from .core.runner import Runner
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
    cols = [(q, c) for q in exam.questions.values() for c in q.checks]
    lines = ["\t".join(['submission', *[q.column(c.name) for q, c in cols], 'total'])]
    for row in grid.load(exam, ws.store):
        cells = []
        for q, c in cols:
            st = row.status[(q.id, c.name)]
            shown = focus.grade_text(st) or '-'
            cells.append(f"{shown} ({st.state})" if not st.counts else shown)
        lines.append("\t".join([row.submission.id, *cells, f"{row.points:g}{'' if row.complete else '+?'}"]))
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
    qids = args.question or list(exam.questions)
    out_dir = Path(args.out) if args.out else ws.root / 'exports' / time.strftime('compare-%Y%m%d-%H%M%S')
    results = compare.run(exam, ws, entrants, submissions=subs, questions=qids,
                          make_client=lambda e: clients.get(e) or compare.client_for(e),
                          progress=print)
    reference = compare.stored(ws, subs, qids)
    path = compare.write(exam, ws, results, out_dir, images=not args.no_images, reference=reference)
    for row in compare.summary(results, reference):
        print("  ".join(f"{k}={v}" for k, v in row.items()))
    print(f"report: {path}")
    return 0


def intake_cmd(args, exam: Exam, ws: Workspace) -> int:
    from .core import identity, intake, split
    if args.roster:
        n = identity.set_roster(ws, Path(args.roster).read_text(encoding='utf-8-sig'))
        print(f"roster: {n} people; first: " + "; ".join(identity.label(p) for p in identity.people(ws)[:3]))
    added = []
    for name in args.pdf:
        src, new = intake.add_source(ws, Path(name))
        print(f"{'added' if new else 'already here'}: {name} -> {src.id} ({src.pages} pages)")
        added.append(src)
    model = None if args.no_check else open_model(args, ws.root)
    if not args.no_check and model is None:
        print("model unavailable: proposals are every N pages, unchecked", file=sys.stderr)
    targets = added or ws.store.sources()
    for src in targets:
        art = intake.reguess(ws, src.id, model=model, length=args.length)
        doubts = split.doubts(art.body, src.pages)
        parts = split.pages(art.body, src.pages)
        flagged = [(k, p, d) for k, (p, d) in enumerate(zip(parts, doubts)) if d]
        print(f"{src.id} {src.name}: {len(parts)} submissions of {art.body['length']} pages"
              f"{', checked' if art.body['checked'] else ''}; {len(flagged)} to look at")
        for k, p, d in flagged:
            span = f"{p[0] + 1}-{p[-1] + 1}" if p else "-"
            print(f"  #{k + 1} pages {span}: {', '.join(split.DOUBTS[x] for x in d)}")
        if args.accept:
            try:
                r = intake.accept(ws, src.id, author=getpass.getuser())
            except intake.Busy as e:
                print(f"  not accepted: {e}", file=sys.stderr)
                continue
            print(f"  accepted: {len(r['new'])} new, {len(r['rewritten'])} rewritten, "
                  f"{len(r['removed'])} removed, {r['unchanged']} unchanged")
    if args.names:
        model = model or open_model(args, ws.root)
        if model is None or not exam.identity:
            print("names: needs the model and identity boxes (Exam(identity=[...]))", file=sys.stderr)
        else:
            for sid in identity.unread(ws):
                identity.read(ws, sid, model)
            view = identity.overview(ws)
            print(f"names: {view['left']} of {len(view['rows'])} to confirm (Scans page, Names)")
            for row in view['rows']:
                if row['who'] is None:
                    top = row['candidates'][0]['label'] if row['candidates'] else "-"
                    print(f"  {row['sub']}: {identity.DOUBTS[row['doubt']]}: {top}")
    if not args.accept:
        print(f"review and accept: examinator serve {args.exam} -w {args.workspace}   (Scans page)")
    return 0


def workspace_cmd(args, exam: Exam, ws: Workspace) -> int:
    """The commands that work on an open workspace."""
    if args.cmd == 'intake':
        return intake_cmd(args, exam, ws)
    ws.sync()
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
        # A new or empty workspace opens on Scans, where submissions come from.
        start = '' if ws.store.submissions() else 'scans'
        print(f"http://127.0.0.1:{args.port}/{start}" + ("" if model else "   (model unavailable: model stages are off)"))
        try:
            uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')
        finally:
            runner.close()
        return 0

    runner = Runner(exam, ws, model=model)
    try:
        subs = tuple(args.submission or [s.id for s in ws.store.submissions()])
        if not args.step:       # results gone outdated (a field or the exam's code changed) first
            for sub in subs:
                runner.refresh(sub)
        sel = batch.Selection(subs, tuple(args.question or exam.questions), tuple(args.step or batch.STEPS),
                              missing_only=args.missing_only, include_protected=args.include_protected)
        plan = batch.plan(exam, ws.store, sel, model_available=model is not None, regions=ws.fields)
        for action in batch.ACTIONS:
            print(f"{action}: {plan.count(action)}")
        if args.dry_run:
            for item in plan.items:
                print(f"  {item.submission} {item.question} {item.step}: {item.action} {item.reason}")
            return 0
        outcomes = batch.BatchRun(runner, plan, parallel=args.parallel).run()
        for o in outcomes:
            if not o.ok:
                print(f"  {o.submission} {o.question} {o.step}: {o.status} {o.detail}")
        print(status_table(exam, ws))
    finally:
        runner.close()
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
    p.add_argument('--stacks', action='store_true',
                   help="instead: synthetic stacks of scans to feed to `intake` (WORKSPACE/incoming/)")

    p = sub.add_parser('intake', help="add scanned PDFs holding many submissions, and split them")
    common(p)
    p.add_argument('pdf', nargs='*', help="scanned PDFs to add (kept unchanged under WORKSPACE/sources/)")
    p.add_argument('--roster', help="students: a CSV with a header (Name or First/Last Name, Student ID, Email), "
                                    "or plain lines of name and ID separated by a comma or tab")
    p.add_argument('--length', type=int, help="sheets per submission (default: the exam's pages; more means cover sheets come first)")
    p.add_argument('--no-check', action='store_true', help="do not read landmarks with the model")
    p.add_argument('--accept', action='store_true', help="write the submissions into pdfs/ as proposed")
    p.add_argument('--names', action='store_true',
                   help="read the name and ID boxes of written submissions with the model and list proposals "
                        "(people confirm them on the Scans page)")

    p = sub.add_parser('selftest', help="run the exam's fixtures end to end with the scripted model")
    p.add_argument('exam')
    p.add_argument('workspace', nargs='?', help="where to build the synthetic workspace (default: a temp dir)")

    p = sub.add_parser('run', help="read fields, then run checks, for submissions")
    common(p)
    p.add_argument('--submission', '-s', action='append', help="default: all")
    p.add_argument('--question', '-q', action='append', help="default: all")
    p.add_argument('--step', action='append', choices=batch.STEPS, help="read, check (default: both)")
    p.add_argument('--missing-only', action='store_true',
                   help="read only fields with no value; run only checks whose result is not current")
    p.add_argument('--include-protected', action='store_true',
                   help="read again what a person set or verified (it stays in history)")
    p.add_argument('--parallel', type=int, default=2)
    p.add_argument('--dry-run', action='store_true', help="print the plan and stop")

    p = sub.add_parser('status', help="print the grade grid")
    common(p)

    p = sub.add_parser('compare', help="transcribe the same answers with several models; writes a report, not the store")
    p.add_argument('exam')
    p.add_argument('--workspace', '-w', required=True)
    p.add_argument('--model-url', default=DEFAULT_URL, help="endpoint for models given without @URL")
    p.add_argument('--model', action='append', default=[], metavar='NAME[@URL]',
                   help="a model that runs the question's own reader as is (repeatable)")
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
    from . import synth
    try:
        return command(args, exam)
    except (synth.BadCase, synth.NoScript, FileExistsError) as e:
        print(f"examinator {args.cmd}: {e}", file=sys.stderr)
        return 2


def command(args, exam: Exam) -> int:
    if args.cmd == 'synth':
        from . import synth
        if args.stacks:
            paths = synth.stacks(exam, args.workspace)
            print("synthetic stacks:", *paths, sep="\n  ")
            print(f"next: examinator intake {args.exam} -w {args.workspace} --fake "
                  f"--roster {Path(args.workspace) / 'roster.csv'} {' '.join(map(str, paths))}")
            return 0
        root = synth.generate(exam, args.workspace, synth.load_cases(exam))
        print(f"synthetic workspace: {root}")
        print(f"next: examinator run {args.exam} -w {args.workspace} --fake")
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

    try:
        ws = Workspace(args.workspace, exam, create=args.cmd in ('intake', 'serve'))
    except FileNotFoundError as e:
        print(f"examinator {args.cmd}: {e}", file=sys.stderr)
        return 2
    try:
        return workspace_cmd(args, exam, ws)
    finally:
        ws.close()


if __name__ == '__main__':
    sys.exit(main())

"""Command line: `examinator synth | selftest | intake | run | serve`."""
from __future__ import annotations

import argparse
import getpass
import sys
import tempfile
from pathlib import Path

from .core import batch, focus, grid
from .core.defs import Exam, load_exam
from .core.runner import Runner
from .core.status import LABELS
from .core.workspace import Workspace

DEFAULT_URL = 'http://localhost:8241/v1'
DEFAULT_MODEL = 'google/gemma-4-26b-a4b-qat'


def open_model(args, root: Path):
    """The scripted model for `--fake`, else the loopback model if it answers, else None."""
    if getattr(args, 'fake', False):
        from . import synth
        return synth.fake_model(root)
    from .model.client import Client, unavailable
    why = unavailable(args.model_url, args.model)
    if why is not None:
        print(f"model unavailable: {why}", file=sys.stderr)
        return None
    return Client(args.model_url, args.model)


def status_table(exam: Exam, ws: Workspace) -> str:
    """The grade grid as text, printed after `run`."""
    cols = [(q, c) for q in exam.questions.values() for c in q.checks]
    lines = ["\t".join(['submission', *[q.column(c.name) for q, c in cols], 'total'])]
    for row in grid.load(exam, ws.store):
        cells = []
        for q, c in cols:
            st = row.status[(q.id, c.name)]
            shown = focus.grade_text(st) or '-'
            cells.append(f"{shown} ({LABELS[st.state]})" if not st.counts else shown)
        lines.append("\t".join([row.submission.id, *cells, f"{row.points:g}{'' if row.complete else '+?'}"]))
    return "\n".join(lines)


def intake_cmd(args, exam: Exam, ws: Workspace) -> int:
    from .core import identity, intake, roster, split
    if args.roster:
        n = identity.set_roster(ws, Path(args.roster).read_text(encoding='utf-8-sig'))
        print(f"roster: {n} people; first: " + "; ".join(roster.label(p) for p in identity.people(ws)[:3]))
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
        doubts = split.doubts(art.body, exam.pages)
        parts = split.runs(art.body)
        flagged = [(k, p, d) for k, (p, d) in enumerate(zip(parts, doubts)) if d]
        print(f"{src.id} {src.name}: {len(parts)} submissions of {art.body['length']} pages"
              f"{', pages read' if art.body['readings'] else ''}; {len(flagged)} to look at")
        for k, p, d in flagged:
            span = f"{p[0] + 1}-{p[-1] + 1}"
            print(f"  #{k + 1} pages {span}: {', '.join(split.DOUBTS[x] for x in d)}")
        if args.accept:
            try:
                r = intake.accept(ws, src.id)
            except intake.Busy as e:
                print(f"  not accepted: {e}", file=sys.stderr)
                continue
            print(f"  accepted: {len(r['new'])} new, {len(r['rewritten'])} rewritten, "
                  f"{len(r['removed'])} removed, {r['unchanged']} unchanged")
    if args.names:
        model = model or open_model(args, ws.root)
        if model is None or not identity.boxed(ws):
            print("names: needs the model and identity fields with regions (Exam(identity={...}))", file=sys.stderr)
        else:
            runner = Runner(ws, model=model)
            try:
                for sid in identity.unread(ws):
                    for o in identity.read(runner, sid):
                        if not o.ok:
                            print(f"  {sid}: {o.status} {o.detail}", file=sys.stderr)
            finally:
                runner.close()
            view = identity.overview(ws)
            print(f"names: {view['left']} of {len(view['rows'])} to confirm (Scans page, Names)")
            for row in view['rows']:
                if row['who'] is None:
                    match = next((c['label'] for c in row['candidates'] if c['key'] == row['match']), "-")
                    print(f"  {row['sub']}: {roster.DOUBTS[row['doubt']]}: {match}")
    if not args.accept:
        fake = " --fake" if args.fake else ""
        print(f"review and accept: examinator serve {args.exam} -w {args.workspace}{fake}   (Scans page)")
    return 0


def workspace_cmd(args, exam: Exam, ws: Workspace) -> int:
    """The commands that work on an open workspace (it has its overrides applied)."""
    for s in ws.skipped:
        print(f"override not applied: {s.path}: {s.reason}", file=sys.stderr)
    if args.cmd == 'intake':
        return intake_cmd(args, exam, ws)
    ws.sync()
    unknown = [q for q in args.question or () if q not in exam.questions] if args.cmd == 'run' else []
    if unknown:
        print(f"examinator run: no question {', '.join(unknown)} (questions: {', '.join(exam.questions)})",
              file=sys.stderr)
        return 2
    model = open_model(args, ws.root)

    if args.cmd == 'serve':
        import uvicorn
        from .web.app import create_app
        runner = Runner(ws, model=model)
        app = create_app(ws, runner, author=args.author or getpass.getuser(),
                         public_url=args.public_url)
        # A new or empty workspace opens on Scans, where submissions come from.
        start = '' if ws.store.submissions() else 'scans'
        print(f"http://127.0.0.1:{args.port}/{start}" + ("" if model else "   (no model: nothing is read)"))
        try:
            uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')
        finally:
            runner.close()
        return 0

    runner = Runner(ws, model=model)
    try:
        subs = tuple(args.submission or [s.id for s in ws.store.submissions()])
        sel = batch.Selection(subs, tuple(args.question or exam.questions), missing_only=args.missing_only,
                              include_protected=args.include_protected)
        plan = batch.plan(ws, sel, model_available=model is not None)
        for action in batch.ACTIONS:
            print(f"{action}: {plan.count(action)}")
        if args.dry_run:
            for item in plan.items:
                print(f"  {item.submission} {item.question}: {item.action} {item.reason}")
            return 0
        run = batch.BatchRun(runner, plan, parallel=args.parallel)
        outcomes = run.run()
        for o in outcomes:
            if not o.ok or o.read < o.asked:
                print(f"  {o.submission} {o.question} {o.step}: {o.status} {o.detail}")
        print(f"fields read: {run.got} of {run.asked}" + (" (the rest stay not read)" if run.got < run.asked else ""))
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
    p.add_argument('--length', type=int, help="sheets per submission (default: the exam's pages; sheets beyond them are extra)")
    p.add_argument('--no-check', action='store_true', help="do not read landmarks with the model")
    p.add_argument('--accept', action='store_true', help="write the submissions as proposed (into WORKSPACE/made/)")
    p.add_argument('--names', action='store_true',
                   help="read the name and ID boxes of written submissions with the model and list proposals "
                        "(people confirm them on the Scans page)")

    p = sub.add_parser('selftest', help="run the exam's fixtures end to end with the scripted model")
    p.add_argument('exam')
    p.add_argument('workspace', nargs='?', help="where to build the synthetic workspace (default: a temp dir)")

    p = sub.add_parser('run', help="read fields for submissions; checks follow by themselves")
    common(p)
    p.add_argument('--submission', '-s', action='append', help="default: all")
    p.add_argument('--question', '-q', action='append', help="default: all")
    p.add_argument('--missing-only', action='store_true',
                   help="read only fields with no value (resumes a read that stopped)")
    p.add_argument('--include-protected', action='store_true',
                   help="read again what a person set or verified (it stays in history)")
    p.add_argument('--parallel', type=int, default=2)
    p.add_argument('--dry-run', action='store_true', help="print the plan and stop")

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
        return workspace_cmd(args, ws.exam, ws)      # the workspace's own definition
    finally:
        ws.close()


if __name__ == '__main__':
    sys.exit(main())

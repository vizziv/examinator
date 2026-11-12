"""Compare transcription models on the same answers: `examinator compare`.

Each model transcribes the same submissions and questions, one model at a time.
Nothing is written to the workspace's store: the results go to a new folder
under `WORKSPACE/exports/` as `report.html` (side by side, with what the model
saw) and `results.json` (the same, with timings).

A model takes part in one of two ways:

- `stage`: the exam's own transcript stage runs with that model, prompt and
  images exactly as in a normal run.
- `ocr`: for a dedicated OCR model, which expects a short fixed prompt and one
  region at a time. Each answer box (or each page, when the question has no
  boxes) is sent alone with the given prompt and no system message.

The report holds whatever the workspace holds, so for real exams it is student
data: it stays inside the workspace.
"""
from __future__ import annotations

import base64
import difflib
import html
import io
import json
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from .core import runner
from .core.context import BadOutput, Blocked, Unavailable
from .core.defs import Exam, Question
from .core.workspace import Workspace
from .model.client import Client, ModelError

STAGE = 'transcript'
OCR_PROMPT = "OCR:"


@dataclass(frozen=True)
class Entrant:
    """One model in the comparison. `mode` is 'stage' or 'ocr'."""
    name: str
    url: str
    mode: str = 'stage'
    prompt: str = OCR_PROMPT

    @property
    def label(self) -> str:
        return self.name if self.mode == 'stage' else f"{self.name} (ocr)"


def parse_entrant(spec: str, default_url: str, mode: str, prompt: str = OCR_PROMPT) -> Entrant:
    """`NAME`, `NAME@URL`, and for an OCR model optionally `...::PROMPT` (its own prompt)."""
    spec, sep, own = spec.partition('::')
    prompt = own if sep else prompt
    name, _, url = spec.partition('@')
    if not name:
        raise ValueError(f"no model name in {spec!r}")
    return Entrant(name, url or default_url, mode, prompt)


@dataclass
class Result:
    submission: str
    question: str
    model: str
    seconds: float = 0.0
    calls: int = 0
    text: str | None = None
    error: str = ""             # structural only: never model output


class _Timed:
    """A model client that adds up the time spent in calls."""

    def __init__(self, client: Any):
        self.client, self.seconds, self.calls = client, 0.0, 0

    def complete(self, prompt, **kw):
        start = time.perf_counter()
        try:
            return self.client.complete(prompt, **kw)
        finally:
            self.seconds += time.perf_counter() - start
            self.calls += 1


def regions(ws: Workspace, submission: str, q: Question) -> list[tuple[str, Any]]:
    """What an OCR model is shown, one image at a time: `(label, image)`.
    The answer boxes when the question has any, else its pages."""
    boxes = [(f.label, ws.crop(submission, f.region)) for f in q.fields if f.region is not None]
    if boxes:
        return boxes
    pages, _ = ws.selected_pages(submission, q)
    return [(f"page {p}", ws.page_image(submission, p)) for p in pages]


def _run_stage(ws: Workspace, q: Question, submission: str, model: _Timed) -> str:
    """The stage as a normal run would run it, with the memo kept in memory."""
    stage = q.stage(STAGE)
    ctx, _ = runner.stage_context(ws, submission, q, stage, runner.Services(ws, model, memo={}))
    out = stage.fn(ctx)
    return out if isinstance(out, str) else json.dumps(out, indent=1, ensure_ascii=False)


def _run_ocr(ws: Workspace, q: Question, submission: str, model: _Timed, prompt: str) -> str:
    parts = []
    for label, image in regions(ws, submission, q):
        parts.append(f"{label}: {model.complete(prompt, images=[image]).strip()}")
    return "\n".join(parts)


def client_for(e: Entrant) -> Client:
    """The loopback client for an entrant. An OCR model gets no system message
    and no extra request fields."""
    if e.mode == 'ocr':
        return Client(e.url, e.name, extra_body={}, preamble=None)
    return Client(e.url, e.name)


def run(exam: Exam, ws: Workspace, entrants: Sequence[Entrant], *, submissions: Sequence[str],
        questions: Sequence[str], make_client: Callable[[Entrant], Any] | None = None,
        progress: Callable[[str], None] = lambda line: None) -> list[Result]:
    """Every entrant on every (submission, question) that has a transcript stage."""
    make_client = make_client or client_for
    items = [(s, exam.questions[qid]) for s in submissions for qid in questions
             if exam.questions[qid].stage(STAGE) is not None]
    results = []
    for e in entrants:
        client = make_client(e)
        for i, (submission, q) in enumerate(items, 1):
            timed = _Timed(client)
            r = Result(submission, q.id, e.label)
            try:
                r.text = (_run_ocr(ws, q, submission, timed, e.prompt) if e.mode == 'ocr'
                          else _run_stage(ws, q, submission, timed))
            except (ModelError, Blocked, Unavailable, BadOutput) as ex:      # structural messages
                r.error = f"{type(ex).__name__}: {ex}"
            except Exception as ex:         # anything else might quote student text: its type only
                r.error = type(ex).__name__
            r.seconds, r.calls = timed.seconds, timed.calls
            results.append(r)
            progress(f"{e.label}  {i}/{len(items)}  {submission} {q.id}  "
                     f"{r.seconds:.1f}s{'  ' + r.error if r.error else ''}")
    return results


def stored(ws: Workspace, submissions: Sequence[str], questions: Sequence[str]) -> dict[tuple[str, str], tuple[str, bool]]:
    """The workspace's own transcripts: `(submission, question) -> (text, verified)`."""
    out = {}
    for s in submissions:
        for qid in questions:
            a = ws.store.get(s, qid, STAGE)
            if a is not None and isinstance(a.body, str):
                out[(s, qid)] = (a.body, a.verified)
    return out


def summary(results: Sequence[Result], reference: dict[tuple[str, str], tuple[str, bool]]) -> list[dict]:
    """One row per model: counts, time per answer, and likeness to the workspace's
    transcripts (0 to 1, by characters) where the workspace has one."""
    rows = []
    for model in dict.fromkeys(r.model for r in results):
        mine = [r for r in results if r.model == model]
        good = [r for r in mine if r.text is not None]
        like = [difflib.SequenceMatcher(None, reference[(r.submission, r.question)][0], r.text, autojunk=False).ratio()
                for r in good if (r.submission, r.question) in reference]
        times = [r.seconds for r in good]
        rows.append({
            'model': model, 'answers': len(mine), 'failed': len(mine) - len(good),
            'median_seconds': round(statistics.median(times), 2) if times else None,
            'total_seconds': round(sum(r.seconds for r in mine), 1),
            'likeness': round(statistics.mean(like), 3) if like else None,
        })
    return rows


def _data_url(image: Any, width: int) -> str:
    image = image.convert('RGB')
    if image.width > width:
        image = image.resize((width, round(image.height * width / image.width)))
    buf = io.BytesIO()
    image.save(buf, format='JPEG', quality=70)
    return 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode('ascii')


_CSS = """
body { font: 14px/1.4 system-ui, sans-serif; margin: 16px; color: #1c2024; background: #fff; }
@media (prefers-color-scheme: dark) { body { color: #e6e8ea; background: #16191b; } th, td { border-color: #3a4044 !important; } }
table { border-collapse: collapse; margin: 8px 0 20px; }
th, td { border: 1px solid #d5d9dd; padding: 3px 8px; text-align: left; vertical-align: top; }
td.n { text-align: right; font-variant-numeric: tabular-nums; }
h2 { font-size: 15px; margin: 22px 0 4px; }
.cols { display: grid; gap: 8px; align-items: start; }
.col { min-width: 0; }
.col h3 { font-size: 12px; margin: 0 0 2px; font-weight: 600; }
pre { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; font: 12px/1.35 ui-monospace, Menlo, monospace;
      border: 1px solid #8884; border-radius: 4px; padding: 5px; }
.err { color: #b42318; }
details { margin: 4px 0 6px; } img { max-width: 100%; border: 1px solid #8884; }
"""


def report(exam: Exam, ws: Workspace, results: Sequence[Result], reference: dict, *, images: bool = True,
           image_width: int = 1000) -> str:
    """The side-by-side page. Self-contained: no scripts, images inline."""
    e = html.escape
    models = list(dict.fromkeys(r.model for r in results))
    out = [f"<!doctype html><meta charset='utf-8'><title>{e(exam.title)}: model comparison</title><style>{_CSS}</style>",
           f"<h1>{e(exam.title)}: model comparison</h1>",
           "<table><tr><th>model</th><th>answers</th><th>failed</th><th>median s</th><th>total s</th>"
           "<th title='likeness to the workspace transcript, by characters'>likeness</th></tr>"]
    for row in summary(results, reference):
        cells = [row['answers'], row['failed'], row['median_seconds'], row['total_seconds'], row['likeness']]
        out.append(f"<tr><td>{e(row['model'])}</td>" + "".join(
            f"<td class='n'>{'' if c is None else c}</td>" for c in cells) + "</tr>")
    out.append("</table>")
    by_item: dict[tuple[str, str], dict[str, Result]] = {}
    for r in results:
        by_item.setdefault((r.submission, r.question), {})[r.model] = r
    for (submission, qid), got in by_item.items():
        q = exam.questions[qid]
        out.append(f"<h2>{e(submission)} · {e(qid)} {e(q.title or '')}</h2>")
        if images:
            shown = "".join(f"<div>{e(label)}<br><img src='{_data_url(image, image_width)}'></div>"
                            for label, image in regions(ws, submission, q))
            out.append(f"<details open><summary>scan</summary>{shown}</details>")
        ref = reference.get((submission, qid))
        n = len(models) + (1 if ref else 0)
        out.append(f"<div class='cols' style='grid-template-columns: repeat({n}, minmax(0, 1fr))'>")
        if ref:
            out.append(f"<div class='col'><h3>workspace{' (verified)' if ref[1] else ''}</h3><pre>{e(ref[0])}</pre></div>")
        for model in models:
            r = got.get(model)
            if r is None:
                continue
            body = f"<pre>{e(r.text)}</pre>" if r.text is not None else f"<pre class='err'>{e(r.error)}</pre>"
            out.append(f"<div class='col'><h3>{e(model)} · {r.seconds:.1f}s</h3>{body}</div>")
        out.append("</div>")
    return "\n".join(out)


def write(exam: Exam, ws: Workspace, results: Sequence[Result], out_dir: Path, *, images: bool = True,
          reference: dict[tuple[str, str], tuple[str, bool]] | None = None) -> Path:
    """`report.html` and `results.json` in `out_dir`; returns the report's path.
    `reference`: the workspace's transcripts (`stored`), if the caller has them already."""
    if reference is None:
        reference = stored(ws, list(dict.fromkeys(r.submission for r in results)),
                           list(dict.fromkeys(r.question for r in results)))
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = {'exam': exam.id, 'summary': summary(results, reference),
            'results': [vars(r) for r in results]}
    (out_dir / 'results.json').write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding='utf-8')
    path = out_dir / 'report.html'
    path.write_text(report(exam, ws, results, reference, images=images), encoding='utf-8')
    return path

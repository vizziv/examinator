"""Standard stages: the pieces, and bundles that return lists of them.

    stages.math(key=...)            ==  [transcribe(), parse_math(...), check_math(...), grade(math.all_correct)]
    stages.pseudocode(entries, fn)  ==  [transcribe(...), parse_pseudocode(...), check(fn), grade(...)]
    stages.read_fields()            a parse of only the fields with a reader (printed fields)

Each factory returns a `Stage` whose function is a small frozen dataclass, so
its configuration is part of the question's definition (and its hash). When a
bundle does not fit, assemble the list by hand from the same pieces.

Artifact bodies written by these stages:

    'transcript'  str
    'parse'       {label: str | None}  (math, choices, printed fields)  or  str  (pseudocode source)
    'evidence'    {'data': ..., 'blocks': [...]}
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from ..core.context import BadOutput, Context
from ..core.defs import Stage
from ..core.evidence import Evidence, EvidenceView, Undecided
from ..core.reading import normalize
from . import fields as fields_
from . import prompts
from .blocks import Details, Table

BLANK = 'BLANK'


# --- transcribe ---------------------------------------------------------------

@dataclass(frozen=True)
class Transcribe:
    prompt: str = prompts.TRANSCRIBE_ANSWERS
    notes: str = ""
    crops: bool = False     # send each field's crop instead of whole pages

    def __call__(self, ctx: Context) -> str:
        if self.crops:
            images = [ctx.crop(f.region) for f in ctx.question.fields if f.region is not None and f.read is None]
        else:
            images = ctx.pages()
        return ctx.model(prompts.join(self.prompt, prompts.context(ctx.question, ctx.note), self.notes),
                         images=images).strip()


def transcribe(prompt: str = prompts.TRANSCRIBE_ANSWERS, *, notes: str = "", crops: bool = False) -> Stage:
    """Model, images in, text out. Records what is on the page, mistakes included."""
    return Stage('transcript', Transcribe(prompt, notes, crops), uses_model=True, editable=True)


# --- parse --------------------------------------------------------------------

@dataclass(frozen=True)
class ParseMath:
    prompt: str = prompts.PARSE_ANSWERS
    symbols: tuple[str, ...] = ()
    functions: tuple[str, ...] | None = None
    notes: str = ""

    def __call__(self, ctx: Context) -> dict:
        out = ctx.model(
            prompts.join(self.prompt, prompts.context(ctx.question, ctx.note),
                         prompts.notation(self.symbols, self.functions), self.notes),
            text=ctx.input('transcript'), expect='json')
        return answers_from_json(out, ctx.question.written)


def answers_from_json(text: str, labels: Sequence[str]) -> dict[str, str | None]:
    """Validate a parse reply structurally: `{"answers": {label: str | null}}`."""
    try:
        data = json.loads(text)
    except ValueError:
        raise BadOutput("parse output is not JSON") from None
    answers = data.get('answers') if isinstance(data, dict) else None
    if not isinstance(answers, dict):
        raise BadOutput("parse output has no answers object")
    out = {}
    for label in labels:
        v = answers.get(label)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            v = str(v)
        if v is not None and not isinstance(v, str):
            raise BadOutput("parse output has a non-text answer")
        out[label] = None if v is None or v.strip() in ('', BLANK) else v.strip()
    return out


def parse_math(prompt: str = prompts.PARSE_ANSWERS, *, symbols: Sequence[str] = (),
               functions: Sequence[str] | None = None, notes: str = "") -> Stage:
    """Model, text in, `{label: answer}` out. This is what graders edit and verify.
    The question's printed fields are read beside it (`Field.read`)."""
    return Stage('parse', ParseMath(prompt, tuple(symbols), None if functions is None else tuple(functions), notes),
                 inputs=('transcript',), uses_model=True, editable=True, shape='fields')


@dataclass(frozen=True)
class ReadFields:
    """Nothing the student writes: the runner reads every field with a reader."""

    def __call__(self, ctx: Context) -> dict:
        return {}


def read_fields() -> Stage:
    """A parse of only the fields with a reader, for a question whose fields are all
    printed (the exam's printing, which other questions read). Graders verify it."""
    return Stage('parse', ReadFields(), uses_model=True, editable=True, shape='fields')


@dataclass(frozen=True)
class ParsePseudocode:
    entries: tuple[str, ...]
    language: Any = None                 # a `pseudocode.Language`; None means the base language
    prompt: str = prompts.PARSE_PSEUDOCODE
    notes: str = ""
    then: Callable[[str], str] | None = None    # deterministic rewrite of the parsed source

    def __call__(self, ctx: Context) -> str:
        from . import pseudocode
        language = self.language or pseudocode.BASE
        source = ctx.model(
            prompts.join(self.prompt, language.description, prompts.context(ctx.question, ctx.note),
                         f"Requested functions: {', '.join(self.entries)}", self.notes),
            text=ctx.input('transcript'), expect='code')
        source = source.strip('\n') + '\n'
        return self.then(source) if self.then else source


def parse_pseudocode(entries: Sequence[str], *, language: Any = None,
                     prompt: str = prompts.PARSE_PSEUDOCODE, notes: str = "",
                     then: Callable[[str], str] | None = None, facets: Sequence[Any] | None = None) -> Stage:
    """Model, text in, source in the pseudocode language out. By default the
    parsed source's comments are a searchable facet (`pseudocode.comment_facet`)."""
    from . import pseudocode
    return Stage('parse', ParsePseudocode(tuple(entries), language, prompt, notes, then),
                 inputs=('transcript',), uses_model=True, editable=True, shape='code',
                 facets=tuple((pseudocode.comment_facet(),) if facets is None else facets))


# --- check --------------------------------------------------------------------

def keyed_by_value(key: Mapping[Any, Any]) -> bool:
    """A key whose every value is itself a mapping is one key per value of a field."""
    return bool(key) and all(isinstance(v, Mapping) for v in key.values())


def pick(keys: Mapping[str, Any], value: str) -> tuple[str, Any]:
    """The key for a field's value (names compared normalised), or `Undecided`."""
    for name, key in keys.items():
        if normalize(name) == normalize(value):
            return name, key
    raise Undecided(f"no key for {value!r}"[:120])


@dataclass(frozen=True)
class CheckMath:
    key: Mapping[Any, Any]               # {label or (label, ...): entry}, or {value: {...}} with `by`
    symbols: Mapping[str, str] | tuple[str, ...] = ()
    functions: tuple[str, ...] | None = None
    by: Any = None                       # with a key per value: what picks it (`fields.value`, ...)

    def validate(self, question, exam) -> None:
        """A key per value comes with `by`, and its names are the values the
        printed fields `by` names can take (called by `Exam.question`)."""
        per_value = keyed_by_value(self.key)
        if per_value != (self.by is not None):
            raise ValueError(f"{question.id}: " + ("a key per value needs `by=` to pick one" if per_value
                                                   else "`by=` picks one of several keys: give one key per value"))
        if self.by is None:
            return
        can = fields_.possible(exam, question, self.by, question.id)
        if can is None:
            return
        names = {normalize(str(n)) for n in self.key}
        missing, extra = sorted(can - names), sorted(names - can)
        if missing or extra:
            raise ValueError(f"{question.id}: the key is one per value of {getattr(self.by, 'name', 'by')}, but "
                             + "; ".join(([f"{missing} have no key"] if missing else [])
                                         + ([f"{extra} are not values it reads to"] if extra else [])))

    def __call__(self, ctx: Context) -> Evidence:
        from . import math
        name, key = pick(self.key, self.by(ctx)) if self.by is not None else (None, self.key)
        data = math.check_answers(ctx.input('parse'), key, symbols=self.symbols, functions=self.functions)
        rows = [[label, f.get('answer') or "", f.get('outcome') or "?", f.get('note') or ""]
                for label, f in data['fields'].items()]
        shown = [[label if isinstance(label, str) else ", ".join(label), show_entry(entry)]
                 for label, entry in key.items()]
        return Evidence(data, [Table(['', "answer", "", ""], rows),
                               Details("Key" if name is None else f"Key: {name}", Table(['', "key"], shown),
                                       audience='grader')])


def show_entry(entry: Any) -> str:
    """A key entry as graders see it. A callable entry may carry a `label`."""
    if isinstance(entry, str):
        return entry
    label = getattr(entry, 'label', None)
    if label:
        return str(label)
    accepted = getattr(entry, 'accepted', None)
    if accepted:
        return " | ".join(accepted)
    if hasattr(entry, 'value') and hasattr(entry, 'tol'):
        return f"{entry.value} ± {entry.tol}"
    return getattr(entry, '__name__', type(entry).__name__)


def check_math(key: Mapping[str, Any], *, by: str | fields_.Chooser | None = None,
               symbols: Mapping[str, str] | Sequence[str] = (),
               functions: Sequence[str] | None = None, timeout: float | None = None) -> Stage:
    """Compare parsed answers with a key, field by field.

    A key entry is an expression string, a `math.Choice`, or a function
    `answer -> outcome`. A tuple of labels maps to a function over those
    fields' answers (`math.check_group`). With `by`, `key` is one key per value
    of a field, and `by` picks it: a label of the question's own field, a
    `fields.value(label, question=...)` of another question's, `fields.agree(...)`,
    or any `fn(ctx) -> value` (raise `Undecided` when it cannot tell). `symbols` maps
    names to assumptions (`{'a': 'positive'}`), or lists names. `functions=[]` means
    no function calls, so juxtaposition is multiplication. A field carrying a review
    marker is undecided.
    """
    per_value = keyed_by_value(key)
    if not isinstance(symbols, Mapping):
        symbols = tuple(symbols)
    from . import math as m
    labels: list[str] = []
    for k in (k for sub in (key.values() if per_value else [key]) for k in sub):
        labels += [x for x in (k if isinstance(k, tuple) else (k,)) if x not in labels]
    return Stage('evidence', CheckMath(key, symbols, None if functions is None else tuple(functions),
                                       None if by is None else fields_.as_chooser(by)),
                 inputs=('parse',), isolate=True, timeout=timeout,
                 accepts_markers=True, facets=tuple(m.answer_facet(label) for label in labels))


def check(fn: Callable[[Context], Any], *, timeout: float | None = None,
          inputs: Sequence[str] = ('parse',), isolate: bool = True,
          accepts_markers: bool = False) -> Stage:
    """A custom check: `fn(ctx) -> Evidence`. Local code, no model. It may read
    other questions' parses (`ctx.read`); the library records what it read."""
    return Stage('evidence', fn, inputs=tuple(inputs), isolate=isolate, timeout=timeout,
                 accepts_markers=accepts_markers)


# --- grade --------------------------------------------------------------------

@dataclass(frozen=True)
class GradeRule:
    fn: Callable[[EvidenceView], Any]

    def __call__(self, ctx: Context) -> Any:
        return self.fn(EvidenceView(ctx.input('evidence')))


def grade(fn: Callable[[EvidenceView], Any]) -> Stage:
    """A grade rule: `fn(evidence) -> value`, or a dict of part name to value.

    A value is anything the part's result spec accepts: a `Grade`, a bool or
    number, or (for `Bool`) a standard outcome: 'correct', 'incorrect', 'blank', None.
    """
    return Stage('grade', GradeRule(fn), inputs=('evidence',))


# --- bundles ------------------------------------------------------------------

def math(key: Mapping[str, Any], *, by: str | fields_.Chooser | None = None,
         symbols: Mapping[str, str] | Sequence[str] = (),
         functions: Sequence[str] | None = None, notes: str = "",
         grade_rule: Callable[[EvidenceView], Any] | None = None,
         timeout: float | None = None) -> list[Stage]:
    """Transcribe, parse and check expressions and choices against a key
    (one key per value of a field with `by`, as in `check_math`)."""
    from . import math as m
    return [transcribe(notes=notes),
            parse_math(symbols=list(symbols), functions=functions, notes=notes),
            check_math(key, by=by, symbols=symbols, functions=functions, timeout=timeout),
            grade(grade_rule or m.all_correct)]


def pseudocode(entries: Sequence[str], check_fn: Callable[[Context], Any], *, language: Any = None,
               notes: str = "", then: Callable[[str], str] | None = None,
               grade_rule: Callable[[EvidenceView], Any], timeout: float | None = None) -> list[Stage]:
    """Transcribe and parse pseudocode, then run a custom check."""
    return [transcribe(prompts.TRANSCRIBE_PSEUDOCODE, notes=notes),
            parse_pseudocode(entries, language=language, notes=notes, then=then),
            check(check_fn, timeout=timeout),
            grade(grade_rule)]

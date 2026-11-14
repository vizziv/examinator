"""Handwriting readers: how a question's handwritten fields are read.

    exam.question('sum', ..., read=readers.answers(notes=NUMBERS))          # short answers
    exam.question('sampler', ..., read=readers.code(['sampler'], language=LANG))  # pseudocode

A reader reads all the fields it is given in one go and returns a `Reading`
per field it read (text, '' a blank, and a review note when the model was
unsure). Inside, it makes two model calls: one to transcribe the pages, one to
turn the transcript into values. The transcript is not stored, shown or
verified: one read, one value per field.

The second reply has one shape (`core.reading`): an object of field objects
`{"value": ..., "unsure": ...}`, keyed by label (answers) or by function name
(code). A field the reply does not state in that shape is left out: it stays
not read. The model transcribes; the program splits: the code reader has the
question's code converted once, function by function, and each field takes its
own functions from it (`Code.split`, by its lines: `functions`).

Each reader is a small frozen dataclass (a `core.reading.Reader`), so its
configuration (prompts, notes) is part of the definition and can be edited in
the app. Model calls
are tagged with a step ('transcribe', 'parse') for a scripted model; `script`
writes the replies that would read given values (`examinator.synth`).
"""
from __future__ import annotations

import ast
import json
import re
import warnings
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from ..core.context import ReadContext
from ..core.defs import Field
from ..core.reading import Reader, Reading, field_object, json_object, reading, readings
from . import prompts

EMPTY = '(empty)'       # how a transcript says a box is empty (the parse step turns it into "")


def _prompts(reader: Any, what: str) -> None:
    """A reader with no prompt reads nothing: refused where the definition is validated."""
    for name in ('prompt', 'parse_prompt'):
        if not getattr(reader, name).strip():
            raise ValueError(f"{what}: `{name}` is empty")


@dataclass(frozen=True)
class Answers(Reader):
    """Short answers: transcribe the question's pages (or each field's crop),
    then turn the transcript into one value per field."""
    prompt: str = prompts.TRANSCRIBE_ANSWERS
    parse_prompt: str = prompts.PARSE_ANSWERS
    symbols: tuple[str, ...] = ()
    functions: tuple[str, ...] | None = None
    notes: str = ""
    crops: bool = False     # send each field's crop instead of whole pages

    def validate(self, what: str) -> None:
        _prompts(self, what)

    @property
    def needs_region(self) -> bool:
        """With `crops`, it reads each field's region (a field needs one), not the pages."""
        return self.crops

    def read(self, ctx: ReadContext, fields: Sequence[Field]) -> dict[str, Reading]:
        labels = [f.label for f in fields]
        context = prompts.context(ctx.question, labels)
        images = [ctx.crop(f.region) for f in fields] if self.crops else ctx.pages()
        transcript = ctx.model(prompts.join(self.prompt, context, self.notes), images=images,
                               step='transcribe').strip()
        out = ctx.model(prompts.join(self.parse_prompt, context, prompts.notation(self.symbols, self.functions),
                                     self.notes), text=transcript, expect='json', step='parse')
        return readings(out, labels)

    def script(self, fields: Sequence[Field], given: Mapping[str, Any]) -> dict[str, str]:
        """The replies that read `given` ({label: text, '' or None blank})."""
        labels = [f.label for f in fields]
        return {'transcribe': "\n".join(f"{label}: {given.get(label) or EMPTY}" for label in labels),
                'parse': json.dumps({label: field_object(given.get(label) or "") for label in labels})}


def answers(*, symbols: Sequence[str] = (), functions: Sequence[str] | None = None, notes: str = "",
            crops: bool = False, prompt: str = prompts.TRANSCRIBE_ANSWERS,
            parse_prompt: str = prompts.PARSE_ANSWERS) -> Answers:
    """Short answers: expressions, numbers, choices. `symbols` and `functions`
    are notation hints for the prompt (never a key)."""
    return Answers(prompt, parse_prompt, tuple(symbols), None if functions is None else tuple(functions),
                   notes, crops)


@dataclass(frozen=True)
class Code(Reader):
    """Pseudocode: transcribe the question's pages, convert the transcript once,
    function by function, into the pseudocode language, and give each field its
    own functions (`split`)."""
    entries: Mapping[str, tuple[str, ...]] | tuple[str, ...]    # per field, or the same for every field
    language: Any = None                 # a `pseudocode.Language`; None means the base language
    prompt: str = prompts.TRANSCRIBE_PSEUDOCODE
    parse_prompt: str = prompts.PARSE_PSEUDOCODE
    notes: str = ""
    then: Callable[[str], str] | None = None    # deterministic rewrite of each field's source
    multiline = True                     # its fields' values span lines

    def validate(self, what: str) -> None:
        _prompts(self, what)

    def wanted(self, label: str) -> tuple[str, ...]:
        if isinstance(self.entries, Mapping):
            return tuple(self.entries.get(label, ()))
        return tuple(self.entries)

    def read(self, ctx: ReadContext, fields: Sequence[Field]) -> dict[str, Reading]:
        from . import pseudocode
        language = self.language or pseudocode.BASE
        labels = [f.label for f in fields]
        context = prompts.join(prompts.context(ctx.question, labels),
                               prompts.functions({label: self.wanted(label) for label in labels}))
        transcript = ctx.model(prompts.join(self.prompt, context, self.notes), images=ctx.pages(), step='transcribe')
        out = ctx.model(prompts.join(self.parse_prompt, language.description, context, self.notes),
                        text=transcript, expect='json', step='parse')
        return self.split(out, labels)

    def split(self, out: str, labels: Sequence[str]) -> dict[str, Reading]:
        """Each field's reading from the conversion reply `{function: {"value":
        source, "unsure": ...}}` (a function whose source does not define it is
        not stated). A field takes its requested functions and the helpers they
        call, transitively, but not a function another field requests; its
        review is theirs. A field with a requested function the reply does not
        state is not read; one whose requested functions are all empty (`pass`,
        comments) is blank."""
        defs = {name: r for name, obj in json_object(out).items()
                if (r := reading(obj)) is not None and name in functions(r.text)[0]}
        owner = {name: label for label in labels for name in self.wanted(label)}
        got = {}
        for label in labels:
            wanted = self.wanted(label)
            if not wanted or any(name not in defs for name in wanted):
                continue
            take, todo = set(wanted), list(wanted)
            while todo:
                for name in calls(defs[todo.pop()].text, defs):
                    if name not in take and owner.get(name, label) == label:
                        take.add(name)
                        todo.append(name)
            names = [name for name in defs if name in take]
            notes = [f"{name}: {defs[name].review}" if defs[name].review else name
                     for name in names if defs[name].review is not None]
            text = "\n\n".join(defs[name].text.strip('\n') for name in names) + "\n"
            if all(is_empty(defs[name].text) for name in wanted):
                text = ''
            got[label] = Reading(self.then(text) if self.then and text else text,
                                 "; ".join(notes) if notes else None)
        return got

    def script(self, fields: Sequence[Field], given: Mapping[str, Any]) -> dict[str, str]:
        """The replies that read `given` ({label: source, None or '' blank}): the
        question's functions in field order, each once, and an empty stub for a
        requested function no field writes."""
        defs: dict[str, str] = {}
        for f in fields:
            for name, text in functions(given.get(f.label) or '')[0].items():
                defs.setdefault(name, text.strip('\n') + "\n")
        for f in fields:
            for name in self.wanted(f.label):
                defs.setdefault(name, f"def {name}():\n    pass\n")
        return {'transcribe': "\n\n".join(given.get(f.label) or f"{f.label}: {EMPTY}" for f in fields),
                'parse': json.dumps({name: field_object(text) for name, text in defs.items()})}

    def facets(self, question) -> list:
        """The comments in each field's source, searchable."""
        from .pseudocode import comment_facet
        fields = [f.label for f in question.written]
        return [comment_facet(label, name='comments' if len(fields) == 1 else f'comments/{label}',
                              label="comments" if len(fields) == 1 else f"{label} comments") for label in fields]


def code(entries: Sequence[str] | Mapping[str, Sequence[str]], *, language: Any = None, notes: str = "",
         then: Callable[[str], str] | None = None, prompt: str = prompts.TRANSCRIBE_PSEUDOCODE,
         parse_prompt: str = prompts.PARSE_PSEUDOCODE) -> Code:
    """Pseudocode. `entries` names the functions the student writes: a list (for
    a question with one code field), or `{label: [functions]}` per field. `then`
    rewrites each field's source deterministically (it is what is stored)."""
    entries = {k: tuple(v) for k, v in entries.items()} if isinstance(entries, Mapping) else tuple(entries)
    return Code(entries, language, prompt, parse_prompt, notes, then)


# --- functions in source, found from its lines (for splitting a question's code) ---

_DEF_LINE = r'def\s+([A-Za-z_]\w*)\s*\('
_FENCE_LINE = r'\s*```'
_NAME = r'[A-Za-z_]\w*'


def functions(source: str) -> tuple[dict[str, str], str]:
    """The top-level functions in `source`, found from its lines alone, so code
    that does not parse still splits: `({name: text}, rest)`. A function is a
    `def` line at the left margin and the lines after it that are indented,
    blank or comments; `rest` is every other line that is not blank (a Markdown
    fence line is dropped). A name defined twice keeps both texts, in order."""
    defs: dict[str, str] = {}
    rest, current = [], None
    for line in source.splitlines():
        if re.match(_FENCE_LINE, line):
            continue
        m = re.match(_DEF_LINE, line)
        if m:
            current = m.group(1)
            defs[current] = f"{defs[current].rstrip()}\n\n{line}" if current in defs else line
        elif current is not None and (not line.strip() or line[0] in ' \t' or line.startswith('#')):
            defs[current] += "\n" + line
        else:
            current = None
            if line.strip():
                rest.append(line)
    return {name: text.rstrip() + "\n" for name, text in defs.items()}, "\n".join(rest)


def calls(text: str, names) -> list:
    """The `names` that a function's text (from `functions`) mentions outside
    comments and its own `def` name, in order of first mention."""
    lines = [line.split('#', 1)[0] for line in text.splitlines()]
    if lines:
        lines[0] = re.sub(_DEF_LINE, '(', lines[0], count=1)
    return [n for n in dict.fromkeys(re.findall(_NAME, "\n".join(lines))) if n in names]


def is_empty(text: str) -> bool:
    """A function's text (from `functions`) that states nothing: after the `def`
    line only `pass`, `...`, a docstring or comments. Parsed only, never run."""
    code = [line.split('#', 1)[0].rstrip() for line in text.splitlines()]
    code = [line for line in code if line.strip()]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            tree = ast.parse("\n".join(code))
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return len(code) == 1 and code[0].endswith(':')         # a `def` line and nothing else
    fn = tree.body[0] if len(tree.body) == 1 and type(tree.body[0]) is ast.FunctionDef else None
    return fn is not None and all(type(s) is ast.Pass or type(s) is ast.Expr and type(s.value) is ast.Constant
                                  and (s.value.value is Ellipsis or isinstance(s.value.value, str)) for s in fn.body)

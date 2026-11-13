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
own functions from it (`Code.split`).

Each reader is a small frozen dataclass, so its configuration (prompts,
notes) is part of the definition and can be edited in the app. Model calls
are tagged with a step ('transcribe', 'parse') for a scripted model; `script`
writes the replies that would read given values (`examinator.synth`).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from ..core.context import ReadContext
from ..core.defs import Field
from ..core.reading import Reading, field_object, json_object, reading, readings
from . import prompts

EMPTY = '(empty)'       # how a transcript says a box is empty (the parse step turns it into "")


def _prompts(reader: Any, what: str) -> None:
    """A reader with no prompt reads nothing: refused where the definition is validated."""
    for name in ('prompt', 'parse_prompt'):
        if not getattr(reader, name).strip():
            raise ValueError(f"{what}: `{name}` is empty")


@dataclass(frozen=True)
class Answers:
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
class Code:
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
        from .pseudocode import calls, functions, is_empty
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
        from .pseudocode import functions
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

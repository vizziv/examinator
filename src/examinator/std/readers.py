"""Handwriting readers: how a question's handwritten fields are read.

    exam.question('sum', ..., read=readers.answers(notes=NUMBERS))          # short answers
    exam.question('sampler', ..., read=readers.code(['sampler'], language=LANG))  # pseudocode

A reader reads all the fields it is given in one go and returns one value per
field (text; '' is blank). Inside, it makes two model calls: one to transcribe
the pages, one to turn the transcript into values. The transcript is not
stored, shown or verified: one read, one value per field.

What a reply makes of each field is the reading layer's one rule
(`core.reading.value`): a field has a value only where the reply states one
for that field; BLANK is the blank value; anything else is not read, a review
marker that waits for a person. The model transcribes; the program splits:
the code reader converts the question's code once, and each field takes its
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
from ..core.defs import Field, has_marker
from ..core.reading import BLANK, NOT_READ, json_object, value
from . import prompts


def answers_from_json(text: str, labels: Sequence[str]) -> dict[str, str]:
    """Each label's value from a parse reply `{"answers": {label: text}}`
    (`reading.value`: a label the reply does not state is not read)."""
    answers = json_object(text).get('answers')
    if not isinstance(answers, dict):
        answers = {}
    return {label: value(answers.get(label)) for label in labels}


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

    def read(self, ctx: ReadContext, fields: Sequence[Field]) -> dict[str, str]:
        labels = [f.label for f in fields]
        context = prompts.context(ctx.question, labels)
        images = [ctx.crop(f.region) for f in fields] if self.crops else ctx.pages()
        transcript = ctx.model(prompts.join(self.prompt, context, self.notes), images=images,
                               step='transcribe').strip()
        out = ctx.model(prompts.join(self.parse_prompt, context, prompts.notation(self.symbols, self.functions),
                                     self.notes), text=transcript, expect='json', step='parse')
        return answers_from_json(out, labels)

    def script(self, fields: Sequence[Field], given: Mapping[str, Any]) -> dict[str, str]:
        """The replies that read `given` ({label: text, None or '' blank})."""
        labels = [f.label for f in fields]
        return {'transcribe': "\n".join(f"{label}: {given.get(label) or BLANK}" for label in labels),
                'parse': json.dumps({'answers': {label: given.get(label) or BLANK for label in labels}})}


def answers(*, symbols: Sequence[str] = (), functions: Sequence[str] | None = None, notes: str = "",
            crops: bool = False, prompt: str = prompts.TRANSCRIBE_ANSWERS,
            parse_prompt: str = prompts.PARSE_ANSWERS) -> Answers:
    """Short answers: expressions, numbers, choices. `symbols` and `functions`
    are notation hints for the prompt (never a key)."""
    return Answers(prompt, parse_prompt, tuple(symbols), None if functions is None else tuple(functions),
                   notes, crops)


@dataclass(frozen=True)
class Code:
    """Pseudocode: transcribe the question's pages, convert the transcript once
    into source in the pseudocode language, and give each field its own
    functions from it (`split`)."""
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

    def read(self, ctx: ReadContext, fields: Sequence[Field]) -> dict[str, str]:
        from . import pseudocode
        language = self.language or pseudocode.BASE
        labels = [f.label for f in fields]
        context = prompts.join(prompts.context(ctx.question, labels),
                               prompts.functions({label: self.wanted(label) for label in labels}))
        transcript = ctx.model(prompts.join(self.prompt, context, self.notes), images=ctx.pages(), step='transcribe')
        source = ctx.model(prompts.join(self.parse_prompt, language.description, context, self.notes),
                           text=transcript, expect='code', step='parse')
        return self.split(source, labels)

    def split(self, source: str, labels: Sequence[str]) -> dict[str, str]:
        """Each field's value from the question's converted source. A field takes
        its requested functions and the helpers they call, transitively, but not
        a function another field requests; lines outside every function go to
        every field. A field none of whose requested functions the source
        defines is not read; one whose requested functions are all empty
        (`pass`, comments) is blank; a requested function missing beside others
        is marked. A source that is only BLANK says every field is blank."""
        from .pseudocode import calls, functions, is_empty
        if source.strip() == BLANK:
            return {label: '' for label in labels}
        defs, rest = functions(source)
        owner = {name: label for label in labels for name in self.wanted(label)}
        out = {}
        for label in labels:
            wanted = self.wanted(label)
            found = [name for name in wanted if name in defs]
            if not found:
                out[label] = NOT_READ
                continue
            take, todo = set(found), list(found)
            while todo:
                for name in calls(defs[todo.pop()], defs):
                    if name not in take and owner.get(name, label) == label:
                        take.add(name)
                        todo.append(name)
            parts = [*filter(None, [rest]), *(text for name, text in defs.items() if name in take),
                     *(f"# {NOT_READ} {name}" for name in wanted if name not in defs)]
            text = "\n\n".join(p.strip('\n') for p in parts) + "\n"
            if not has_marker(text) and all(is_empty(defs[name]) for name in found):
                text = ''
            out[label] = self.then(text) if self.then and text else text
        return out

    def script(self, fields: Sequence[Field], given: Mapping[str, Any]) -> dict[str, str]:
        """The replies that read `given` ({label: source, None or '' blank}): the
        question's functions in field order, each once, and an empty stub for a
        requested function no field writes."""
        from .pseudocode import functions
        defs: dict[str, str] = {}
        for f in fields:
            for name, text in functions(given.get(f.label) or '')[0].items():
                defs.setdefault(name, text.strip('\n') + "\n")
        stubs = [f"def {name}():\n    pass\n" for f in fields for name in self.wanted(f.label) if name not in defs]
        return {'transcribe': "\n\n".join(given.get(f.label) or f"{f.label}: {BLANK}" for f in fields),
                'parse': "\n".join([*defs.values(), *stubs])}

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

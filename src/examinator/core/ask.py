"""Asking the model one thing about one region: the one model call behind
printed fields, handwritten fields such as a student's name, and intake's
landmarks.

    Field('name', Region(1, 100, 200, 800, 280), read=Ask("the student's full name"))
    Field('side', region=Region(2, 0, 60, 1000, 240),
          read=Printed("the printed side length of the square, as a number",
                       reads={'2': 'red', '3': 'green', '5': 'blue'}))

`Ask` asks `ask` about the field's region with its `prompt` (by default about
handwriting); `Printed` is `Ask` with a prompt about the PRINTED text. With
`reads`, the reading is normalised (`reading.normalize`) and mapped to a value;
the expected readings are listed in the prompt, and the model is told to answer
with a review marker when it sees none of them. A reading that is absent
(null), or not one of `reads`, becomes a value with a review marker, which
waits for a person; an empty reading without `reads` is a blank value.

An `Ask` **vouches** for a value that was one of a closed set of expected
readings (`reads` given, `verify` not set): such a value is verified by the
reader, and nobody need verify it. A field with no region is not read. Each
reading is remembered per submission under `memo_key` (the region, the
question and the scan layout), so reading again is free unless asked `fresh`.

A reader is any object with `read(ctx, fields) -> {label: text}`; it may also
have `vouches(field, text) -> bool`, `needs_region` and, for synthetic
workspaces, `script(fields, given) -> {step: reply}` (`examinator.synth`).
"""
from __future__ import annotations

import functools
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence

from .defs import digest, has_marker
from .reading import json_value, normalize, region_key

if TYPE_CHECKING:
    from .context import ReadContext
    from .defs import Field

HANDWRITTEN_PROMPT = """\
Read the HANDWRITTEN text in this image of a region of an exam page.
Report: {ask}.
Answer with exactly this JSON, without Markdown: {{"value": "the text as written"}}
Copy the handwriting character by character, even where it looks misspelled or incomplete. Use "" for the value if nothing is written there, and null if the writing is unreadable."""

PRINTED_PROMPT = """\
Read the PRINTED text in this image of an exam page region. Ignore all handwriting.
Report: {ask}.
Answer with exactly this JSON, without Markdown: {{"quote": "a short quote of the printed text you relied on", "value": "the value"}}
Use null for the value if the printed text is unreadable or absent. Do not infer the value from anything other than the printed text in this image."""

UNEXPECTED = ("If the text shows none of these, give as the value "
              "[REVIEW: what is there] instead of guessing.")

NOT_READ = "[REVIEW: not read]"


@dataclass(frozen=True)
class Ask:
    """Read a field's value by asking the model `ask` about its region (see the module)."""
    ask: str
    reads: Mapping[str, str] = field(default_factory=dict, metadata={'override': 'map'})   # reading -> value
    prompt: str = HANDWRITTEN_PROMPT
    verify: bool = False      # a person verifies even a reading that was one of those expected
    needs_region = True

    def validate(self, what: str) -> None:
        if not self.ask.strip():
            raise ValueError(f"{what}: a field read by asking needs an `ask`")
        if not all(isinstance(k, str) and k.strip() and isinstance(v, str) for k, v in self.reads.items()):
            raise ValueError(f"{what}: `reads` maps readings (text) to values (text)")

    def text(self) -> str:
        """The prompt. `reads` keys are what is on the page; they never carry an answer key."""
        out = self.prompt.format(ask=self.ask)
        if self.reads:
            out += "\nThe value is expected to be one of: " + " | ".join(self.reads) + ".\n" + UNEXPECTED
        return out

    def call(self, complete: Callable[..., str], image: Any) -> str | None:
        """The one model call: `complete(prompt, images=[image], expect='json')` (a
        model's `complete`, perhaps with a tag bound), reduced to the reading."""
        return json_value(complete(self.text(), images=[image], expect='json'))

    def value(self, reading: str | None) -> str:
        """The field's value for one reading: mapped through `reads`, or marked for a person."""
        if reading is None:
            return NOT_READ
        reading = reading.strip()
        if has_marker(reading) or not self.reads:
            return reading
        if not reading:
            return NOT_READ
        hit = {normalize(k): v for k, v in self.reads.items()}.get(normalize(reading))
        return hit if hit is not None else f"[REVIEW: unexpected] {reading}"

    def vouches(self, f: 'Field', text: str) -> bool:
        """One of a closed set of expected readings, and a person need not verify."""
        return bool(self.reads) and not self.verify and not has_marker(text) \
            and text in set(self.reads.values())

    def memo_key(self, question: str, f: 'Field', layout: Any) -> str:
        """Where the reading of field `f` (with the region it has for this submission)
        under a scan layout is remembered: a new region, prompt or layout asks again."""
        defn = digest({'region': f.region, 'ask': self.ask, 'reads': self.reads, 'prompt': self.prompt})
        return region_key('ask', f'{question}/{f.label}', defn, layout)

    def read(self, ctx: 'ReadContext', fields: Sequence['Field']) -> dict[str, str]:
        out = {}
        for f in fields:
            def once(f=f) -> str:
                complete = functools.partial(ctx.model, step=f'read:{f.label}')
                return json.dumps(self.call(complete, ctx.crop(f.region)))
            got = ctx.remember(self.memo_key(ctx.question.id, f, ctx.layout()), once)
            out[f.label] = self.value(json.loads(got))
        return out

    def reading(self, given: str | None) -> str | None:
        """What the model reads in the field, for a synthetic case that says what
        is there: a reading of `reads` as it is, a value as the first reading
        that maps to it, anything else as it is (a reading the field does not expect)."""
        if given is None or any(normalize(k) == normalize(given) for k in self.reads):
            return given
        return next((k for k, v in self.reads.items() if v == given), given)

    def script(self, fields: Sequence['Field'], given: Mapping[str, Any]) -> dict[str, str]:
        return {f'read:{f.label}': json.dumps({'quote': "synthetic", 'value': self.reading(given[f.label])})
                for f in fields if f.label in given}


@dataclass(frozen=True)
class Printed(Ask):
    """Read a field's value from the printed text in its region: `Ask` about
    the PRINTED text (see the module)."""
    prompt: str = PRINTED_PROMPT

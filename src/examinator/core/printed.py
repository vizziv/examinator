"""A field read from the printed exam rather than from the student's answer.

    Field('side', region=Region(2, 0, 60, 1000, 240),
          read=Printed("the printed side length of the square, as a number",
                       reads={'2': 'red', '3': 'green', '5': 'blue'}))

The model is asked `ask` about the PRINTED text in the field's region
(`core.reading`, shared with intake's landmarks and identity boxes). With
`reads`, the reading is normalised (`reading.normalize`) and mapped to a value;
the expected readings are listed in the prompt, and the model is told to answer
with a review marker when it sees none of them. A reading that is absent, or not
one of `reads`, becomes a value with a review marker, so the parse waits for a
person. The value is stored in the question's parse beside the student's fields
and is edited and verified with them.

A reader is any object called as `reader(ctx, field) -> str | None` during the
parse stage (`Field.read`); `Printed` is the one the library has.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Mapping

from .defs import digest, has_marker
from .reading import json_value, memo_key, normalize, prompt

if TYPE_CHECKING:
    from .context import Context
    from .defs import Field

PRINTED_PROMPT = """\
Read the PRINTED text in this image of an exam page region. Ignore all handwriting.
Report: {ask}.
Answer with exactly this JSON, without Markdown: {{"quote": "a short quote of the printed text you relied on", "value": "the value"}}
Use null for the value if the printed text is unreadable or absent. Do not infer the value from anything other than the printed text in this image."""

UNEXPECTED = ("If the printed text shows none of these, give as the value "
              "[REVIEW: what is printed] instead of guessing.")

NOT_READ = "[REVIEW: not read]"


@dataclass(frozen=True)
class Printed:
    """Read a field's value from the printed text in its region (see the module)."""
    ask: str
    reads: Mapping[str, str] = field(default_factory=dict)    # normalised reading -> value
    prompt: str = PRINTED_PROMPT
    verify: bool = False      # a person verifies even a reading that was one of those expected

    @property
    def trusted(self) -> bool:
        """Vouches for an unmarked value: it is one of a closed set of expected readings."""
        return bool(self.reads) and not self.verify

    def validate(self, what: str) -> None:
        if not self.ask.strip():
            raise ValueError(f"{what}: a printed field needs an `ask`")
        if not all(isinstance(k, str) and k.strip() and isinstance(v, str) for k, v in self.reads.items()):
            raise ValueError(f"{what}: `reads` maps readings (text) to values (text)")

    def text(self) -> str:
        """The prompt. `reads` keys are printed text; they never carry an answer key."""
        out = prompt(self.prompt, self.ask, self.reads)
        return f"{out}\n{UNEXPECTED}" if self.reads else out

    def value(self, reading: str | None) -> str:
        """The field's value for one reading: mapped through `reads`, or marked for a person."""
        if reading is None or not reading.strip():
            return NOT_READ
        if has_marker(reading) or not self.reads:
            return reading.strip()
        hit = {normalize(k): v for k, v in self.reads.items()}.get(normalize(reading))
        return hit if hit is not None else f"[REVIEW: unexpected] {reading.strip()}"

    def __call__(self, ctx: 'Context', f: 'Field') -> str:
        def read() -> str:
            out = ctx.model(self.text(), images=[ctx.crop(f.region)], expect='json')
            return json.dumps(json_value(out))
        defn = digest({'region': f.region, 'ask': self.ask, 'reads': self.reads, 'prompt': self.prompt})
        key = memo_key('printed', f'{ctx.question.id}/{f.label}', defn)
        return self.value(json.loads(ctx.remember(key, read, refresh=ctx.rerun)))

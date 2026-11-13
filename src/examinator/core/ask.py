"""Asking the model one thing about one region: the one model call behind
printed fields, handwritten fields such as a student's name, and intake's
landmarks.

    Field('name', Region(1, 100, 200, 800, 280), read=Ask("the student's full name"))
    Field('side', region=Region(2, 0, 60, 1000, 240),
          read=Printed("the printed side length of the square, as a number (2, 3 or 5)"))

`Ask` asks `ask` about the field's region with its `prompt` (by default about
handwriting); `Printed` is `Ask` with a prompt about the PRINTED text. The
reading is the value: what to expect, and in what form, is the `ask`'s to say.
A reading that is absent (null) becomes a value with a review marker, which
waits for a person; an empty reading is a blank value.

A reader **vouches** for a value when `verify` is off and the value carries no
review marker: such a value is verified by the reader, and nobody need verify
it. `Printed` vouches by default (printed text is read reliably); `Ask`, about
handwriting, does not (a person verifies). A value nothing expects is caught
where it matters: a check that has no key for it is undecided, and says so.
A field with no region is not read. Every read asks the model; a read happens
only when a person asks for one.

A reader is any object with `read(ctx, fields) -> {label: text}`; it may also
have `vouches(field, text) -> bool`, `needs_region` and, for synthetic
workspaces, `script(fields, given) -> {step: reply}` (`examinator.synth`).
"""
from __future__ import annotations

import functools
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence

from .defs import has_marker
from .reading import json_value

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

NOT_READ = "[REVIEW: not read]"


@dataclass(frozen=True)
class Ask:
    """Read a field's value by asking the model `ask` about its region (see the module)."""
    ask: str
    prompt: str = HANDWRITTEN_PROMPT
    verify: bool = True       # a person verifies every value read (else the reader vouches for it)
    needs_region = True

    def validate(self, what: str) -> None:
        if not self.ask.strip():
            raise ValueError(f"{what}: a field read by asking needs an `ask`")
        if not self.prompt.strip():
            raise ValueError(f"{what}: the prompt is empty")
        try:
            self.text()
        except (KeyError, IndexError, ValueError):
            raise ValueError(f"{what}: the prompt may have {{ask}} and no other braces "
                             f"(write a literal brace doubled: {{{{ }}}})") from None

    def text(self) -> str:
        """The prompt."""
        return self.prompt.format(ask=self.ask)

    def call(self, complete: Callable[..., str], image: Any) -> str | None:
        """The one model call: `complete(prompt, images=[image], expect='json')` (a
        model's `complete`, perhaps with a tag bound), reduced to the reading."""
        return json_value(complete(self.text(), images=[image], expect='json'))

    def value(self, reading: str | None) -> str:
        """The field's value for one reading: itself, or marked for a person when there is none."""
        return NOT_READ if reading is None else reading.strip()

    def vouches(self, f: 'Field', text: str) -> bool:
        """A person need not verify this value."""
        return not self.verify and bool(text.strip()) and not has_marker(text)

    def read(self, ctx: 'ReadContext', fields: Sequence['Field']) -> dict[str, str]:
        return {f.label: self.value(self.call(functools.partial(ctx.model, step=f'read:{f.label}'), ctx.crop(f.region)))
                for f in fields}

    def script(self, fields: Sequence['Field'], given: Mapping[str, Any]) -> dict[str, str]:
        return {f'read:{f.label}': json.dumps({'quote': "synthetic", 'value': given[f.label]})
                for f in fields if f.label in given}


@dataclass(frozen=True)
class Printed(Ask):
    """Read a field's value from the printed text in its region: `Ask` about
    the PRINTED text (see the module); it vouches for what it reads."""
    prompt: str = PRINTED_PROMPT
    verify: bool = False

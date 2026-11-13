"""Asking the model one thing about one region: the one model call behind
printed fields, handwritten fields such as a student's name, and intake's
landmarks.

    Field('name', Region(1, 100, 200, 800, 280), read=Ask("the student's full name"))
    Field('side', region=Region(2, 0, 60, 1000, 240),
          read=Printed("the printed side length of the square, as a number (2, 3 or 5)"))

`Ask` asks `ask` about the field's region with its `prompt` (by default about
handwriting); `Printed` is `Ask` with a prompt about the PRINTED text. The
reading is the value: what to expect, and in what form, is the `ask`'s to say.
The reply is one field object, `{"value": ..., "unsure": ...}` (`core.reading`):
an empty value is a blank; `unsure` marks the value for review; a reply that
states no value leaves the field not read.

A reader **vouches** for a value when `verify` is off, the value is not empty
and the model was sure: such a value is verified by the reader, and nobody need
verify it. `Printed` vouches by default (printed text is read reliably); `Ask`,
about handwriting, never does (a person verifies, a blank too). A value nothing
expects is caught where it matters: a check that has no key for it is
undecided, and says so. A field with no region is not read. Every read asks
the model; a read happens only when a person asks for one.

A reader is any object with `read(ctx, fields) -> {label: Reading or text}`
(a label left out is not read); it may also have `vouches(field, reading) -> bool`,
`needs_region` and, for synthetic workspaces, `script(fields, given) -> {step:
reply}` (`examinator.synth`).
"""
from __future__ import annotations

import functools
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence

from .reading import Reading, field_object, json_object, reading

if TYPE_CHECKING:
    from .context import ReadContext
    from .defs import Field

HANDWRITTEN_PROMPT = """\
Read the HANDWRITTEN text in this image of a region of an exam page.
Report: {ask}.
Answer with exactly this JSON, without Markdown: {{"value": "the text as written", "unsure": ""}}
Copy the handwriting character by character, even where it looks misspelled or incomplete. \
If nothing is handwritten there, the value is "". If you cannot be sure of the reading, give your \
best reading as the value and say briefly in "unsure" what is uncertain (otherwise leave it ""). \
If the image does not show such a region the right way up, the value is JSON null."""

PRINTED_PROMPT = """\
Read the PRINTED text in this image of an exam page region. Ignore all handwriting.
Report: {ask}.
Answer with exactly this JSON, without Markdown: {{"quote": "a short quote of the printed text you relied on", "value": "the value", "unsure": ""}}
Give the value in exactly the form the request above asks for (a number, a word, a JSON list); \
within that form copy what is printed exactly, with no added words or units. If you cannot be sure, \
give your best reading and say briefly in "unsure" why (otherwise leave it ""). If the printed text \
is absent, the value is JSON null (not the string "null"). Do not infer the value from anything \
other than the printed text in this image."""


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

    def reading(self, complete: Callable[..., str], image: Any) -> Reading | None:
        """The one model call: `complete(prompt, images=[image], expect='json')` (a
        model's `complete`, perhaps with a tag bound), as a reading; None if the
        reply states none."""
        return reading(json_object(complete(self.text(), images=[image], expect='json')))

    def call(self, complete: Callable[..., str], image: Any) -> str | None:
        """The text read (intake's landmarks), or None."""
        got = self.reading(complete, image)
        return got.text if got is not None else None

    def vouches(self, f: 'Field', got: Reading) -> bool:
        """A person need not verify this reading."""
        return not self.verify and bool(got.text.strip()) and got.review is None

    def read(self, ctx: 'ReadContext', fields: Sequence['Field']) -> dict[str, Reading]:
        out = {}
        for f in fields:
            got = self.reading(functools.partial(ctx.model, step=f'read:{f.label}'), ctx.crop(f.region))
            if got is not None:
                out[f.label] = got
        return out

    def script(self, fields: Sequence['Field'], given: Mapping[str, Any]) -> dict[str, str]:
        """The replies that read `given` ({label: text, '' blank, a `Reading` the model
        is unsure of, None: states nothing})."""
        return {f'read:{f.label}': json.dumps({'quote': "synthetic", **field_object(given[f.label])})
                for f in fields if f.label in given}


@dataclass(frozen=True)
class Printed(Ask):
    """Read a field's value from the printed text in its region: `Ask` about
    the PRINTED text (see the module); it vouches for what it reads."""
    prompt: str = PRINTED_PROMPT
    verify: bool = False

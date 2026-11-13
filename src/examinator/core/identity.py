"""Who wrote each submission: the roster, what the model read in the boxes where
students write who they are, ranked proposals, and the person a grader picks.

    setting 'people'                  the roster: [{'name', 'id', 'email', 'key'}]
    setting 'identity'                the workspace's own boxes, drawn in the app (else the exam's):
                                      {'boxes': [{'name', 'region', 'ask', 'roster'}], 'rev'}
    row (sub, '', 'identity')    what the model read: {'fields': {box: text | None}, 'basis': {box: digest}}
    row (sub, '', 'who')         the pick: a person's, or from an ID in the file name: {'name', 'id', 'email', 'key'?}

The boxes are the exam's `Identity` declarations, or the workspace's own
(`boxes`, `set_boxes`): drawn by a grader for every submission. Reading is memoised per
submission, box and scan layout, so reading again is free. Proposals are
computed, never stored: `propose(readings, roster)` is a pure function from
what was read (by roster field) to ranked candidates with a reason and a
qualitative doubt. Nothing here confirms an identity; only `set_who`, a
person's action, writes the pick.
"""
from __future__ import annotations

import csv
import difflib
import functools
import io
import json
import re
import unicodedata
from typing import Any, Iterable, Mapping, Sequence

from ..pdf.orient import Orient
from .defs import ROSTER_FIELDS, Identity, Region, digest
from .reading import ask, basis, memo_key
from .store import ANY, Conflict, Row, Submission

WHO = 'who'
READ = 'identity'

# --- the roster ---------------------------------------------------------------------

NAME_COLUMNS = ('name', 'full name', 'student name', 'student')
FIRST, LAST = ('first name', 'first', 'given name'), ('last name', 'last', 'surname', 'family name')
EMAIL = ('email', 'e-mail', 'email address')
ID_COLUMNS = ('student id', 'sid', 'id', 'student number', 'netid', 'net id', 'id number')
HEADERS = set(NAME_COLUMNS + FIRST + LAST + EMAIL + ID_COLUMNS)


def looks_like_id(text: str) -> bool:
    """The rule for telling an ID from a name on a pasted line: no spaces, and
    at least one digit."""
    return bool(text) and not any(c.isspace() for c in text) and any(c.isdigit() for c in text)


def _person(name: str, id: str = '', email: str = '') -> dict:
    p = {'name': name.strip()[:200], 'id': id.strip()[:100], 'email': email.strip()[:200]}
    p['key'] = p['email'] or p['id'] or p['name']
    return p


def _delimiter(line: str) -> str:
    return '\t' if '\t' in line else ','


def _cells(line: str) -> list[str]:
    return [c.strip() for c in next(csv.reader([line], delimiter=_delimiter(line)), [])]


def read_roster(text: str) -> tuple[list[dict], list[str]]:
    """People from pasted text or a file, and the problems found (by line).

    With a header line (a CSV or tab-separated table naming a Name column, or
    First Name and Last Name, and optionally Email and Student ID), columns
    are taken by name. Without one, each line is one person: a name and an ID
    separated by a tab or a comma, in either order. The ID is the part with no
    spaces and at least one digit (`looks_like_id`); with more than two parts,
    exactly one may look like an ID and the rest is the name ("Lovelace, Ada,
    12345"). If neither part looks like an ID, the part without spaces is the
    ID ("Ada Lovelace, alovelace"). Blank lines are skipped; a person already
    listed (same email, ID or name) is reported, not added twice."""
    text = text.lstrip('﻿')
    lines = text.splitlines()
    first = next((ln for ln in lines if ln.strip()), '')
    out: list[tuple[int, dict]] = []
    problems: list[str] = []
    if first and any(c.lower() in HEADERS for c in _cells(first)):
        reader = csv.DictReader(io.StringIO(text), delimiter=_delimiter(first))
        for row in reader:
            r = {str(k).strip().lower(): (v or '').strip() for k, v in row.items() if k is not None}

            def pick(names):
                return next((r[n] for n in names if r.get(n)), '')
            name = pick(NAME_COLUMNS) or f"{pick(FIRST)} {pick(LAST)}".strip()
            if not name:
                if any(r.values()):
                    problems.append(f"line {reader.line_num}: no name")
                continue
            out.append((reader.line_num, _person(name, pick(ID_COLUMNS), pick(EMAIL))))
        if not out and not problems:
            problems.append("no names found: expected a Name column (or First Name and Last Name)")
    else:
        for n, line in enumerate(lines, 1):
            if not line.strip():
                continue
            parts = [c for c in _cells(line) if c]
            ids = [c for c in parts if looks_like_id(c)]
            if len(parts) == 2 and not ids:
                ids = [c for c in parts if not any(ch.isspace() for ch in c)]
            if len(parts) < 2 or len(ids) != 1:
                problems.append(f"line {n}: expected a name and an ID")
                continue
            name = ", ".join(c for c in parts if c is not ids[0])
            out.append((n, _person(name, ids[0])))
    people, seen = [], {}
    for n, p in out:
        if p['key'] in seen:
            problems.append(f"line {n}: {p['key']} is already on line {seen[p['key']]}")
            continue
        seen[p['key']] = n
        people.append(p)
    return people, problems


def parse_roster(text: str) -> list[dict]:
    """People from roster text (`read_roster`); any problem is an error."""
    people, problems = read_roster(text)
    if problems:
        raise ValueError("; ".join(problems[:3]) + (f" (and {len(problems) - 3} more)" if len(problems) > 3 else ""))
    if not people:
        raise ValueError("no people found")
    return people


def set_roster(ws, text: str) -> int:
    people = parse_roster(text)
    ws.store.set_setting('people', people)
    by_file(ws)
    return len(people)


def people(ws) -> list[dict]:
    return [dict(p) for p in ws.store.setting('people', [])]


def label(person: Mapping) -> str:
    """How a person appears in the picker (also what it sends back)."""
    extra = person.get('id') or person.get('email')
    return f"{person['name']} <{extra}>" if extra else person['name']


# --- the pick -------------------------------------------------------------------------

def who(ws, submission: str) -> dict:
    """The person picked for a submission: {'name', 'id', 'email'} ('' when nobody)."""
    a = ws.store.get(submission, '', WHO)
    body = a.body if a is not None and isinstance(a.body, dict) else {}
    return {'name': body.get('name', ""), 'id': body.get('id', ""), 'email': body.get('email', "")}


def submission(ws, sid: str, pdf: str) -> Submission:
    """A written submission's record, named by its pick."""
    w = who(ws, sid)
    return Submission(sid, w['name'], w['email'], pdf, w['id'])


def set_who(ws, sid: str, text: str, *, author: str | None, expect_rev: Any = ANY) -> dict:
    """Name a submission: a roster entry's label or key, free text if the roster
    has no match (someone not on it), or nothing to clear it. `expect_rev`:
    the pick's revision the person saw (None: there was none)."""
    s = ws.store.submission(sid)
    if s is None:
        raise KeyError(sid)
    text = (text or '').strip()[:300]
    if not text:
        ws.store.delete(sid, '', WHO, expect_rev=expect_rev)
        body = {'name': "", 'id': "", 'email': ""}
        rev = None
    else:
        roster = people(ws)
        person = next((p for p in roster if text in (label(p), p['key'])), None)
        # "Name, ID" (what the page starts typing from): that person if both match, else kept as typed
        name, comma, id = text.rpartition(',')
        name, id = (name.strip(), id.strip()) if comma and looks_like_id(id.strip()) else (text, "")
        if person is None and id:
            person = next((p for p in roster if p['id'] == id and fold(p['name']) == fold(name)), None)
        body = ({'name': person['name'], 'id': person['id'], 'email': person['email'], 'key': person['key']}
                if person else {'name': name, 'id': id, 'email': ""})
        rev = ws.store.write(sid, '', WHO, body, origin='manual', author=author, expect_rev=expect_rev).rev
    ws.store.put_submissions([Submission(s.id, body['name'], body['email'], s.pdf, body['id'])])
    return {**body, 'rev': rev}


def by_file(ws) -> int:
    """The programmatic way to name submissions: a PDF whose file name carries
    exactly one roster ID, as a whole run of letters and digits ("31415926.pdf",
    "quiz-31415926.pdf"), is picked for that person, unless the submission has
    or ever had a pick: a person's word, clearing included, stands."""
    ids = {fold(p['id']): p for p in people(ws) if p['id']}
    n = 0
    for s in ws.store.submissions() if ids else ():
        found = {t for t in re.findall(r'[^\W_]+', fold(s.id)) if t in ids}
        if len(found) != 1 or ws.store.get(s.id, '', WHO) is not None or ws.store.history(s.id, '', WHO):
            continue
        p = ids[found.pop()]
        body = {'name': p['name'], 'id': p['id'], 'email': p['email'], 'key': p['key']}
        ws.store.write(s.id, '', WHO, body, origin='auto', expect_rev=None)
        ws.store.put_submissions([Submission(s.id, p['name'], p['email'], s.pdf, p['id'])])
        n += 1
    return n


# --- the boxes ------------------------------------------------------------------------

BOXES = 'identity'      # the setting holding the workspace's own boxes
MAX_BOXES = 8
# What to ask about a box drawn in the app, by the roster field it is compared with.
ASKS = {'name': "the student's full name", 'id': "the student ID number",
        'email': "the student's email address"}


def boxes(ws) -> tuple[Identity, ...]:
    """The identity boxes in use: the workspace's own, else the exam's."""
    own = ws.store.setting(BOXES)
    if own is None:
        return tuple(ws.exam.identity)
    return tuple(Identity(b['name'], Region(*b['region']), b['ask'], b.get('roster')) for b in own['boxes'])


def box_json(f: Identity) -> dict:
    r = f.region
    return {'name': f.name, 'region': [r.page, r.left, r.top, r.right, r.bottom], 'ask': f.ask, 'roster': f.roster}


def boxes_state(ws) -> dict:
    """The boxes in use as JSON, whether they are the workspace's own, and its revision
    (None: the exam's are in use)."""
    own = ws.store.setting(BOXES)
    return {'boxes': [box_json(f) for f in boxes(ws)], 'own': own is not None,
            'rev': own['rev'] if own is not None else None, 'declared': len(ws.exam.identity)}


def _box(ws, item: Any) -> dict:
    """One box from the page, checked: {'name', 'region', 'roster', 'ask'?}. A box
    without `ask` keeps the exam's box of that name's, else asks by its roster field."""
    if not isinstance(item, Mapping):
        raise ValueError("a box is {name, region, roster}")
    name = item.get('name')
    if not isinstance(name, str) or not name.strip() or len(name) > 40:
        raise ValueError("a box needs a short name")
    roster = item.get('roster')
    if roster is not None and roster not in ROSTER_FIELDS:
        raise ValueError(f"roster must be one of {ROSTER_FIELDS} or null")
    region = item.get('region')
    ok = isinstance(region, (list, tuple)) and len(region) == 5 and all(
        isinstance(v, int) and not isinstance(v, bool) for v in region)
    if not ok:
        raise ValueError("a region is [page, left, top, right, bottom]")
    page, left, top, right, bottom = region
    if not 1 <= page <= ws.exam.pages:
        raise ValueError(f"page {page} is not in 1..{ws.exam.pages}")
    if not (0 <= left < right <= 1000 and 0 <= top < bottom <= 1000):
        raise ValueError("a region needs 0 <= left < right <= 1000 and 0 <= top < bottom <= 1000")
    ask = item.get('ask')
    if ask is None:
        declared = next((f for f in ws.exam.identity if f.name == name.strip()), None)
        ask = declared.ask if declared else ASKS.get(roster)
    if not isinstance(ask, str) or not ask.strip() or len(ask) > 300:
        raise ValueError("a box needs what to ask about it")
    return {'name': name.strip(), 'region': list(region), 'ask': ask, 'roster': roster}


def set_boxes(ws, items: Sequence | None, *, expect_rev: Any = ANY) -> dict:
    """The workspace's own identity boxes, for every submission (None: back to the
    exam's). Readings under the old boxes no longer apply (`readings`), so the
    boxes are read again. `expect_rev`: the revision the page saw (None: the
    exam's boxes were in use). Raises `ValueError` or `Conflict`."""
    own = ws.store.setting(BOXES)
    if expect_rev is not ANY and expect_rev != (own['rev'] if own is not None else None):
        raise Conflict("the name boxes were changed elsewhere")
    if items is None:
        ws.store.delete_setting(BOXES)
        return boxes_state(ws)
    if not isinstance(items, (list, tuple)) or len(items) > MAX_BOXES:
        raise ValueError(f"expected a list of at most {MAX_BOXES} boxes")
    out = [_box(ws, x) for x in items]
    if len({b['name'] for b in out}) != len(out):
        raise ValueError("two boxes have the same name")
    ws.store.set_setting(BOXES, {'boxes': out, 'rev': (own['rev'] + 1) if own is not None else 1})
    return boxes_state(ws)


# --- reading the boxes ----------------------------------------------------------------

IDENTITY_PROMPT = """\
Read the HANDWRITTEN text in this image of a box on an exam page, where the student writes who they are.
Report: {ask}.
Answer with exactly this JSON, without Markdown: {{"value": "the text as written"}}
Copy the handwriting character by character, even where it looks misspelled or incomplete. Use null for the value if the box is empty or unreadable."""


def _tag(ws, sid: str, page: int) -> tuple[str, str, str]:
    """Which scanned sheet a call reads, for the call's tag (diagnostics, and the
    scripted model): a written submission's source sheet and how it was turned."""
    made = ws.store.made().get(sid)
    i = ws.layout(sid).scan_index(page)
    if made is not None and 0 <= i < len(made.pages):
        sheets = made.orient.get('sheets') or []
        o = Orient.from_json(sheets[i] if i < len(sheets) else None)
        return made.source, f'{made.pages[i]}@{o.code}', READ
    return sid, str(i), READ


def read(ws, sid: str, model, *, fresh: bool = False, prompt: str = IDENTITY_PROMPT) -> Row | None:
    """Ask the model what is written in each of the exam's identity boxes on one
    submission, and store the readings (unchanged readings are not rewritten).
    A failed call is no reading and is not remembered. `fresh` asks again."""
    fields = boxes(ws)
    if not fields or model is None:
        return None
    layout = ws.layout(sid)
    values, failed = {}, []
    for f in fields:
        key = memo_key('identity', f.name, basis(f), list(layout.window), layout.cover_pages)
        hit = None if fresh else ws.store.memo(sid, key)
        if hit is None:
            try:
                complete = functools.partial(model.complete, tag=_tag(ws, sid, f.region.page))
                got = ask(complete, prompt, f.ask, ws.crop(sid, f.region))
            except Exception:
                values[f.name] = None
                failed.append(f.name)
                continue
            hit = json.dumps(got)
            ws.store.set_memo(sid, key, hit)
        values[f.name] = json.loads(hit)
    body = {'fields': values, 'basis': {f.name: basis(f) for f in fields}}
    if failed:
        body['failed'] = failed
    cur = ws.store.get(sid, '', READ)
    if cur is not None and cur.body == body:
        return cur
    return ws.store.write(sid, '', READ, body, origin='auto')


def readings(fields: Sequence[Identity], body: Mapping | None) -> dict[str, str | None]:
    """The stored readings that still apply to the boxes as declared now."""
    body = body or {}
    made = body.get('basis') or {}
    got = body.get('fields') or {}
    return {f.name: got.get(f.name) for f in fields if f.name in got and made.get(f.name) == basis(f)}


def unread(ws) -> list[str]:
    """Submissions whose boxes still need reading: some box has no reading
    under its current declaration, or a call failed last time."""
    fields = boxes(ws)
    reads = {a.submission: a.body for a in ws.store.all(kinds=[READ])}
    return [s.id for s in ws.store.submissions()
            if len(readings(fields, reads.get(s.id))) < len(fields) or (reads.get(s.id) or {}).get('failed')]


def by_roster(fields: Sequence[Identity], read: Mapping[str, str | None]) -> dict[str, str | None]:
    """Readings by the roster field they are compared with; boxes sharing a
    field are joined in declaration order (an ID without spaces)."""
    out: dict[str, list[str]] = {}
    for f in fields:
        if f.roster is not None:
            out.setdefault(f.roster, [])
            if read.get(f.name):
                out[f.roster].append(read[f.name])
    return {k: (('' if k != 'name' else ' ').join(v) or None) for k, v in out.items()}


# --- proposing: pure ------------------------------------------------------------------

DOUBTS = {'agree': "both agree", 'id': "ID only", 'name': "name only", 'disagree': "they disagree",
          'nobody': "nobody close", 'empty': "empty", 'unread': "not read yet"}
ORDER = ('disagree', 'nobody', 'empty', 'unread', 'name', 'id', 'agree')     # most doubtful first
NAME_CLOSE = 0.8        # a name this similar points to a person ...
NAME_MARGIN = 0.05      # ... if nobody else is within this of it
NAME_SOME = 0.6         # below this a name is no evidence at all
NAME_EXACT = 0.999      # a name this similar is the same name
ID_WEIGHT = {0: 1.0, 0.5: 0.9, 1: 0.7, 2: 0.45}       # by distance (0.5: look-alike characters only)
# Characters a reader confuses with one another, each written as its digit. Both the
# reading and the roster's ID are compared in this form, so IDs may mix letters and digits.
ALIKE = str.maketrans({'o': '0', 'q': '0', 'd': '0', 'i': '1', 'l': '1', 'z': '2', 's': '5', 'b': '8', 'g': '9'})


def fold(text: str) -> str:
    """Lower case, accents off, apostrophes dropped."""
    t = unicodedata.normalize('NFKD', text)
    t = ''.join(c for c in t if not unicodedata.combining(c)).lower()
    return re.sub(r"['’`]", '', t)


def name_tokens(text: str) -> list[str]:
    return re.findall(r'[^\W_]+', fold(text))


def id_text(text: str) -> str:
    return re.sub(r'[\W_]+', '', fold(text))


def tolerance(id: str) -> int:
    """How many misread characters an ID of this length may have and still match
    (look-alike characters do not count as misread)."""
    return 2 if len(id) >= 8 else 1 if len(id) >= 4 else 0


def distance(a: str, b: str, limit: int = 2) -> int:
    """Edit distance (insert, delete, substitute, swap neighbours), or `limit + 1`
    when it is more than `limit`."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2, prev = None, list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = a[i - 1] != b[j - 1]
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if prev2 is not None and i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > limit:
            return limit + 1
        prev2, prev = prev, cur
    return min(prev[-1], limit + 1)


def name_score(a: Sequence[str], b: Sequence[str], ratio=None) -> float:
    """How alike two names are, 0 to 1, whatever the order of their parts: each
    part is matched with the most alike part of the other name, weighted by
    length, both ways round. `ratio(t, u)`: how alike two parts are (difflib's)."""
    if not a or not b:
        return 0.0
    if sorted(a) == sorted(b):
        return 1.0
    ratio = ratio or (lambda t, u: difflib.SequenceMatcher(None, t, u).ratio())
    m = [[ratio(t, u) for u in b] for t in a]
    one = sum(max(row) * len(t) for t, row in zip(a, m)) / sum(len(t) for t in a)
    two = sum(max(m[i][j] for i in range(len(a))) * len(u) for j, u in enumerate(b)) / sum(len(u) for u in b)
    return (one + two) / 2


class Matcher:
    """A roster prepared for matching (normalised once, used for many proposals)."""

    def __init__(self, roster: Sequence[Mapping]):
        self.roster = list(roster)
        self._names = [name_tokens(p.get('name') or '') for p in self.roster]
        self._ids = {f: [id_text(p.get(f) or '') for p in self.roster] for f in ('id', 'email')}
        self._alike = {f: [x.translate(ALIKE) for x in ids] for f, ids in self._ids.items()}
        self._exact = {f: {} for f in self._ids}
        for f, ids in self._ids.items():
            for i, x in enumerate(ids):
                if x:
                    self._exact[f].setdefault(x, []).append(i)

    def _id_distances(self, field: str, reading: str) -> dict[int, float]:
        """Roster index -> distance, for entries within tolerance: 0 exact, 0.5 the
        same but for look-alike characters, else the number of misread characters."""
        r = id_text(reading)
        if not r:
            return {}
        if r in self._exact[field]:
            return {i: 0 for i in self._exact[field][r]}
        r = r.translate(ALIKE)
        out = {}
        for i, x in enumerate(self._alike[field]):
            if not x:
                continue
            limit = tolerance(x)
            d = distance(r, x, limit)
            if d <= limit:
                out[i] = d or 0.5
        return out

    def propose(self, read: Mapping[str, str | None], *, taken: Mapping[str, str] | None = None,
                limit: int = 5) -> dict:
        """Ranked candidates for one submission from its readings by roster field
        ('name', 'id', 'email'), each with a short reason; and the doubt:
        'agree' (ID and name point to the same person), 'id' or 'name' (only
        one points to someone), 'disagree' (they point to different people),
        'nobody' (neither is close to anyone), 'unread' (nothing was read).
        `taken` maps roster keys already picked for other submissions to those
        submissions; such candidates are flagged, not left out."""
        taken = taken or {}
        if not any(read.get(f) for f in ('name', 'id', 'email')):
            return {'doubt': 'unread', 'candidates': []}
        dist: dict[int, float] = {}
        for f in ('id', 'email'):
            if read.get(f):
                for i, d in self._id_distances(f, read[f]).items():
                    dist[i] = min(d, dist.get(i, d))
        names: dict[int, float] = {}
        tokens = name_tokens(read.get('name') or '')
        if tokens:
            # one matcher per part read (difflib caches its second sequence), one ratio per distinct pair
            seen: dict[tuple[str, str], float] = {}
            sms = {t: difflib.SequenceMatcher(None, '', t) for t in tokens}

            def ratio(t, u):
                r = seen.get((t, u))
                if r is None:
                    sm = sms[t]
                    sm.set_seq1(u)
                    r = seen[t, u] = sm.ratio() if sm.real_quick_ratio() >= 0.5 and sm.quick_ratio() >= 0.5 else 0.0
                return r
            for i, toks in enumerate(self._names):
                s = name_score(tokens, toks, ratio)
                if s >= NAME_SOME:
                    names[i] = s
        by_id = None
        if dist:
            best = min(dist.values())
            near = [i for i, d in dist.items() if d == best]
            by_id = near[0] if len(near) == 1 else None
        by_name = None
        if names:
            ranked = sorted(names.values(), reverse=True)
            top = max(names, key=lambda i: names[i])
            if ranked[0] >= NAME_CLOSE and (len(ranked) == 1 or ranked[0] - ranked[1] >= NAME_MARGIN):
                by_name = top
        # Evidence that cannot single anyone out alone still agrees with what the other points to:
        # a name shared by two people, beside an ID that picks one of them.
        if by_name is None and by_id is not None and names.get(by_id, 0) >= NAME_CLOSE:
            by_name = by_id
        if by_id is None and by_name is not None and by_name in dist:
            by_id = by_name
        if by_id is not None and by_name is not None:
            doubt = 'agree' if by_id == by_name else 'disagree'
        elif by_id is not None:
            doubt = 'id'
        elif by_name is not None:
            doubt = 'name'
        else:
            doubt = 'nobody'
        scores = {i: ID_WEIGHT.get(dist.get(i, -1), 0.0) + names.get(i, 0.0) for i in set(dist) | set(names)}
        # ties go to the ID: it is the stronger evidence
        order = sorted(scores, key=lambda i: (-scores[i], dist.get(i, 9), i))[:limit]
        cands = []
        for i in order:
            p = self.roster[i]
            why = []
            if i in dist:
                why.append("ID exact" if dist[i] == 0 else "ID look-alike" if dist[i] == 0.5 else f"ID {dist[i]} off")
            if i in names:
                s = names[i]
                why.append("name exact" if s >= NAME_EXACT else "name close" if s >= NAME_CLOSE else f"name {round(s * 100)}%")
            cands.append({'key': p.get('key'), 'name': p.get('name', ''), 'id': p.get('id', ''),
                          'label': label(p), 'score': round(scores[i], 3), 'why': ", ".join(why),
                          'taken': taken.get(p.get('key'))})
        return {'doubt': doubt, 'candidates': cands}


def propose(read: Mapping[str, str | None], roster: Sequence[Mapping], *,
            taken: Mapping[str, str] | None = None, limit: int = 5) -> dict:
    """`Matcher(roster).propose(...)`: see there."""
    return Matcher(roster).propose(read, taken=taken, limit=limit)


# --- everything at once, for the page and the command line ----------------------------

CACHE_LIMIT = 20_000        # proposals an overview cache holds before it starts again

def overview(ws, subs: Iterable[str] | None = None, *, cache: dict | None = None) -> dict:
    """Every submission (or `subs`) with what was read, the proposal and
    the pick, most doubtful unconfirmed first, then the confirmed ones; and the
    roster entries nobody is picked for. `cache`, a dict the caller keeps,
    remembers proposals by reading revision and roster."""
    fields = boxes(ws)
    roster = people(ws)
    rkey = digest([roster, [box_json(f) for f in fields]])
    matcher = None
    cache = {} if cache is None else cache
    if len(cache) > CACHE_LIMIT:
        cache.clear()
    known = [s.id for s in ws.store.submissions()]
    ids = known if subs is None else [s for s in subs if s in set(known)]
    picks = {a.submission: a for a in ws.store.all(kinds=[WHO]) if a.submission in set(known)}
    reads = {a.submission: a for a in ws.store.all(kinds=[READ])}
    taken: dict[str, list[str]] = {}
    for sid, a in picks.items():
        key = (a.body or {}).get('key')
        if key:
            taken.setdefault(key, []).append(sid)

    def other(key: str | None, sid: str) -> str | None:
        """Another submission the person `key` is picked for, if any."""
        return next((s for s in taken.get(key, ()) if s != sid), None)

    rows = []
    for sid in ids:
        r = reads.get(sid)
        got = readings(fields, r.body if r else None)
        ck = (sid, r.rev if r else None, rkey)
        prop = cache.get(ck)
        if prop is None:
            if roster and any(got.values()):
                matcher = matcher or Matcher(roster)
                prop = matcher.propose(by_roster(fields, got))
            else:
                # every box read and none had anything: empty, not unread
                blank = r is not None and len(got) == len(fields) and not (r.body or {}).get('failed')
                prop = {'doubt': 'nobody' if any(got.values()) else 'empty' if blank else 'unread', 'candidates': []}
            cache[ck] = prop
        prop = {'doubt': prop['doubt'],
                'candidates': [{**c, 'taken': other(c['key'], sid)} for c in prop['candidates']]}
        pick = picks.get(sid)
        body = pick.body if pick is not None and isinstance(pick.body, dict) else None
        twice = bool(body and body.get('key') and len(taken.get(body['key'], ())) > 1)
        rows.append({'sub': sid, 'read': got, 'failed': (r.body or {}).get('failed', []) if r else [],
                     'doubt': prop['doubt'], 'candidates': prop['candidates'],
                     'who': ({'name': body.get('name', ""), 'id': body.get('id', ""), 'key': body.get('key'),
                              'twice': twice} if body else None),
                     'rev': pick.rev if pick is not None else None})
    rank = {d: i for i, d in enumerate(ORDER)}
    rows.sort(key=lambda x: (x['who'] is not None, rank[x['doubt']] if x['who'] is None else 0, x['sub']))
    picked = set(taken)
    return {'rows': rows, 'nobody': [p for p in roster if p['key'] not in picked],
            'left': sum(1 for x in rows if x['who'] is None)}

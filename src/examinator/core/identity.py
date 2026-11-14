"""Who wrote each submission: the roster, the identity fields, ranked
proposals, and the person a grader picks.

    setting 'people'                  the roster: [{'name', 'id', 'email', 'key'}]
    row (sub, '@identity/<column>', 'value')   an identity field's value, like any field's
    row (sub, '', 'who')              the pick: the person picked, {'name', 'id', 'email'}

Where students write who they are is the exam's built-in group of identity
fields (`defs.IDENTITY`): one ordinary field per roster column, with a region
set in the package or drawn in the app (for every submission or for one), read
by its reader (`Ask` about the handwriting, by default) with `Runner.read`, and
set or verified like any field. Moving a box changes no value: reading again
is a person's choice (`read`, `again`).

Matching acts only on unique evidence. Each identifying column (of a reading,
or of a pick) yields the roster people it supports; a match needs them to
single out one person. Ties and conflicts between columns (a column that
supports nobody among them) are doubts shown to a person, never resolved
silently. Proposals are computed, never stored: `Matcher.propose(read)` is a
pure function of the values and the roster, and one computation gives who the
evidence singles out (`match`), the doubt, and the order of the candidates. A
roster is valid exactly when every person on it is found as themselves
(`Matcher.find`).
Nothing here confirms an identity; only `pick`, a person's action, writes the
pick, which records the person (not a roster key): its link to the current
roster, and so "taken" and "twice", are derived by the same matching
(`Matcher.find`). Whether someone is taken never changes a proposal.
"""
from __future__ import annotations

import csv
import difflib
import io
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .defs import IDENTITY, Field, Question, digest, split_key
from .status import Ledger, Value
from .store import ANY, Conflict

WHO = 'who'
COLUMNS = ('name', 'id', 'email')

# --- the roster ---------------------------------------------------------------------

NAME_COLUMNS = ('name', 'full name', 'student name', 'student')
FIRST, LAST = ('first name', 'first', 'given name'), ('last name', 'last', 'surname', 'family name')
EMAIL = ('email', 'e-mail', 'email address')
ID_COLUMNS = ('student id', 'sid', 'id', 'student number', 'id number')
HEADERS = set(NAME_COLUMNS + FIRST + LAST + EMAIL + ID_COLUMNS)
LONGEST = 200       # characters of a name (an email, an ID: less)


def looks_like_id(text: str) -> bool:
    """The rule for telling an ID from a name on a pasted line: no spaces, and
    at least one digit."""
    return bool(text) and not any(c.isspace() for c in text) and any(c.isdigit() for c in text)


def _person(name: str, id: str = '', email: str = '') -> dict:
    p = {'name': name.strip()[:LONGEST], 'id': id.strip()[:100], 'email': email.strip()[:LONGEST]}
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
    ID ("Ada Lovelace, alovelace"). Blank lines are skipped. A line whose
    person could not be told apart from an earlier line's (`Matcher.find`
    would not find each as themselves: the same email or ID, or for someone
    with neither, a name someone else has) is a problem."""
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
    people, lines = [p for _, p in out], [n for n, _ in out]
    if Matcher(people).lost():          # rare: then line by line, keeping the earlier person
        people, lines = [], []
        for n, p in out:
            m = Matcher(people + [p])
            lost = m.lost()
            if lost:
                other = lines[min(i for i in lost + m.finds(p) if i < len(people))]
                problems.append(f"line {n}: {label(p)} cannot be told apart from line {other}")
                continue
            people.append(p)
            lines.append(n)
    return people, problems


def parse_roster(text: str) -> list[dict]:
    """People from roster text (`read_roster`); any problem is an error."""
    people, problems = read_roster(text)
    if problems:
        raise ValueError("; ".join(problems[:3]) + (f" (and {len(problems) - 3} more)" if len(problems) > 3 else ""))
    if not people:
        raise ValueError("no people found")
    return people


def roster_rev(ws) -> str | None:
    """The roster's revision (a digest of it; None: no roster)."""
    people = ws.store.setting('people', [])
    return digest(people)[:16] if people else None


def set_roster(ws, text: str, *, expect_rev: Any = ANY) -> int:
    """Replace the roster, against the revision the person saw (`expect_rev`)."""
    people = parse_roster(text)
    if expect_rev is not ANY and roster_rev(ws) != expect_rev:
        e = Conflict("the roster changed")
        e.current = {'rev': roster_rev(ws), 'people': len(ws.store.setting('people', []))}
        raise e
    ws.store.set_setting('people', people)
    by_file(ws)
    return len(people)


def people(ws) -> list[dict]:
    return [dict(p) for p in ws.store.setting('people', [])]


def on_roster(ws, key: str) -> dict | None:
    """The roster person a page names by `key` (None: nobody, as after the roster changed)."""
    return next((p for p in people(ws) if p['key'] == key), None)


def label(person: Mapping) -> str:
    """How a person appears in lists."""
    extra = person.get('id') or person.get('email')
    return f"{person['name']} <{extra}>" if extra else person['name']


# --- the identity fields: ordinary fields of the built-in group ------------------------

def group(ws) -> Question:
    """The built-in group of identity fields (`defs.IDENTITY`), live."""
    return ws.exam.identity


def placed(ws, sid: str | None = None) -> tuple[Field, ...]:
    """The identity fields with a region: every submission's (or this submission's)."""
    g = group(ws) if sid is None else ws.layers(sid).question(IDENTITY)
    return tuple(f for f in g.fields if f.region is not None)


def boxed(ws) -> bool:
    """Whether any submission has a name or ID box: the one test for "are there boxes"
    (to show "read names", to read them)."""
    return bool(placed(ws)) or any(placed(ws, s.id) for s in ws.store.submissions())


@dataclass(frozen=True)
class At:
    """One submission's placed identity fields (with its own regions) and its print area."""
    fields: tuple[Field, ...]
    layout: Any

    def unread(self, got: Mapping[str, Value]) -> list[str]:
        """The placed fields with no value."""
        return [f.label for f in self.fields if f.label not in got]


def at(ws, sid: str) -> At:
    lay = ws.layers(sid)
    return At(tuple(f for f in lay.question(IDENTITY).fields if f.region is not None), lay.layout)


def values(ws, sid: str | None = None) -> dict[str, dict[str, Value]]:
    """Every submission's identity values (or one's): {submission: {column: Value}}."""
    return {s: {split_key(k)[1]: v for k, v in got.values.items()}
            for s, got in Ledger.load(ws.store, sid, kinds=('value',), prefix=IDENTITY + '/').items()}


def unread(ws, *, again: bool = False, ats: Mapping[str, At] | None = None) -> list[str]:
    """What reading names reads: the submissions nobody has named yet with a placed
    identity field that has no value (`again`: or one no person verified). A named
    submission's pick stands, and its boxes serve only to propose who wrote it, so
    reading them would change nothing. `ats`: each submission's `at`, if the caller has them."""
    got, named = values(ws), {a.submission for a in ws.store.all(kinds=[WHO])}

    def to_read(sid: str) -> bool:
        a, have = (ats or {}).get(sid) or at(ws, sid), got.get(sid, {})
        return bool(a.unread(have)) or again and any(
            not have[f.label].protected for f in a.fields if f.label in have)
    return [s.id for s in ws.store.submissions() if s.id not in named and to_read(s.id)]


def read(runner, sid: str, *, again: bool = False) -> list:
    """Read one submission's placed identity fields that have no value (`again`: also
    those no person verified): `Runner.read` of the group."""
    labels = [f.label for f in placed(runner.ws, sid)]
    return runner.read(sid, IDENTITY, labels=labels, missing_only=not again) if labels else []


def usable(v: Value | None) -> str | None:
    """A value as evidence of who it is: its text, unless blank or it needs review."""
    return v.text if v is not None and v.text.strip() and v.review is None else None


# --- the pick -------------------------------------------------------------------------

def who(ws, submission: str) -> dict:
    """The person picked for a submission: {'name', 'id', 'email'} ('' when nobody)."""
    a = ws.store.get(submission, '', WHO)
    body = a.body if a is not None and isinstance(a.body, dict) else {}
    return {c: body.get(c, "") for c in COLUMNS}


def typed(text: str) -> dict:
    """Someone typed in rather than picked from the roster: "Name", or "Name, ID"."""
    text = text.strip() if isinstance(text, str) else ''
    if not text or len(text) > LONGEST:
        raise ValueError(f"expected a name (at most {LONGEST} characters)")
    name, comma, id = text.rpartition(',')
    return _person(name, id) if comma and name.strip() and looks_like_id(id.strip()) else _person(text)


def stale(ws, sid: str) -> Conflict:
    """A naming refused because the pick changed since the person saw it, saying who
    the submission is now."""
    return Conflict(f"the name changed: now {who(ws, sid)['name'] or 'nobody'}", name='', kind=WHO)


def pick(ws, sid: str, person: Mapping | None, *, author: str | None, expect_rev: int | None,
         origin: str = 'manual') -> dict:
    """Name a submission: the person (`on_roster`, `typed`), or None to unname it.
    `expect_rev` is the pick's revision the person saw (None: there was none);
    otherwise `stale`."""
    s = ws.store.submission(sid)
    if s is None:
        raise KeyError(sid)
    try:
        if person is None:
            ws.store.delete(sid, '', WHO, expect_rev=expect_rev)
            rev = None
        else:
            body = {c: str(person.get(c) or '') for c in COLUMNS}
            rev = ws.store.write(sid, '', WHO, body, origin=origin, author=author, expect_rev=expect_rev).rev
    except Conflict:
        raise stale(ws, sid) from None
    return {**who(ws, sid), 'rev': rev}


def restore(ws, sid: str, rev: int, *, expect_rev: int | None) -> dict:
    """Undo: the pick as it was at revision `rev`, a version in its history (a
    removal is undone so; unnaming again is `pick(None)`), under a new revision."""
    if ws.store.submission(sid) is None:
        raise KeyError(sid)
    try:
        row = ws.store.restore(sid, '', WHO, rev, expect_rev=expect_rev)
    except Conflict:
        raise stale(ws, sid) from None
    return {**who(ws, sid), 'rev': row.rev}


def by_file(ws) -> int:
    """The programmatic way to name submissions: a PDF whose file name carries
    exactly one roster ID that only one person has, as a whole run of letters
    and digits ("31415926.pdf", "quiz-31415926.pdf"), is picked for that
    person, unless the submission has or ever had a pick: a person's word,
    clearing included, stands."""
    count: dict[str, int] = {}
    for p in people(ws):
        if id_text(p['id']):
            count[id_text(p['id'])] = count.get(id_text(p['id']), 0) + 1
    ids = {id_text(p['id']): p for p in people(ws) if count.get(id_text(p['id'])) == 1}
    n = 0
    for s in ws.store.submissions() if ids else ():
        found = {t for t in re.findall(r'[^\W_]+', fold(s.id)) if t in ids}
        if len(found) != 1 or ws.store.get(s.id, '', WHO) is not None or ws.store.history(s.id, '', WHO):
            continue
        pick(ws, s.id, ids[found.pop()], author=None, expect_rev=None, origin='auto')
        n += 1
    return n


# --- proposing: pure ------------------------------------------------------------------

DOUBTS = {'agree': "both agree", 'id': "ID only", 'email': "email only", 'name': "name only",
          'disagree': "they disagree", 'tie': "several match", 'nobody': "nobody close",
          'unreadable': "unreadable", 'empty': "empty", 'unread': "not read yet"}
ORDER = ('disagree', 'tie', 'nobody', 'unreadable', 'empty', 'unread', 'name', 'email', 'id', 'agree')   # most doubtful first
NAME_CLOSE = 0.8        # a name this similar supports a person ...
NAME_MARGIN = 0.05      # ... unless someone else is within this of the best
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
        self.index = {p.get('key'): i for i, p in enumerate(self.roster)}
        self._names = [name_tokens(p.get('name') or '') for p in self.roster]
        self._ids = {f: [id_text(p.get(f) or '') for p in self.roster] for f in ('id', 'email')}
        self._alike = {f: [x.translate(ALIKE) for x in ids] for f, ids in self._ids.items()}
        self._exact: dict[str, dict[str, list[int]]] = {f: {} for f in self._ids}
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

    def _name_scores(self, reading: str | None) -> dict[int, float]:
        """Roster index -> how alike the names are, for those at least `NAME_SOME`."""
        tokens = name_tokens(reading or '')
        if not tokens:
            return {}
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
        out = {}
        for i, toks in enumerate(self._names):
            s = name_score(tokens, toks, ratio)
            if s >= NAME_SOME:
                out[i] = s
        return out

    def propose(self, read: Mapping[str, str | None], *, limit: int = 5) -> dict:
        """For one submission, from its readings by roster column ('name', 'id',
        'email'): who the evidence singles out (`match`: a roster index, or None),
        the doubt, and ranked candidates, each with a short reason.

        Each column read (one the roster has) supports the people it fits best:
        an ID or email the nearest within tolerance, a name those within
        `NAME_MARGIN` of the best if that is at least `NAME_CLOSE`; it may
        support nobody, and that is evidence too. The people every column read
        supports decide: exactly one is the match, 'agree' (two columns or more)
        or the column's name ('id', 'email', 'name'); several, 'tie'; none,
        'disagree' if some column supports someone, else 'nobody'. Nothing
        read, 'unread'. Candidates come in the order of the evidence: those
        every column supports, then by how many columns support them, then by
        how alike they are."""
        cols = [c for c in COLUMNS if read.get(c) and (c == 'name' or self._exact[c])]
        if not cols:
            return {'doubt': 'unread', 'match': None, 'candidates': []}
        support: dict[str, set[int]] = {}
        dist: dict[int, float] = {}
        for c in cols:
            if c != 'name':
                d = self._id_distances(c, read[c])
                support[c] = {i for i, x in d.items() if x == min(d.values())}
                for i, x in d.items():
                    dist[i] = min(x, dist.get(i, x))
        names = self._name_scores(read.get('name'))
        if 'name' in cols:
            top = max(names.values(), default=0.0)
            support['name'] = {i for i, s in names.items() if s >= NAME_CLOSE and top - s < NAME_MARGIN}
        common = set.intersection(*support.values())
        anyone = set().union(*support.values())
        doubt = ('nobody' if not anyone else 'disagree' if not common else 'tie' if len(common) > 1
                 else 'agree' if len(support) > 1 else cols[0])
        scores = {i: ID_WEIGHT.get(dist.get(i, -1), 0.0) + names.get(i, 0.0) for i in set(dist) | set(names)}
        votes = {i: sum(i in s for s in support.values()) for i in scores}
        # ties go to the ID: it is the stronger evidence
        order = sorted(scores, key=lambda i: (i not in common, -votes[i], -scores[i], dist.get(i, 9), i))[:limit]
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
                          'label': label(p), 'score': round(scores[i], 3), 'why': ", ".join(why)})
        return {'doubt': doubt, 'match': next(iter(common)) if len(common) == 1 else None, 'candidates': cands}

    def finds(self, person: Mapping) -> list[int]:
        """The roster people (indices) a pick could be on exact evidence: by ID and
        email where the pick and the roster have them (a person whose other column
        differs is not them), else by the whole name."""
        strong = {c: id_text(person.get(c) or '') for c in ('id', 'email')}
        strong = {c: v for c, v in strong.items() if v and self._exact[c]}
        if strong:
            hits = {i for c, v in strong.items() for i in self._exact[c].get(v, ())}
            return sorted(i for i in hits if all(self._ids[c][i] in ('', v) for c, v in strong.items()))
        tokens = sorted(name_tokens(person.get('name') or ''))
        return [i for i, t in enumerate(self._names) if tokens and sorted(t) == tokens]

    def find(self, person: Mapping) -> int | None:
        """The roster person a pick is (index), when exactly one (`finds`); else None."""
        ok = self.finds(person)
        return ok[0] if len(ok) == 1 else None

    def lost(self) -> list[int]:
        """The people on the roster not found as themselves: none, for a valid roster."""
        return [i for i, p in enumerate(self.roster) if self.find(p) != i]


def propose(read: Mapping[str, str | None], roster: Sequence[Mapping], *, limit: int = 5) -> dict:
    """`Matcher(roster).propose(...)`: see there."""
    return Matcher(roster).propose(read, limit=limit)


# --- everything at once, for the page and the command line ----------------------------

CACHE_LIMIT = 20_000        # proposals an overview cache holds before it starts again


def overview(ws, subs: Iterable[str] | None = None, *, cache: dict | None = None,
             ats: Mapping[str, At] | None = None) -> dict:
    """Every submission (or `subs`) with its identity values, its placed fields
    with no value (`At.unread`), the proposal (`match`: the key of who the evidence
    singles out, or None) and the pick, the unpicked first (most doubtful
    first), then the confirmed ones; who on the roster is picked for which
    submissions; and those nobody is picked for. `cache`, a dict the caller
    keeps, remembers proposals by value revisions and roster; `ats`, each
    submission's `at`, if the caller has them."""
    roster = people(ws)
    rkey = digest(roster)
    cache = {} if cache is None else cache
    if len(cache) > CACHE_LIMIT:
        cache.clear()
    matcher = cache.get(('matcher', rkey))
    if matcher is None:
        matcher = cache['matcher', rkey] = Matcher(roster)
    known = [s.id for s in ws.store.submissions()]
    have = set(known)
    ids = known if subs is None else [s for s in subs if s in have]
    picks = {a.submission: a for a in ws.store.all(kinds=[WHO]) if a.submission in have}
    every = values(ws)
    # who each pick is on the roster now: derived, so a new roster relinks every pick
    links = {sid: matcher.find(a.body or {}) for sid, a in picks.items()}
    taken: dict[int, list[str]] = {}
    for sid, i in sorted(links.items()):
        if i is not None:
            taken.setdefault(i, []).append(sid)

    def elsewhere(key: str | None, sid: str) -> str | None:
        return next((s for s in taken.get(matcher.index.get(key), ()) if s != sid), None)

    rows = []
    for sid in ids:
        a = (ats or {}).get(sid) or at(ws, sid)
        got = every.get(sid, {})
        unread = a.unread(got)
        have = {f.label: got[f.label] for f in a.fields if f.label in got}
        read = {c: usable(v) for c, v in have.items()}
        ck = (sid, tuple((c, v.rev) for c, v in have.items()), tuple(unread), rkey)
        prop = cache.get(ck)
        if prop is None:
            if any(read.values()):
                prop = matcher.propose(read)
            else:
                # nothing usable: still to read, could not be read, or every box blank
                doubt = ('unread' if unread or not have else
                         'unreadable' if any(v.review is not None for v in have.values()) else 'empty')
                prop = {'doubt': doubt, 'match': None, 'candidates': []}
            cache[ck] = prop
        # who is already picked elsewhere is shown, never used to rank: a misreading must not cascade
        cands = [{**c, 'taken': elsewhere(c['key'], sid)} for c in prop['candidates']]
        pick_row = picks.get(sid)
        body = pick_row.body if pick_row is not None and isinstance(pick_row.body, dict) else None
        link = links.get(sid)
        rows.append({'sub': sid, 'read': read, 'unread': unread,
                     'values': {c: {'text': v.text, 'rev': v.rev, 'verified': v.verified, 'review': v.review}
                                for c, v in got.items()},
                     'doubt': prop['doubt'], 'candidates': cands,
                     'match': roster[prop['match']]['key'] if prop['match'] is not None else None,
                     'who': ({**{c: body.get(c, "") for c in COLUMNS},
                              'person': roster[link]['key'] if link is not None else None,
                              'twice': link is not None and len(taken[link]) > 1} if body else None),
                     'rev': pick_row.rev if pick_row is not None else None})
    rank = {d: i for i, d in enumerate(ORDER)}
    rows.sort(key=lambda x: (x['who'] is not None, 0 if x['who'] else rank[x['doubt']], x['sub']))
    return {'rows': rows, 'nobody': [p for i, p in enumerate(roster) if i not in taken],
            'taken': {roster[i]['key']: subs for i, subs in taken.items()},
            'left': sum(1 for x in rows if x['who'] is None)}

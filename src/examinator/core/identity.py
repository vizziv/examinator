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

Proposals are computed, never stored, by the pure matching of `core.roster`
(`Matcher.propose`), from the values and the roster.
Nothing here confirms an identity; only `pick`, a person's action, writes the
pick, which records the person (not a roster key): its link to the current
roster, and so "taken" and "twice", are derived by the same matching
(`Matcher.find`). Whether someone is taken never changes a proposal.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .defs import IDENTITY, ROSTER_FIELDS, Field, digest, split_key
from .roster import ORDER, Matcher, fold, id_text, parse_roster
from .status import Ledger, Value
from .store import ANY, Conflict

WHO = 'who'

def roster_rev(ws) -> str | None:
    """The roster's revision (a digest of it; None: no roster)."""
    people = ws.store.setting('people', [])
    return digest(people)[:16] if people else None


def set_roster(ws, text: str, *, expect_rev: Any = ANY) -> int:
    """Replace the roster, against the revision the person saw (`expect_rev`)."""
    people = parse_roster(text)
    if expect_rev is not ANY and roster_rev(ws) != expect_rev:
        raise Conflict("the roster changed",
                       current={'rev': roster_rev(ws), 'people': len(ws.store.setting('people', []))})
    ws.store.set_setting('people', people)
    by_file(ws)
    return len(people)


def people(ws) -> list[dict]:
    return [dict(p) for p in ws.store.setting('people', [])]


def on_roster(ws, key: str) -> dict | None:
    """The roster person a page names by `key` (None: nobody, as after the roster changed)."""
    return next((p for p in people(ws) if p['key'] == key), None)


# --- the identity fields: ordinary fields of the built-in group ------------------------

def placed(ws, sid: str | None = None) -> tuple[Field, ...]:
    """The identity fields with a region: every submission's (or this submission's)."""
    g = ws.exam.identity if sid is None else ws.layers(sid).question(IDENTITY)
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
    return {c: body.get(c, "") for c in ROSTER_FIELDS}


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
            body = {c: str(person.get(c) or '') for c in ROSTER_FIELDS}
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
                     'who': ({**{c: body.get(c, "") for c in ROSTER_FIELDS},
                              'person': roster[link]['key'] if link is not None else None,
                              'twice': link is not None and len(taken[link]) > 1} if body else None),
                     'rev': pick_row.rev if pick_row is not None else None})
    rank = {d: i for i, d in enumerate(ORDER)}
    rows.sort(key=lambda x: (x['who'] is not None, 0 if x['who'] else rank[x['doubt']], x['sub']))
    return {'rows': rows, 'nobody': [p for i, p in enumerate(roster) if i not in taken],
            'taken': {roster[i]['key']: subs for i, subs in taken.items()},
            'left': sum(1 for x in rows if x['who'] is None)}

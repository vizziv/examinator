"""Who wrote each submission: rosters, the matcher, readings and picks. Synthetic material only."""
import random
from pathlib import Path

import pytest

from examinator import load_exam, synth
from examinator.core import batch, focus, grid, identity, intake, overrides
from examinator.core.defs import IDENTITY
from examinator.core import roster
from examinator.core.roster import Matcher, distance, name_score, name_tokens, read_roster
from examinator.core.reading import Reading
from examinator.core.runner import Runner
from examinator.core.store import Conflict
from examinator.core.workspace import Workspace
from examinator.model.fake import FakeModel
from conftest import first_of

TOY = Path(__file__).resolve().parents[1] / 'exams' / 'toy_exam'

ROSTER = [
    {'name': "Ada Lovelace", 'id': "31415926", 'key': 'ada'},
    {'name': "Alan Turing", 'id': "27182818", 'key': 'alan'},
    {'name': "Grace Hopper", 'id': "16180339", 'key': 'grace'},
    {'name': "Émile Borél", 'id': "14142135", 'key': 'emile'},
    {'name': "John Smith", 'id': "11111111", 'key': 'john'},
    {'name': "Jon Smith", 'id': "22222222", 'key': 'jon'},
    {'name': "Kim Park", 'id': "33333333", 'key': 'kim1'},
    {'name': "Kim Park", 'id': "44444444", 'key': 'kim2'},
]


# --- pasted rosters ---

def test_plain_lines_in_either_order_with_commas_or_tabs():
    people, problems = read_roster("Ada Lovelace, 31415926\n27182818\tAlan Turing\n\n"
                                   "Hopper, Grace, 16180339\n\"Borél, Émile\",14142135\n")
    assert problems == []
    assert [(p['name'], p['id']) for p in people] == [
        ("Ada Lovelace", "31415926"), ("Alan Turing", "27182818"),
        ("Hopper, Grace", "16180339"), ("Borél, Émile", "14142135")]
    assert people[0]['key'] == "31415926" and people[0]['email'] == ''


def test_an_id_is_the_part_without_spaces():
    people, problems = read_roster("Ada Lovelace, alovelace\nabc12, Alan Turing\n")
    assert problems == [] and [p['id'] for p in people] == ["alovelace", "abc12"]


def test_lines_that_cannot_be_read_are_reported():
    people, problems = read_roster("Ada Lovelace\nAda, Lovelace\n12, 34\nAlan Turing, 1\nAlan T, 1\n")
    assert [p['name'] for p in people] == ["Alan Turing"]
    assert problems == ["line 1: expected a name and an ID", "line 2: expected a name and an ID",
                        "line 3: expected a name and an ID", "line 5: Alan T <1> cannot be told apart from line 4"]
    with pytest.raises(ValueError, match="line 1"):
        roster.parse_roster("Ada Lovelace\n")


def test_a_roster_is_valid_exactly_when_everyone_on_it_is_found_as_themselves():
    """One rule (`Matcher.find`) instead of duplicate checks: a line whose person could not
    be told apart from an earlier one's is a problem, so a pick of anyone kept links to them."""
    people, problems = read_roster("Name,Student ID\nJohn Smith,44444444\nJohn Smith,\n")
    assert [p['id'] for p in people] == ["44444444"]
    assert problems == ["line 3: John Smith cannot be told apart from line 2"]
    people, problems = read_roster("Name,Student ID\nJohn Smith,\nJohn Smith,44444444\n")    # either order
    assert [p['id'] for p in people] == [''] and len(problems) == 1
    people, problems = read_roster("Name,Email,Student ID\nAda Lovelace,ada@example.invalid,31415926\n"
                                   "Ada King,,31415926\nKim Park,,\nKim  park,,\n"
                                   "Ada King,ada.king@example.invalid,31415926\n")
    assert [p['name'] for p in people] == ["Ada Lovelace", "Kim Park", "Ada King"]     # the email tells her apart
    assert problems == ["line 3: Ada King <31415926> cannot be told apart from line 2",
                        "line 5: Kim  park cannot be told apart from line 4"]
    rng = random.Random(4580)
    for _ in range(200):
        lines = [",".join((rng.choice(["Ada Lovelace", "Ada King", "lovelace ada", "Kim Park"]),
                           rng.choice(["", "a@x.invalid", "b@x.invalid"]), rng.choice(["", "11111111", "22222222"])))
                 for _ in range(rng.randint(1, 6))]
        people, problems = read_roster("Name,Email,Student ID\n" + "\n".join(lines) + "\n")
        m = Matcher(people)
        assert all(m.find(p) == i for i, p in enumerate(people)) and len(people) + len(problems) == len(lines)
    # and should a roster hold such people anyway, an ID two people have singles out nobody
    twins = [{'name': "Ada Lovelace", 'id': "31415926", 'key': 'a'}, {'name': "Ada King", 'id': "31415926", 'key': 'b'}]
    assert Matcher(twins).propose({'id': "31415926"})['doubt'] == 'tie'
    assert Matcher(twins).propose({'id': "31415926", 'name': "Ada King"})['doubt'] == 'agree'
    assert Matcher(twins).find({'name': "Ada King", 'id': "31415926"}) is None


def test_header_names_are_generic():
    people, problems = read_roster("Name,NetID\nAda Lovelace,al815\n")
    assert problems == [] and people[0]['id'] == ''          # one institution's word is not a column here


def test_a_header_means_columns_by_name():
    people, _ = read_roster("Name,Email,Student ID\nAda Lovelace,ada@example.invalid,31415926\n")
    assert people == [{'name': "Ada Lovelace", 'id': "31415926", 'email': "ada@example.invalid",
                       'key': "ada@example.invalid"}]
    people, _ = read_roster("First Name\tLast Name\tSID\nAda\tLovelace\t31415926\n")
    assert (people[0]['name'], people[0]['id']) == ("Ada Lovelace", "31415926")
    people, problems = read_roster("﻿Email\nx@example.invalid\n")
    assert people == [] and problems == ["line 2: no name"]


# --- the matcher, pure ---

def top(result):
    return result['candidates'][0]['key'] if result['candidates'] else None


def test_clean_read_both_agree():
    r = Matcher(ROSTER).propose({'name': "Ada Lovelace", 'id': "31415926"})
    assert r['doubt'] == 'agree' and top(r) == 'ada'
    assert r['candidates'][0]['why'] == "ID exact, name exact"


def test_names_ignore_order_case_accents_and_punctuation():
    for written in ("LOVELACE, ada", "lovelace ada.", "Ada  Lovelace"):
        r = Matcher(ROSTER).propose({'name': written})
        assert r['doubt'] == 'name' and top(r) == 'ada' and r['candidates'][0]['why'] == "name exact"
    assert top(Matcher(ROSTER).propose({'name': "Emile Borel"})) == 'emile'
    assert name_tokens("O'Brien-Smith, Zoë") == ['obrien', 'smith', 'zoe']


def test_an_id_misread_by_a_character_or_two():
    r = Matcher(ROSTER).propose({'name': "Alan Turing", 'id': "27182819"})
    assert r['doubt'] == 'agree' and top(r) == 'alan' and r['candidates'][0]['why'] == "ID 1 off, name exact"
    r = Matcher(ROSTER).propose({'id': "27128818"})          # two digits swapped: one edit
    assert r['doubt'] == 'id' and top(r) == 'alan' and r['candidates'][0]['why'] == "ID 1 off"
    r = Matcher(ROSTER).propose({'id': "2718281O"})          # a wrong last character
    assert top(r) == 'alan'
    r = Matcher(ROSTER).propose({'id': "2718281B"})          # a letter B for an eight
    assert top(r) == 'alan' and r['candidates'][0]['why'] == "ID look-alike"
    r = Matcher(ROSTER).propose({'id': "99182811"})          # three off: nobody
    assert r['doubt'] == 'nobody' and r['candidates'] == []


def test_ids_of_letters_and_digits():
    roster = [{'key': k, 'name': n, 'id': i} for k, n, i in (
        ('a', "Ada Lovelace", 'al815'), ('b', "Alan Turing", 'amt54'), ('c', "Kim Park", 'kp7'))]
    look = lambda id: Matcher(roster).propose({'id': id})
    r = look("AL815")
    assert top(r) == 'a' and r['candidates'][0]['why'] == "ID exact"
    for written in ("a1815", "alb15", "al8l5", "AL81S"):        # look-alikes, either way round
        r = look(written)
        assert r['doubt'] == 'id' and top(r) == 'a' and r['candidates'][0]['why'] == "ID look-alike"
    r = look("amt45")                                           # five characters: one may be misread
    assert top(r) == 'b' and r['candidates'][0]['why'] == "ID 1 off"
    assert look("axt99")['doubt'] == 'nobody'                   # but not two
    assert top(look("kp7")) == 'c' and look("kp1")['doubt'] == 'nobody'     # a short ID: exact or nothing
    r = Matcher(roster).propose({'name': "Alan Turing", 'id': "al815"})             # an exact ID outranks a name
    assert r['doubt'] == 'disagree' and top(r) == 'a'


def test_a_misread_name_with_the_id_unread():
    r = Matcher(ROSTER).propose({'name': "Grase Hoper", 'id': None})
    assert r['doubt'] == 'name' and top(r) == 'grace' and r['candidates'][0]['why'] == "name close"


def test_a_proposal_is_one_computation_of_who_and_in_what_order():
    """Who the evidence singles out (`match`), the doubt and the order come from one place:
    the people every column read supports first; a column that supports nobody is a conflict."""
    roster = [{'key': 'ada', 'name': "Ada Lovelace", 'id': "11111111"}, {'key': 'bob', 'name': "Bob Stone", 'id': "22222222"}]
    r = Matcher(roster).propose({'name': "Bobby Stane", 'id': "11111199"})     # the ID, two off, is Ada's; the name nobody's
    assert r['doubt'] == 'disagree' and r['match'] is None and top(r) == 'ada'
    r = Matcher(roster).propose({'name': "Ada Lovelace", 'id': "99999999"})    # an ID that fits nobody is evidence
    assert r['doubt'] == 'disagree' and r['match'] is None
    r = Matcher(roster).propose({'name': "Bob Ston", 'id': "11111111"})
    assert r['doubt'] == 'disagree' and [c['key'] for c in r['candidates']] == ['ada', 'bob']
    for read in ({'name': "Ada Lovelace", 'id': "11111111"}, {'id': "11111112"}, {'name': "Lovelace Ada"}):
        r = Matcher(roster).propose(read)
        assert roster[r['match']]['key'] == top(r) == 'ada' and r['doubt'] in ('agree', 'id', 'name')
    rng = random.Random(4580)
    for _ in range(300):                # whenever there is a match, it is the first candidate
        read = {'name': rng.choice([None, "Ada Lovelace", "Bob Stone", "Ada Stone", "Bobby Lovelace"]),
                'id': rng.choice([None, "11111111", "22222222", "11111122", "12121212", "99999999"])}
        r = Matcher(roster).propose(read)
        assert (r['match'] is not None) == (r['doubt'] in ('agree', 'id', 'name'))
        assert r['match'] is None or roster[r['match']]['key'] == top(r)


def test_id_and_name_point_at_different_people():
    r = Matcher(ROSTER).propose({'name': "Grace Hopper", 'id': "31415926"})
    assert r['doubt'] == 'disagree'
    assert [c['key'] for c in r['candidates'][:2]] == ['ada', 'grace']      # an exact ID ranks first
    assert r['candidates'][0]['why'] == "ID exact"


def test_nobody_close_and_nothing_read():
    r = Matcher(ROSTER).propose({'name': "Finley Dummond", 'id': "95830142"})
    assert r['doubt'] == 'nobody' and r['candidates'] == []
    assert Matcher(ROSTER).propose({'name': None, 'id': ''}) == {'doubt': 'unread', 'match': None, 'candidates': []}


def test_names_too_alike_are_a_tie_and_all_are_candidates():
    r = Matcher(ROSTER).propose({'name': "Kim Park"})                           # two people of one name
    assert r['doubt'] == 'tie'
    assert [c['key'] for c in r['candidates'][:2]] == ['kim1', 'kim2']
    r = Matcher(ROSTER).propose({'name': "Kim Park", 'id': "44444444"})         # the ID settles it
    assert r['doubt'] == 'agree' and top(r) == 'kim2'
    r = Matcher(ROSTER).propose({'name': "John Smith"})                         # one letter apart is apart enough
    assert r['doubt'] == 'name' and [c['key'] for c in r['candidates'][:2]] == ['john', 'jon']


def test_a_name_that_fits_others_than_the_id_is_a_disagreement():
    roster = ROSTER + [{'name': "Ada Lovelace", 'id': "55555555", 'key': 'ada2'}]
    r = Matcher(roster).propose({'name': "Ada Lovelace", 'id': "27182818"})     # two Adas, and Turing's ID
    assert r['doubt'] == 'disagree' and top(r) == 'alan'
    r = Matcher(roster).propose({'name': "Ada Lovelace", 'id': "31415926"})     # the ID picks one of them
    assert r['doubt'] == 'agree' and top(r) == 'ada'
    assert Matcher(roster).propose({'name': "Ada Lovelace"})['doubt'] == 'tie'
    assert Matcher(roster).propose({'id': "31415926", 'email': "x@example.invalid"})['doubt'] == 'id'


def test_finding_a_pick_on_a_roster():
    m = Matcher(ROSTER + [{'name': "Ada Lovelace", 'id': '', 'email': "ada@example.invalid", 'key': 'ada3'}])
    assert m.find({'name': "Someone", 'id': "27182818"}) == 1           # by ID, whatever the name
    assert m.find({'name': "Alan Turing", 'id': "99999999"}) is None    # an ID nobody has: not by name
    assert m.find({'name': "turing, alan"}) == 1                        # nothing else to go by: the whole name
    assert m.find({'name': "Kim Park"}) is None                         # two of them
    assert m.find({'name': "Ada Lovelace", 'id': "31415926", 'email': "ada@example.invalid"}) is None   # conflict
    assert Matcher([{'name': "Ada Lovelace", 'key': 'a'}]).find({'name': "Ada Lovelace", 'id': "1"}) == 0


def test_distance_and_name_score():
    assert distance('12345678', '12345678') == 0
    assert distance('12345678', '12345687') == 1          # neighbours swapped
    assert distance('12345678', '1234567') == 1
    assert distance('12345678', '99945678', 2) == 3       # more than the limit
    assert name_score(['ada', 'lovelace'], ['lovelace', 'ada']) == 1.0
    assert name_score(['ada'], []) == 0.0
    assert name_score(['grace', 'hopper'], ['ada', 'lovelace']) < 0.6


def test_short_ids_tolerate_less():
    roster = [{'name': "A", 'id': "12", 'key': 'a'}, {'name': "B", 'id': "1234", 'key': 'b'}]
    assert Matcher(roster).propose({'id': "13"})['candidates'] == []
    assert top(Matcher(roster).propose({'id': "1235"})) == 'b'


# --- reading, storing, picking: the synthetic stacks end to end ---

@pytest.fixture
def written(tmp_path):
    """Both synthetic stacks split as they should be, accepted, with the roster."""
    toy = load_exam(TOY)
    root = tmp_path / 'ws'
    paths = synth.stacks(toy, root)
    ws = Workspace(root, toy, create=True)
    model = synth.fake_model(root)
    one, _ = intake.add_source(ws, paths[0])
    art = intake.reguess(ws, one.id, model=model)
    for _ in range(2):                  # one exam upside down: two quarter turns
        art = intake.edit(ws, one.id, 'turn', sheet=first_of(ws, one.id, 4), expect_rev=art.rev, author='t')
    intake.accept(ws, one.id)
    two, _ = intake.add_source(ws, paths[1])
    art = intake.plan(ws, two.id)
    for _ in range(2):
        art = intake.edit(ws, two.id, 'turn_source', expect_rev=art.rev, author='t')
    intake.reguess(ws, two.id, model=model)
    intake.accept(ws, two.id)
    identity.set_roster(ws, (root / 'roster.csv').read_text())
    runner = Runner(ws, model=model)
    yield ws, runner, model
    runner.close()
    ws.close()


def texts(ws, sid):
    return {c: v.text for c, v in identity.values(ws, sid).get(sid, {}).items()}


def test_identity_fields_are_ordinary_values(written):
    ws, runner, model = written
    assert sorted(ws.store.made()) == [f's{n:04d}' for n in range(1, 11)]
    assert [o.status for o in identity.read(runner, 's0001')] == ['done', 'done']     # email: no region, not read
    assert texts(ws, 's0001') == {'name': "Avery Testwell", 'id': "90412873"}
    got = runner.values('s0001')
    assert got['@identity/name'].verified is None                    # nobody stands behind it yet
    calls = len(model.calls)
    assert identity.read(runner, 's0001') == []                             # nothing left to read
    assert all(c.prompt.count("Read the HANDWRITTEN") == 1 and c.tag[1] == IDENTITY for c in model.calls[-2:])
    assert "Avery" not in " ".join(c.prompt for c in model.calls)            # no roster in any prompt
    identity.read(runner, 's0001', again=True)
    assert len(model.calls) == calls + 2 and runner.values('s0001') == got  # asked again; the same values
    assert ws.store.get('s0001', '', identity.WHO) is None                  # reading never picks
    # set, verify, history, and a read never replaces what a person set
    v = runner.set('s0001', IDENTITY, 'name', "Avery  Testwell", author='t')
    assert v.verified == 'person' and ws.store.history('s0001', '@identity/name', 'value')
    runner.verify('s0001', IDENTITY, 'id', author='t', expect_rev=got['@identity/id'].rev)
    identity.read(runner, 's0001', again=True)
    assert texts(ws, 's0001')['name'] == "Avery  Testwell" and runner.values('s0001')['@identity/id'].verified == 'person'
    assert identity.overview(ws, ['s0001'])['rows'][0]['read'] == {'name': "Avery  Testwell", 'id': "90412873"}


def test_moving_a_box_changes_no_value_and_reading_again_is_asked(written):
    """The standing rule: changing how a field is read changes no value. Reading
    again is explicit (`again`), and asks the model about every box no person verified."""
    ws, runner, model = written
    for sid in ('s0001', 's0002'):
        identity.read(runner, sid)
    before = {sid: runner.values(sid) for sid in ('s0001', 's0002')}
    g = ws.exam.identity
    path, scope = '@identity/fields/name/region', overrides.scope(overrides.SUBMISSION, 's0001')
    overrides.set(ws, path, [1, 100, 120, 800, 200], scope=scope, author='t')
    assert ws.layers('s0001').question(g.id).field('name').region.top == 120 and ws.layers('s0002').question(g.id).field('name').region == g.field('name').region
    assert {sid: runner.values(sid) for sid in before} == before                  # no value changed
    row = identity.overview(ws, ['s0001'])['rows'][0]
    assert 's0001' not in identity.unread(ws) and row['unread'] == [] and row['read']['name'] == "Avery Testwell"
    calls = len(model.calls)
    assert identity.read(runner, 's0001') == [] and len(model.calls) == calls     # only missing values are read
    identity.read(runner, 's0001', again=True)
    assert len(model.calls) == calls + 2        # both boxes asked again
    # every submission's box moved: reading again replaces what no person verified, only
    runner.verify('s0002', IDENTITY, 'name', author='t', expect_rev=runner.values('s0002')['@identity/name'].rev)
    overrides.set(ws, '@identity/fields/id/region', [1, 100, 330, 600, 420], author='t')
    assert runner.values('s0002') == {**before['s0002'], '@identity/name': runner.values('s0002')['@identity/name']}
    calls = len(model.calls)
    identity.read(runner, 's0002', again=True)
    assert len(model.calls) == calls + 1 and [c.tag[2] for c in model.calls[-1:]] == ['read:id']   # name: verified
    assert runner.values('s0002')['@identity/name'].verified == 'person'


def test_the_group_is_kept_apart_from_grading(written):
    ws, runner, model = written
    identity.read(runner, 's0001')
    exam = ws.exam
    row = next(r for r in grid.load(exam, ws.store) if r.submission.id == 's0001')
    assert '@identity/name' in row.ledger.values
    assert all(q != IDENTITY for q, _ in row.status) and IDENTITY not in row.fields
    assert IDENTITY not in {d['id'] for d in focus.describe_facets(exam)}
    assert not any(k.startswith(IDENTITY) for k in focus.values_table(exam, ws.store))
    assert IDENTITY not in exam.questions and exam.group(IDENTITY) is exam.identity     # asked for by name
    with pytest.raises(KeyError):           # a batch selects among the questions graders work through
        batch.plan(ws, batch.Selection(('s0001',), (IDENTITY,)), model_available=True)


def test_the_awkward_cases(written):
    ws, runner, model = written
    for sid in sorted(ws.store.made()):
        identity.read(runner, sid)
    rows = {r['sub']: r for r in identity.overview(ws)['rows']}
    got = {sid: (r['doubt'], r['candidates'][0]['name'] if r['candidates'] else None) for sid, r in rows.items()}
    assert got == {
        's0001': ('agree', "Avery Testwell"),
        's0002': ('agree', "Blake Mockford"),       # ID one off
        's0003': ('agree', "Casey Stubbs"),         # "Kasey Stubs" is still close
        's0004': ('disagree', "Gray Placeholt"),    # the ID is someone else's: the ID ranks first ...
        's0005': ('agree', "Emery Sampleton"),      # turned upright before accepting
        's0006': ('nobody', None),                  # not on the roster
        's0007': ('name', "Gray Placeholt"),        # no ID written
        's0008': ('id', "Harper Fixtura"),          # name unreadable
        's0009': ('agree', "Indigo Exemplar"),
        's0010': ('agree', "Jules Synthe"),
    }
    assert rows['s0004']['candidates'][1]['name'] == "Devon Fakely"          # ... and the name second
    assert rows['s0002']['candidates'][0]['why'] == "ID 1 off, name exact"
    view = identity.overview(ws)
    assert [r['sub'] for r in view['rows']][:2] == ['s0004', 's0006']       # most doubtful first
    assert view['left'] == 10 and {p['name'] for p in view['nobody']} >= {"Kit Absentia"}


def test_picks_confirm_flag_twice_and_name_submissions(written):
    ws, runner, model = written
    for sid in ('s0004', 's0007'):
        identity.read(runner, sid)
    gray = next(p for p in identity.people(ws) if p['name'] == "Gray Placeholt")
    body = identity.pick(ws, 's0007', gray, author='t', expect_rev=None)
    assert body['id'] == "96174385" and ws.store.submission('s0007').student == "96174385"
    with pytest.raises(Conflict):                   # someone else picked meanwhile
        identity.pick(ws, 's0007', gray, author='t', expect_rev=None)
    rows = {r['sub']: r for r in identity.overview(ws)['rows']}
    # Gray (by the ID) and Devon (by the name) match equally well: still a doubt, ranked by the
    # match alone (the ID first); Gray, already picked for s0007, is flagged, not moved
    cands = rows['s0004']['candidates']
    assert rows['s0004']['doubt'] == 'disagree' and cands[0]['score'] == cands[1]['score']
    assert cands[0]['key'] == gray['key'] and cands[0]['taken'] == 's0007'
    assert cands[1]['name'] == "Devon Fakely" and cands[1]['taken'] is None
    identity.pick(ws, 's0004', gray, author='t', expect_rev=None)
    rows = {r['sub']: r for r in identity.overview(ws)['rows']}
    assert rows['s0004']['who']['twice'] and rows['s0007']['who']['twice']
    view = identity.overview(ws)
    assert view['left'] == 8 and [r['sub'] for r in view['rows']][-2:] == ['s0004', 's0007']
    assert "Gray Placeholt" not in {p['name'] for p in view['nobody']}
    assert view['taken'] == {gray['key']: ['s0004', 's0007']}
    # someone not on the roster: typed; "Name, ID" keeps its ID; clearing
    rev = identity.pick(ws, 's0006', roster.typed("Finley Dummond"), author='t', expect_rev=None)['rev']
    assert ws.store.submission('s0006').name == "Finley Dummond" and ws.store.submission('s0006').student == ''
    rev = identity.pick(ws, 's0006', roster.typed("Finley Dummond, 95830142"), author='t', expect_rev=rev)['rev']
    assert (ws.store.submission('s0006').name, ws.store.submission('s0006').student) == ("Finley Dummond", "95830142")
    assert roster.typed("Dummond, Finley")['name'] == "Dummond, Finley"
    with pytest.raises(ValueError):
        roster.typed("  ")
    # typed with a roster person's ID: it is that person, derived like any pick
    rev = identity.pick(ws, 's0006', roster.typed("G. Placeholt, 96174385"), author='t', expect_rev=rev)['rev']
    assert {r['sub']: r for r in identity.overview(ws)['rows']}['s0006']['who']['person'] == gray['key']
    identity.pick(ws, 's0006', None, author='t', expect_rev=rev)
    assert ws.store.submission('s0006').name == ""
    assert ws.sync() == 0 and ws.store.submission('s0007').student == "96174385"


def test_who_is_taken_never_changes_a_proposal(written):
    """The owner's rule: picks elsewhere never move the order, scores or doubt of proposals."""
    ws, runner, model = written
    for sid in sorted(ws.store.made()):
        identity.read(runner, sid)
    strip = lambda view: {r['sub']: (r['doubt'], [{k: v for k, v in c.items() if k != 'taken'} for c in r['candidates']])
                          for r in view['rows']}
    before = strip(identity.overview(ws))
    roster = identity.people(ws)
    for n, sid in enumerate(sorted(ws.store.made())):           # everyone picked: some twice, some wrongly
        identity.pick(ws, sid, roster[n % 3], author='t', expect_rev=None)
    after = identity.overview(ws)
    assert strip(after) == before
    assert any(c['taken'] for r in after['rows'] for c in r['candidates'])


def test_a_new_roster_relinks_the_picks(written):
    """Picks record the person; who they are on the roster is derived, so a roster
    replaced by one keyed differently (emails added) keeps them matched, and "twice" still holds."""
    ws, runner, model = written
    with_emails = (ws.root / 'roster.csv').read_text()
    identity.set_roster(ws, "\n".join(f"{p['name']}, {p['id']}" for p in identity.people(ws)) + "\n")
    old = identity.people(ws)                       # keyed by ID
    identity.pick(ws, 's0001', old[0], author='t', expect_rev=None)
    identity.pick(ws, 's0002', old[0], author='t', expect_rev=None)
    identity.pick(ws, 's0003', old[2], author='t', expect_rev=None)
    identity.set_roster(ws, with_emails)
    new = identity.people(ws)                       # keyed by email
    assert new[0]['key'] != old[0]['key']
    view = identity.overview(ws)
    rows = {r['sub']: r for r in view['rows']}
    assert rows['s0001']['who']['person'] == rows['s0002']['who']['person'] == new[0]['key']
    assert rows['s0001']['who']['twice'] and not rows['s0003']['who']['twice']
    assert old[0]['name'] not in {p['name'] for p in view['nobody']}
    # a roster with names only: matched by the whole name
    identity.set_roster(ws, "Name\n" + "\n".join(p['name'] for p in old) + "\n")
    assert {r['sub']: r for r in identity.overview(ws)['rows']}['s0003']['who']['person'] == old[2]['name']


def test_empty_is_not_unreadable(written):
    ws, runner, model = written
    for col in ('name', 'id'):
        runner._put_read('s0001', IDENTITY, col, Reading(""), False, False)
    assert identity.overview(ws, ['s0001'])['rows'][0]['doubt'] == 'empty'
    runner._put_read('s0001', IDENTITY, 'name', Reading("Ann?", "smudged"), False, False)
    row = identity.overview(ws, ['s0001'])['rows'][0]
    assert row['doubt'] == 'unreadable' and row['values']['name']['review'] == "smudged"


def test_rewriting_a_submission_drops_its_unverified_readings(written):
    """Split's Accept rewrites a submission from other sheets: what a reader wrote and no
    person typed or verified is dropped (identity and grading fields alike: not read);
    values a person typed or verified, and the pick, stay."""
    ws, runner, model = written
    identity.read(runner, 's0001')
    runner.set('s0001', IDENTITY, 'id', "90412873", author='t')
    identity.pick(ws, 's0001', identity.people(ws)[0], author='t', expect_rev=None)
    one = ws.store.made()['s0001'].source
    intake.edit(ws, one, 'drop', sheet=3, expect_rev=intake.plan(ws, one).rev, author='t')
    with pytest.raises(intake.Busy):                                    # named: asked about first
        intake.accept(ws, one)
    assert intake.accept(ws, one, allow=['s0001'])['rewritten'] == ['s0001']
    row = identity.overview(ws, ['s0001'])['rows'][0]
    assert texts(ws, 's0001') == {'id': "90412873"} and row['unread'] == ['name']
    assert row['who']['name'] == "Avery Testwell" and ws.store.submission('s0001').name == "Avery Testwell"
    assert 's0001' not in identity.unread(ws)            # named: "read names" leaves it alone
    calls = len(model.calls)
    identity.read(runner, 's0001')
    assert len(model.calls) == calls + 1 and texts(ws, 's0001')['name'] == "Avery Testwell"
    # grading values: the same rule
    runner._put_read('s0001', 'areas', 'A.1', Reading("4"), False, False)                 # read, unverified
    runner._put_read('s0001', 'areas', 'side', Reading("red"), True, False)              # read, vouched by the reader
    runner._put_read('s0001', 'areas', 'A.2', Reading("4*x"), False, False)
    runner.verify('s0001', 'areas', 'A.2', author='t', expect_rev=runner.values('s0001')['areas/A.2'].rev)
    runner.set('s0001', 'parity', 'B', "even", author='t')                       # typed
    runner.verify('s0001', IDENTITY, 'name', author='t', expect_rev=runner.values('s0001')['@identity/name'].rev)
    intake.edit(ws, one, 'turn_page', sheet=0, expect_rev=intake.plan(ws, one).rev, author='t')
    with pytest.raises(intake.Busy):
        intake.accept(ws, one)
    assert intake.accept(ws, one, allow=['s0001'])['rewritten'] == ['s0001']
    got = {k: v.text for k, v in runner.values('s0001').items()}
    assert got == {'areas/A.2': "4*x", 'parity/B': "even", '@identity/id': "90412873", '@identity/name': "Avery Testwell"}
    assert identity.who(ws, 's0001')['name'] == "Avery Testwell"


def test_a_failed_call_is_no_reading(tmp_path):
    toy = load_exam(TOY)
    root = tmp_path / 'ws'
    paths = synth.stacks(toy, root)
    ws = Workspace(root, toy, create=True)
    one, _ = intake.add_source(ws, paths[0])
    intake.accept(ws, one.id)
    broken = Runner(ws, model=FakeModel({}))
    assert {o.status for o in identity.read(broken, 's0001')} == {'failed'}
    assert texts(ws, 's0001') == {} and 's0001' in identity.unread(ws)
    identity.read(Runner(ws, model=synth.fake_model(root)), 's0001')
    assert texts(ws, 's0001')['id'] == "90412873"
    ws.close()


def test_a_box_drawn_in_the_app_is_read_on_synthetic_stacks(tmp_path):
    """An exam that declares no identity fields: boxes drawn for every submission are read
    with the scripted model, as declared ones are."""
    from examinator import Exam, Field, Region
    toy = load_exam(TOY)
    root = tmp_path / 'ws'
    paths = synth.stacks(toy, root)
    bare = Exam('bare', pages=toy.pages, print_area=toy.print_area, landmarks=toy.landmarks)
    for qid, q in toy.questions.items():
        bare.questions[qid] = q
    ws = Workspace(root, bare, create=True)
    one, _ = intake.add_source(ws, paths[0])
    intake.accept(ws, one.id)
    assert identity.placed(ws) == () and identity.unread(ws) == []
    overrides.set(ws, '@identity/fields/name/region', [1, 100, 100, 800, 200], author='t')
    assert [f.label for f in identity.placed(ws)] == ['name'] and identity.unread(ws)[0] == 's0001'
    runner = Runner(ws, model=synth.fake_model(root))
    identity.read(runner, 's0001')
    assert texts(ws, 's0001') == {'name': "Avery Testwell"}
    ws.close()


def test_who_a_submission_is_is_read_from_its_pick(written):
    """No stored copy to keep in step: whatever writes or undoes the pick, the submission's
    name, email and student ID are the pick's."""
    ws, runner, model = written
    ws.store.write('s0001', '', identity.WHO, {'name': "Ada", 'id': "1", 'email': "a@x.invalid"}, origin='manual')
    s = ws.store.submission('s0001')
    assert (s.name, s.student, s.email) == ("Ada", "1", "a@x.invalid") and s in ws.store.submissions()
    ws.store.delete('s0001', '', identity.WHO)
    assert ws.store.submission('s0001').name == "" and ws.sync() == 0 and ws.store.submission('s0001').name == ""

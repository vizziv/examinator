"""Who wrote each submission: rosters, the matcher, readings and picks. Synthetic material only."""
from pathlib import Path

import pytest

from examinator import Exam, Identity, Region, load_exam, synth
from examinator.core import identity, intake
from examinator.core.identity import Matcher, distance, name_score, name_tokens, propose, read_roster
from examinator.core.store import Conflict
from examinator.core.workspace import Workspace
from examinator.model.fake import FakeModel

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
                        "line 3: expected a name and an ID", "line 5: 1 is already on line 4"]
    with pytest.raises(ValueError, match="line 1"):
        identity.parse_roster("Ada Lovelace\n")


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
    r = propose({'name': "Ada Lovelace", 'id': "31415926"}, ROSTER)
    assert r['doubt'] == 'agree' and top(r) == 'ada'
    assert r['candidates'][0]['why'] == "ID exact, name exact"


def test_names_ignore_order_case_accents_and_punctuation():
    for written in ("LOVELACE, ada", "lovelace ada.", "Ada  Lovelace"):
        r = propose({'name': written}, ROSTER)
        assert r['doubt'] == 'name' and top(r) == 'ada' and r['candidates'][0]['why'] == "name exact"
    assert top(propose({'name': "Emile Borel"}, ROSTER)) == 'emile'
    assert name_tokens("O'Brien-Smith, Zoë") == ['obrien', 'smith', 'zoe']


def test_an_id_misread_by_a_character_or_two():
    r = propose({'name': "Alan Turing", 'id': "27182819"}, ROSTER)
    assert r['doubt'] == 'agree' and top(r) == 'alan' and r['candidates'][0]['why'] == "ID 1 off, name exact"
    r = propose({'id': "27128818"}, ROSTER)          # two digits swapped: one edit
    assert r['doubt'] == 'id' and top(r) == 'alan' and r['candidates'][0]['why'] == "ID 1 off"
    r = propose({'id': "2718281O"}, ROSTER)          # a wrong last character
    assert top(r) == 'alan'
    r = propose({'id': "2718281B"}, ROSTER)          # a letter B for an eight
    assert top(r) == 'alan' and r['candidates'][0]['why'] == "ID look-alike"
    r = propose({'id': "99182811"}, ROSTER)          # three off: nobody
    assert r['doubt'] == 'nobody' and r['candidates'] == []


def test_ids_of_letters_and_digits():
    roster = [{'key': k, 'name': n, 'id': i} for k, n, i in (
        ('a', "Ada Lovelace", 'al815'), ('b', "Alan Turing", 'amt54'), ('c', "Kim Park", 'kp7'))]
    look = lambda id: propose({'id': id}, roster)
    r = look("AL815")
    assert top(r) == 'a' and r['candidates'][0]['why'] == "ID exact"
    for written in ("a1815", "alb15", "al8l5", "AL81S"):        # look-alikes, either way round
        r = look(written)
        assert r['doubt'] == 'id' and top(r) == 'a' and r['candidates'][0]['why'] == "ID look-alike"
    r = look("amt45")                                           # five characters: one may be misread
    assert top(r) == 'b' and r['candidates'][0]['why'] == "ID 1 off"
    assert look("axt99")['doubt'] == 'nobody'                   # but not two
    assert top(look("kp7")) == 'c' and look("kp1")['doubt'] == 'nobody'     # a short ID: exact or nothing
    r = propose({'name': "Alan Turing", 'id': "al815"}, roster)             # an exact ID outranks a name
    assert r['doubt'] == 'disagree' and top(r) == 'a'


def test_a_misread_name_with_the_id_unread():
    r = propose({'name': "Grase Hoper", 'id': None}, ROSTER)
    assert r['doubt'] == 'name' and top(r) == 'grace' and r['candidates'][0]['why'] == "name close"


def test_id_and_name_point_at_different_people():
    r = propose({'name': "Grace Hopper", 'id': "31415926"}, ROSTER)
    assert r['doubt'] == 'disagree'
    assert [c['key'] for c in r['candidates'][:2]] == ['ada', 'grace']      # an exact ID ranks first
    assert r['candidates'][0]['why'] == "ID exact"


def test_nobody_close_and_nothing_read():
    r = propose({'name': "Finley Dummond", 'id': "95830142"}, ROSTER)
    assert r['doubt'] == 'nobody' and r['candidates'] == []
    assert propose({'name': None, 'id': ''}, ROSTER) == {'doubt': 'unread', 'candidates': []}


def test_names_too_alike_point_at_nobody_but_all_are_candidates():
    r = propose({'name': "Kim Park"}, ROSTER)                           # two people of one name
    assert r['doubt'] == 'nobody'
    assert [c['key'] for c in r['candidates'][:2]] == ['kim1', 'kim2']
    r = propose({'name': "Kim Park", 'id': "44444444"}, ROSTER)         # the ID settles it
    assert r['doubt'] == 'agree' and top(r) == 'kim2'
    r = propose({'name': "John Smith"}, ROSTER)                         # one letter apart is apart enough
    assert r['doubt'] == 'name' and [c['key'] for c in r['candidates'][:2]] == ['john', 'jon']


def test_someone_already_picked_is_flagged_not_left_out():
    r = propose({'name': "Ada Lovelace", 'id': "31415926"}, ROSTER, taken={'ada': 's0007'})
    assert top(r) == 'ada' and r['candidates'][0]['taken'] == 's0007'


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
    assert propose({'id': "13"}, roster)['candidates'] == []
    assert top(propose({'id': "1235"}, roster)) == 'b'


def test_a_prepared_roster_gives_the_same_answers():
    m = Matcher(ROSTER)
    for read in ({'name': "Ada Lovelace"}, {'id': "16180338"}, {'name': "x", 'id': "y"}):
        assert m.propose(read) == propose(read, ROSTER)


def test_by_roster_joins_boxes_that_share_a_field():
    fields = [Identity('first', Region(1, 0, 0, 1, 1), "", roster='name'),
              Identity('last', Region(1, 0, 0, 1, 1), "", roster='name'),
              Identity('id', Region(1, 0, 0, 1, 1), "", roster='id'),
              Identity('section', Region(1, 0, 0, 1, 1), "")]
    got = identity.by_roster(fields, {'first': "Ada", 'last': "Lovelace", 'id': "3141 5926", 'section': "B"})
    assert got == {'name': "Ada Lovelace", 'id': "3141 5926"}
    assert identity.by_roster(fields, {'first': None}) == {'name': None, 'id': None}


def test_identity_declarations_are_checked():
    with pytest.raises(ValueError, match="roster"):
        Exam('x', pages=1, identity=[Identity('name', Region(1, 0, 0, 9, 9), "", roster='nickname')])
    with pytest.raises(ValueError, match="duplicate"):
        Exam('x', pages=1, identity=[Identity('a', Region(1, 0, 0, 9, 9), ""), Identity('a', Region(1, 0, 0, 9, 9), "")])


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
    art = intake.edit(ws, one.id, 'turn', k=4, expect_rev=art.rev, author='t')
    intake.accept(ws, one.id)
    two, _ = intake.add_source(ws, paths[1])
    art = intake.plan(ws, two.id)
    for _ in range(2):
        art = intake.edit(ws, two.id, 'turn_source', expect_rev=art.rev, author='t')
    intake.reguess(ws, two.id, model=model)
    intake.accept(ws, two.id)
    identity.set_roster(ws, (root / 'roster.csv').read_text())
    yield ws, model
    ws.close()


def test_reading_is_memoised_and_stored_with_history(written):
    ws, model = written
    assert sorted(ws.store.made()) == [f's{n:04d}' for n in range(1, 11)]
    a = identity.read(ws, 's0001', model)
    assert a.body['fields'] == {'name': "Avery Testwell", 'id': "90412873"} and a.origin == 'auto'
    calls = len(model.calls)
    assert identity.read(ws, 's0001', model).rev == a.rev and len(model.calls) == calls   # free, unchanged
    assert all(c.prompt.count("Read the HANDWRITTEN") == 1 for c in model.calls[-2:])
    assert "Avery" not in " ".join(c.prompt for c in model.calls)            # no roster in any prompt
    b = identity.read(ws, 's0001', model, fresh=True)
    assert len(model.calls) == calls + 2 and b.rev == a.rev                  # asked again, same answer
    # the pick is a different row; reading never writes it
    assert ws.store.get('s0001', '', identity.WHO) is None
    ws.store.write('s0001', '', identity.READ, {'fields': {}, 'basis': {}}, origin='auto')
    assert identity.read(ws, 's0001', model).rev != a.rev
    assert ws.store.history('s0001', '', identity.READ)


def test_the_awkward_cases(written):
    ws, model = written
    for sid in sorted(ws.store.made()):
        identity.read(ws, sid, model)
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
    ws, model = written
    for sid in ('s0004', 's0007'):
        identity.read(ws, sid, model)
    gray = next(p for p in identity.people(ws) if p['name'] == "Gray Placeholt")
    body = identity.set_who(ws, 's0007', gray['key'], author='t', expect_rev=None)
    assert body['id'] == "96174385" and ws.store.submission('s0007').student == "96174385"
    with pytest.raises(Conflict):                   # someone else picked meanwhile
        identity.set_who(ws, 's0007', gray['key'], author='t', expect_rev=None)
    rows = {r['sub']: r for r in identity.overview(ws)['rows']}
    assert rows['s0004']['candidates'][0]['taken'] == 's0007'               # flagged, still first
    identity.set_who(ws, 's0004', identity.label(gray), author='t')
    rows = {r['sub']: r for r in identity.overview(ws)['rows']}
    assert rows['s0004']['who']['twice'] and rows['s0007']['who']['twice']
    view = identity.overview(ws)
    assert view['left'] == 8 and [r['sub'] for r in view['rows']][-2:] == ['s0004', 's0007']
    assert "Gray Placeholt" not in {p['name'] for p in view['nobody']}
    # someone not on the roster: free text; clearing
    identity.set_who(ws, 's0006', "Finley Dummond", author='t')
    assert ws.store.submission('s0006').name == "Finley Dummond" and ws.store.submission('s0006').student == ''
    identity.set_who(ws, 's0006', "", author='t')
    assert ws.store.submission('s0006').name == ""
    # "Name, ID" as the page starts typing from what was read: the roster person when both
    # match, else kept as typed with its ID
    assert identity.set_who(ws, 's0006', "gray placeholt, 96174385", author='t')['key'] == gray['key']
    body = identity.set_who(ws, 's0006', "Finley Dummond, 95830142", author='t')
    assert (body['name'], body['id']) == ("Finley Dummond", "95830142")
    assert identity.set_who(ws, 's0006', "Dummond, Finley", author='t')['name'] == "Dummond, Finley"
    identity.set_who(ws, 's0006', "", author='t')
    assert ws.sync_roster() == 10 and ws.store.submission('s0007').student == "96174385"


def test_rewriting_a_submission_drops_what_was_read_but_keeps_the_pick(written):
    ws, model = written
    identity.read(ws, 's0001', model)
    identity.set_who(ws, 's0001', identity.people(ws)[0]['key'], author='t')
    one = ws.store.made()['s0001'].source
    intake.edit(ws, one, 'drop', sheet=3, expect_rev=intake.plan(ws, one).rev, author='t')
    assert intake.accept(ws, one)['rewritten'] == ['s0001']               # names are not grading work
    assert ws.store.get('s0001', '', identity.READ) is None
    assert ws.store.submission('s0001').name == "Avery Testwell"


def test_a_failed_call_is_no_reading_and_not_remembered(tmp_path):
    toy = load_exam(TOY)
    root = tmp_path / 'ws'
    paths = synth.stacks(toy, root)
    ws = Workspace(root, toy, create=True)
    one, _ = intake.add_source(ws, paths[0])
    intake.accept(ws, one.id)
    a = identity.read(ws, 's0001', FakeModel({}))
    assert a.body['fields'] == {'name': None, 'id': None} and a.body['failed'] == ['name', 'id']
    assert identity.read(ws, 's0001', synth.fake_model(root)).body['fields']['id'] == "90412873"
    ws.close()


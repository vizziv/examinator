"""Synthetic fixtures and the authoring commands: mistakes get one plain line, no partial workspace."""
from pathlib import Path

import pytest

from examinator import cli, load_exam, synth
from examinator.synth import Case

TOY = Path(__file__).resolve().parents[1] / 'exams' / 'toy_exam'


@pytest.fixture(scope='module')
def toy():
    return load_exam(TOY)


@pytest.mark.parametrize('case, said', [
    (Case('c', answers={'sum': {'1': '2'}}), "case 'c': answers: no question 'sum'"),
    (Case('c', answers={'parity': {'C': 'even'}}), "case 'c': answers['parity']: no field 'C'"),
    (Case('c', answers={'parity': 'even'}), "case 'c': answers['parity']: expected {label"),
    (Case('c', variants={'parity': 'red'}), "case 'c': variants['parity']: 'red' is not one of []"),
    (Case('c', variants={'areas': 'pink'}), "case 'c': variants['areas']: 'pink'"),
    (Case('c', variants={'sum': 'red'}), "case 'c': variants: no question 'sum'"),
    (Case('c', expect={'areas.1': 3}), "case 'c': expect: no grid column 'areas.1'"),
    (Case('c', readings={'side': None}), "case 'c': readings: no cue 'side'")])
def test_a_case_that_does_not_fit_names_itself_and_the_key(toy, tmp_path, case, said):
    with pytest.raises(synth.BadCase) as e:
        synth.generate(toy, tmp_path / 'ws', [case])
    assert said in str(e.value)
    assert not (tmp_path / 'ws').exists()           # checked before anything is written


def test_cases_with_the_same_name(toy, tmp_path):
    with pytest.raises(synth.BadCase, match="case 'c': two cases"):
        synth.check_cases(toy, [Case('c'), Case('c')])


def test_a_failed_synth_leaves_nothing(toy, tmp_path, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("drawing failed")
    monkeypatch.setattr(synth, '_draw_answers', broken)
    with pytest.raises(RuntimeError):
        synth.generate(toy, tmp_path / 'ws', synth.load_cases(toy))
    assert not (tmp_path / 'ws').exists()
    (tmp_path / 'empty').mkdir()
    with pytest.raises(RuntimeError):
        synth.stacks(toy, tmp_path / 'empty')
    assert list((tmp_path / 'empty').iterdir()) == []      # an empty directory given is left empty


def test_synth_wants_a_new_directory(toy, tmp_path, capsys):
    (tmp_path / 'ws').mkdir()
    (tmp_path / 'ws' / 'notes.txt').write_text("mine")
    for argv in (['synth', str(TOY), str(tmp_path / 'ws')], ['synth', str(TOY), str(tmp_path / 'ws'), '--stacks'],
                 ['selftest', str(TOY), str(tmp_path / 'ws')]):
        assert cli.main(argv) == 2
        err = capsys.readouterr().err
        assert "already exists and is not empty: give a new directory" in err and err.count("\n") == 1
    assert [p.name for p in (tmp_path / 'ws').iterdir()] == ['notes.txt']


def test_a_bad_case_from_the_cli_is_one_line(toy, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(synth, 'load_cases', lambda exam: [Case('c', answers={'sum': {}})])
    assert cli.main(['synth', str(TOY), str(tmp_path / 'ws')]) == 2
    assert capsys.readouterr().err == "examinator synth: case 'c': answers: no question 'sum' " \
                                      "(questions: ['areas', 'parity'])\n"


def test_fake_needs_a_workspace_made_by_synth(toy, tmp_path, capsys):
    root = synth.generate(toy, tmp_path / 'ws', synth.load_cases(toy))
    (root / synth.SCRIPT).unlink()
    for cmd in ('run', 'status'):
        assert cli.main([cmd, str(TOY), '-w', str(root), '--fake']) == 2
        err = capsys.readouterr().err
        assert "has no scripted model" in err and "not made by `synth`" in err and err.count("\n") == 1
    assert cli.main(['status', str(TOY), '-w', str(tmp_path / 'nowhere'), '--fake']) == 2
    assert "no pdfs/ folder" in capsys.readouterr().err

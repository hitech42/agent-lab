import io

import pytest
from wordcount import main


def run(argv, text):
    out = io.StringIO()
    status = main(argv, stdin=io.StringIO(text), stdout=out)
    return status, out.getvalue()


def test_top_orders_by_count_then_alphabetically():
    text = "pear apple apple pear zebra mango mango kiwi\n"
    assert run(["--top", "4"], text) == (0, "apple 2\nmango 2\npear 2\nkiwi 1\n")


def test_top_is_case_insensitive_and_lowercases_output():
    assert run(["--top", "2"], "The the THE cat Cat dog\n") == (0, "the 3\ncat 2\n")


def test_top_larger_than_number_of_unique_words_prints_all():
    assert run(["--top", "10"], "b a b\n") == (0, "b 2\na 1\n")


def test_top_ties_across_many_words_are_alphabetical():
    assert run(["--top", "3"], "d c b a\n") == (0, "a 1\nb 1\nc 1\n")


def test_top_keeps_apostrophes_in_words():
    assert run(["--top", "1"], "Don't don't DON'T stop\n") == (0, "don't 3\n")


def test_top_with_files(tmp_path):
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    first.write_text("x y x\n")
    second.write_text("y y z\n")
    out = io.StringIO()
    assert main(["--top", "2", str(first), str(second)], stdout=out) == 0
    assert out.getvalue() == "y 3\nx 2\n"


def test_top_replaces_the_summary_line():
    status, output = run(["--top", "1"], "one\n")
    assert status == 0
    assert output == "one 1\n"


def test_top_on_empty_input_prints_nothing():
    assert run(["--top", "3"], "") == (0, "")


@pytest.mark.parametrize("bad", ["0", "-1", "-5", "abc", "1.5", "", "2x"])
def test_invalid_top_exits_with_status_2(bad):
    with pytest.raises(SystemExit) as excinfo:
        run(["--top", bad], "a b c\n")
    assert excinfo.value.code == 2


def test_top_without_a_value_exits_with_status_2():
    with pytest.raises(SystemExit) as excinfo:
        run(["--top"], "a b c\n")
    assert excinfo.value.code == 2


def test_default_behaviour_is_unchanged():
    assert run([], "one two\nthree\n") == (0, "2 3 14\n")
    assert run([], "") == (0, "0 0 0\n")
    assert run([], "Hello, hello!\n") == (0, "1 2 14\n")

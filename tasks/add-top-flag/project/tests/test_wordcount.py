import io

from wordcount import format_summary, longest_word, main, summarize, tokenize


def run(argv, text):
    out = io.StringIO()
    status = main(argv, stdin=io.StringIO(text), stdout=out)
    return status, out.getvalue()


def test_tokenize_keeps_case_and_apostrophes():
    assert tokenize("Don't stop, Stop!") == ["Don't", "stop", "Stop"]


def test_summarize():
    assert summarize("a b\nc\n") == (2, 3, 6)


def test_format_summary():
    assert format_summary(1, 2, 3) == "1 2 3"


def test_longest_word():
    assert longest_word("a bbb cc dd") == "bbb"
    assert longest_word("") == ""


def test_default_summary_from_stdin():
    assert run([], "one two\nthree\n") == (0, "2 3 14\n")


def test_summary_from_file(tmp_path):
    path = tmp_path / "in.txt"
    path.write_text("x y z\n")
    out = io.StringIO()
    assert main([str(path)], stdout=out) == 0
    assert out.getvalue() == "1 3 6\n"


def test_top_one_prints_most_frequent_word():
    assert run(["--top", "1"], "a b b c\n") == (0, "b 2\n")

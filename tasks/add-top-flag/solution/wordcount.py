"""A tiny ``wc``-style command line tool.

Usage: ``python wordcount.py [FILE ...]`` prints ``<lines> <words> <chars>`` for the
given files, or for standard input when no files are given.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from collections.abc import Sequence
from typing import TextIO

WORD_RE = re.compile(r"[A-Za-z0-9']+")


def tokenize(text: str) -> list[str]:
    """Split text into words (letters, digits and apostrophes), keeping their case."""
    return WORD_RE.findall(text)


def read_file(path: str) -> str:
    """Read one UTF-8 text file."""
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def read_input(paths: Sequence[str], stdin: TextIO) -> str:
    """Concatenate the contents of ``paths``, or read ``stdin`` when there are none."""
    if not paths:
        return stdin.read()
    return "".join(read_file(path) for path in paths)


def summarize(text: str) -> tuple[int, int, int]:
    """Return ``(lines, words, chars)`` for ``text``."""
    return len(text.splitlines()), len(tokenize(text)), len(text)


def format_summary(lines: int, words: int, chars: int) -> str:
    """The default one-line report: ``<lines> <words> <chars>``."""
    return f"{lines} {words} {chars}"


def longest_word(text: str) -> str:
    """The longest word in ``text`` (the first one if several tie), or ``""``."""
    return max(tokenize(text), key=len, default="")


def top_words(text: str, n: int) -> list[tuple[str, int]]:
    """The ``n`` most frequent words (case-insensitive), ties in alphabetical order."""
    counts = Counter(word.lower() for word in tokenize(text))
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:n]


def positive_int(value: str) -> int:
    """argparse type: a strictly positive integer."""
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer") from None
    if number < 1:
        raise argparse.ArgumentTypeError(f"{value!r} is not a positive integer")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wordcount", description="Count lines, words and chars.")
    parser.add_argument("files", nargs="*", help="files to read (default: standard input)")
    parser.add_argument(
        "--top",
        type=positive_int,
        metavar="N",
        help="print the N most frequent words instead of the summary",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> int:
    """Run the tool and return the process exit status."""
    args = build_parser().parse_args(argv)
    text = read_input(args.files, stdin if stdin is not None else sys.stdin)
    out = stdout if stdout is not None else sys.stdout
    if args.top is not None:
        for word, count in top_words(text, args.top):
            print(f"{word} {count}", file=out)
        return 0
    lines, words, chars = summarize(text)
    print(format_summary(lines, words, chars), file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

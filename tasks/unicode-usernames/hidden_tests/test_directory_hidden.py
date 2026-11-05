import pytest
from directory import DuplicateUserError, UserDirectory, canonical


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("  Alice ", "alice"),
        ("Straße", "strasse"),
        ("STRASSE", "strasse"),
        ("ﬁsh", "fish"),
        ("Ａlice", "alice"),
        ("é", "é"),
        ("É", "é"),
        ("É", "é"),
        ("ǰ", "ǰ"),
        ("J̌", "ǰ"),
    ],
)
def test_the_key_is_nfkc_then_casefold_then_nfkc(name, key):
    assert canonical(name) == key


@pytest.mark.parametrize("blank", ["", " ", "\t\n"])
def test_an_empty_key_is_an_error(blank):
    with pytest.raises(ValueError):
        canonical(blank)
    with pytest.raises(ValueError):
        UserDirectory().add(blank)


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("Straße", "STRASSE"),
        ("café", "café"),
        ("ﬁsh", "FISH"),
        ("Ａlice", "alice"),
        ("Émile", "ÉMILE"),
    ],
)
def test_names_that_read_the_same_are_one_user(first, second):
    users = UserDirectory()
    users.add(first)
    with pytest.raises(DuplicateUserError):
        users.add(second)
    assert users.find(second) == first
    assert users.remove(second) is True
    assert users.find(first) is None


def test_the_display_name_is_kept_as_given_but_stripped():
    users = UserDirectory()
    assert users.add("  Straße ") == "Straße"
    assert users.find("STRASSE") == "Straße"
    assert users.find("  strasse\t") == "Straße"


def test_blank_names_match_nobody_and_never_raise():
    users = UserDirectory()
    users.add("alice")
    assert users.find("") is None and users.find("   ") is None
    assert users.remove("") is False and users.remove(" \t") is False
    assert users.names() == ["alice"]


def test_names_are_sorted_by_key():
    users = UserDirectory()
    for name in ["Bob", "alice", "Émile", "Zed"]:
        users.add(name)
    assert users.names() == ["alice", "Bob", "Zed", "Émile"]  # "z" sorts before "é"


def test_rename_to_another_spelling_of_the_same_name_is_allowed():
    users = UserDirectory()
    users.add("alice")
    assert users.rename("alice", "Alice") == "Alice"
    assert users.find("ALICE") == "Alice" and users.names() == ["Alice"]
    users.add("café")
    assert users.rename("café", "CAFÉ") == "CAFÉ"
    assert users.names() == ["Alice", "CAFÉ"]


def test_a_failed_rename_changes_nothing():
    users = UserDirectory()
    users.add("alice")
    users.add("Bob")
    with pytest.raises(DuplicateUserError):
        users.rename("alice", " BOB ")
    with pytest.raises(KeyError):
        users.rename("nobody", "carol")
    assert users.names() == ["alice", "Bob"]


def test_search_matches_the_start_of_the_key():
    users = UserDirectory()
    for name in ["alice", "Alfred", "Bob", "Émile"]:
        users.add(name)
    assert users.search("AL") == ["Alfred", "alice"]
    assert users.search("é") == ["Émile"]
    assert users.search("é") == ["Émile"]
    assert users.search("zzz") == []


@pytest.mark.parametrize("blank", ["", "  "])
def test_a_blank_prefix_matches_everyone(blank):
    users = UserDirectory()
    for name in ["bob", "Alice"]:
        users.add(name)
    assert users.search(blank) == ["Alice", "bob"]

import pytest
from directory import DuplicateUserError, UserDirectory, canonical


def test_canonical_strips_and_ignores_case():
    assert canonical("  Alice ") == "alice"


def test_canonical_rejects_empty_names():
    with pytest.raises(ValueError):
        canonical("   ")


def test_add_returns_the_display_name():
    users = UserDirectory()
    assert users.add("  Alice ") == "Alice"


def test_names_that_differ_only_in_case_are_the_same_user():
    users = UserDirectory()
    users.add("alice")
    with pytest.raises(DuplicateUserError):
        users.add("ALICE")


def test_find_and_remove():
    users = UserDirectory()
    users.add("Alice")
    assert users.find("alice") == "Alice"
    assert users.find("bob") is None
    assert users.remove("ALICE") is True
    assert users.remove("alice") is False
    assert users.find("alice") is None


def test_rename_to_a_new_name():
    users = UserDirectory()
    users.add("alice")
    users.add("bob")
    assert users.rename("alice", "carol") == "carol"
    assert users.find("alice") is None and users.find("carol") == "carol"
    with pytest.raises(KeyError):
        users.rename("nobody", "dave")
    with pytest.raises(DuplicateUserError):
        users.rename("carol", "BOB")


def test_names_and_search():
    users = UserDirectory()
    for name in ["bob", "alice", "alfred"]:
        users.add(name)
    assert users.names() == ["alfred", "alice", "bob"]
    assert users.search("al") == ["alfred", "alice"]

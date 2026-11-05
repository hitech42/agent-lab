"""A directory of user names that matches names the way people read them."""

import unicodedata


class DuplicateUserError(ValueError):
    """The name matches a user who is already in the directory."""


def canonical(name: str) -> str:
    """The matching key of a user name: two names are the same user iff their keys are equal.

    The key is the name without leading and trailing whitespace, normalized to Unicode
    NFKC, then case-folded with ``str.casefold`` (not ``str.lower``), then normalized to
    NFKC again, because case folding can leave text denormalized. So ``"Straße"`` and
    ``"STRASSE"`` are the same user; so are ``"é"`` written as one code point and as
    ``"e"`` plus a combining accent, ``"ﬁsh"`` and ``"fish"``, and ``"Ａlice"`` (with a
    full-width A) and ``"alice"``. Raises ``ValueError`` if the key is empty.
    """
    key = unicodedata.normalize("NFKC", unicodedata.normalize("NFKC", name.strip()).casefold())
    if not key:
        raise ValueError("a user name cannot be empty")
    return key


class UserDirectory:
    """Users keyed by ``canonical``; each is shown as its display name.

    The display name is the name as it was given, without leading and trailing whitespace.
    """

    def __init__(self) -> None:
        self._users: dict[str, str] = {}

    def add(self, name: str) -> str:
        """Add a user and return the display name.

        Raises ``DuplicateUserError`` if the name matches a user already in the directory,
        and ``ValueError`` if it is empty.
        """
        key = canonical(name)
        if key in self._users:
            raise DuplicateUserError(name)
        self._users[key] = name.strip()
        return self._users[key]

    def find(self, name: str) -> str | None:
        """The display name of the user the name matches, or ``None``.

        Never raises: an empty or blank name matches nobody.
        """
        try:
            return self._users.get(canonical(name))
        except ValueError:
            return None

    def remove(self, name: str) -> bool:
        """Remove the user the name matches; ``True`` if there was one.

        Never raises: an empty or blank name matches nobody and returns ``False``.
        """
        try:
            return self._users.pop(canonical(name), None) is not None
        except ValueError:
            return False

    def rename(self, old: str, new: str) -> str:
        """Give a user a new name and return the new display name.

        Raises ``KeyError`` if ``old`` matches nobody and ``DuplicateUserError`` if ``new``
        matches a different user. A user may be renamed to another spelling of the same
        name (``"alice"`` to ``"Alice"``): that is allowed and changes the display name.
        When it raises, nothing changes.
        """
        old_key = canonical(old)
        if old_key not in self._users:
            raise KeyError(old)
        new_key = canonical(new)
        if new_key != old_key and new_key in self._users:
            raise DuplicateUserError(new)
        del self._users[old_key]
        self._users[new_key] = new.strip()
        return self._users[new_key]

    def names(self) -> list[str]:
        """The display names of all users, sorted by key (plain code point order)."""
        return [name for _, name in sorted(self._users.items())]

    def search(self, prefix: str) -> list[str]:
        """The display names of the users whose key starts with the key of ``prefix``.

        Sorted like ``names``. An empty or blank prefix matches every user.
        """
        try:
            wanted = canonical(prefix)
        except ValueError:
            wanted = ""
        return [name for key, name in sorted(self._users.items()) if key.startswith(wanted)]

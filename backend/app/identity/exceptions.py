"""Identity domain errors."""

from __future__ import annotations


class AuthError(Exception):
    """Base auth failure."""


class InvalidCredentials(AuthError):
    pass


class EmailTaken(AuthError):
    pass


class UserNotFound(AuthError):
    pass


class AmbiguousLogin(AuthError):
    """Same email exists in multiple orgs — caller must disambiguate."""

    def __init__(self, org_choices: list[dict[str, object]]) -> None:
        self.org_choices = org_choices
        super().__init__("Email exists in multiple organizations")


class Forbidden(AuthError):
    pass


class TargetUserNotFound(AuthError):
    pass

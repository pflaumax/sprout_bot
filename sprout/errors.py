"""Reading the SDK's request errors without importing the SDK, so the bot loop
stays testable with a fake."""

from __future__ import annotations

# What a PDS answers when the session or the password is no good. Retrying
# does not fix any of these; logging in again might.
AUTH_ERRORS = {"ExpiredToken", "InvalidToken", "AuthenticationRequired", "AuthMissing"}


def status(e: BaseException) -> int | None:
    return getattr(getattr(e, "response", None), "status_code", None)


def xrpc_error(e: BaseException) -> str | None:
    """The `error` field of an XRPC error body, e.g. "RecordNotFound"."""
    content = getattr(getattr(e, "response", None), "content", None)
    if isinstance(content, dict):
        return content.get("error")
    return getattr(content, "error", None)


def is_auth_error(e: BaseException) -> bool:
    return status(e) == 401 or xrpc_error(e) in AUTH_ERRORS

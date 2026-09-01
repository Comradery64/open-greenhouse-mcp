"""User-relayable error messages.

End users are recruiters, not engineers. When a Greenhouse call fails they need
two things: a plain-English sentence telling them whether they can fix it
themselves, and a short support code they can paste into a message to whoever
maintains this connector. Everything an engineer needs is carried alongside under
`technical_detail`, and the same record is written to the server log.

Scope limit worth knowing: this only covers failures the *server observes* — i.e.
errors returned by the Greenhouse API. If the Claude client or API rejects a call
before or after the server runs (an oversized tool result, for example), this code
never executes and can produce no message. Oversized results are handled by
prevention instead, in `shaping.py`.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

# Plain-English message per status, plus whether the user can act on it alone.
# `fixable_by_user` drives whether we tell them to contact their admin.
_MESSAGES: dict[int, tuple[str, bool]] = {
    400: (
        "Greenhouse rejected the details of this request — usually a filter value "
        "it doesn't recognise. Your access is fine.",
        False,
    ),
    401: (
        "Greenhouse would not accept this connector's credentials. The client ID "
        "or secret may have been revoked, expired, or typed incorrectly. This is "
        "not something you can fix from here.",
        False,
    ),
    403: (
        "This connector's credentials do not have permission to view that in "
        "Greenhouse. Harvest v3 grants access per endpoint, so this scope needs "
        "to be added before it will work.",
        False,
    ),
    404: (
        "Greenhouse has no record matching that. It may have been deleted, or the "
        "name or ID may not be quite right — worth double-checking the spelling.",
        True,
    ),
    409: (
        "Greenhouse refused this because it conflicts with the current state of "
        "the record — often it has already been changed by someone else. "
        "Re-checking the record and trying again usually resolves it.",
        True,
    ),
    422: (
        "Greenhouse understood the request but rejected the values in it. "
        "Something in the details isn't valid for this record.",
        True,
    ),
    429: (
        "Greenhouse is temporarily limiting how quickly we can request data, "
        "because a lot has been requested at once. Waiting a minute and asking "
        "again almost always works.",
        True,
    ),
}

_SERVER_MESSAGE = (
    "Greenhouse's own servers are having trouble right now. This is not a problem "
    "with your data, your key, or anything you did — trying again in a few minutes "
    "usually works."
)
_NETWORK_MESSAGE = (
    "Could not reach Greenhouse at all. This is usually a network or connection "
    "problem rather than anything to do with your request."
)
_UNKNOWN_MESSAGE = (
    "Greenhouse returned an error we don't have a specific explanation for. "
    "Your access is most likely fine."
)

_RELAY_INSTRUCTION = (
    "TELL THE USER THIS FAILED. Show them `user_message` and `support_code` "
    "exactly as written — copy the support code character for character, do not "
    "paraphrase it or make one up. Then tell them to send that support code to "
    "whoever set up this Greenhouse connector. Do not retry this call more than "
    "once, and do not silently substitute a different tool or invent data."
)


def _endpoint_of(url: str | None) -> str:
    """Reduce a full URL to a stable endpoint label, with IDs masked."""
    if not url:
        return "unknown"
    path = re.sub(r"^https?://[^/]+", "", str(url).split("?", 1)[0])
    # Harvest is on v3; the Job Board and Ingestion APIs are still v1.
    path = re.sub(r"/v[13](/partner)?/", "/", path, count=1)
    path = re.sub(r"/\d+", "/{id}", path)
    return path or "unknown"


def support_code(status_code: int, endpoint: str, when: datetime) -> str:
    """Build a short code the user can read aloud or paste into a message.

    Shape: GH<status>-<MMDD>-<HHMM>-<XXXX>, e.g. GH403-0730-1421-A7F3. The final
    group is a hash of the endpoint, so the same failure on the same call always
    produces the same suffix — which makes repeat reports easy to group — while the
    timestamp still pins each report to a specific log line.
    """
    digest = hashlib.sha256(f"{status_code}:{endpoint}".encode()).hexdigest()[:4].upper()
    return f"GH{status_code}-{when:%m%d}-{when:%H%M}-{digest}"


def build_error(
    status_code: int,
    detail: Any = None,
    url: str | None = None,
    message_override: str | None = None,
    fixable_override: bool | None = None,
) -> dict[str, Any]:
    """Build the structured, relayable error payload returned to the model."""
    if message_override is not None:
        message, fixable = message_override, False
    elif status_code in _MESSAGES:
        message, fixable = _MESSAGES[status_code]
    elif 500 <= status_code < 600:
        message, fixable = _SERVER_MESSAGE, True
    elif status_code == 0:
        message, fixable = _NETWORK_MESSAGE, True
    else:
        message, fixable = _UNKNOWN_MESSAGE, False
    if fixable_override is not None:
        fixable = fixable_override

    when = datetime.now(timezone.utc)
    endpoint = _endpoint_of(url)
    code = support_code(status_code, endpoint, when)

    if not fixable:
        message = (
            f"{message} Please pass the support code below to whoever set up this "
            f"connector — they will be able to see exactly what went wrong."
        )

    payload: dict[str, Any] = {
        # `error` and `status_code` are the keys client._is_error and
        # shaping._shape_result key off — do not rename them.
        "error": message,
        "status_code": status_code,
        "user_message": message,
        "support_code": code,
        "occurred_at_utc": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "greenhouse_endpoint": endpoint,
        "user_can_resolve": fixable,
        "action_for_claude": _RELAY_INSTRUCTION,
    }
    if detail not in (None, {}, ""):
        payload["technical_detail"] = detail

    _log_error(payload)
    return payload


CONFIG_MESSAGE = (
    "This Greenhouse connector has not been given credentials yet, so it cannot "
    "look anything up. Nothing is wrong with your request. Someone with admin "
    "access needs to add a Greenhouse client ID and secret to the connector's "
    "settings."
)
INTERNAL_MESSAGE = (
    "Something went wrong inside the Greenhouse connector itself — this is a bug "
    "on our side, not a mistake you made, and retrying is unlikely to help."
)


def config_error(detail: Any, url: str | None = None) -> dict[str, Any]:
    """Error for a connector that has no usable credentials."""
    return build_error(0, detail, url, message_override=CONFIG_MESSAGE)


def internal_error(detail: Any, url: str | None = None) -> dict[str, Any]:
    """Error for an unexpected exception inside a tool."""
    return build_error(0, detail, url, message_override=INTERNAL_MESSAGE)


def _log_error(payload: dict[str, Any]) -> None:
    """Mirror the error to the server log and the always-on diagnostics file."""
    try:
        from greenhouse_mcp.logging import logger

        logger.error(
            "greenhouse_api_error",
            support_code=payload["support_code"],
            status=payload["status_code"],
            endpoint=payload["greenhouse_endpoint"],
            detail=payload.get("technical_detail"),
        )
    except Exception:  # pragma: no cover — logging must never break a tool call
        pass

    # Separate from the logger above: this one is unconditional, so the failure is
    # captured even at the default "warning" level with stderr going nowhere useful.
    from greenhouse_mcp.diagnostics import record

    record(
        "api_error",
        support_code=payload["support_code"],
        status=payload["status_code"],
        endpoint=payload["greenhouse_endpoint"],
        detail=payload.get("technical_detail"),
    )

"""
Capability Broker — README gap-list item 9, "the part that actually does
something."

Every other module in this codebase, in either mode, has always stopped one
step short of the world: `proposed_tool_call` has always been
classification-and-audit metadata, never something that actually happened
(see llm/aldric_reply.py and llm/governed_reply.py's own docstrings —
"nothing this module does with that tier decision changes: it is still
audit metadata, never an execution"). `core.pa_action_kernel.classify_tier()`
decides WHETHER an action may run; this module is the only place in the
codebase where an action ACTUALLY runs. That split is deliberate and
load-bearing (CLAUDE.md Section 1): nothing here decides a tier, and the two
callers wiring this module in — `aldric_chat.py` for ALDRIC Mode's Tier A/B
path, and (once built) Governance Stack Mode's post-adjudication Tier C path
— must never call these functions before a `TierDecision` or a cleared
`AdjudicationRecord` already says the specific action is allowed to run.

Real actions today: Gmail (send / create a draft) and Google Calendar
(create / update / delete an event) — chosen because they're the two
connectors the operator already has a Google account for, needing no custom
backend of ALDRIC's own (the "path 3" direction: govern real, existing
tools rather than build a bespoke phone/accessibility-tree pipeline first).
Each function here is a thin wrapper around the official Google API client,
kept honest on purpose: no retry-hiding, no silent partial success, no
swallowed exceptions. A failed call raises `CapabilityBrokerError` and the
caller is responsible for logging that failure to the digest, the same as
any other event — this module does not decide what a failure means for the
operator.

Every public function takes an already-built `service` object as an
optional keyword so tests can inject a fake one instead of touching the
network or a real Google account — see tests/test_capability_broker.py.
Nothing in this module ever decides whether it should run; it only knows
how to run once told to.
"""
from __future__ import annotations

import base64
import os
from email.mime.text import MIMEText
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Gmail: send + compose drafts only. Calendar: full event management. Kept
# to the minimum scope each real function below actually needs — this
# credential should never be ABLE to do more than these named jobs, not just
# be trusted not to (the same "don't rely on trust where a structural limit
# is available" instinct as PERMANENT_TIER_C_CATEGORIES being unreachable
# from any mutation path).
SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar",
]

DEFAULT_CLIENT_SECRET_PATH = "client_secret.json"
DEFAULT_TOKEN_PATH = "token.json"


class CapabilityBrokerError(RuntimeError):
    """Raised when a real action could not be completed — missing
    credentials, a rejected API call, an unrecognized tool name. Never
    caught and silently swallowed inside this module; the caller (the
    governance layer that authorized the call in the first place) decides
    what a failure means for the digest and the operator."""


def get_credentials(
    client_secret_path: str = DEFAULT_CLIENT_SECRET_PATH,
    token_path: str = DEFAULT_TOKEN_PATH,
) -> Credentials:
    """The only function in this codebase that ever touches the operator's
    actual Google login. Loads a cached token if one is still valid,
    refreshes it silently if it just expired, or — only if neither works —
    runs the one-time interactive consent flow (opens a browser window) and
    caches the result for next time. Every other function below takes a
    `service` object already built from credentials this function
    produced; none of them re-derive or re-request access on their own."""
    creds: Optional[Credentials] = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(client_secret_path):
                raise CapabilityBrokerError(
                    f"No cached Google credentials and no '{client_secret_path}' to start the "
                    "one-time setup from. See README, 'Giving ALDRIC real hands'."
                )
            flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "w") as token_file:
            token_file.write(creds.to_json())
    return creds


def _gmail_service(service=None):
    return service if service is not None else build("gmail", "v1", credentials=get_credentials())


def _calendar_service(service=None):
    return service if service is not None else build("calendar", "v3", credentials=get_credentials())


def _build_email_message(to: str, subject: str, body: str) -> dict:
    message = MIMEText(body)
    message["to"] = to
    message["subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    return {"raw": raw}


def send_email(to: str, subject: str, body: str, service=None) -> dict:
    """TOOL_REGISTRY: `send_email`, Tier B (external-facing, irreversible
    once sent). Must only ever be called after classify_tier() has already
    cleared this specific action — see module docstring."""
    try:
        gmail = _gmail_service(service)
        result = gmail.users().messages().send(
            userId="me", body=_build_email_message(to, subject, body)
        ).execute()
    except Exception as exc:  # noqa: BLE001 — re-raised as our own type, not swallowed
        raise CapabilityBrokerError(f"send_email to {to!r} failed: {exc}") from exc
    return {"tool": "send_email", "to": to, "subject": subject, "message_id": result.get("id")}


def create_email_draft(to: str, subject: str, body: str, service=None) -> dict:
    """TOOL_REGISTRY: `create_email_draft`, Tier A. A draft sits in the
    operator's own Drafts folder — nothing external happens until a human
    sends it, so this is internal and reversible (the draft can be deleted)
    even though the eventual send is not."""
    try:
        gmail = _gmail_service(service)
        result = gmail.users().drafts().create(
            userId="me", body={"message": _build_email_message(to, subject, body)}
        ).execute()
    except Exception as exc:  # noqa: BLE001
        raise CapabilityBrokerError(f"create_email_draft to {to!r} failed: {exc}") from exc
    return {"tool": "create_email_draft", "to": to, "subject": subject, "draft_id": result.get("id")}


def create_calendar_event(
    summary: str, start: str, end: str, description: str = "",
    attendees: Optional[list[str]] = None, calendar_id: str = "primary", service=None,
) -> dict:
    """TOOL_REGISTRY: `create_calendar_event`, Tier B. `start`/`end` are ISO
    8601 datetimes (e.g. "2026-09-10T14:00:00+08:00"). The registry entry
    doesn't vary by whether `attendees` is actually populated (CLAUDE.md
    Section 1 — tier comes from the static registry, not runtime argument
    inspection), so an internal-only event is classified the same as one
    with real external attendees on it."""
    body = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start},
        "end": {"dateTime": end},
    }
    if attendees:
        body["attendees"] = [{"email": address} for address in attendees]
    try:
        calendar = _calendar_service(service)
        result = calendar.events().insert(calendarId=calendar_id, body=body).execute()
    except Exception as exc:  # noqa: BLE001
        raise CapabilityBrokerError(f"create_calendar_event {summary!r} failed: {exc}") from exc
    return {"tool": "create_calendar_event", "summary": summary, "event_id": result.get("id")}


def update_calendar_event(event_id: str, calendar_id: str = "primary", service=None, **fields) -> dict:
    """TOOL_REGISTRY: `update_calendar_event`, Tier B — modifying an event
    any attendees already on it can see change. Reads the existing event
    first so an update only touches the fields actually supplied, rather
    than replacing the whole event body."""
    try:
        calendar = _calendar_service(service)
        existing = calendar.events().get(calendarId=calendar_id, eventId=event_id).execute()
        for key, value in fields.items():
            if key in ("start", "end") and isinstance(value, str):
                existing[key] = {"dateTime": value}
            else:
                existing[key] = value
        result = calendar.events().update(calendarId=calendar_id, eventId=event_id, body=existing).execute()
    except Exception as exc:  # noqa: BLE001
        raise CapabilityBrokerError(f"update_calendar_event {event_id!r} failed: {exc}") from exc
    return {"tool": "update_calendar_event", "event_id": result.get("id", event_id)}


def delete_calendar_event(event_id: str, calendar_id: str = "primary", service=None) -> dict:
    """TOOL_REGISTRY: `delete_calendar_event`, Tier B — deleting an event
    any attendees already on it can see disappear."""
    try:
        calendar = _calendar_service(service)
        calendar.events().delete(calendarId=calendar_id, eventId=event_id).execute()
    except Exception as exc:  # noqa: BLE001
        raise CapabilityBrokerError(f"delete_calendar_event {event_id!r} failed: {exc}") from exc
    return {"tool": "delete_calendar_event", "event_id": event_id}


# Central dispatch — the only tool_name -> function mapping the callers
# (aldric_chat.py today) need to know about. Keeping resolution here, not
# scattered across call sites, means there is exactly one place to check
# when asking "what does this tool name actually do".
_DISPATCH = {
    "send_email": send_email,
    "create_email_draft": create_email_draft,
    "create_calendar_event": create_calendar_event,
    "update_calendar_event": update_calendar_event,
    "delete_calendar_event": delete_calendar_event,
}


def execute_action(tool_name: str, arguments: dict) -> dict:
    """The only entry point callers should use. Deliberately carries no
    tier/permission logic of its own — see module docstring. Raises
    CapabilityBrokerError for an unrecognized tool name rather than
    guessing what it might have meant, the same fail-closed instinct as
    core.pa_action_kernel.build_action_request's unknown-tool path."""
    func = _DISPATCH.get(tool_name)
    if func is None:
        raise CapabilityBrokerError(f"No capability wired up for tool '{tool_name}'")
    return func(**arguments)

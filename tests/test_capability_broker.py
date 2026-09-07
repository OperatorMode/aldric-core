"""
Tests for core.capability_broker — the only place in the codebase that
actually calls a real external API (README gap-list item 9). Every test
here injects a fake `service` object (see the module docstring: every
public function takes one as an optional keyword) so nothing ever touches
the network or a real Google account.
"""
import pytest
from unittest.mock import MagicMock

from core import capability_broker


def _fake_gmail_service(send_result=None, draft_result=None):
    service = MagicMock()
    service.users.return_value.messages.return_value.send.return_value.execute.return_value = (
        send_result if send_result is not None else {"id": "msg_123"}
    )
    service.users.return_value.drafts.return_value.create.return_value.execute.return_value = (
        draft_result if draft_result is not None else {"id": "draft_456"}
    )
    return service


def _fake_calendar_service(insert_result=None, get_result=None, update_result=None):
    service = MagicMock()
    service.events.return_value.insert.return_value.execute.return_value = (
        insert_result if insert_result is not None else {"id": "evt_789"}
    )
    service.events.return_value.get.return_value.execute.return_value = (
        get_result if get_result is not None else {"summary": "Old title"}
    )
    service.events.return_value.update.return_value.execute.return_value = (
        update_result if update_result is not None else {"id": "evt_789"}
    )
    return service


def test_send_email_calls_gmail_send_and_returns_message_id():
    service = _fake_gmail_service(send_result={"id": "msg_abc"})
    result = capability_broker.send_email(
        to="client@example.com", subject="Hi", body="Hello there", service=service,
    )
    assert result == {
        "tool": "send_email", "to": "client@example.com", "subject": "Hi", "message_id": "msg_abc",
    }
    service.users.return_value.messages.return_value.send.assert_called_once()
    _, kwargs = service.users.return_value.messages.return_value.send.call_args
    assert kwargs["userId"] == "me"
    assert "raw" in kwargs["body"]


def test_create_email_draft_calls_gmail_drafts_create():
    service = _fake_gmail_service(draft_result={"id": "draft_xyz"})
    result = capability_broker.create_email_draft(
        to="client@example.com", subject="Hi", body="Draft body", service=service,
    )
    assert result["draft_id"] == "draft_xyz"
    service.users.return_value.drafts.return_value.create.assert_called_once()


def test_create_calendar_event_calls_calendar_insert_with_attendees():
    service = _fake_calendar_service(insert_result={"id": "evt_1"})
    result = capability_broker.create_calendar_event(
        summary="Client call",
        start="2026-09-10T14:00:00+08:00",
        end="2026-09-10T14:30:00+08:00",
        attendees=["client@example.com"],
        service=service,
    )
    assert result["event_id"] == "evt_1"
    _, kwargs = service.events.return_value.insert.call_args
    assert kwargs["body"]["attendees"] == [{"email": "client@example.com"}]
    assert kwargs["body"]["start"] == {"dateTime": "2026-09-10T14:00:00+08:00"}


def test_create_calendar_event_with_no_attendees_omits_the_field():
    service = _fake_calendar_service()
    capability_broker.create_calendar_event(
        summary="Internal planning", start="2026-09-10T09:00:00+08:00",
        end="2026-09-10T09:30:00+08:00", service=service,
    )
    _, kwargs = service.events.return_value.insert.call_args
    assert "attendees" not in kwargs["body"]


def test_update_calendar_event_merges_fields_onto_the_existing_event():
    service = _fake_calendar_service(
        get_result={"id": "evt_1", "summary": "Old title", "start": {"dateTime": "2026-09-10T14:00:00+08:00"}},
        update_result={"id": "evt_1"},
    )
    result = capability_broker.update_calendar_event(
        event_id="evt_1", summary="New title", start="2026-09-10T15:00:00+08:00", service=service,
    )
    assert result["event_id"] == "evt_1"
    _, kwargs = service.events.return_value.update.call_args
    assert kwargs["body"]["summary"] == "New title"
    assert kwargs["body"]["start"] == {"dateTime": "2026-09-10T15:00:00+08:00"}


def test_delete_calendar_event_calls_calendar_delete():
    service = _fake_calendar_service()
    result = capability_broker.delete_calendar_event(event_id="evt_1", service=service)
    assert result == {"tool": "delete_calendar_event", "event_id": "evt_1"}
    service.events.return_value.delete.assert_called_once()


def test_a_failed_api_call_raises_capability_broker_error_not_the_raw_exception():
    service = MagicMock()
    service.users.return_value.messages.return_value.send.return_value.execute.side_effect = RuntimeError("network down")
    with pytest.raises(capability_broker.CapabilityBrokerError):
        capability_broker.send_email(to="x@example.com", subject="s", body="b", service=service)


def test_execute_action_dispatches_by_tool_name(monkeypatch):
    called = {}

    def fake_send_email(**kwargs):
        called.update(kwargs)
        return {"ok": True}

    monkeypatch.setitem(capability_broker._DISPATCH, "send_email", fake_send_email)
    result = capability_broker.execute_action("send_email", {"to": "a@b.com", "subject": "s", "body": "b"})
    assert result == {"ok": True}
    assert called == {"to": "a@b.com", "subject": "s", "body": "b"}


def test_execute_action_raises_for_an_unrecognized_tool_name():
    with pytest.raises(capability_broker.CapabilityBrokerError):
        capability_broker.execute_action("delete_everything", {})


def test_get_credentials_fails_closed_when_nothing_is_set_up_yet(tmp_path):
    with pytest.raises(capability_broker.CapabilityBrokerError):
        capability_broker.get_credentials(
            client_secret_path=str(tmp_path / "client_secret.json"),
            token_path=str(tmp_path / "token.json"),
        )

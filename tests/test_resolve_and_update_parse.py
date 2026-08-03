from __future__ import annotations

from requests import Response

from exchange_ews_mcp.config import AppConfig
from exchange_ews_mcp.ews import EwsClient


class RecordingSession:
    def __init__(self, responses: list[bytes]) -> None:
        self.responses = responses
        self.payloads: list[bytes] = []
        self.auth = None
        self.headers: dict[str, str] = {}

    def post(self, *args, **kwargs):
        self.payloads.append(kwargs["data"])
        response = Response()
        response.status_code = 200
        response._content = self.responses.pop(0)
        response.encoding = "utf-8"
        return response


def config(**kwargs) -> AppConfig:
    return AppConfig(
        ews_url="https://mail.example.com/EWS/Exchange.asmx",
        username="DOMAIN\\user",
        **kwargs,
    )


def resolve_response(*, email: str = "user@example.com", name: str = "Test User") -> bytes:
    return f'''<?xml version="1.0"?>
    <soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
      xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages"
      xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types">
      <soap:Body><m:ResolveNamesResponse><m:ResponseMessages>
        <m:ResolveNamesResponseMessage ResponseClass="Success"><m:ResponseCode>NoError</m:ResponseCode>
          <m:ResolutionSet TotalItemsInView="1" IncludesLastItemInRange="true">
            <t:Resolution><t:Mailbox><t:Name>{name}</t:Name>
              <t:EmailAddress>{email}</t:EmailAddress><t:RoutingType>SMTP</t:RoutingType>
              <t:MailboxType>Mailbox</t:MailboxType></t:Mailbox>
              <t:Contact><t:DisplayName>{name}</t:DisplayName><t:Department>R&amp;D</t:Department></t:Contact>
            </t:Resolution>
          </m:ResolutionSet>
        </m:ResolveNamesResponseMessage>
      </m:ResponseMessages></m:ResolveNamesResponse></soap:Body>
    </soap:Envelope>'''.encode("utf-8")


def no_results_response() -> bytes:
    return b'''<?xml version="1.0"?>
    <soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
      xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages"
      xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types">
      <soap:Body><m:ResolveNamesResponse><m:ResponseMessages>
        <m:ResolveNamesResponseMessage ResponseClass="Error">
          <m:ResponseCode>ErrorNameResolutionNoResults</m:ResponseCode>
        </m:ResolveNamesResponseMessage>
      </m:ResponseMessages></m:ResolveNamesResponse></soap:Body>
    </soap:Envelope>'''


def get_draft_response(change_key: str = "OLD") -> bytes:
    return f'''<?xml version="1.0"?>
    <soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
      xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages"
      xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types">
      <soap:Body><m:GetItemResponse><m:ResponseMessages>
        <m:GetItemResponseMessage ResponseClass="Success"><m:ResponseCode>NoError</m:ResponseCode>
          <m:Items><t:Message><t:ItemId Id="DRAFT" ChangeKey="{change_key}"/>
            <t:Subject>old</t:Subject><t:IsDraft>true</t:IsDraft><t:Importance>Normal</t:Importance>
            <t:Body BodyType="HTML">old</t:Body>
          </t:Message></m:Items>
        </m:GetItemResponseMessage>
      </m:ResponseMessages></m:GetItemResponse></soap:Body>
    </soap:Envelope>'''.encode()


def update_response() -> bytes:
    return b'''<?xml version="1.0"?>
    <soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
      xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages"
      xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types">
      <soap:Body><m:UpdateItemResponse><m:ResponseMessages>
        <m:UpdateItemResponseMessage ResponseClass="Success"><m:ResponseCode>NoError</m:ResponseCode>
          <m:Items><t:Message><t:ItemId Id="DRAFT" ChangeKey="NEW"/></t:Message></m:Items>
        </m:UpdateItemResponseMessage>
      </m:ResponseMessages></m:UpdateItemResponse></soap:Body>
    </soap:Envelope>'''


def test_resolve_names_merges_contacts_and_directory_by_email() -> None:
    session = RecordingSession([resolve_response(), resolve_response()])
    client = EwsClient(config(), "secret", session=session)
    resolved = client.resolve_names(query="testuser")
    assert resolved["status"] == "resolved"
    assert resolved["returned"] == 1
    candidate = resolved["candidates"][0]
    assert candidate["email"] == "user@example.com"
    assert candidate["sources"] == [
        "contacts_resolvenames",
        "directory_resolvenames",
    ]
    assert len(session.payloads) == 2
    assert b"FindPeople" not in b"".join(session.payloads)


def test_configured_current_user_uses_romanized_resolver() -> None:
    session = RecordingSession([resolve_response(), resolve_response()])
    client = EwsClient(config(primary_email="user@example.com"), "secret", session=session)
    current = client.get_current_user()
    assert current["status"] == "resolved"
    assert current["primary_email"] == "user@example.com"


def test_directory_only_resolves_alias() -> None:
    session = RecordingSession([resolve_response()])
    client = EwsClient(config(), "secret", session=session)
    resolved = client.resolve_names(query="user", search_scope="ActiveDirectory")
    assert resolved["returned"] == 1
    assert resolved["candidates"][0]["sources"] == ["directory_resolvenames"]
    assert b"user" in session.payloads[0]


def test_full_email_resolves_from_directory() -> None:
    session = RecordingSession([resolve_response()])
    client = EwsClient(config(), "secret", session=session)
    resolved = client.resolve_names(
        query="user@example.com", search_scope="ActiveDirectory"
    )
    assert resolved["returned"] == 1
    assert resolved["candidates"][0]["email"] == "user@example.com"


def test_non_ascii_query_is_rejected_without_network_request() -> None:
    session = RecordingSession([])
    client = EwsClient(config(), "secret", session=session)
    resolved = client.resolve_names(query="王小明")
    assert resolved["returned"] == 0
    assert resolved["status"] == "romanized_query_required"
    assert resolved["requires_romanized_query"] is True
    assert "拼音" in resolved["message"]
    assert session.payloads == []


def test_ascii_no_results_remains_not_found() -> None:
    session = RecordingSession([no_results_response(), no_results_response()])
    client = EwsClient(config(), "secret", session=session)
    resolved = client.resolve_names(query="nobody")
    assert resolved["returned"] == 0
    assert resolved["status"] == "not_found"
    assert resolved["requires_romanized_query"] is False


def test_update_draft_refreshes_current_changekey() -> None:
    session = RecordingSession([get_draft_response(), update_response()])
    client = EwsClient(config(), "secret", session=session)
    result = client.update_draft(
        item_id="DRAFT",
        change_key="STALE",
        subject="new",
        body_html="<p>new</p>",
        importance="High",
    )
    assert result.change_key == "NEW"
    assert b'ChangeKey="OLD"' in session.payloads[1]
    assert b'ChangeKey="STALE"' not in session.payloads[1]

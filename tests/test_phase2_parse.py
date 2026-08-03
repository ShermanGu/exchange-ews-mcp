from requests import Response
import pytest

from exchange_ews_mcp.config import AppConfig
from exchange_ews_mcp.ews import EwsClient
from exchange_ews_mcp.errors import EwsError


class FakeSession:
    def __init__(self, bodies: list[bytes]) -> None:
        self.bodies = bodies
        self.auth = None
        self.headers: dict[str, str] = {}

    def post(self, *args, **kwargs):
        response = Response()
        response.status_code = 200
        response._content = self.bodies.pop(0)
        response.encoding = "utf-8"
        return response


def config() -> AppConfig:
    return AppConfig(
        ews_url="https://mail.example.com/EWS/Exchange.asmx",
        username="DOMAIN\\user",
    )


def test_search_and_get_parse() -> None:
    find_response = b'''<?xml version="1.0"?>
    <soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
      xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages"
      xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types">
      <soap:Body><m:FindItemResponse><m:ResponseMessages>
        <m:FindItemResponseMessage ResponseClass="Success">
          <m:ResponseCode>NoError</m:ResponseCode>
          <m:RootFolder IncludesLastItemInRange="false" IndexedPagingOffset="1" TotalItemsInView="2">
            <t:Items><t:Message>
              <t:ItemId Id="ITEM1" ChangeKey="CK1"/>
              <t:Subject>Hello</t:Subject>
              <t:From><t:Mailbox><t:Name>Alice</t:Name><t:EmailAddress>alice@example.com</t:EmailAddress></t:Mailbox></t:From>
              <t:DateTimeReceived>2026-07-28T01:00:00Z</t:DateTimeReceived>
              <t:IsRead>false</t:IsRead><t:IsDraft>false</t:IsDraft><t:HasAttachments>true</t:HasAttachments>
            </t:Message></t:Items>
          </m:RootFolder>
        </m:FindItemResponseMessage>
      </m:ResponseMessages></m:FindItemResponse></soap:Body>
    </soap:Envelope>'''
    get_response = b'''<?xml version="1.0"?>
    <soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
      xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages"
      xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types">
      <soap:Body><m:GetItemResponse><m:ResponseMessages>
        <m:GetItemResponseMessage ResponseClass="Success"><m:ResponseCode>NoError</m:ResponseCode>
          <m:Items><t:Message>
            <t:ItemId Id="ITEM1" ChangeKey="CK1"/><t:Subject>Hello</t:Subject>
            <t:IsRead>false</t:IsRead><t:IsDraft>false</t:IsDraft><t:HasAttachments>true</t:HasAttachments>
            <t:Body BodyType="HTML" IsTruncated="false">&lt;p&gt;Body&lt;/p&gt;</t:Body>
            <t:UniqueBody BodyType="HTML" IsTruncated="false">&lt;p&gt;Unique&lt;/p&gt;</t:UniqueBody>
            <t:ToRecipients><t:Mailbox><t:EmailAddress>bob@example.com</t:EmailAddress></t:Mailbox></t:ToRecipients>
            <t:Attachments><t:FileAttachment><t:AttachmentId Id="ATT1"/><t:Name>a.txt</t:Name><t:Size>5</t:Size></t:FileAttachment></t:Attachments>
          </t:Message></m:Items>
        </m:GetItemResponseMessage>
      </m:ResponseMessages></m:GetItemResponse></soap:Body>
    </soap:Envelope>'''
    client = EwsClient(config(), "secret", session=FakeSession([find_response, get_response]))
    page = client.list_emails(limit=1)
    assert page["next_offset"] == 1
    assert page["items"][0]["from"]["email"] == "alice@example.com"
    detail = client.get_email(item_id="ITEM1")
    assert detail["body_html"] == "<p>Body</p>"
    assert detail["unique_body_html"] == "<p>Unique</p>"
    assert detail["unique_body_type"] == "HTML"
    assert detail["body_server_truncated"] is False
    assert detail["unique_body_server_truncated"] is False
    assert detail["attachments"][0]["name"] == "a.txt"


class StatusSession(FakeSession):
    def __init__(self, status: int, body: bytes) -> None:
        super().__init__([body])
        self.status = status

    def post(self, *args, **kwargs):
        response = super().post(*args, **kwargs)
        response.status_code = self.status
        return response


def test_http_500_surfaces_soap_fault() -> None:
    body = (
        b'<?xml version="1.0"?>'
        b'<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
        b'<soap:Body><soap:Fault><faultcode>soap:Client</faultcode>'
        b'<faultstring>The request is invalid.</faultstring>'
        b'</soap:Fault></soap:Body></soap:Envelope>'
    )
    client = EwsClient(config(), "secret", session=StatusSession(500, body))
    with pytest.raises(EwsError, match="The request is invalid"):
        client.list_emails(limit=1)

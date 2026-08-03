from __future__ import annotations

import base64
from xml.etree import ElementTree as ET

from requests import Response

from exchange_ews_mcp.config import AppConfig
from exchange_ews_mcp.ews import EwsClient
from exchange_ews_mcp.xml_builder import MESSAGES_NS, TYPES_NS, build_get_attachments_request, q


class FakeSession:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.auth = None
        self.headers = {}
        self.last_payload = None

    def post(self, *args, **kwargs):
        self.last_payload = kwargs.get("data")
        response = Response()
        response.status_code = 200
        response._content = self.body
        response.encoding = "utf-8"
        return response


def config() -> AppConfig:
    return AppConfig(ews_url="https://mail/EWS/Exchange.asmx", username="D\\u")


def test_get_attachment_request_contains_all_ids() -> None:
    root = ET.fromstring(
        build_get_attachments_request(
            exchange_version="Exchange2010_SP2", attachment_ids=["A1", "A2"]
        )
    )
    ids = [node.attrib["Id"] for node in root.findall(f".//{q(MESSAGES_NS, 'AttachmentIds')}/{q(TYPES_NS, 'AttachmentId')}")]
    assert ids == ["A1", "A2"]


def test_get_attachments_decodes_inline_content() -> None:
    encoded = base64.b64encode(b"image-bytes").decode("ascii")
    body = f'''<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
      xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages"
      xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types">
      <soap:Body><m:GetAttachmentResponse><m:ResponseMessages>
        <m:GetAttachmentResponseMessage ResponseClass="Success"><m:ResponseCode>NoError</m:ResponseCode>
          <m:Attachments><t:FileAttachment>
            <t:AttachmentId Id="A1"/><t:Name>logo.png</t:Name>
            <t:ContentType>image/png</t:ContentType><t:Size>11</t:Size>
            <t:IsInline>true</t:IsInline><t:ContentId>logo-cid</t:ContentId>
            <t:Content>{encoded}</t:Content>
          </t:FileAttachment></m:Attachments>
        </m:GetAttachmentResponseMessage>
      </m:ResponseMessages></m:GetAttachmentResponse></soap:Body>
    </soap:Envelope>'''.encode()
    client = EwsClient(config(), "secret", session=FakeSession(body))
    attachment = client.get_attachments(attachment_ids=["A1"])[0]
    assert attachment.filename == "logo.png"
    assert attachment.is_inline is True
    assert attachment.content_id == "logo-cid"
    assert attachment.content == b"image-bytes"


def test_get_attachments_handles_one_response_message_per_attachment() -> None:
    a1 = base64.b64encode(b"one").decode("ascii")
    a2 = base64.b64encode(b"two").decode("ascii")
    body = f'''<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
      xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages"
      xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types">
      <soap:Body><m:GetAttachmentResponse><m:ResponseMessages>
        <m:GetAttachmentResponseMessage ResponseClass="Success"><m:ResponseCode>NoError</m:ResponseCode>
          <m:Attachments><t:FileAttachment><t:AttachmentId Id="A1"/><t:Name>1.txt</t:Name><t:Content>{a1}</t:Content></t:FileAttachment></m:Attachments>
        </m:GetAttachmentResponseMessage>
        <m:GetAttachmentResponseMessage ResponseClass="Success"><m:ResponseCode>NoError</m:ResponseCode>
          <m:Attachments><t:FileAttachment><t:AttachmentId Id="A2"/><t:Name>2.txt</t:Name><t:Content>{a2}</t:Content></t:FileAttachment></m:Attachments>
        </m:GetAttachmentResponseMessage>
      </m:ResponseMessages></m:GetAttachmentResponse></soap:Body>
    </soap:Envelope>'''.encode()
    client = EwsClient(config(), "secret", session=FakeSession(body))
    attachments = client.get_attachments(attachment_ids=["A2", "A1"])
    assert [item.attachment_id for item in attachments] == ["A2", "A1"]
    assert [item.content for item in attachments] == [b"two", b"one"]

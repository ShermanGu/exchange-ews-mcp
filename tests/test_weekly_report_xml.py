from __future__ import annotations

from xml.etree import ElementTree as ET

from exchange_ews_mcp.xml_builder import (
    MESSAGES_NS,
    TYPES_NS,
    build_get_attachment_request,
    build_get_item_request,
    build_reply_draft_request,
    build_update_draft_request,
    q,
)


def test_get_item_explicitly_requests_unique_body_as_html() -> None:
    root = ET.fromstring(
        build_get_item_request(
            exchange_version="Exchange2010_SP2",
            item_id="WK3",
            change_key="CK3",
        )
    )
    body_type = root.find(f".//{q(TYPES_NS, 'BodyType')}")
    assert body_type is not None and body_type.text == "HTML"
    fields = [
        node.attrib.get("FieldURI")
        for node in root.findall(f".//{q(TYPES_NS, 'FieldURI')}")
    ]
    assert "item:UniqueBody" in fields


def test_get_attachment_requests_one_file_attachment() -> None:
    root = ET.fromstring(
        build_get_attachment_request(
            exchange_version="Exchange2010_SP2",
            attachment_id="ATT1",
        )
    )
    request = root.find(f".//{q(MESSAGES_NS, 'GetAttachment')}")
    assert request is not None
    attachment_id = root.find(f".//{q(TYPES_NS, 'AttachmentId')}")
    assert attachment_id is not None and attachment_id.attrib["Id"] == "ATT1"


def test_update_weekly_reply_xml_contains_agent_html_only() -> None:
    wk4 = '<table width="760" style="width:570pt;table-layout:fixed">WK4_TEXT</table>'
    root = ET.fromstring(
        build_reply_draft_request(
            exchange_version="Exchange2010_SP2",
            item_id="WK3-ID",
            change_key="WK3-CK",
            body_html=wk4,
            reply_all=True,
        )
    )
    reply = root.find(f".//{q(TYPES_NS, 'ReplyAllToItem')}")
    assert reply is not None
    reference = root.find(f".//{q(TYPES_NS, 'ReferenceItemId')}")
    assert reference is not None
    assert reference.attrib == {"Id": "WK3-ID", "ChangeKey": "WK3-CK"}
    body = root.find(f".//{q(TYPES_NS, 'NewBodyContent')}")
    assert body is not None
    assert body.attrib["BodyType"] == "HTML"
    assert body.text is not None
    assert "WK4_TEXT" in body.text
    assert "WK2_MARKER" not in body.text
    assert "WK1_MARKER" not in body.text
    assert 'width="760" style="width:570pt;table-layout:fixed"' in body.text


def test_weekly_subject_update_xml_does_not_set_body() -> None:
    root = ET.fromstring(
        build_update_draft_request(
            exchange_version="Exchange2010_SP2",
            item_id="DRAFT1",
            change_key="DCK1",
            subject="新周报主题",
        )
    )
    assert root.findall(f".//{q(TYPES_NS, 'Body')}") == []
    assert root.find(f".//{q(TYPES_NS, 'AppendToItemField')}") is None


def test_get_file_attachment_parses_inline_content(monkeypatch) -> None:
    import base64
    from requests import Response

    from exchange_ews_mcp.config import AppConfig
    from exchange_ews_mcp.ews import EwsClient

    response = Response()
    response.status_code = 200
    response._content = f'''<?xml version="1.0" encoding="utf-8"?>
    <soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
      xmlns:m="{MESSAGES_NS}" xmlns:t="{TYPES_NS}">
      <soap:Body><m:GetAttachmentResponse><m:ResponseMessages>
        <m:GetAttachmentResponseMessage ResponseClass="Success">
          <m:ResponseCode>NoError</m:ResponseCode>
          <m:Attachments><t:FileAttachment>
            <t:AttachmentId Id="ATT1"/>
            <t:Name>image001.png</t:Name>
            <t:ContentType>image/png</t:ContentType>
            <t:ContentId>image001.png</t:ContentId>
            <t:IsInline>true</t:IsInline>
            <t:Content>{base64.b64encode(b'PNGDATA').decode()}</t:Content>
          </t:FileAttachment></m:Attachments>
        </m:GetAttachmentResponseMessage>
      </m:ResponseMessages></m:GetAttachmentResponse></soap:Body>
    </soap:Envelope>'''.encode()
    config = AppConfig(
        ews_url="https://mail.company.com/EWS/Exchange.asmx",
        username="DOMAIN\\user",
    )
    client = EwsClient(config, "password")
    monkeypatch.setattr(client, "_post", lambda payload: response)
    result = client.get_file_attachment(attachment_id="ATT1")
    assert result["content"] == b"PNGDATA"
    assert result["content_id"] == "image001.png"
    assert result["is_inline"] is True

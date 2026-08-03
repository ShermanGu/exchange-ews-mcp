from __future__ import annotations

from xml.etree import ElementTree as ET

from requests import Response

from exchange_ews_mcp.config import AppConfig
from exchange_ews_mcp.ews import EwsClient
from exchange_ews_mcp.xml_builder import TYPES_NS, build_get_item_identity_request, q


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


def config() -> AppConfig:
    return AppConfig(
        ews_url="https://mail.example.com/EWS/Exchange.asmx",
        username="DOMAIN\\user",
    )


def get_identity_response(item_id: str = "ITEM", change_key: str = "CURRENT") -> bytes:
    return f'''<?xml version="1.0"?>
    <soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
      xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages"
      xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types">
      <soap:Body><m:GetItemResponse><m:ResponseMessages>
        <m:GetItemResponseMessage ResponseClass="Success"><m:ResponseCode>NoError</m:ResponseCode>
          <m:Items><t:Message><t:ItemId Id="{item_id}" ChangeKey="{change_key}"/></t:Message></m:Items>
        </m:GetItemResponseMessage>
      </m:ResponseMessages></m:GetItemResponse></soap:Body>
    </soap:Envelope>'''.encode()


def create_response(draft_id: str = "DRAFT", change_key: str = "DRAFTCK") -> bytes:
    return f'''<?xml version="1.0"?>
    <soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
      xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages"
      xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types">
      <soap:Body><m:CreateItemResponse><m:ResponseMessages>
        <m:CreateItemResponseMessage ResponseClass="Success"><m:ResponseCode>NoError</m:ResponseCode>
          <m:Items><t:Message><t:ItemId Id="{draft_id}" ChangeKey="{change_key}"/></t:Message></m:Items>
        </m:CreateItemResponseMessage>
      </m:ResponseMessages></m:CreateItemResponse></soap:Body>
    </soap:Envelope>'''.encode()


def test_identity_request_is_id_only_and_has_no_stale_changekey() -> None:
    root = ET.fromstring(
        build_get_item_identity_request(
            exchange_version="Exchange2010_SP2",
            item_id="ITEM",
        )
    )
    base_shape = root.find(f".//{q(TYPES_NS, 'BaseShape')}")
    assert base_shape is not None and base_shape.text == "IdOnly"
    item = root.find(f".//{q(TYPES_NS, 'ItemId')}")
    assert item is not None and item.attrib == {"Id": "ITEM"}


def test_reply_automatically_fetches_current_changekey() -> None:
    session = RecordingSession([get_identity_response(change_key="CURRENT"), create_response()])
    client = EwsClient(config(), "secret", session=session)
    result = client.reply_as_draft(
        item_id="ITEM",
        change_key="STALE",
        body_html="<p>reply</p>",
    )
    assert result.item_id == "DRAFT"
    assert len(session.payloads) == 2
    create_root = ET.fromstring(session.payloads[1])
    reference = create_root.find(f".//{q(TYPES_NS, 'ReferenceItemId')}")
    assert reference is not None
    assert reference.attrib == {"Id": "ITEM", "ChangeKey": "CURRENT"}


def test_forward_automatically_fetches_current_changekey() -> None:
    session = RecordingSession([get_identity_response(change_key="FRESH"), create_response()])
    client = EwsClient(config(), "secret", session=session)
    client.forward_as_draft(
        item_id="ITEM",
        to=["safe@example.com"],
        body_html="<p>forward</p>",
        change_key=None,
    )
    create_root = ET.fromstring(session.payloads[1])
    reference = create_root.find(f".//{q(TYPES_NS, 'ReferenceItemId')}")
    assert reference is not None and reference.attrib["ChangeKey"] == "FRESH"

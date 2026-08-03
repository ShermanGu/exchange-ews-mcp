import base64
from xml.etree import ElementTree as ET

from exchange_ews_mcp.xml_builder import (
    MESSAGES_NS,
    TYPES_NS,
    SearchCriteria,
    build_create_attachment_request,
    build_find_items_request,
    build_forward_draft_request,
    build_get_item_request,
    build_reply_draft_request,
    q,
)


def test_find_items_request_has_paging_filters_and_sort() -> None:
    payload = build_find_items_request(
        exchange_version="Exchange2010_SP2",
        folder="inbox",
        limit=25,
        offset=50,
        criteria=SearchCriteria(
            subject_contains="weekly",
            sender="sender@example.com",
            unread_only=True,
            after="2026-07-01T00:00:00Z",
            before="2026-08-01T00:00:00Z",
        ),
    )
    root = ET.fromstring(payload)
    view = root.find(f".//{q(MESSAGES_NS, 'IndexedPageItemView')}")
    assert view is not None
    assert view.attrib["MaxEntriesReturned"] == "25"
    assert view.attrib["Offset"] == "50"
    restriction = root.find(f".//{q(MESSAGES_NS, 'Restriction')}")
    assert restriction is not None
    assert restriction.find(q(TYPES_NS, "And")) is not None
    folder = root.find(f".//{q(TYPES_NS, 'DistinguishedFolderId')}")
    assert folder is not None and folder.attrib["Id"] == "inbox"


def test_get_item_requests_html_body() -> None:
    payload = build_get_item_request(
        exchange_version="Exchange2010_SP2",
        item_id="ITEM",
        change_key="CK",
    )
    root = ET.fromstring(payload)
    body_type = root.find(f".//{q(TYPES_NS, 'BodyType')}")
    assert body_type is not None and body_type.text == "HTML"
    field_uris = [
        node.attrib.get("FieldURI")
        for node in root.findall(f".//{q(TYPES_NS, 'FieldURI')}")
    ]
    assert "item:UniqueBody" in field_uris
    item = root.find(f".//{q(TYPES_NS, 'ItemId')}")
    assert item is not None and item.attrib == {"Id": "ITEM", "ChangeKey": "CK"}


def test_reply_and_forward_are_save_only_drafts() -> None:
    reply = ET.fromstring(
        build_reply_draft_request(
            exchange_version="Exchange2010_SP2",
            item_id="ITEM",
            change_key="CK",
            body_html="<p>reply</p>",
            reply_all=True,
        )
    )
    create_reply = reply.find(f".//{q(MESSAGES_NS, 'CreateItem')}")
    assert create_reply is not None and create_reply.attrib["MessageDisposition"] == "SaveOnly"
    assert reply.find(f".//{q(TYPES_NS, 'ReplyAllToItem')}") is not None
    folder = reply.find(f".//{q(TYPES_NS, 'DistinguishedFolderId')}")
    assert folder is not None and folder.attrib["Id"] == "drafts"

    forward = ET.fromstring(
        build_forward_draft_request(
            exchange_version="Exchange2010_SP2",
            item_id="ITEM",
            change_key=None,
            to=["a@example.com"],
            cc=[],
            bcc=[],
            body_html="<p>forward</p>",
        )
    )
    create_forward = forward.find(f".//{q(MESSAGES_NS, 'CreateItem')}")
    assert create_forward is not None and create_forward.attrib["MessageDisposition"] == "SaveOnly"
    assert forward.find(f".//{q(TYPES_NS, 'ForwardItem')}") is not None


def test_attachment_request_encodes_content() -> None:
    payload = build_create_attachment_request(
        exchange_version="Exchange2010_SP2",
        item_id="ITEM",
        change_key="CK",
        filename="report.txt",
        content_type="text/plain",
        content=b"hello",
    )
    root = ET.fromstring(payload)
    parent = root.find(f".//{q(MESSAGES_NS, 'ParentItemId')}")
    assert parent is not None and parent.attrib == {"Id": "ITEM", "ChangeKey": "CK"}
    content = root.find(f".//{q(TYPES_NS, 'Content')}")
    assert content is not None and base64.b64decode(content.text or "") == b"hello"


def test_find_items_uses_valid_isdraft_and_sender_property() -> None:
    payload = build_find_items_request(
        exchange_version="Exchange2010_SP2",
        folder="inbox",
        limit=10,
        offset=0,
        criteria=SearchCriteria(sender="sender@example.com"),
    )
    root = ET.fromstring(payload)
    field_uris = [
        node.attrib.get("FieldURI")
        for node in root.findall(f".//{q(TYPES_NS, 'FieldURI')}")
    ]
    assert "item:IsDraft" in field_uris
    assert "message:IsDraft" not in field_uris
    extended = root.find(f".//{q(TYPES_NS, 'ExtendedFieldURI')}")
    assert extended is not None
    assert extended.attrib == {"PropertyTag": "0x5D01", "PropertyType": "String"}

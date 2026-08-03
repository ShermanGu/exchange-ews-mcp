from xml.etree import ElementTree as ET

from exchange_ews_mcp.xml_builder import (
    MESSAGES_NS,
    TYPES_NS,
    build_create_draft_request,
    q,
)


def test_build_create_draft_request() -> None:
    payload = build_create_draft_request(
        exchange_version="Exchange2010_SP2",
        to=["a@example.com"],
        cc=["b@example.com"],
        bcc=[],
        subject="测试 & subject",
        body_html="<p><strong>Hello</strong> & world</p>",
    )
    root = ET.fromstring(payload)
    create_item = root.find(f".//{q(MESSAGES_NS, 'CreateItem')}")
    assert create_item is not None
    assert create_item.attrib["MessageDisposition"] == "SaveOnly"
    folder = root.find(f".//{q(TYPES_NS, 'DistinguishedFolderId')}")
    assert folder is not None and folder.attrib["Id"] == "drafts"
    subject = root.find(f".//{q(TYPES_NS, 'Subject')}")
    assert subject is not None and subject.text == "测试 & subject"
    body = root.find(f".//{q(TYPES_NS, 'Body')}")
    assert body is not None
    assert body.attrib["BodyType"] == "HTML"
    assert body.text == "<p><strong>Hello</strong> & world</p>"

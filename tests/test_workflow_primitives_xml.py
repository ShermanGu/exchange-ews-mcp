from xml.etree import ElementTree as ET

from exchange_ews_mcp.ews import normalize_resolve_scope
from exchange_ews_mcp.xml_builder import (
    MESSAGES_NS,
    TYPES_NS,
    SearchCriteria,
    build_find_items_request,
    build_resolve_names_request,
    build_update_draft_request,
    q,
)


def test_resolve_names_request_searches_contacts_and_directory() -> None:
    root = ET.fromstring(
        build_resolve_names_request(
            exchange_version="Exchange2010_SP2",
            query="wangxiaoming",
        )
    )
    node = root.find(f".//{q(MESSAGES_NS, 'ResolveNames')}")
    assert node is not None
    assert node.attrib["SearchScope"] == "ContactsActiveDirectory"
    assert node.attrib["ReturnFullContactData"] == "true"
    entry = root.find(f".//{q(MESSAGES_NS, 'UnresolvedEntry')}")
    assert entry is not None and entry.text == "wangxiaoming"
    folder = root.find(f".//{q(TYPES_NS, 'DistinguishedFolderId')}")
    assert folder is not None and folder.attrib["Id"] == "contacts"


def test_enhanced_find_item_fields_and_filters() -> None:
    root = ET.fromstring(
        build_find_items_request(
            exchange_version="Exchange2010_SP2",
            folder="inbox",
            limit=20,
            offset=0,
            criteria=SearchCriteria(
                to_contains="小明",
                cc_contains="小红",
                participant_contains="person@example.com",
                has_attachments=True,
                conversation_id="CONV",
                internet_message_id="<id@example.com>",
            ),
        )
    )
    fields = [
        node.attrib.get("FieldURI")
        for node in root.findall(f".//{q(TYPES_NS, 'FieldURI')}")
    ]
    assert "item:DisplayTo" in fields
    assert "item:DisplayCc" in fields
    assert "item:ConversationId" in fields
    assert "item:ParentFolderId" in fields
    assert root.find(f".//{q(TYPES_NS, 'Or')}") is not None


def test_update_draft_is_save_only_and_supports_clear_recipients() -> None:
    root = ET.fromstring(
        build_update_draft_request(
            exchange_version="Exchange2010_SP2",
            item_id="DRAFT",
            change_key="CK",
            subject="updated",
            body_html="<p>updated</p>",
            to=["a@example.com"],
            cc=[],
            importance="High",
        )
    )
    update = root.find(f".//{q(MESSAGES_NS, 'UpdateItem')}")
    assert update is not None
    assert update.attrib["MessageDisposition"] == "SaveOnly"
    assert update.attrib["ConflictResolution"] == "AutoResolve"
    item_id = root.find(f".//{q(TYPES_NS, 'ItemId')}")
    assert item_id is not None and item_id.attrib == {"Id": "DRAFT", "ChangeKey": "CK"}
    deletes = root.findall(f".//{q(TYPES_NS, 'DeleteItemField')}")
    assert any(
        node.find(q(TYPES_NS, "FieldURI")).attrib["FieldURI"] == "message:CcRecipients"
        for node in deletes
    )


def test_resolve_scope_aliases_match_outlook_address_books() -> None:
    assert normalize_resolve_scope("contacts") == "Contacts"
    assert normalize_resolve_scope("全球通讯簿") == "ActiveDirectory"
    assert normalize_resolve_scope("GAL") == "ActiveDirectory"
    assert normalize_resolve_scope("both") == "ContactsActiveDirectory"

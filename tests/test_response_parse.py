from xml.etree import ElementTree as ET

from exchange_ews_mcp.ews import EwsClient


def test_no_error_response() -> None:
    root = ET.fromstring('''
    <soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
      xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages">
      <soap:Body><m:CreateItemResponse><m:ResponseMessages>
        <m:CreateItemResponseMessage ResponseClass="Success">
          <m:ResponseCode>NoError</m:ResponseCode>
        </m:CreateItemResponseMessage>
      </m:ResponseMessages></m:CreateItemResponse></soap:Body>
    </soap:Envelope>
    ''')
    EwsClient._raise_for_ews_error(root)


def test_get_email_can_return_full_body_without_local_character_cap(monkeypatch) -> None:
    import html as html_module

    from requests import Response

    from exchange_ews_mcp.config import AppConfig

    large_body = "<html><body>" + ("x" * 500_100) + "</body></html>"
    escaped = html_module.escape(large_body)
    xml = f'''<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
      xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages"
      xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types">
      <soap:Body><m:GetItemResponse><m:ResponseMessages>
        <m:GetItemResponseMessage ResponseClass="Success">
          <m:ResponseCode>NoError</m:ResponseCode>
          <m:Items><t:Message>
            <t:ItemId Id="ITEM1" ChangeKey="CK1"/>
            <t:Subject>周报</t:Subject>
            <t:Body BodyType="HTML">{escaped}</t:Body>
            <t:UniqueBody BodyType="HTML">top</t:UniqueBody>
          </t:Message></m:Items>
        </m:GetItemResponseMessage>
      </m:ResponseMessages></m:GetItemResponse></soap:Body>
    </soap:Envelope>'''
    response = Response()
    response.status_code = 200
    response._content = xml.encode("utf-8")
    config = AppConfig(
        ews_url="https://mail.company.com/EWS/Exchange.asmx",
        username="DOMAIN\\user",
        primary_email="self@company.com",
        company_email_domains=["company.com"],
    )
    client = EwsClient(config, "password")
    monkeypatch.setattr(client, "_post", lambda payload: response)

    full = client.get_email(item_id="ITEM1", max_body_chars=None)
    assert full["body_html"] == large_body
    assert full["body_local_truncated"] is False
    assert full["body_server_truncated"] is False
    assert full["body_truncated"] is False

    limited = client.get_email(item_id="ITEM1", max_body_chars=500_000)
    assert len(limited["body_html"]) == 500_000
    assert limited["body_local_truncated"] is True
    assert limited["body_truncated"] is True

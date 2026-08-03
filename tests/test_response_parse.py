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

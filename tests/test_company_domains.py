from exchange_ews_mcp.config import AppConfig, effective_company_domains


def test_effective_company_domains_include_primary_email_domain_and_deduplicate():
    config = AppConfig(
        ews_url="https://mail.company.com/EWS/Exchange.asmx",
        username="DOMAIN\\user",
        primary_email="self@company.com",
        company_email_domains=["COMPANY.COM", "subsidiary.company.com"],
    )
    assert effective_company_domains(config) == ["company.com", "subsidiary.company.com"]


def test_primary_domain_and_additional_domain_are_both_effective():
    config = AppConfig(
        ews_url="https://mail.company.com/EWS/Exchange.asmx",
        username="DOMAIN\\user",
        primary_email="self@company.com",
        company_email_domains=["company2.com"],
    )
    assert effective_company_domains(config) == ["company2.com", "company.com"]

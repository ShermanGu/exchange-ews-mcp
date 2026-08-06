# Security policy

**English** | [简体中文](SECURITY.zh-CN.md)

Exchange EWS MCP handles mailbox metadata, local credentials, attachments, server-owned HTML, and write-capable Exchange operations. Report security issues privately and do not test against mailboxes you do not own or administer.

## Supported versions

Security fixes target the current `main` branch and the latest published release. Older source snapshots may not receive patches.

## Reporting a vulnerability

Use GitHub's **Report a vulnerability** option in the repository Security tab to open a private security advisory.

Include, when possible:

- affected version or commit;
- Windows, Python, and Exchange environment;
- impact and realistic attack scenario;
- minimal reproduction using synthetic or sanitized data;
- suggested mitigation, if known.

Do not include real credentials, mailbox content, addresses, internal hostnames, EWS URLs, ItemId/ChangeKey values, attachment data, or personal information. Do not open a public issue until disclosure is approved.

## If a secret was exposed

Immediately revoke or rotate the affected password, token, or certificate. Removing a value from a later commit does not remove it from Git history.

## Security design notes

- Passwords are stored in Windows Credential Manager.
- Raw Exchange identifiers remain behind local opaque references.
- Normal mail workflows create drafts instead of sending automatically.
- Meeting invitations require explicit confirmation.
- Attachment paths are allow-listed and validated.
- Weekly-report HTML stays server-side; the Agent submits only escaped text-slot updates.
- Weekly-report updates require a random, expiring, single-use token.
- The server verifies source hashes, slot manifests, and HTML structure before creating a draft.

These safeguards do not replace Exchange permissions, endpoint security, mailbox policy, administrator controls, or MCP-client safeguards.

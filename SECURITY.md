# Security policy

**English** | [简体中文](SECURITY.zh-CN.md)

Exchange EWS MCP handles mailbox metadata, local credentials, attachments, and write-capable Exchange operations. Please report security issues privately and avoid testing against mailboxes you do not own or administer.

## Supported versions

Security fixes target the current `main` branch and the latest published release. Older source snapshots may not receive patches.

## Reporting a vulnerability

Use GitHub's **Report a vulnerability** option in the repository Security tab to open a private security advisory.

Please include, when possible:

- affected version or commit;
- operating system, Python version, and Exchange environment;
- impact and realistic attack scenario;
- minimal reproduction steps or a proof of concept with synthetic data;
- suggested mitigation, if known.

Do not include real credentials, access tokens, mailbox content, internal hostnames, or personal data. Do not open a public issue until a maintainer confirms that disclosure is safe.

## If a secret was exposed

Immediately revoke or rotate the affected password, token, or certificate. Removing a value from a later commit does not remove it from Git history.

## Security design notes

- Passwords are stored in Windows Credential Manager.
- Exchange identifiers and complete templates remain behind local opaque references.
- Mail workflows create drafts instead of sending automatically.
- Meeting invitations require explicit confirmation.
- Attachment paths and template resources are validated before remote state is created.

These safeguards do not replace Exchange access controls, endpoint security, or administrator policy.

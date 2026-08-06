## Summary

## User-visible impact

## Safety and compatibility

- [ ] Mail writes remain draft-first.
- [ ] Meeting sends still require explicit confirmation.
- [ ] No credentials, mailbox content, internal URLs, ItemId, or ChangeKey values are included.
- [ ] Tool-surface changes are documented.

## Validation

- [ ] `python -m pytest -W error::ResourceWarning`
- [ ] Package build completed.
- [ ] Documentation updated where needed.

## What and why

<!-- What this changes and the problem it solves. Link the issue: "Closes #123". -->

## How it was tested

<!-- Paste the summary lines. Both dbt versions must pass. -->
- [ ] Offline suite, floor environment (dbt-core 1.8): `pytest -m "not sandbox"`
- [ ] Offline suite, latest dbt
- [ ] Sandbox tests I added or changed (if any), with their summary

## Checklist

- [ ] Tests first: a test fails without the change and passes with it
- [ ] No value is interpolated into SQL text; identifiers go through the validated helper ([docs/conventions.md](../docs/conventions.md))
- [ ] Docs updated (README, docs/, the views reference) when behaviour or a view column changes
- [ ] CHANGELOG.md updated under "Unreleased"

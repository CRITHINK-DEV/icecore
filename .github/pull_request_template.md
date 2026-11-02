## Story

<!-- Title and link or id. -->

## What changed

<!-- The files and the behaviour. -->

## Acceptance criteria

<!-- Each criterion as a checkbox, with its evidence (pytest summary lines, a sample row). -->

- [ ] 

## Checks

- [ ] Test written first and seen to fail for the right reason
- [ ] Offline suite passes on the floor and on the latest dbt (`pytest -m "not sandbox"`, from a clean `target/`, `dbt_packages/` and `package-lock.yml`)
- [ ] Sandbox tests for the changed code ran, with `run_id` on everything they create (or: no sandbox access, say so)
- [ ] No `INSERT`, `MERGE`, `CREATE TABLE` or `CREATE STAGE` in package macros
- [ ] No secrets, account identifiers or webhook URLs

## Not done

<!-- What is left, and why. -->

A person reviews and merges.

# Security policy

## Supported versions

icecore is before 1.0. Security fixes go to the latest release only.

| Version | Supported |
| --- | --- |
| latest release | yes |
| older releases | no |

## Report a vulnerability

Do not open a public issue for a security problem.

Report it privately through GitHub: on the repository's **Security** tab, choose **Report a vulnerability**. The report reaches the maintainers only.

Say what you found, how to reproduce it, and which version, dbt version and Snowflake edition you used. You will get an answer within 5 working days, and a fix or a plan within 30 days. We credit reporters in the release notes unless you ask us not to.

## What counts

icecore runs inside your Snowflake account with the role that runs dbt, and setup creates procedures that run with their owner's rights. Anything that lets a project value, a `meta` value, a var or an event-table value change the SQL icecore runs, read or write objects it should not, or reach another role's data is a vulnerability. So is any way to make icecore write credentials or data values into the event table that its settings say it does not write.

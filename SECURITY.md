# Security Policy

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Use GitHub's private vulnerability reporting: open the repository's **Security** tab and choose
**Report a vulnerability**, or go straight to <https://github.com/othermind-tech/eva/security/advisories/new>. This sends your report privately to the maintainer.

Please include:

- a description of the issue and its impact,
- the EVA version and your operating system,
- steps to reproduce, or a proof of concept,
- any suggested fix, if you have one.

EVA is maintained on a best-effort basis. There is **no service-level agreement**: we do not
guarantee an acknowledgement, a response time, a fix, or a release for any report. We will try to
acknowledge reports, keep you informed, and credit you in the release notes if you wish.

## Supported versions

Only the latest release is considered for security fixes, and even then without any guarantee.
Future releases are not promised.

## No security guarantee

Like all software, EVA has bugs, and some will be security vulnerabilities that nobody has found
yet. EVA also runs AI agents that can act on your machine. Use it at your own risk and with the
approval gates and permission limits you are comfortable with. It is provided "AS IS" under the
[Apache License 2.0](LICENSE) (sections 7 and 8), without warranty of any kind.

## What EVA does for security

These are design intentions, not guarantees.

- Provider API keys are stored in the operating system's keyring and are never returned by any API.
- By default the backend listens on the loopback interface only (127.0.0.1) and requires authentication.
- See [`docs/16-appendix/02-privacy-and-security.md`](docs/16-appendix/02-privacy-and-security.md)
  for the full security and privacy model.

## Scope notes

EVA runs AI agents that can read files and run commands on your machine, with approval gates you
control. Reports about unsafe default permissions, bypassable approval gates, prompt-injection paths
that lead to unapproved actions, or secret leakage are especially welcome.

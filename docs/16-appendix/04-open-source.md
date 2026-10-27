# Open Source

EVA's source code is open. This page explains what that means for you: the licence, how to use and share the code, how to contribute, and where to report a security problem.

---

## The licence

EVA is released under the **Apache License 2.0**. In plain terms, you may use EVA for any purpose — personal, internal or commercial — and you may read, modify and redistribute the code, as long as you keep the licence and copyright notices and state any changes you made. The licence also includes a patent grant from contributors, and it comes with no warranty.

Copyright in EVA, including contributions made under the Contributor License Agreement (see below), belongs to its maintainer, **Anand Balagopalan**, or to a successor or organisation the project is later transferred to. The full terms are in the `LICENSE` file at the root of the repository, and `NOTICE` lists the attributions the licence requires you to keep.

Versions of EVA that have been released under Apache-2.0 stay available under Apache-2.0. The maintainer may release future versions under different terms.

## Third-party code

EVA is built on many open-source libraries, each under its own licence. `THIRD_PARTY_LICENSES.md` lists them, with their licences, and is generated from the dependency lock file so that it matches what actually ships. The few places where EVA includes adapted code from another project carry that project's notice in the source file and in `NOTICE`.

EVA's dependencies are checked for their licences before they're added, and strong copyleft licences are kept out of the runtime so that EVA stays free for you to build on.

## Running it from source

The repository's READMEs describe how to set up each part — the backend (Python 3.11, installed from the hash-locked requirements file), the interface (Node.js) and the desktop shell and installer. EVA is built and tested on **Windows 10 and 11 (64-bit)**; other platforms aren't supported yet.

## Contributing

Contributions are welcome — bug reports, documentation fixes, and code. Start with `CONTRIBUTING.md`, which covers how to propose a change, the conventions the code follows, and the checks a change should pass.

Before your first contribution is merged, you'll be asked to sign a **Contributor License Agreement** (`CLA.md`). In short:

- **The maintainer owns the copyright.** You assign your copyright in your contribution to the maintainer. Where the law of your country doesn't allow that, you grant the broadest licence it does allow instead.
- **You get a limited licence back.** You can reuse your contribution, as you submitted it, in your own projects. It doesn't cover later changes, other parts of EVA, or the EVA name and logo.
- **The maintainer can change the terms of future versions**, including making them proprietary, and can use contributions commercially. You receive no payment.
- **It doesn't change what's already public.** Versions released under Apache-2.0 stay under Apache-2.0.

You're asked to agree once, and a bot guides you through it on your first pull request. If you're not comfortable with these terms, please don't submit a contribution.

## Reporting a security problem

Please **don't** open a public issue for a vulnerability. Follow `SECURITY.md` to report it privately. Reports that include steps to reproduce, the version you tested, and what an attacker could achieve are the easiest to act on. Reporting privately helps, but it doesn't create any obligation to respond, fix or release within a set time — see below.

## Support and warranty

EVA is free software provided **as is**, without warranty of any kind (Apache-2.0, sections 7 and 8).

- **No service-level agreement.** There is no guaranteed support, response time, fix time or availability, for bug reports, security reports, pull requests or questions. This includes the update service EVA checks for new versions.
- **No security guarantee.** All software has bugs, and some are security vulnerabilities that no one has found yet. EVA also runs AI agents that can act on your computer, so use the approval and permission settings you're comfortable with. See [Privacy & Security](02-privacy-and-security.md).
- **No promise of future versions.** The maintainer plans to keep developing EVA, but doesn't commit to any release, feature, schedule or licence for future versions. Only the latest release is considered for security fixes, and even then without a guarantee.

This applies to the free, open-source EVA. If paid services are offered in the future, they would have their own terms.

## Your data and the open-source release

Making the code open doesn't change how EVA treats your data: your conversations, memory and files stay on your machine, and EVA has no server of its own that receives them. The code is there for anyone to check — see [Privacy & Security](02-privacy-and-security.md) for what EVA contacts on its own.

---

> *This is the end of the EVA documentation. To return to the beginning, see [Getting Started](../01-introduction/01-getting-started.md).*

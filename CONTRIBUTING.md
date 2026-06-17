# Contributing to Xorics

Thanks for your interest. Contributions are welcome under a simple, contributor-friendly
arrangement:

- **You keep your copyright.** Your work stays yours, with your name on it.
- **The public gets it under the AGPL**, like the rest of Xorics.
- **The maintainer gets a standing license** to use and relicense your contribution (including in
  closed products) without asking again.

The full terms are in [CLA.md](CLA.md). They exist so the project can stay open *and* keep evolving
without any one contributor holding a veto over a future license decision — while never taking
ownership away from the person who wrote the code.

## The two rules

**1. Sign off every commit (DCO).**
Add a `Signed-off-by: Your Name <you@example.com>` line to each commit. Git adds it for you:

```
git commit -s -m "your message"
```

The sign-off certifies you wrote the code (or have the right to submit it) under the Developer
Certificate of Origin reproduced below. Use a **real name and a reachable email** — a sign-off you
can't stand behind doesn't satisfy the DCO.

**2. By contributing, you agree to the CLA.**
Submitting a signed-off contribution (e.g. opening a pull request) constitutes your agreement to
[CLA.md](CLA.md). There's no separate signing ceremony.

## How to contribute

- Keep each change a focused, reviewable unit — one logical change per pull request, in the spirit
  of the project's `apply-*.sh` discipline (small, self-contained, reversible).
- If your change ships as a patch script, keep it idempotent and plan-by-default, and run its
  dry-run / `ast.parse` checks before submitting.
- Match the existing code style; explain *what* changed and *why* in the PR.
- Don't introduce a dependency on non-FOSS tooling without flagging it; the project defaults to
  FOSS (MIT/Apache for dependencies, AGPL for Xorics itself).

## Keeping closed code separate

Xorics is AGPL. If you maintain proprietary software, it may *use* Xorics as a separate program
(running it and consuming its output) without becoming subject to the AGPL — but it must not
`import` Xorics modules or embed Xorics source. Keep closed code in its own repository, never in
this tree. See the project's `LICENSE-EXCEPTION` for the output exception.

---

## Developer Certificate of Origin 1.1

By making a contribution to this project, I certify that:

```
Developer Certificate of Origin
Version 1.1

Copyright (C) 2004, 2006 The Linux Foundation and its contributors.

Everyone is permitted to copy and distribute verbatim copies of this
license document, but changing it is not allowed.


Developer's Certificate of Origin 1.1

By making a contribution to this project, I certify that:

(a) The contribution was created in whole or in part by me and I
    have the right to submit it under the open source license
    indicated in the file; or

(b) The contribution is based upon previous work that, to the best
    of my knowledge, is covered under an appropriate open source
    license and I have the right under that license to submit that
    work with modifications, whether created in whole or in part
    by me, under the same open source license (unless I am
    permitted to submit under a different license), as indicated
    in the file; or

(c) The contribution was provided directly to me by some other
    person who certified (a), (b) or (c) and I have not modified
    it.

(d) I understand and agree that this project and the contribution
    are public and that a record of the contribution (including all
    personal information I submit with it, including my sign-off) is
    maintained indefinitely and may be redistributed consistent with
    this project or the open source license(s) involved.
```

Canonical source: <https://developercertificate.org/>

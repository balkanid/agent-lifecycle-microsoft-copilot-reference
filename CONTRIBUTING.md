# Contributing

This is a reference implementation. The most useful contributions correct something that
is wrong, rather than adding surface area.

## Especially welcome

- Corrections to claims about Microsoft APIs, provider resources or CLI behaviour.
  Microsoft's Copilot surface moves quickly, and parts of this repository will go stale.
  Please include a Microsoft Learn link.
- Gate implementations for other IGA platforms, following the shape described in
  [`docs/SAILPOINT.md`](docs/SAILPOINT.md#adapting-this-to-another-platform).
- Failure modes you hit in a real tenant that are not in
  [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md).

## Less likely to be merged

- Additional Copilot surfaces or cloud platforms. The pattern generalises; the
  repository does not need to.
- Dependencies. The scripts are standard library only by design, so that reviewing them
  is quick and adopting them requires no supply-chain assessment.
- Production hardening such as remote state backends or secret managers. Those are
  correctly the adopter's choices, and documenting them is more useful than picking one.

## Before opening a pull request

```bash
python3 scripts/verify_imports.py
python3 -m compileall -q scripts
ruff check scripts
terraform fmt -check -recursive terraform
bash -n scripts/terraform-local.sh
```

CI runs all of these plus `terraform validate` and manifest shape checks.

## Ground rules

- No secrets, tenant ids, environment ids, organisation URLs or employee emails in a
  diff. This repository is public.
- Every new environment variable is documented in `env.example`.
- Every claim about a Microsoft API carries a Learn link, in the code comment or the
  docs.
- Changes that alter the control flow should update
  [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) in the same pull request.

## Reporting problems

Bugs and documentation fixes:
[GitHub Issues](https://github.com/balkanid/agent-lifecycle-copilot-reference/issues).

Security findings: [SECURITY.md](SECURITY.md). Do not use a public issue.

Questions about the BalkanID platform rather than this repository: your BalkanID contact,
or [balkan.id](https://balkan.id).

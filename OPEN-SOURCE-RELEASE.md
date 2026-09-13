# Open-sourcing Resnik

Maintainer checklist for the first public release. Open source starts at a
**public GitHub tag**, not at PyPI.

As of this writing the tree is already a git repo on `main`, licensed
GPL-3.0-or-later (`LICENSE.md`), with README install/test docs and remote
`https://github.com/bitWarrior/resnik.git`. That URL currently 404s for an
anonymous visitor (private, or the GitHub repo is not created yet). The work
below is identity, legal, hygiene, then flipping visibility.

## Minimum path (public this week)

1. Pick one GitHub home (`bitWarrior/resnik` or an org) and fix URLs/email.
2. Untrack `__pycache__` + `coverage.xml`; gitignore `.claude/`.
3. ~~Commit the ESP32-C3 / `TargetSpec` work~~ — done; `main` is clean.
4. Write `CODE_OF_CONDUCT.md`, `SECURITY.md`, `CHANGELOG.md`
   (`CONTRIBUTING.md` is done).
5. Decide on CI: add the pytest workflow, or ship without one (the docs no
   longer claim it exists).
6. Note HAL/S PDF provenance (`docs/THIRD_PARTY.md` or a README note).
7. Flip the GitHub repo to **public**, tag `v1.0.0`.

## 1. Decide identity

Pick one GitHub home and one contact, then use them everywhere. Three names
are in flight today:

| Place | What it says |
| ----- | ------------ |
| `git remote` / README clone URL | `github.com/bitWarrior/resnik` |
| `pyproject.toml`, CLI about text | `github.com/resnik-compiler/resnik` |
| Generated Rust header (`codegen.py`) | `github.com/user/resnik` |
| Package author email (`pyproject.toml`) | `resnik@example.com` |
| Git commits | `contact.bitWarrior@proton.me` |

Decide:

- GitHub user or org (`bitWarrior` vs a `resnik-compiler` org).
- Public contact email (the Proton address is fine).
- Copyright display name (`Steven Doran` vs “Resnik Compiler Project”).

Then replace the placeholders in:

- `README.md`
- `pyproject.toml` (`authors`, `[project.urls]`)
- `src/resnik/cli.py`
- `src/resnik/backend/codegen.py`

PyPI and Read the Docs URLs can stay unset until those sites exist. Do not
leave `resnik.readthedocs.io` in `pyproject.toml` if the project is not there.

## 2. Legal

### License

`LICENSE.md` is a full GNU GPLv3. Keep GPL if copyleft is the intent
(downstream firmware that *ships the compiler* is a different question from
shipping *generated* Rust). Switching to MIT/Apache so companies can embed the
compiler in proprietary tools is a license **change**, not a release step —
decide before the first public tag.

GPLv3 also asks you to:

- Put a short copyright + GPL pointer at the top of source files (none of the
  `.py` files have this yet).
- Name a copyright holder and year, for example
  `Copyright (C) 2025–2026 Steven Doran`.
- Get a copyright disclaimer from an employer or school if they might claim
  the work.

See “How to Apply These Terms” at the end of `LICENSE.md`.

### HAL/S PDFs

These are the only tree files that can create a third-party copyright issue:

| File | Size | Origin on the title page |
| ---- | ---- | ------------------------ |
| `docs/HAL-S-Language-Specification/HAL-S-Language-Specification.pdf` | ~21 MB | NASA-CR-160910 / Intermetrics HAL/S Language Specification |
| `docs/HAL-S-Programmers-Guide/HAL-S-Language-Programmers-Guide.pdf` | ~2.7 MB | USA003088, Space Flight Operations Contract NAS9-20000 (November 2005) |

US government works are often public domain; **contractor manuals are not
automatically**. Safer options:

- Link to [NASA NTRS](https://ntrs.nasa.gov/) instead of vendoring the PDFs, or
- Keep the PDFs and add `docs/THIRD_PARTY.md` stating origin and that Resnik
  claims no copyright on NASA/USA text.

GitHub accepts 21 MB blobs; Git LFS is optional. Original Resnik `.hal`
examples and the shuttle-history markdown under
`docs/Space_Shuttle_Flight_Software_History/` are yours to license with the
rest of the tree.

This is not legal advice. If the repo will live under an employer GitHub org,
have them look at the PDFs before you make the repository public.

## 3. Repo hygiene

Do this before the first public push.

### Untrack generated / local files

These are in git today and should not ship:

- `coverage.xml`
- `tests/**/__pycache__/*.pyc`
- empty `CODE_OF_CONDUCT.md` (fill it; do not leave a zero-byte file —
  `CONTRIBUTING.md` has since been written)

`resnik-venv/` is already in `.gitignore`. Keep it that way.

`.claude/` (local agent permissions) is **not** ignored and is not currently
tracked. Add it to `.gitignore` so it never is:

```gitignore
.claude/
```

Then:

```bash
git rm -r --cached coverage.xml
git rm -r --cached tests/__pycache__ \
  tests/test_backend/__pycache__ \
  tests/test_lexer/__pycache__ \
  tests/test_parser/__pycache__
git add .gitignore
```

### Clean `main`

Done — the ESP32-C3 / `TargetSpec` work (and everything since: the no_std
roadmap items, the cyclic executive, the IDE) is committed and pushed;
keep `main` a coherent snapshot before the public flip.

### Last scan

```bash
git grep -I -E 'api[_-]?key|SECRET|password|BEGIN (RSA |OPENSSH )?PRIVATE'
git grep -n '/home/sdoran'
git ls-files | grep -E 'venv|__pycache__|\.pyc$|coverage.xml|\.claude'
```

## 4. Community files

| File | Status | Purpose |
| ---- | ------ | ------- |
| `LICENSE.md` | Present (GPLv3) | License text |
| `CODE_OF_CONDUCT.md` | Empty | Contributor Covenant (or any CoC you will enforce) |
| `CONTRIBUTING.md` | **Done** | Setup, project layout, and all four test layers |
| `SECURITY.md` | Missing | Where to report vulnerabilities (email is enough) |
| `CHANGELOG.md` | Missing | First entry for the tag you are about to cut (`pyproject.toml` already links this path) |

### CI honesty

The docs no longer claim `.pre-commit-config.yaml` or `.github/workflows/`
exist (that stale project-tree listing has been fixed). The remaining
decision is whether to add a minimal pytest workflow before going public.

A one-job Actions file is enough for v1:

```yaml
# .github/workflows/test.yml
name: test
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.9", "3.12"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - run: pip install -e ".[test]"
      - run: python -m pytest
```

Do **not** run `./tests/regression/run_regression.sh` on CI unless the runner
has `rustc` and you either drop the script’s PATH sanitization or install
Cargo in the job. The regression script also requires a git root
(`git rev-parse --show-toplevel`).

## 5. Honest version and status

The package is already **1.0.0** (`pyproject.toml`, `src/resnik/__init__.py`).
That is fine if it means “usable compiler,” but say so plainly in the README:

- Hosted `std` path works (lexer, parser, Rust backend, regression).
- `--target cortex-m` is a **stub** (crate shape, semihosting WRITE; a
  SCHEDULE program additionally needs a `hal_exec_now_us` shim).
- `--target esp32-c3` is the first silicon profile; `VECTOR`/`MATRIX`,
  `COMPOOL` and `SCHEDULE`/`TASK` (cyclic executive) all lower — the
  remaining `compile_error!` gaps are `WAIT`, `READ` and `CHARACTER`
  variables.
- Many Appendix B keywords are still future work (already listed in the
  README).

If you would rather not imply “production 1.0,” retag as `0.1.0` **before**
PyPI. After a public `1.0.0`, dropping to `0.x` is messy.

## 6. Make GitHub public

Local `origin` is `bitWarrior/resnik`.

If the repo exists and is private:

1. GitHub → Settings → General → Danger Zone → **Change repository visibility
   → Public**.
2. Confirm the license picker sees GPLv3 (`LICENSE.md` usually works; a
   `LICENSE` symlink is optional).
3. About box: short description, topics (`hal-s`, `compiler`, `nasa`, `rust`,
   `python`, `embedded`, `esp32`).
4. Enable Issues (and Discussions if you want Q&A).
5. Default branch is already `main`.
6. Protect `main` once CI exists: require the pytest job.

If the remote does not exist yet:

```bash
gh repo create bitWarrior/resnik --public --source=. --remote=origin
git push -u origin main
```

## 7. Cut a release

From a clean `main`, after identity/hygiene/docs above:

```bash
python -m pytest
./tests/regression/run_regression.sh   # needs rustc
git tag -a v1.0.0 -m "Resnik 1.0.0"
git push origin main --tags
```

Then GitHub → Releases → create `v1.0.0` (Generate release notes is fine).
That tag is the artifact people cite.

## 8. Optional after the repo is public

Not required to be open source; these are distribution.

- **PyPI.** `pip install resnik` is the real on-ramp. Check the name is free
  (`pip index versions resnik`). If taken, use `resnik-compiler` or
  `hals-resnik`. GitHub Actions trusted publishing is the current default.
  Skip PyPI on day one if you want.
- **Read the Docs / MkDocs.** `pyproject.toml` lists a `docs` extra and a
  Read the Docs URL. Stand it up, or drop the URL.
- **DCO vs CLA.** For a one-maintainer GPL project, a Developer Certificate of
  Origin (`Signed-off-by`) in `CONTRIBUTING.md` is enough. A CLA is overhead
  you do not need yet.

## Related docs in this tree

- [README.md](README.md) — install, `--target`, testing
- [LICENSE.md](LICENSE.md) — GPLv3
- [docs/Resnik Users Guide/RESNIK_USER_GUIDE.md](docs/Resnik%20Users%20Guide/RESNIK_USER_GUIDE.md)
- [docs/Resnik Requirements/resnik-compiler-requirements.md](docs/Resnik%20Requirements/resnik-compiler-requirements.md)

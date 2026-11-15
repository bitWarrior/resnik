# Third-party material

Everything in this repository is original to the Resnik project and licensed
GPL-3.0-or-later (see [LICENSE.md](../LICENSE.md)), **except** the documents
listed here. They are redistributed unmodified, for reference only, and are
not covered by the Resnik license. Resnik claims no copyright in them.

| File | Document | Origin |
| ---- | -------- | ------ |
| `docs/HAL-S-Language-Specification/HAL-S-Language-Specification.pdf` | *HAL/S Language Specification* | NASA contractor report NASA-CR-160910, prepared by Intermetrics, Inc. for NASA |
| `docs/HAL-S-Programmers-Guide/HAL-S-Language-Programmers-Guide.pdf` | *HAL/S Programmer's Guide*, USA003088 (November 2005) | United Space Alliance, prepared under NASA Space Flight Operations Contract NAS9-20000 |

Both documents describe the HAL/S language that Resnik implements and are
publicly distributed by NASA; search the
[NASA Technical Reports Server](https://ntrs.nasa.gov/) for the report
numbers above to obtain them from the source.

Rights in these documents remain with their respective owners. If you are a
rights holder and want a document removed from this repository, open an
issue or write to <contact.bitWarrior@proton.me> and it will be replaced
with a link.

## Software dependencies

Resnik's runtime dependencies (`rply`, `click`, `rich`) are installed from
PyPI and are not vendored in this repository; each is distributed under its
own license. Rust crates named in generated Cargo projects are likewise
fetched from crates.io at build time.

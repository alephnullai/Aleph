# Contributing to Aleph

Thanks for your interest in Aleph — a universal semantic compression layer for
LLMs. Aleph is **free and open source under Apache-2.0**; contributions are
welcome from everyone.

## Where help is most valuable

- **More languages** — Java, Ruby, Swift, Kotlin parsers. We have the
  tree-sitter grammars; what's needed is salience policies for each.
- **MCP host testing** — verify Aleph on Cursor, Windsurf, VS Code, and Cline.
  The configs are generated but only Claude Code is validated end-to-end. Tell
  us what breaks.
- **Benchmark contributions** — run `aleph build` on a large open-source repo
  and share the token-count line from `.aleph/project.aleph.map`.
- **Bug reports with `.aleph/` output attached** — the compiled artifacts make
  most issues reproducible and tractable.

## Before you open a PR

**Open an issue first** to coordinate — it saves duplicated effort and lets us
point you at the right seam.

Some areas are the subject of pending patent applications (see [NOTICE](NOTICE))
and need discussion before modification:

- salience scoring — `src/aleph/link/project_salience.py`
- body-pruning policy — `src/aleph/compress/policies.py`

The Apache-2.0 patent grant (Section 3) covers these methods for all users; the
"discuss first" ask is about keeping the design coherent, not about permission.

## Development setup

```bash
git clone https://github.com/alephnullai/Aleph.git
cd Aleph
pip install -e ".[dev]"
```

Optional: install the `fastembed` extra to run the semantic-index tests
(they download a ~130 MB ONNX model on first run and are skipped otherwise).

## Tests

```bash
pytest tests/ -q
```

All tests should pass before you open a PR. Semantic tests skip automatically
when `fastembed` is not installed — CI runs on the text-only artifacts, so keep
new tests green in that mode too.

## Coding conventions

- Match the style and structure of the surrounding code.
- Prefer small, focused PRs with a clear description of the behavior change.
- Add or update tests for any behavior you change.
- Keep public-facing docs (`README.public.md`, `CONSUMER_GUIDE.md`) in sync when
  you change user-visible behavior.

## Symbol IDs and reproducibility

Aleph's symbol IDs are content-addressed and reformat-invariant. If you touch
the ID or hashing logic, include a portability test (see
`tests/property/test_id_portability.py`) so IDs stay deterministic across
machines and checkouts.

## License

By contributing, you agree that your contributions are licensed under the
Apache License 2.0, the same license as the project.

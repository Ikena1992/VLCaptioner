# Contributing to VLCaptioner

Thanks for helping improve VLCaptioner. Small, focused changes are easiest to
review. For a large feature or workflow change, open an issue describing the
problem and proposed behavior first.

## Development setup

Follow the [installation guide](docs/setup.md), copy `config.example.txt` to
`config.txt`, and use the Python environment in `data/env`. Run commands from
the repository root. Keep credentials in your local configuration.

The GUI entry point is `data/GUI.pyw`; the command-line entry point is
`data/pipeline_runner.py`. Both use the stages defined in `data/pipeline.py`.
See the [architecture guide](docs/development.md) for a map of the code.

## Making a change

1. Create a branch describing the change.
2. Keep the change focused and follow nearby code conventions.
3. Update documentation when behavior, options, or setup requirements change.
4. Run `python scripts/check_repository.py` and relevant local checks.
5. Open a pull request explaining the problem, resulting behavior, and validation.

For GUI work, inspect the affected window in both light and dark themes and
check that controls remain accessible at smaller window sizes. For pipeline
work, use a small copied dataset and inspect images and companion files in
`done/` and `captionReview/`. Verify interrupted work can be resumed when relevant.

## Local tests and data

`tests/`, `test/`, and `.test-tmp/` must remain local and outside Git. You may
create and run tests there, but never stage or commit their contents, add ignore
exceptions, or use `git add -f` to include them. Before committing, check both:

```console
git ls-files -- tests test .test-tmp
git diff --cached --name-only -- tests test .test-tmp
```

Both commands should produce no output. If a test file is already tracked,
remove it from the index with `git rm --cached` while preserving the local file.
Do not commit datasets, model downloads, writable caches, logs, or `config.txt`.
The small bundled cache seeds are intentional repository assets.

## Reporting bugs

Use the bug report template. Include your OS, Python version, local or remote
Ollama setup, reproduction steps, expected behavior, and relevant log excerpts.
Remove API keys, private addresses, personal paths, and private image content
before sharing logs or screenshots. Report suspected vulnerabilities using the
[security policy](SECURITY.md).

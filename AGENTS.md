# Repository instructions

## Local test folders

The user requires `tests/`, `test/`, and `.test-tmp/` to stay local and outside
Git. You may create and run tests locally, but never stage or commit these
folders or files within them. Do not add ignore exceptions or use `git add -f`
to include them. Keep the folder-level rules in `.gitignore`.

Before committing or pushing, verify that neither `git ls-files` nor the staged
changes contain files under these folders. If a test file is already tracked,
remove it from the index with `git rm --cached` while preserving the local file.

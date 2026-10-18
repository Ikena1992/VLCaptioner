# Development guide

[Back to README](../README.md) · [Contributing](../.github/CONTRIBUTING.md)

VLCaptioner is a Python desktop application with a shared command-line pipeline.
The `data/` directory contains application code as well as ignored runtime folders.

## Code map

| Area | Entry points and modules |
| --- | --- |
| GUI | `data/GUI.pyw` launches `data/gui.py`; platform launchers are `GUI.cmd` and `GUI.sh`. |
| Pipeline | `data/pipeline.py` defines stages; `data/pipeline_runner.py` runs them from the command line. |
| Downloads | `data/downloader_gui.py`, `data/booru_downloader.py`, and the Danbooru/Gelbooru clients. |
| Dataset tools | `data/dataset_manager.py`, `data/copy_move_images_GUI.py`, and the filesystem operations in `data/dataset_transfer.py`. |
| Inference | `data/ollama_client.py`, Torii/refiner generation modules, and `data/tag_images_with_wd14.py`. |
| Outputs | `data/check_caption_quality.py`, `data/finalize_caption_dataset.py`, and `data/refiner_json.py`. |
| Queue and progress | `data/caption_queue.py` and `data/caption_progress.py`. |
| Configuration | `data/runtime_config.py` reads local `config.txt`; `config.example.txt` documents defaults. |
| Shared file policies | `data/dataset_files.py` defines image formats, companion suffixes, grouping, conversion inputs, and training-text exports. |
| Shared file I/O | `data/file_io.py` provides streaming hashes and atomic writes/publication. |

Import format and companion policies from `dataset_files` instead of maintaining
another extension list. Compound `.review.json` files belong to their image's
bundle; an exact image basename takes precedence when it ends in `.review`.
Finalization removes intermediates, while copy/move and review tools retain
them with the image. Source-tag scans exclude generated caption variants such
as `.long.txt`.

Use `file_io.file_hash` for content-based file identities; its default SHA-256
format preserves existing cache keys. Download verification still computes the
site's MD5 while streaming, and structured request-cache keys keep their existing
input format. Use `atomic_write_text`, `atomic_write_json`, or `atomic_output`
for complete file writes; `atomic_path` supports encoders such as Pillow and
ZIP writers. All stage beside the destination and publish after successful
completion. Pass `overwrite=False` for create-only files and `fsync=True` when
disk synchronization is required. `publish_file` handles already-staged files.
Multi-file operations still need their bundle journals and recovery logic.
The shared Danbooru client owns rate limiting and HTTP 429 retries. Callers pass
their request interval and retry budget instead of adding another retry loop.

## Local files

Images enter through `images/`; completed sets go to `done/` and flagged sets to
`captionReview/`. Numbered `images` and `done` folders are local datasets too.
`data/env/`, `data/models/`, and writable `data/caches/` contents are local runtime
assets. The explicitly allowed SQLite seed files are versioned to initialize caches.

Keep tests in the local-only folders described in [CONTRIBUTING.md](../.github/CONTRIBUTING.md).

## Automated checks

Run from the repository root with Python 3.12:

```console
python scripts/check_repository.py
```

The check parses application Python files without importing them, checks relative
Markdown and HTML file links, and rejects tracked local-only data or credentials.
It uses the Python standard library and Git, so it needs no models, API keys, GPU,
or installed application dependencies. GitHub Actions runs the same check.

These checks do not validate model output, GUI behavior, dependency installation,
external services, or runtime compatibility. Validate the affected workflow locally
and describe the result in your pull request.

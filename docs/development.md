# Development guide

[Back to README](../README.md) · [Contributing](../CONTRIBUTING.md)

VLCaptioner is a Python desktop application with a shared command-line pipeline.
The `data/` directory contains application code as well as ignored runtime folders.

## Code map

| Area | Entry points and modules |
| --- | --- |
| GUI | `data/GUI.pyw` launches `data/gui.py`; platform launchers are `GUI.cmd` and `GUI.sh`. |
| Pipeline | `data/pipeline.py` defines stages; `data/pipeline_runner.py` runs them from the command line. |
| Downloads | `data/downloader_gui.py`, `data/booru_downloader.py`, and the Danbooru/Gelbooru clients. |
| Dataset tools | `data/dataset_manager.py` and `data/copy_move_images_GUI.py`. |
| Inference | `data/ollama_client.py`, Torii/refiner generation modules, and `data/tag_images_with_wd14.py`. |
| Outputs | `data/check_caption_quality.py`, `data/finalize_caption_dataset.py`, and `data/refiner_json.py`. |
| Queue and progress | `data/caption_queue.py` and `data/caption_progress.py`. |
| Configuration | `data/runtime_config.py` reads local `config.txt`; `config.example.txt` documents defaults. |

## Local files

Images enter through `images/`; completed sets go to `done/` and flagged sets to
`captionReview/`. Numbered `images` and `done` folders are local datasets too.
`data/env/`, `data/models/`, and writable `data/caches/` contents are local runtime
assets. The explicitly allowed SQLite seed files are versioned to initialize caches.

Keep tests in the local-only folders described in [CONTRIBUTING.md](../CONTRIBUTING.md).

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

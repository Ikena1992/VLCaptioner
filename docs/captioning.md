# Captioning reference

[Back to README](../README.md)

## Captioning images

The conversion step saves images as
WebP, resizes images to fit within 3000 × 3000 pixels, and flattens transparency
onto white. JPEG inputs may use lossy WebP compression to reduce file size.
Existing WebP files are left unchanged by this conversion step.
Supported input extensions are `.jpg`, `.jpeg`, `.png`, `.bmp`, `.tif`, `.tiff`,
`.avif`, `.gif`, and `.webp`. Animated images are not preserved as animations.

Work from copies if you need to retain the source files. Successful conversion
replaces each non-WebP input in `images/` with a WebP file. Successful
finalization removes the processed image and source `.txt` from `images/` after
placing them in `done/`.

The **Caption images** tab has three boxes, from top to bottom:
**1. Check setup**, **2. Download images (optional)**, and **3. Add images**.

1. Start the GUI with `.\GUI.cmd` on Windows or `bash GUI.sh` on Linux.
2. Review **1. Check setup** for configuration, API access, and Ollama readiness.
   Missing Ollama models are reported and downloaded when captioning starts.
3. Use **2. Download images (optional)** to download originals and matching tags,
   or **3. Add images** to open `images/` and add your own files. Place images
   directly inside `images/`; the captioner does not process subfolders.
   **Copy / move by tags** opens the existing image-transfer tool.
4. For manually added images, add any existing tags as matching text files,
   such as `sample.webp` and
   `sample.txt`. A simple `sample.txt` might contain
   `1girl, blue_hair, outdoors`. MD5-named images support automatic Danbooru
   lookups. For a first run, provide a `.txt` file so you can test captioning
   without relying on local AnimeTimm model access.
5. Select **Check again** after adding or downloading images. Choose your settings
   in the **Options** tab, then click **Start captioning**.
6. Watch the current stage and open completed files or files needing review
   from the GUI. The **Activity** log is always visible. Use **Light / dark** to switch themes.

Rename images with the same base filename before starting. Conversion and
tagging skip conflicting images to protect existing files.

### Automatic Danbooru tags

For images named after their original MD5 hash, such as
`2e3eee4f9c2183d3c977144050ff74d8.webp`, the pipeline searches Danbooru and
downloads the matching tags and metadata. Image conversion preserves the
filename's hash, which is used for the Danbooru lookup.

Existing `.txt` tag files are kept and their Danbooru lookup is skipped unless
**Replace existing tags with fresh Danbooru or Gelbooru tags** is enabled. If an
image has no matching Danbooru post or uses another filename, its tags can come
from a matching `.txt` file or from the local tagger. Images still without a
`.txt` file are tagged with AnimeTimm, or SmilingWolf if AnimeTimm access is denied.
Blank or metadata-only `.txt` files also receive full tagging, preserving their
metadata, even when adding high-confidence tags to existing files is disabled.

This step fetches tags for images already in `images/`. It does not download
image files.

### GUI options

The captioner automatically saves the options in **Options** and **Tag options**,
along with the light/dark theme, and restores them on the next launch. Settings
are stored locally in `data/caches/captioner_settings.json`. On the first launch,
refiner thinking and JSON creation use the defaults from `config.txt`.
Closing an unchanged window preserves newer choices saved by another window.
Unreadable or invalid settings fall back to defaults without replacing the saved
file just because the window closes.

| Option | On | Off (default) |
| --- | --- | --- |
| **Add missing tags to existing tag files** | Appends missing high-confidence tags. | Leaves existing tag files unchanged. Images without tags are tagged either way. |
| **Add lighting tags (Booru Lighting Tagger)** | Appends lighting and tonal-key tags before captioning, preserving existing tags and metadata. | Skips the lighting model. |
| **Replace existing tags with fresh Danbooru or Gelbooru tags** | Replaces matching source tags with tags from the first matching post. | Keeps existing tag files and skips their lookup. |
| **Bypass refinement caption cache** | Bypasses long, short and JSON caches. With JSON creation enabled, regenerates existing JSON files too. Torii reports and Parquet inputs are reused. | Reuses cached captions when available and keeps existing JSON files. Existing long/short files follow the option below. |
| **Overwrite existing .long and .short files** | Replaces both caption files even when they already exist; cache use follows the setting above. | Keeps existing `.long` files and skips images that have both caption files. |
| **Add upload year to .tag files** | Adds `year YYYY` when a post date is available. | Omits the year tag. |
| **Include copyright and series tags in .tag files** | Includes them in `.tag` and `.combined`. | Omits them there; source TXT and CSV retain them. |

The pipeline converts images, fetches Danbooru tags, and runs local AnimeTimm
tagging where needed. It looks up tag and character explanations and creates
the CSV metadata needed for captioning. Eligible clean, single-character images also
supply generated character references.

Torii then produces visual analysis, which is normalized and passed to the
refinement model. The final stage creates long and short captions and cleans the
tags. Each asset set is checked before publication: passing sets go straight to
`done/`, and flagged sets go straight to `captionReview/`. Existing caches are
reused when available.

The **Steps** tab shows how the selected options affect each step. During a
run it reports Pending, Running, Done, Skipped, Failed, or Stopped. Work is
checked immediately before each step, so tags fetched earlier in the run can
make local tagging unnecessary. Steps after a failure are marked Not run.
The metadata step creates a CSV from TXT tags only when no CSV exists.
Existing CSVs are skipped, even if the TXT tags have changed. Post metadata
is restored from the TXT metadata record when creating a new CSV. Unresolved
tag categories skip the affected image for the current run so incomplete
metadata can be fixed and retried. Per-image conversion, tagging, metadata,
captioning, and finalization failures are logged; remaining images continue.
Skipped images stay in `images/`, and a new run retries them. The GUI reports
the skipped count on completion. Missing required models, invalid setup, or
other run-wide failures still stop the pipeline.

### Optional lighting tags

Enable **Tag options → Add lighting tags (Booru Lighting Tagger)** to supplement
the regular tags with lighting details such as `backlighting`, `rim light`,
and `warm lighting`, plus a tonal key when detected. This option is independent
of **Add missing tags to existing tag files** and also processes images that
already have source tags. It defaults to off and is saved with the other GUI
options and queued batch settings.

The [Booru Lighting Tagger](https://huggingface.co/MarioTheKnight/booru-lighting-tagger)
runs locally with its v1.0.2 three-seed ensemble and default per-label thresholds.
The first use downloads about 2.4 GB of lighting weights plus the base model
(about 1.2 GB); later runs reuse the cache. It uses CUDA when available and CPU
otherwise, and adds processing time. Its scores are ratings from 0 to 10, so
the regular tagger's 0.91 confidence threshold does not apply.

The lighting stage appends missing tags to source TXT files and updates the
general tags in existing CSV metadata without replacing other fields. Known
lighting labels are categorized locally, including labels absent from Danbooru.
Lighting tags then participate in tag generation and caption prompts. Existing
completed captions and Torii reports still follow their usual reuse rules.
To regenerate existing long/short captions, enable caption overwrite and cache
bypass as described below.

For command-line runs, use:

```sh
python data/pipeline_runner.py --lighting-tags
```

### Stopping and resuming

Click **Stop** to interrupt a run. Completed assets remain in `done/`.
Fix any reported issue and start the pipeline again to process remaining
images in `images/`. Existing results and caches are reused where possible.

To regenerate captions for a completed image, copy the image and its source
`.txt` tags back into `images/`. Enable **Overwrite existing .long and .short
files** to replace present caption files. Also enable **Bypass refinement caption
cache** if you want fresh model output instead of a matching cached result.

After an interrupted save, `.finalize-*` folders in `done/` hold the recovery
information needed by the next run. Removing them prevents automatic recovery.
Recovery uses the original batch folder and file hashes recorded in the journal.
If source files were edited after publication, the whole source set is retained
for retry. Changed destination files stop cleanup and preserve the originals.
Older journals without source identity preserve the originals and log a warning.
Journal files are stored separately from image sidecars, so layouts named
`transaction.json` or `committed.json` cannot overwrite recovery information.

Torii reports are saved and cached only after both analysis responses finish
without truncation. Empty or truncated responses leave the image for a later
run; other images continue. Report files are published atomically so interrupted
writes cannot leave a partial report for the next run to reuse.

## Output files

Each completed image has these companion files in `done/`:

| File | Contents |
| --- | --- |
| `NAME.webp` | Processed image. |
| `NAME.short` | Compact natural-language caption. |
| `NAME.long` | Detailed natural-language caption. |
| `NAME.tag` | Cleaned and ordered Danbooru-style tags. |
| `NAME.combined` | Tag caption followed by a dot and the short caption. |
| `NAME.txt` | Comma-separated source tags, including fetched meta tags. |

The `.tag` generator intentionally drops most booru meta tags. It includes
only medium-related meta tags, such as `watercolor (medium)` and
`traditional media`; resolution tags such as `highres` are not needed for
training. The fetched meta tags remain available in the CSV until finalization.
Fetched `.txt` files retain the complete meta list as plain tags. CSV files
store post ID, source, score, rating, date, and tag categories from the booru
response. If a CSV is missing, it can be rebuilt from the `.txt` tags, but
post metadata absent from the text cannot be recovered without another lookup.

Generated captions can contain errors and benefit from review before training.

For example, an input pair `sample.webp` and `sample.txt` containing
`1girl, blue_hair, outdoors` may produce files like these. The actual text
depends on the image and models:

```text
done/sample.short     A blue-haired character stands outdoors.
done/sample.long      A character with blue hair stands in an outdoor scene...
done/sample.tag       1girl, blue hair, outdoors
done/sample.combined  1girl, blue hair, outdoors. A blue-haired character stands outdoors.
done/sample.txt       1girl, blue_hair, outdoors
done/sample.webp      The processed image
```

### Caption quality review

During finalization, the GUI and command-line pipeline check caption sets before
placing them in `done/`. Missing or empty `.short`/`.long` captions, leaked control text such
as `END_CAPTION` or `<think>`, or more than 250 words between sentence breaks in a long caption
trigger review. The image and its companion files are placed together in
`captionReview/`. The pipeline log shows each flagged image and the reason.
Suspicious but uncertain wording is not flagged.
Generated captions that fail validation receive one fresh corrective attempt
with changed sampling before going to review. This shares the retry budget with
server token-repeat errors. Invalid or truncated attempts are never cached.
Caption requests use a neutral repetition penalty: stronger penalties can force
the refiner into runaway synonym chains. Actual repetition remains checked by
the caption validator.
Torii reasoning blocks are removed before refinement, including inline, nested,
escaped, and unfinished `<think>` blocks; only the remaining report is supplied.
Run `python data/check_caption_quality.py --dry-run` to preview findings
without moving files.

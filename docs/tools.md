# Downloader and dataset tools

[Back to README](../README.md)

## Optional tools

### Image downloader

Download searches use a compact table suitable for thousands of rows. Select a
row and edit its source, search tags, and post limit in the fields below the table.
Use **Search queue** to manage searches and **Filters & captioning** to set the
shared blacklist and automatic caption batches. A target in done disables the
post limit because gap mode uses the target instead.
Changes are saved with the downloader settings. Use Ctrl or Shift to select
multiple rows, then **Remove selected** (or Delete) to remove them.

Download and Copy / move are owned windows of the captioning GUI. Opening either
tool again brings its existing window forward. Closing the captioning GUI stops
active work and waits for its tool windows to close. Launching a tool directly
also opens its owning captioning GUI.
Copy / move waits until downloads and captioning finish. Manual captioning also
waits for downloads to finish; automatic batch captioning can run alongside them.

To download and caption simultaneously, keep the captioning GUI open and choose
its caption options. In the downloader, enable **Caption automatically** and set
**Images per batch** (default: 50). Completed downloads appear in the captioning
GUI's **Queue** tab. Each batch runs through all steps while downloads continue.
The final smaller batch is queued when downloads finish or stop.

Settings and detailed progress stay in the captioning GUI. Settings are captured
when each batch starts and retained for retries. **Pause queue** lets the active
batch finish; **Stop** stops the active captioning batch. Use **Retry selected**
and **Resume queue** for stopped or failed batches. Cancelling a waiting batch returns its images and companion files to `images/`,
then removes its folder and queue record. Completed batches are also cleaned up. Queued requests survive closing the GUIs; interrupted active
batches appear as stopped when the captioning GUI reopens.
If the downloader exits unexpectedly, its completed images are recovered as a
stopped batch in Queue. Select that batch and retry it; partial downloads are
excluded. Queued captioning waits until the captioning setup check succeeds.

Start the captioning GUI with `GUI.cmd` on Windows or `bash GUI.sh` on Linux,
then select **Open image downloader** in **2. Download images (optional)**.
The downloader opens in a separate window with no viewer and does not require
Ollama. No separate downloader launcher is needed. On Windows, `GUI.cmd`
launches `data/GUI.pyw` using the installed Python environment.

1. Add a row for each search and choose **Danbooru** or **Gelbooru**.
2. Enter space-separated search tags, such as `blue_eyes long_hair`.
3. Set a positive **Post limit**. This is the number of posts checked, including
   blacklisted posts, images already in `images/` or `done/`, unavailable
   originals, unsupported files, and failed downloads. A limit of 100 can
   download fewer than 100 images.
4. Optionally enter a **Tag blacklist**, such as `tall_image watermark comic`.
   Use spaces, just like search tags. Keep underscores within tags:
   `tall_image` matches that tag; `tall image` does not. Any matching blacklist
   tag skips the image. Matching uses whole tags and ignores letter case and
   extra spaces. The blacklist applies to every row; leave it blank to allow
   all tags. Older saved comma-separated blacklists are converted on reopening.
5. Select **Start downloads**. Rows run sequentially. **Stop** cancels the queue;
   an active network request may need to return first. Closing the window
   waits for cancellation and cleanup.

Original image files are saved in `images/` as `<md5>.<original extension>`.
Each download also saves `<md5>.txt` and `<md5>.csv` using the captioner's
existing tag writer: categorized tags, readable spaces instead of underscores,
safety/quality tags, and CSV source metadata. Gelbooru categories use the shared
tag cache and API. Existing nonempty TXT files are preserved; skipped images
are not modified. CSV metadata is enriched using the same rules as the captioner,
preserving existing nonempty fields.

The downloader remembers all rows and the blacklist when starting downloads
or closing the window, and restores them on reopening. Settings are stored
locally in `data/caches/downloader_settings.json`; reopening never starts
downloads automatically.

The log shows search pages, post IDs, original filenames, tag-file preparation,
and reasons for skips (including the matching blacklist tags or existing MD5
images). It also explains download failures and when the post limit is reached.
Each row ends with downloaded, skipped, blacklisted, unavailable, and failed counts.

Matching MD5 image filenames anywhere in `images/` or `done/` (including subfolders
and converted images with a different extension) are skipped. Downloaded files
are verified against the site's MD5 before publication; partial files are removed.
Tag files are prepared separately before publication, so cancellation during tag
preparation does not leave TXT/CSV files without an image. Failed image publication
rolls back changes to tag files. The completion status reports errors explicitly.
Existing images are never overwritten. API credentials use the existing
`DANBOORU_LOGIN` / `DANBOORU_API_KEY` and `GELBOORU_USER_ID` /
`GELBOORU_API_KEY` settings in `config.txt`. Site search and account limits apply.

### Dataset manager

Click **Open dataset manager** in **2. Download images (optional)** to browse
the tags in your datasets. The folder list presets `done/` and `images/`, both
selected. Click folders to toggle inclusion, and use **Add folder…** to include
more datasets. Scanning starts only when you click **Scan selected**, and
**Cancel scan** stops it while keeping previous results. Each scanned folder
gets its own count column and can be chosen in the count scope filter.
**Save settings** remembers the folder list, selections and filters; settings
also save when scanning or closing. They stay local in
`data/caches/dataset_manager_settings.json`.
You can choose other folders, filter tags by
category and image count, preview matching images and TXT files, and export
the filtered statistics as CSV. Categories come from the local Danbooru and
Gelbooru caches; missing classifications appear as Unknown. Scanning does not
modify images, tag files, or caches. Reopening the manager focuses its existing
window, and closing the captioner closes the manager too.

Select one or more tags (Ctrl-click or Shift-click) and click **Download more…**
to add a pair of downloader rows for each tag: a Danbooru row with `order:score`
and a Gelbooru row with `sort:score`, with automatic captioning enabled.
Both rows share gap progress, so the second source fills only the remaining gap.
Set **Target in done** independently for each row to the desired total
number of matching images in `done/`, then start downloads. Gap filling uses
one positive tag per row and counts matching image/TXT pairs in `done/` and
`done1/` when available. Copies sharing an MD5 filename count only once.
It downloads the difference, ignoring the post limit; duplicates, blacklisted
posts, and failed downloads do not consume the gap. It stops when enough new
images have downloaded, search results run out, or you click Stop. Leave the
target blank for ordinary downloads. Captioning must finish successfully to
reach the target in `done/`; rerun gap filling after captioning if images failed
or went to review. `done1/` also remains an optional duplicate-check folder.

The combined dataset counts each image once, including when selected folders
overlap. Each dataset column counts membership in its own folder. Caption
variants such as `.long.txt` and `.short.txt`, comment lines, and VLTagger
metadata are excluded from tag statistics.

### Copy or move images by tags

The helper searches the selected file extension for tags (default `.txt`) and
keeps matching images and sidecar files together. Enter another extension, such
as `.tag` or `.combined`, to search those files instead.
Click **Copy / move by tags** in the captioning GUI's **3. Add images** box.

### Export captions as a ZIP

Run `scripts/2_createZipFile.bat` on Windows or
`bash scripts/6_createZipFile.sh` on Linux. This creates
`naturalLanguage_files.zip` from the `.short`, `.long`, `.tag`, `.combined`,
and `.txt` files in `done/`. Images are not included.

### Run without the GUI

Windows:

```powershell
.\data\env\Scripts\python.exe data/pipeline_runner.py --skip-wd14
```

Linux:

```bash
data/env/bin/python data/pipeline_runner.py --skip-wd14
```

`--skip-wd14` skips only the high-confidence missing-tag pass for images with
existing text files. Images still without text files after Danbooru are tagged
either way. Omit it to also update existing tag files. Use
`--overwrite-danbooru-txt` to refresh
source tags or `--overwrite-caption-cache` to bypass cached captions when
generating. Use `--overwrite-caption-files` to replace existing
`.long` and `.short` files.

Refinement cache matching uses the image contents, output kind, model, and prompt.
Moving or renaming an unchanged image does not invalidate its cached output.
Artist prefixes added to saved captions, line-ending differences, and extra blank
lines left by Torii thinking-header removal do not cause fresh generation.
Compatible entries from older cache keys remain readable without deleting them.
Changes to scene information, caption instructions, thinking mode, or the layout
coordinate recipe still require a matching entry or new generation. The log shows
`Cache hit`, `Cache miss`, or `Cache bypass` for each requested output.
Caption references preserve literal spacing, line breaks, Unicode spelling, and
visible words such as `END_CAPTION`. Cache hits also check the stored inputs so
older keys that discarded these differences cannot return a stale result.
With an explicit model name, the refiner checks model availability only when a
cache miss or bypass needs inference. Cached outputs can be restored without a
model check. `OLLAMA_MODEL=auto` still queries Ollama to identify the model.

Tag lookup searches Danbooru first, then Gelbooru when no matching post is found.
Both use the MD5 in the image filename. Gelbooru results retain tag categories,
rating, score, post ID, and original source URL; the CSV `source` is `gelbooru`.
The optional year tag uses the matched post's upload year and is omitted when
the source has no usable date. The pipeline does not generate aesthetic score
tags such as `score_1` through `score_9`; post scores only determine quality
labels such as `good quality`.

Quality thresholds depend on the post rating. For general and sensitive posts,
scores above 130 receive `masterpiece`; for questionable and explicit posts,
scores above 230 receive it. The remaining quality ranges are scaled in the
same proportion. `best quality` starts at 71 for general/sensitive posts and
125 for questionable/explicit posts; `good quality` starts at 36 and 63,
respectively. Unknown ratings use the questionable/explicit thresholds.
Existing TXT files are still skipped unless `--overwrite-danbooru-txt` is used.
The standalone refresh script also uses this fallback.
If Gelbooru requires authentication, add `GELBOORU_USER_ID` and `GELBOORU_API_KEY`
from your Gelbooru account options to `config.txt` (see `config.example.txt`).
API failures are reported and do not write partial Gelbooru metadata.
Gelbooru tag types persist between runs in
`data/caches/gelbooru_tag_categories.sqlite3`, using the existing SQLite cache
implementation. Only uncached types are requested; Danbooru's cache stays separate
because the sites can classify the same tag differently.
New installations copy the bundled `gelbooru_tag_categories.seed.sqlite3` into
the writable cache on first use. Later tag-type updates stay local.

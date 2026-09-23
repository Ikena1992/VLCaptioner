# VLCaptioner

VLCaptioner prepares image datasets with short captions, detailed captions, and
Danbooru-style tags. It uses ToriiGate to analyze images and a second Ollama
vision model to refine the captions. Finished images and text files are saved
in `done/`.

Ollama can run locally or on another computer on your network. Optional
AnimeTimm/WD14 tagging runs on the computer running VLCaptioner.

For a first run: [install the project](#setup), [copy and edit the
configuration](#configuration), put one image and a matching tag file in
`images/`, then [start the GUI](#captioning-images). Check the resulting files
in `done/` before processing a larger dataset.

## Setup

You need Python 3, Git, a Danbooru account and API key, and an
[Ollama](https://ollama.com/download) server with enough memory and disk space
for your chosen vision models. An NVIDIA GPU is strongly recommended.
Both installers install CUDA-enabled PyTorch locally, even if Ollama runs on
another computer. A local NVIDIA GPU speeds up AnimeTimm tagging. The tagger
can also run on CPU. The Ollama host must have enough memory for both selected
vision models and the configured context size.

If an image has no `.txt` tags after the Danbooru lookup, the pipeline runs
AnimeTimm/WD14 locally and requires access to its Hugging Face model.

Run the commands below from the VLCaptioner folder unless stated otherwise.

### Download

Install Git, then clone the project:

```console
git clone https://github.com/Ikena1992/VLCaptioner.git
cd VLCaptioner
```

The repository includes small optional tag and explanation cache seeds. On first
use, VLCaptioner copies them to writable local caches. Later cache updates
stay outside Git. Python dependencies, downloaded models, and image datasets
require additional disk space.

### Windows

Install Python with **Add Python to PATH** enabled and current NVIDIA drivers
if using an NVIDIA GPU. Run the installer:

```powershell
.\install.bat
```

The installer creates `data/env` and installs the Python dependencies.

### Linux

Install Python, its virtual-environment support, and Tk. On Debian or Ubuntu:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-tk git
bash scripts/4_install.sh
```

The installer creates `data/env` and may request administrator access to
install GUI support.

### Ollama

Install and start Ollama on the computer that will run the vision models.
For a local server, the default address is `http://localhost:11434`.

The default models are:

- Torii analysis: `hf.co/DraconicDragon/ToriiGate-0.5-GGUF:Q4_K_M`
- Caption refinement: `orcarouter/Qwen3.8-27B-Uncensored:q4_K_M`

VLCaptioner automatically checks whether the configured models are installed
and asks Ollama to pull any missing models. Manual model downloads are
optional. Download progress
appears in the pipeline log, and the first run can take longer while models
download. With a remote server, the downloads and model storage stay on that
server.

To download the models before starting a batch, run these commands on the
Ollama host:

```console
ollama pull hf.co/DraconicDragon/ToriiGate-0.5-GGUF:Q4_K_M
ollama pull orcarouter/Qwen3.8-27B-Uncensored:q4_K_M
```

The refinement model is configurable in `config.txt`. It must accept images
and fit within the Ollama host's available memory.

### Optional AnimeTimm access

To enable local tagging, accept the access terms on the
[AnimeTimm model page](https://huggingface.co/animetimm/convnextv2_huge.dbv4-full),
then log in using the installed environment.

Windows:

```powershell
.\data\env\Scripts\hf.exe auth login
```

Linux:

```bash
data/env/bin/hf auth login
```

Follow the login prompts. Images still without text tag files after the
Danbooru step are tagged by AnimeTimm, regardless of the GUI checkbox. In the
GUI, clear **Skip WD14 high-confidence missing-tag step** to also append
high-confidence WD14 tags to existing text files.

## Configuration

Copy `config.example.txt` to `config.txt`, then edit it. On Windows PowerShell,
run `Copy-Item config.example.txt config.txt`. On Linux, run
`cp config.example.txt config.txt`. Replace the placeholder credentials and
adjust the Ollama settings as needed:

```text
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=orcarouter/Qwen3.8-27B-Uncensored:q4_K_M
TORII_OLLAMA_MODEL=hf.co/DraconicDragon/ToriiGate-0.5-GGUF:Q4_K_M
OLLAMA_TIMEOUT_SECONDS=300
OLLAMA_CONTEXT_SIZE=65536

DANBOORU_LOGIN=your-login
DANBOORU_API_KEY=your-api-key
DANBOORU_USER_AGENT=VLCaptioner/1.0 (your-login)

# Optional Gelbooru fallback credentials
GELBOORU_USER_ID=
GELBOORU_API_KEY=
```

| Setting | Purpose |
| --- | --- |
| `OLLAMA_URL` | Address of the local or remote Ollama server. |
| `OLLAMA_MODEL` | Image-capable model used to refine captions. |
| `TORII_OLLAMA_MODEL` | ToriiGate model used for image analysis. |
| `OLLAMA_TIMEOUT_SECONDS` | Maximum wait for an Ollama request, in seconds. |
| `OLLAMA_CONTEXT_SIZE` | Refinement context window. Defaults to `65536`. High-resolution images may need more. |
| `DANBOORU_LOGIN`, `DANBOORU_API_KEY` | Credentials used for Danbooru metadata requests. |
| `DANBOORU_USER_AGENT` | Optional identifier for Danbooru requests. |
| `GELBOORU_USER_ID`, `GELBOORU_API_KEY` | Optional credentials for Gelbooru fallback requests. Obtain both from your Gelbooru account options; API access may require them. |

Gelbooru is searched when Danbooru has no matching post. Leave the Gelbooru
credentials blank for unauthenticated access, or fill in both when required.
Tag categories are cached automatically in
`data/caches/gelbooru_tag_categories.sqlite3`; no cache setting is needed.

Keep `config.txt` private: it contains your API keys. VLCaptioner reads these
settings from the file, not from environment variables.

### Ollama on another computer

Set `OLLAMA_URL` to the server's private address, for example
`http://192.168.1.50:11434`. Model downloads and vision inference run on that
server. VLCaptioner sends it the images and prompts.

Configure Ollama to listen on the network. For a manually started server:

Windows PowerShell:

```powershell
$env:OLLAMA_HOST = "0.0.0.0:11434"
ollama serve
```

Linux:

```bash
OLLAMA_HOST=0.0.0.0:11434 ollama serve
```

Stop any existing Ollama instance before starting another. If Ollama runs as a
service, set `OLLAMA_HOST` in its service environment and restart it.

Allow TCP port `11434` through the server's private-network firewall.
Keep access restricted to trusted computers. Do not expose the Ollama API
directly to the internet.

## Captioning images

The conversion step saves images as
WebP, resizes images to fit within 3000 × 3000 pixels, and flattens transparency
onto white. JPEG inputs may use lossy WebP compression to reduce file size.
Existing WebP files are left unchanged by this conversion step.
Supported input extensions are `.jpg`, `.jpeg`, `.png`, `.bmp`, `.tiff`, `.gif`,
and `.webp`. Animated images are not preserved as animations.

Work from copies if you need to retain the source files. Successful conversion
replaces each non-WebP input in `images/` with a WebP file. Successful
finalization removes the processed image and source `.txt` from `images/` after
placing them in `done/`.

1. Create an `images/` folder if needed and place your images directly inside
   it. Subfolders are not processed.
2. Add any existing tags as matching text files, such as `sample.webp` and
   `sample.txt`. A simple `sample.txt` might contain
   `1girl, blue_hair, outdoors`. MD5-named images support automatic Danbooru
   lookups. For a first run, provide a `.txt` file so you can test captioning
   without relying on local AnimeTimm model access.
3. Start the GUI with `.\GUI.bat` on Windows or `bash GUI.sh` on Linux.
4. Choose the options below and click **Start pipeline**.
5. Check the log for failures and collect completed assets from `done/`.

Files with the same base filename share an output name and can overwrite one
another during conversion or finalization.

### Automatic Danbooru tags

For images named after their original MD5 hash, such as
`2e3eee4f9c2183d3c977144050ff74d8.webp`, the pipeline searches Danbooru and
downloads the matching tags and metadata. Image conversion preserves the
filename's hash, which is used for the Danbooru lookup.

Existing `.txt` tag files are kept and their Danbooru lookup is skipped unless
**Overwrite existing TXT files with fresh Danbooru or Gelbooru tags** is enabled. If an
image has no matching Danbooru post or uses another filename, its tags can come
from a matching `.txt` file or from AnimeTimm. Images still without a `.txt`
file at the tagging stage require AnimeTimm model access.

This step fetches tags for images already in `images/`. It does not download
image files.

### GUI options

| Option | Behavior |
| --- | --- |
| **Skip WD14 high-confidence missing-tag step** | Enabled by default. Skips adding high-confidence WD14 tags to existing text files. Images still without text files after Danbooru are tagged by AnimeTimm regardless of this setting. |
| **Overwrite existing TXT files with fresh Danbooru or Gelbooru tags** | Replaces existing source tags with fresh tags from the first matching booru. Existing tags are preserved when unchecked. |
| **Add year tag to .tag files** | Off by default. Adds `year YYYY` from the Danbooru or Gelbooru post upload date when available. |
| **Overwrite cached short and long captions** | Regenerates short and long captions instead of reusing previous results. |

The pipeline converts images, fetches Danbooru tags, and runs local AnimeTimm
tagging where needed. It looks up tag and character explanations and creates
the CSV metadata needed for captioning. Eligible clean, single-character images also
supply generated character references.

Torii then produces visual analysis, which is normalized and passed to the
refinement model. The final stage creates long and short captions, cleans the
tags, and saves each completed asset set in `done/`. The pipeline then checks
captions for definite failures and moves affected image sets to `captionReview/`. Existing
caches are reused when available.

### Stopping and resuming

Click **Stop** to interrupt a run. Completed assets remain in `done/`.
Fix any reported issue and start the pipeline again to process remaining
images in `images/`. Existing results and caches are reused where possible.

To regenerate captions for a completed image, copy the image and its source
`.txt` tags back into `images/` and enable **Overwrite cached short and long
captions**. The new output replaces matching files in `done/`.

After an interrupted save, `.finalize-*` folders in `done/` hold the recovery
information needed by the next run. Removing them prevents automatic recovery.

## Output files

Each completed image has these companion files in `done/`:

| File | Contents |
| --- | --- |
| `NAME.webp` | Processed image. |
| `NAME.short` | Compact natural-language caption. |
| `NAME.long` | Detailed natural-language caption. |
| `NAME.tag` | Cleaned and ordered Danbooru-style tags. |
| `NAME.combined` | Tag caption followed by the short caption. |
| `NAME.txt` | All fetched source tags, followed by a structured record of the booru metadata used to rebuild the CSV. Plain tag-only files remain supported. |

The `.tag` generator intentionally drops most booru meta tags. It includes
only medium-related meta tags, such as `watercolor (medium)` and
`traditional media`; resolution tags such as `highres` are not needed for
training. The fetched meta tags remain available in the CSV until finalization.
Fetched `.txt` files retain the complete meta list as well. The pipeline keeps
the structured record out of prompts and tag matching; it reads that record to
restore post ID, source, score, rating, date, and tag categories when rebuilding
a missing CSV. WD14 additions to the visible tag list are retained as general
tags in a rebuilt CSV. Older tag-only `.txt` files still use category lookup.

Generated captions can contain errors and benefit from review before training.

For example, an input pair `sample.webp` and `sample.txt` containing
`1girl, blue_hair, outdoors` may produce files like these. The actual text
depends on the image and models:

```text
done/sample.short     A blue-haired character stands outdoors.
done/sample.long      A character with blue hair stands in an outdoor scene...
done/sample.tag       1girl, blue hair, outdoors
done/sample.combined  1girl, blue hair, outdoors A blue-haired character stands outdoors.
done/sample.txt       1girl, blue_hair, outdoors
done/sample.webp      The processed image
```

### Caption quality review

After captioning, the GUI and command-line pipeline check caption sets in
`done/`. Missing or empty `.short`/`.long` captions, leaked control text such
as `END_CAPTION` or `<think>`, or more than 250 words between sentence breaks in a long caption
trigger review. The image and its companion files move together to
`captionReview/`. The pipeline log shows each image and the reason, followed
by a count of each failure type. Suspicious but uncertain wording is not moved.
Run `python data/check_caption_quality.py --dry-run` to preview findings
without moving files.

## Optional tools

### Copy or move images by tags

The helper keeps images and matching `.txt` files together.
Click **Open copy/move images GUI** in the main caption GUI.

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
source tags or `--overwrite-caption-cache` to regenerate captions.

Tag lookup searches Danbooru first, then Gelbooru when no matching post is found.
Both use the MD5 in the image filename. Gelbooru results retain tag categories,
rating, score, post ID, and original source URL; the CSV `source` is `gelbooru`.
The optional year tag uses the matched post's upload year and is omitted when
the source has no usable date. The pipeline does not generate aesthetic score
tags such as `score_1` through `score_9`; post scores only determine quality
labels such as `good quality`.
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

## Troubleshooting

### Cannot connect to Ollama

Check that Ollama is running and `OLLAMA_URL` points to the correct computer.
Test the address from the VLCaptioner computer:

```console
curl http://localhost:11434/api/version
```

For a remote server, replace `localhost` with its address and check its
network binding and firewall rules.

### Model download fails

Run `ollama pull` with the configured model name on the Ollama host. Check
the name, network connection, available disk space, and any access requirements.
Both caption models must support images.

### Requests time out or run out of memory

Increase `OLLAMA_TIMEOUT_SECONDS` for slow requests. For memory errors, reduce
other GPU workloads or select a smaller refinement model or quantization.

If the error says `exceeds the available context size`, increase
`OLLAMA_CONTEXT_SIZE` above the reported token count, allowing room for the
response. Larger contexts need more memory. Oversized requests are skipped
while the rest of the queue continues. Retry the remaining images after
adjusting the setting.

### Local tagging fails

Check Hugging Face access for AnimeTimm and the local PyTorch/GPU installation.
The Ollama server's GPU does not run this tagger. If you already have suitable
tags, enable **Skip WD14 high-confidence missing-tag step**.

### Images are skipped without captions

Check the log for missing CSV metadata or unusable prompts. Make sure each
image has source tags: use a matching `.txt` file, a successful Danbooru lookup,
or enable AnimeTimm tagging. Run the full pipeline again so it can prepare the
metadata before captioning.

## License

VLCaptioner's software is licensed under [Apache 2.0](LICENSE). Third-party
models and data retain their respective terms.

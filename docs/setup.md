# Setup and configuration

[Back to README](../README.md)

## Setup

You need Python 3, Git, a Danbooru account and API key, and an
[Ollama](https://ollama.com/download) server with enough memory and disk space
for your chosen vision models. An NVIDIA GPU is strongly recommended.
Both installers install CUDA-enabled PyTorch locally, even if Ollama runs on
another computer. A local NVIDIA GPU speeds up AnimeTimm tagging. The tagger
can also run on CPU. The Ollama host must have enough memory for both selected
vision models and the configured context size.

If an image has no `.txt` tags after the Danbooru lookup, the pipeline runs
AnimeTimm/WD14 locally, using cached files or downloading its Hugging Face model.
If AnimeTimm access is denied, it downloads and uses SmilingWolf EVA02 Large v3.

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
GUI, enable **Add missing tags to existing tag files** to also
append missing high-confidence WD14 tags to existing text files.

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

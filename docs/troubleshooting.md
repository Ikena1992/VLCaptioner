# Troubleshooting

[Back to README](../README.md)

## Troubleshooting

GUI crash diagnostics are appended to `data/caches/crashes.log` and kept across
restarts. This records startup errors, unhandled GUI and background-thread
exceptions, and Python fatal-crash tracebacks, without recording activity output.
Restart the GUI after updating to enable it. Forced process termination or a
power loss may leave no traceback. The log stays local and outside Git.

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
AnimeTimm is preferred when its files are cached or accessible. If Hugging Face
denies access, VLCaptioner automatically downloads and uses
`SmilingWolf/wd-eva02-large-tagger-v3` instead. The log identifies the active
model. The fallback uses ONNX Runtime with general/character thresholds of
0.35/0.85; the existing-TXT pass uses 0.91 and ratings remain disabled.
To enable AnimeTimm, accept/request access on its model page, then run
`hf auth login` on the computer running VLCaptioner with a read token from
the approved account, or set `HF_TOKEN`. The token must allow access to the
gated model. Network failures are reported separately from access denials.
The Ollama server's GPU does not run this tagger. If you already have suitable
tags, leave **Add missing tags to existing tag files** off.

### Images are skipped without captions

Check the log for missing CSV metadata or unusable prompts. Make sure each
image has source tags: use a matching `.txt` file, a successful Danbooru lookup,
or enable AnimeTimm tagging. Run the full pipeline again so it can prepare the
metadata before captioning.

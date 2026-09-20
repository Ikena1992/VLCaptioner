"""Load optional long captions indexed by image MD5 filename."""


from parquet_lookup import matching_rows


def load_parquet_captions(folder, wanted_stems=None, *, batch_size=2048):
    """Load captions, optionally restricting the scan to image filename stems.

    Scan normalized IDs first and read payloads only in matching row groups.
    Payload decoding and Python conversion use bounded batches.
    """
    files = sorted(folder.glob("*.parquet"))
    if not files:
        return {}
    try:
        import pyarrow.parquet as pq
    except ImportError as error:
        raise RuntimeError(
            "Parquet captions require pyarrow. Install with: python -m pip install pyarrow"
        ) from error

    wanted = None
    if wanted_stems is not None:
        wanted = {str(stem).strip().lower() for stem in wanted_stems if str(stem).strip()}
        if not wanted:
            print("No local image stems to look up in Parquet captions.", flush=True)
            return {}

    captions = {}
    print(
        f"Looking up {len(wanted)} local image stem(s) in {len(files)} Parquet file(s)..."
        if wanted is not None
        else f"Loading captions from {len(files)} Parquet file(s)...",
        flush=True,
    )
    for path in files:
        parquet = pq.ParquetFile(path)
        if not {"md5", "caption"}.issubset(parquet.schema_arrow.names):
            print(f"Skipping {path.name}: missing md5 or caption column", flush=True)
            parquet.close()
            continue
        print(f"Reading captions from {path.name}...", flush=True)
        try:
            for row in matching_rows(parquet, "md5", ["md5", "caption"], wanted, batch_size):
                md5, caption = row["md5"], row["caption"]
                if not isinstance(md5, str) or not isinstance(caption, str):
                    continue
                md5, caption = md5.strip().lower(), caption.strip()
                if md5 and caption and (wanted is None or md5 in wanted):
                    # Stable precedence: first nonempty caption in filename order.
                    captions.setdefault(md5, caption)
                    if wanted is not None:
                        wanted.discard(md5)
        finally:
            parquet.close()
        if wanted == set():
            break
    print(f"Loaded {len(captions)} matching Parquet caption(s) from {folder}", flush=True)
    return captions

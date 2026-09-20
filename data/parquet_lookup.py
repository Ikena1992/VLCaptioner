"""ID-first, bounded-batch Parquet lookup."""
import time


def matching_rows(parquet, key_column, columns, wanted, batch_size=2048):
    """Callers remove satisfied keys from wanted; normalize before matching."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    last_status = time.monotonic()
    for group in range(parquet.num_row_groups):
        if wanted is not None and not wanted:
            return
        print(f"Parquet row group {group + 1}/{parquet.num_row_groups}: scanning IDs...", flush=True)
        # A byte per row bounds lookup memory even for duplicate-heavy groups.
        offsets = bytearray(parquet.metadata.row_group(group).num_rows)
        position = 0
        last_match = -1
        if wanted is not None:
            for batch in parquet.iter_batches(row_groups=[group], columns=[key_column], batch_size=batch_size):
                for index, value in enumerate(batch.column(0).to_pylist()):
                    if isinstance(value, str) and value.strip().lower() in wanted:
                        offsets[position + index] = 1
                        last_match = position + index
                position += batch.num_rows
                if time.monotonic() - last_status >= 5:
                    print(f"Parquet: checked {position:,} IDs in group {group + 1}", flush=True)
                    last_status = time.monotonic()
            if last_match < 0:
                continue
        print(f"Parquet row group {group + 1}: reading payload batches...", flush=True)
        position = 0
        for batch in parquet.iter_batches(row_groups=[group], columns=columns, batch_size=batch_size):
            for index in range(batch.num_rows):
                if wanted is not None and not offsets[position + index]:
                    continue
                value = batch.column(columns.index(key_column))[index].as_py()
                if not isinstance(value, str):
                    continue
                key = value.strip().lower()
                if not key or (wanted is not None and key not in wanted):
                    continue
                yield {field.name: batch.column(i)[index].as_py() for i, field in enumerate(batch.schema)}
                if wanted is not None and not wanted:
                    return
            position += batch.num_rows
            if wanted is not None and position > last_match:
                break
            if time.monotonic() - last_status >= 5:
                print(f"Parquet: read {position:,} payload rows in group {group + 1}", flush=True)
                last_status = time.monotonic()

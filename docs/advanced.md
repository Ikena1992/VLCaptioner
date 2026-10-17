# Advanced captioning

[Back to README](../README.md)

## Advanced captioning

### Refiner thinking and layout JSON prompts

Options includes **Enable refiner thinking** and **Create .json files**.
Both default to off. Set `REFINER_THINKING=true` or `REFINER_JSON=true` in
`config.txt` to change command-line and first-launch GUI defaults. Later GUI
launches restore the saved selections.
The pipeline runner and refiner script also accept `--refiner-thinking`,
`--no-refiner-thinking`, `--refiner-json`, and `--no-refiner-json` overrides.
Thinking applies only to the refiner, requires model support, and can increase
processing time and token usage.

JSON generation uses the configured refiner model in a separate image request.
`NAME.json` uses the MOSS-Image Layout Prompts schema: `summary` (meta, canvas,
caption), `items` (label, caption, bbox, plus exact text for text elements),
`bbox_coord_space: "abs_centralized"`, and `bbox_format: "yxyx"`.
Boxes use absolute pixels centered on the original, EXIF-oriented canvas in
[y_min, x_min, y_max, x_max] order. The model estimates these boxes from the image;
they are validated for ordering and canvas bounds, not detection accuracy.
Existing JSON files are kept unless cache bypass is enabled together with JSON
creation. In that case JSON is generated fresh and atomically replaces the old
sidecar after validation; a failed generation leaves the old file intact. Missing JSON files are
created even when both ordinary captions already exist in the input folder.
Valid JSON results use the refinement cache; cache bypass requests fresh output
for both missing and existing sidecars. Thinking mode is included in refinement cache identity.
JSON sidecars are published to `done/` with their image and captions.

Caption prompts check for visible writing regardless of tags and preserve its
original script, spelling, punctuation, and line breaks without translation.
They describe text arrangement where relevant, avoid guessing unreadable text,
and exclude watermarks from caption prose. Short captions retain writing only
when central to the scene; layout JSON includes readable scene text as text items.


The layout refiner drafts top-left pixel boxes in `xyxy` order on the actual
inference preview (EXIF-oriented, aspect-preserving thumbnail, at most 1024x1024,
without cropping or padding). Python validates those preview coordinates, scales
x and y to the original oriented image using the actual thumbnail dimensions,
then converts to centered `yxyx`, enclosing fractional edges with floor/ceil.
Coordinates describe pixel edges: the full canvas is `[0, 0, width, height]`,
including the right and bottom edges. Decimal coordinates and scaling ratios
are evaluated exactly before rounding, preserving even very narrow valid boxes.
Saved sidecar and cache validation also retain exact decimal edges, rejecting
out-of-bounds values before floating-point rounding could hide them.
The shared preview helper also prepares the image sent to Ollama, so coordinate
dimensions follow the same resize rules.
Saved sidecars and cache entries contain compact compiled JSON, without draft
width/height fields. Supported labels are `object`, `character`, and `text`.
Summary canvas types are `single`, `multi_panel`, and `collage`; summary `meta`,
`canvas`, and `palette` are optional. Items may include `tags` and `palette`.
All boxes use global canvas coordinates, including text on objects or in panels;
overlap is allowed. Unknown fields, empty layouts, duplicate JSON keys, invalid
metadata, and out-of-bounds boxes are rejected and retried.
Original image dimensions are preserved, including odd sizes (which yield half-pixel
centered coordinates). MOSS generation requires dimensions divisible by 16; if
resizing a layout for generation, rescale its boxes to the new canvas as well.
Existing sidecars are not automatically corrected: their coordinate accuracy cannot
be inferred from bounds alone. Enable JSON creation and cache bypass to regenerate them.
The preview-to-original draft prompt uses a separate cache identity, so missing
sidecars do not reuse results from the former model-estimated original-pixel recipe.
The exact-edge conversion also uses a new cache identity. The viewer uses the same
validation and outward rounding for draft and compiled boxes, and skips invalid
boxes instead of clipping them into apparently valid placements.

Thinking requests read only the final response content, never the separate reasoning
field. Caption stop markers are removed locally so a marker mentioned during
reasoning cannot end generation early. If a thinking request returns only reasoning or hits its output token
limit, it retries once with twice the budget; persistent truncation is rejected and
not cached. Layout validation retries remain separate. These retries may increase
processing time; `OLLAMA_TIMEOUT_SECONDS` still applies to each request.

### Inspect layout JSON

To inspect generated layout JSON on its source image, run `LayoutViewer.cmd`.
Choose an image to load its matching `NAME.json` sidecar automatically, or open
a JSON file directly. The viewer draws every item box over the image; select an
item to read its caption prompt and exact visible text. The scene summary appears
above the item list.

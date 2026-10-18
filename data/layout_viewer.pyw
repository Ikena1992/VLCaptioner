"""Browse generated layout JSON sidecars and preview their boxes on images."""
from __future__ import annotations

from dataset_files import IMAGE_EXTENSIONS

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox
import ttkbootstrap as ttk

from PIL import Image, ImageOps, ImageTk
from refiner_json import box_to_pixels


IMAGE_TYPES = (
    ("Images", ' '.join('*' + extension for extension in IMAGE_EXTENSIONS)),
    ("All files", "*.*"),
)
JSON_TYPES = (("JSON files", "*.json"), ("All files", "*.*"))
COLORS = ("#ff4d6d", "#43d9bd", "#ffd166", "#70a1ff", "#c77dff", "#ff9f43")


class LayoutViewer:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Layout JSON Viewer")
        self.root.geometry("1180x800")
        self.image_path: Path | None = None
        self.data: dict = {}
        self.pending_layout_path: Path | None = None
        self.source_image: Image.Image | None = None
        self.photo: ImageTk.PhotoImage | None = None
        self.preview_size = None
        self.draw_after_id = None
        self.scale = 1.0
        self.offset = (0, 0)
        self.rendered_boxes = []

        colors = getattr(getattr(root, "style", None), "colors", None)
        input_bg = getattr(colors, "inputbg", "#f3f4f6")
        input_fg = getattr(colors, "inputfg", "#202124")
        select_bg = getattr(colors, "primary", "#3978d4")

        toolbar = ttk.Frame(root, padding=(12, 10))
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="Open image", command=self.open_image, bootstyle="primary").pack(side="left")
        ttk.Button(toolbar, text="Open layout JSON", command=self.open_json, bootstyle="secondary-outline").pack(side="left", padx=(8, 0))
        self.status = ttk.Label(toolbar, text="Choose an image or its generated JSON sidecar", bootstyle="secondary")
        self.status.pack(side="left", padx=14, fill="x", expand=True)

        body = ttk.Panedwindow(root, orient="horizontal")
        body.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        preview = ttk.Labelframe(body, text="Image preview", padding=5)
        side = ttk.Frame(body, width=390, padding=(10, 0, 0, 0))
        body.add(preview, weight=4)
        body.add(side, weight=2)
        self.canvas = tk.Canvas(preview, background="#17191d", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self.schedule_draw)
        self.canvas.bind("<Button-1>", self.select_box_at_pointer)

        summary_panel = ttk.Labelframe(side, text="Scene summary", padding=8)
        summary_panel.pack(fill="x", pady=(0, 10))
        summary_frame = ttk.Frame(summary_panel)
        summary_frame.pack(fill="x")
        self.summary = tk.Text(summary_frame, height=6, wrap="word", state="disabled", background=input_bg, foreground=input_fg, relief="flat", padx=8, pady=7)
        summary_scroll = ttk.Scrollbar(summary_frame, orient="vertical", command=self.summary.yview)
        self.summary.configure(yscrollcommand=summary_scroll.set)
        self.summary.pack(side="left", fill="both", expand=True)
        summary_scroll.pack(side="right", fill="y")
        self.summary_meta = ttk.Label(summary_panel, text="", bootstyle="secondary", wraplength=340)
        self.summary_meta.pack(anchor="w", fill="x", pady=(6, 0))

        item_panel = ttk.Labelframe(side, text="Layout items", padding=8)
        item_panel.pack(fill="both", expand=True)
        ttk.Label(item_panel, text="Select a row or a numbered box to inspect its prompt.",
                  bootstyle="secondary", wraplength=340).pack(anchor="w", pady=(0, 6))
        split = ttk.Panedwindow(item_panel, orient="vertical")
        split.pack(fill="both", expand=True)
        list_frame = ttk.Frame(split)
        self.items = tk.Listbox(list_frame, width=42, activestyle="none", exportselection=False,
                                background=input_bg, foreground=input_fg,
                                selectbackground=select_bg, selectforeground="#ffffff",
                                highlightthickness=0, relief="flat", borderwidth=0,
                                font=("Segoe UI", 9))
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.items.yview)
        self.items.configure(yscrollcommand=scroll.set)
        self.items.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        split.add(list_frame, weight=1)
        self.items.bind("<<ListboxSelect>>", self.select_item)
        self.items.bind("<Double-Button-1>", self.select_item)
        detail_frame = ttk.Frame(split)
        self.item_detail = tk.Text(detail_frame, height=12, wrap="word", state="disabled", background=input_bg, foreground=input_fg, relief="flat", padx=9, pady=8)
        detail_scroll = ttk.Scrollbar(detail_frame, orient="vertical", command=self.item_detail.yview)
        self.item_detail.configure(yscrollcommand=detail_scroll.set)
        self.item_detail.pack(side="left", fill="both", expand=True)
        detail_scroll.pack(side="right", fill="y")
        split.add(detail_frame, weight=2)
        self.text_widgets = (self.summary, self.item_detail)
        self.listbox = self.items
        self.apply_theme()
        root.bind("<Control-o>", lambda _event: self.open_image())

    def apply_theme(self):
        colors = getattr(getattr(self.root, "style", None), "colors", None)
        background = getattr(colors, "inputbg", "#f3f4f6")
        foreground = getattr(colors, "inputfg", "#202124")
        selection = getattr(colors, "primary", "#3978d4")
        for widget in self.text_widgets:
            widget.configure(background=background, foreground=foreground,
                             insertbackground=foreground)
        self.listbox.configure(background=background, foreground=foreground,
                               selectbackground=selection, selectforeground="#ffffff")

    def open_image(self):
        path = filedialog.askopenfilename(title="Select image", filetypes=IMAGE_TYPES)
        if path:
            self.load_image(Path(path), preserve_layout=self.pending_layout_path is not None)

    def open_json(self):
        path = filedialog.askopenfilename(title="Select layout JSON", filetypes=JSON_TYPES)
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8-sig"), parse_float=Decimal)
            if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                raise ValueError("Expected a layout object with an items array.")
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError, InvalidOperation) as error:
            messagebox.showerror("Could not read layout JSON", str(error), parent=self.root)
            return
        self.data = data
        self.pending_layout_path = Path(path)
        self.fill_details()
        sibling = Path(path)
        candidate = next((sibling.with_suffix(suffix) for suffix in
                          IMAGE_EXTENSIONS
                          if sibling.with_suffix(suffix).is_file()), None)
        if candidate is not None:
            # Keep the explicitly selected JSON, including its dotted basename.
            self.image_path = None
            self.source_image = None
            self.load_image(candidate, preserve_layout=True)
        else:
            # Never leave boxes from an unrelated previously opened image visible.
            self.image_path = None
            self.source_image = None
            self.status.configure(text=f"Layout: {Path(path).name}  ·  Open its matching image")
        self.draw()

    def load_image(self, path: Path, *, preserve_layout=False):
        try:
            with Image.open(path) as image:
                self.source_image = ImageOps.exif_transpose(image).convert("RGB")
        except (OSError, ValueError) as error:
            messagebox.showerror("Could not open image", str(error), parent=self.root)
            return
        self.image_path = path
        self.preview_size = None
        self.photo = None
        layout_path = self.pending_layout_path if preserve_layout else None
        # Ordinary image browsing must not reuse another image's boxes.
        if not preserve_layout:
            self.data = {}
            self.fill_details()
        json_path = path.with_suffix(".json")
        sidecar_error = None
        if not preserve_layout and json_path.is_file():
            try:
                data = json.loads(json_path.read_text(encoding="utf-8-sig"), parse_float=Decimal)
                if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                    raise ValueError("Expected a layout object with an items array.")
                self.data = data
                self.fill_details()
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError, InvalidOperation) as error:
                sidecar_error = f"Could not read {json_path.name}: {error}"
        status = f"{path.name}  ·  {self.source_image.width} × {self.source_image.height}"
        if layout_path is not None:
            status += f"  ·  Layout: {layout_path.name}"
        self.pending_layout_path = None
        if sidecar_error:
            status += f"  ·  {sidecar_error}"
        self.status.configure(text=status)
        self.draw()

    def fill_details(self):
        summary = self.data.get("summary", {})
        summary_text = summary.get("caption", "") if isinstance(summary, dict) else ""
        self.set_text(self.summary, summary_text or "No scene summary in this JSON.")
        if isinstance(summary, dict):
            meta = []
            if summary.get("canvas"):
                meta.append(str(summary["canvas"]).replace("_", " ").title())
            if summary.get("meta"):
                meta.extend(map(str, summary["meta"]))
            if summary.get("palette"):
                meta.append("Palette: " + ", ".join(map(str, summary["palette"])))
            self.summary_meta.configure(text="  ·  ".join(meta))
        else:
            self.summary_meta.configure(text="")
        self.items.delete(0, "end")
        self.set_text(self.item_detail, "Select an item to see its full prompt and coordinates.")
        for index, item in enumerate(self.data.get("items", []), start=1):
            if not isinstance(item, dict):
                self.items.insert("end", f"{index}. [invalid item]")
                continue
            label = str(item.get("label", "item")).title()
            caption = str(item.get("caption", ""))
            self.items.insert("end", f"{index}. {label}: {caption[:72]}")
        if self.data.get("items"):
            self.items.selection_set(0)
            self.items.event_generate("<<ListboxSelect>>")

    @staticmethod
    def set_text(widget: tk.Text, value: str):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.yview_moveto(0)
        widget.configure(state="disabled")

    def select_item(self, _event=None):
        selection = self.items.curselection()
        index = selection[0] if selection else None
        items = self.data.get("items", [])
        if index is None or index >= len(items) or not isinstance(items[index], dict):
            self.set_text(self.item_detail, "Select an item to see its prompt and coordinates.")
        else:
            item = items[index]
            lines = [f"{str(item.get('label', 'item')).title()} prompt", str(item.get("caption", ""))]
            if item.get("text"):
                lines.extend(("", "Visible text", str(item["text"])))
            if item.get("tags"):
                lines.extend(("", "Tags: " + ", ".join(map(str, item["tags"]))))
            lines.extend(("", "Box: " + str(item.get("bbox", "missing"))))
            self.set_text(self.item_detail, "\n".join(lines))
        self.draw()

    def box_xyxy(self, box):
        if self.source_image is None:
            return None
        try:
            coord_space = self.data.get("bbox_coord_space")
            preview_size = None
            if coord_space == "abs" and ("width" in self.data or "height" in self.data):
                preview_size = (self.data.get("width"), self.data.get("height"))
            return box_to_pixels(box, *self.source_image.size,
                                 coord_space=coord_space,
                                 bbox_format=self.data.get("bbox_format"),
                                 preview_size=preview_size)
        except (OverflowError, TypeError, ValueError):
            return None

    def select_box_at_pointer(self, event):
        for index, x1, y1, x2, y2 in reversed(self.rendered_boxes):
            if x1 <= event.x <= x2 and y1 <= event.y <= y2:
                self.items.selection_clear(0, "end")
                self.items.selection_set(index)
                self.items.activate(index)
                self.items.see(index)
                self.select_item()
                return

    def schedule_draw(self, _event=None):
        if self.draw_after_id is not None:
            self.root.after_cancel(self.draw_after_id)
        self.draw_after_id = self.root.after(60, self.draw)

    def draw(self):
        self.draw_after_id = None
        self.canvas.delete("all")
        self.rendered_boxes = []
        if self.source_image is None:
            self.canvas.create_text(24, 28, anchor="nw", fill="#c9cbd0", text="Open an image or its .json sidecar to start.", font=("Segoe UI", 13))
            return
        cw, ch = max(self.canvas.winfo_width(), 1), max(self.canvas.winfo_height(), 1)
        iw, ih = self.source_image.size
        self.scale = min(cw / iw, ch / ih)
        rw, rh = max(1, round(iw * self.scale)), max(1, round(ih * self.scale))
        self.offset = ((cw - rw) / 2, (ch - rh) / 2)
        if self.preview_size != (rw, rh):
            resized = self.source_image.resize((rw, rh), Image.Resampling.LANCZOS)
            self.photo = ImageTk.PhotoImage(resized)
            self.preview_size = (rw, rh)
        ox, oy = self.offset
        self.canvas.create_image(ox, oy, anchor="nw", image=self.photo)
        selected = self.items.curselection()
        selected_index = selected[0] if selected else None
        for index, item in enumerate(self.data.get("items", [])):
            if not isinstance(item, dict):
                continue
            coords = self.box_xyxy(item.get("bbox"))
            if coords is None:
                continue
            # Scale against the actual resized dimensions to avoid fractional
            # drift when the preview size rounds to integer pixels.
            sx, sy = rw / iw, rh / ih
            x1, y1, x2, y2 = (ox + coords[0] * sx, oy + coords[1] * sy,
                              ox + coords[2] * sx, oy + coords[3] * sy)
            color = COLORS[index % len(COLORS)]
            width = 4 if index == selected_index else 2
            self.canvas.create_rectangle(x1, y1, x2, y2, outline=color, width=width)
            self.rendered_boxes.append((index, x1, y1, x2, y2))
            badge = str(index + 1)
            badge_width = min(rw, max(30, len(badge) * 10 + 12))
            badge_x = min(max(ox, x1), ox + rw - badge_width)
            badge_y = max(oy, y1 - 22)
            self.canvas.create_rectangle(badge_x, badge_y, badge_x + badge_width, badge_y + 20,
                                         fill=color, outline=color)
            self.canvas.create_text(badge_x + badge_width / 2, badge_y + 10, text=badge,
                                    fill="#111318", font=("Segoe UI", 10, "bold"))


def main():
    root = ttk.Window(themename="flatly")
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except (ImportError, AttributeError, OSError):
        pass
    LayoutViewer(root)
    root.mainloop()


if __name__ == "__main__":
    main()

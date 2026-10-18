"""Small standalone, row-based booru download queue."""
import queue
import json
from pathlib import Path
from file_io import atomic_write_json
import threading
import uuid
import tkinter as tk
from tkinter import messagebox, scrolledtext
from tkinter import font as tkfont

import ttkbootstrap as ttk

from booru_downloader import DatasetCountError, DownloadJob, ImageIndex, failure_reason, gap_tag, parse_blacklist, run_job
from runtime_config import PROJECT_DIR
import caption_queue

SETTINGS_FILE = PROJECT_DIR / "data" / "caches" / "downloader_settings.json"


def download_job_from_row(row, blacklist):
    tags = row['tags'].strip()
    if not tags:
        raise ValueError('Search tags are empty.')
    if row['source'] not in ('Danbooru', 'Gelbooru'):
        raise ValueError('Choose Danbooru or Gelbooru.')
    target = row.get('target', '').strip()
    if target:
        try:
            target = int(target)
            if target < 1:
                raise ValueError()
        except ValueError:
            raise ValueError('Target in done must be a positive whole number.') from None
        gap_tag(row['source'], tags)
        return DownloadJob(row['source'], tags, 100, blacklist, target)
    try:
        limit = int(row['limit'])
        if limit < 1:
            raise ValueError()
    except ValueError:
        raise ValueError('Post limit must be a positive whole number when Target in done is blank.') from None
    return DownloadJob(row['source'], tags, limit, blacklist)


class DownloaderGUI:
    def __init__(self, root, settings_file=SETTINGS_FILE, can_start=None):
        self.root = root
        self.settings_file = Path(settings_file)
        self.can_start = can_start or (lambda automatic: True)
        self.rows = {}
        self.loading_editor = False
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.running = False
        self.closing = False
        self.poll_job = None
        self.close_job = None
        root.title("VLCaptioner — Image downloader")
        root.geometry("1040x820")
        root.minsize(860, 680)
        frame = ttk.Frame(root, padding=18)
        frame.pack(fill="both", expand=True)
        header = ttk.Frame(frame)
        header.pack(fill="x", pady=(0, 14))
        ttk.Label(header, text="Image downloader", font=("Segoe UI", 21, "bold")).pack(anchor="w")
        ttk.Label(header, text="Queue searches from Danbooru and Gelbooru. Originals and tags are saved to images/.",
                  bootstyle="secondary", wraplength=800).pack(anchor="w", pady=(4, 0))

        notebook = ttk.Notebook(frame)
        notebook.pack(fill="both", expand=True)
        search_page = ttk.Frame(notebook, padding=12)
        settings_page = ttk.Frame(notebook, padding=12)
        notebook.add(search_page, text="Search queue")
        notebook.add(settings_page, text="Filters & captioning")
        self.search_notebook = notebook
        self.search_page = search_page
        self.settings_page = settings_page

        row_tools = ttk.Frame(search_page)
        row_tools.pack(fill="x", pady=(0, 8))
        self.add_button = ttk.Button(row_tools, text="Add search", command=self.new_row, bootstyle="primary")
        self.add_button.pack(side="left")
        self.remove_button = ttk.Button(row_tools, text="Remove selected", command=self.remove_selected, bootstyle="secondary-outline")
        self.remove_button.pack(side="left", padx=8)
        self.row_count = tk.StringVar()
        ttk.Label(row_tools, textvariable=self.row_count, bootstyle="secondary").pack(side="right")
        table_panel = ttk.Frame(search_page)
        table_panel.pack(fill="both", expand=True)
        table_panel.rowconfigure(0, weight=1)
        table_panel.columnconfigure(0, weight=1)
        ttk.Style().configure("DownloadRows.Treeview",
                              rowheight=max(30, tkfont.nametofont("TkDefaultFont").metrics("linespace") + 10))
        self.table = ttk.Treeview(table_panel, columns=("source", "tags", "limit", "target"),
                                 show="headings", selectmode="extended", height=7,
                                 style="DownloadRows.Treeview")
        for column, title, width in (("source", "Source", 110), ("tags", "Search tags", 430), ("limit", "Post limit", 100), ("target", "Target in done", 120)):
            self.table.heading(column, text=title)
            self.table.column(column, width=width, minwidth=80, stretch=column == "tags")
        self.table.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(table_panel, orient="vertical", command=self.table.yview)
        horizontal = ttk.Scrollbar(table_panel, orient="horizontal", command=self.table.xview)
        self.table.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal.set)
        scrollbar.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        self.table.bind("<<TreeviewSelect>>", self.select_row)
        self.table.bind("<Delete>", lambda event: self.remove_selected())
        editor = ttk.Labelframe(search_page, text="Edit selected search", padding=10)
        editor.pack(fill="x", pady=(10, 0))
        editor.columnconfigure(1, weight=1)
        self.edit_source = tk.StringVar(value="Danbooru")
        self.edit_tags = tk.StringVar()
        self.edit_limit = tk.StringVar(value="100")
        self.edit_target = tk.StringVar()
        self.editor_widgets = [
            ttk.Combobox(editor, textvariable=self.edit_source, values=("Danbooru", "Gelbooru"), state="readonly", width=12),
            ttk.Entry(editor, textvariable=self.edit_tags),
            ttk.Entry(editor, textvariable=self.edit_limit, width=9),
            ttk.Entry(editor, textvariable=self.edit_target, width=12),
        ]
        for column, (title, widget) in enumerate(zip(("Source", "Search tags", "Post limit", "Target in done"), self.editor_widgets)):
            ttk.Label(editor, text=title, bootstyle="secondary").grid(row=0, column=column, sticky="w", padx=(0, 8), pady=(0, 4))
            widget.grid(row=1, column=column, sticky="ew", padx=(0, 8))
        self.editor_hint = tk.StringVar(value="Select one search to edit. Changes are saved immediately to the row.")
        ttk.Label(editor, textvariable=self.editor_hint, bootstyle="secondary", wraplength=760).grid(
            row=2, column=0, columnspan=4, sticky="w", pady=(8, 0))
        for variable in (self.edit_source, self.edit_tags, self.edit_limit, self.edit_target):
            variable.trace_add("write", self.update_selected_row)
        ttk.Label(search_page, text="Use spaces between tags and underscores within tags, e.g. blue_eyes long_hair. Ctrl / Shift selects multiple searches for removal.",
                  bootstyle="secondary", wraplength=800).pack(anchor="w", pady=(8, 0))

        filters = ttk.Labelframe(settings_page, text="Tag blacklist", padding=12)
        filters.pack(fill="x", pady=(0, 12))
        self.blacklist = tk.StringVar()
        self.blacklist_entry = ttk.Entry(filters, textvariable=self.blacklist)
        self.blacklist_entry.pack(fill="x")
        ttk.Label(filters, text="Applies to every search. Separate whole tags with spaces, e.g. tall_image watermark comic. Any matching tag skips the image. Leave blank to allow all tags.",
                  wraplength=780, bootstyle="secondary").pack(anchor="w", pady=(8, 0))
        batching = ttk.Labelframe(settings_page, text="Automatic captioning", padding=12)
        batching.pack(fill="x", pady=(0, 12))
        self.auto_caption = tk.BooleanVar(value=False)
        self.batch_size = tk.StringVar(value="50")
        self.caption_toggle = ttk.Checkbutton(batching, text="Caption downloaded images automatically", variable=self.auto_caption,
                                              command=self.update_batch_controls, bootstyle="primary-round-toggle")
        self.caption_toggle.pack(anchor="w")
        batch_row = ttk.Frame(batching)
        batch_row.pack(fill="x", pady=10)
        ttk.Label(batch_row, text="Images per batch").pack(side="left", padx=(0, 8))
        self.batch_entry = ttk.Entry(batch_row, textvariable=self.batch_size, width=7, state="disabled")
        self.batch_entry.pack(side="left")
        ttk.Label(batching, text="Smaller batches give earlier results; larger batches reduce model switches. Leftover images form the final batch. Keep the Caption GUI open; use Options for caption settings and Queue for progress.",
                  wraplength=780, bootstyle="secondary").pack(anchor="w")
        help_panel = ttk.Labelframe(settings_page, text="How download limits work", padding=12)
        help_panel.pack(fill="x")
        ttk.Label(help_panel, text="Post limit counts posts checked, including duplicates, blacklisted posts, missing files and failures. Checking 100 posts may download fewer than 100 images. Existing MD5 filenames in images/, done/ and done1/ are skipped.",
                  wraplength=780, justify="left").pack(anchor="w")
        ttk.Label(help_panel, text="Target in done fills a dataset gap for a single tag. It counts done/ and done1/, ignores the post limit, and downloads until the gap is covered or results run out. Leave the target blank for normal downloads. Enable automatic captioning to send downloads through to done/.",
                  wraplength=780, justify="left").pack(anchor="w", pady=(10, 0))

        controls = ttk.Frame(frame)
        controls.pack(fill="x", pady=(12, 8))
        self.start_button = ttk.Button(controls, text="Start downloads", command=self.start, bootstyle="success")
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(controls, text="Stop", command=self.stop, state="disabled", bootstyle="danger-outline")
        self.stop_button.pack(side="left", padx=8)
        self.status = tk.StringVar(value="Ready")
        ttk.Label(controls, textvariable=self.status, wraplength=650).pack(side="left", padx=8, fill="x", expand=True)
        activity = ttk.Labelframe(frame, text="Activity", padding=8)
        activity.pack(fill="both", pady=(0, 4))
        self.log = scrolledtext.ScrolledText(activity, height=6, state="disabled", wrap="word", relief="flat", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True)
        self.apply_theme()
        ttk.Label(frame, text="Searches and settings are remembered. Downloads start only when you click Start downloads.",
                  bootstyle="secondary", wraplength=900).pack(anchor="w", pady=(4, 0))
        self.restore_settings()
        self.update_batch_controls()
        if self.rows:
            self.table.selection_set(next(iter(self.rows)))
        self.select_row()
        self.update_row_count()
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.poll_job = root.after(100, self.poll)

    def update_batch_controls(self):
        self.batch_entry.configure(state="normal" if self.auto_caption.get() and not self.running else "disabled")

    def apply_theme(self):
        colors = ttk.Style().colors
        self.log.configure(background=colors.inputbg, foreground=colors.inputfg,
                           insertbackground=colors.inputfg)

    def restore_settings(self):
        try:
            settings = json.loads(self.settings_file.read_text(encoding="utf-8"))
            if not isinstance(settings, dict) or not isinstance(settings.get("auto_caption", False), bool):
                raise ValueError()
            rows = settings["rows"]
            blacklist = settings["blacklist"]
            if not isinstance(blacklist, str) or not isinstance(rows, list):
                raise ValueError()
            for row in rows:
                if (not isinstance(row, dict) or row.get("source") not in ("Danbooru", "Gelbooru")
                        or not isinstance(row.get("tags"), str) or not isinstance(row.get("limit"), str)):
                    raise ValueError()
        except FileNotFoundError:
            self.add_row()
            return
        except (OSError, ValueError, KeyError, TypeError):
            self.add_row()
            self.status.set("Saved settings unavailable; using defaults")
            return
        if settings.get("blacklist_separator") != "spaces":
            blacklist = " ".join(blacklist.replace(",", " ").split())
        self.auto_caption.set(bool(settings.get("auto_caption", False)))
        self.batch_size.set(str(settings.get("batch_size", "50")))
        self.blacklist.set(blacklist)
        for row in rows:
            identifier = self.add_row(row["source"], row["tags"], row["limit"], str(row.get("target", "")))
            if isinstance(row.get('gap_group'), str):
                self.rows[identifier]['gap_group'] = row['gap_group']

    def save_settings(self):
        settings = {"auto_caption": self.auto_caption.get(), "batch_size": self.batch_size.get(), "blacklist_separator": "spaces", "blacklist": self.blacklist.get(), "rows": list(self.rows.values())}
        try:
            self.settings_file.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(self.settings_file, settings, ensure_ascii=False, indent=2,
                              trailing_newline=True, prefix=".downloader-settings-")
        except OSError:
            messagebox.showwarning("Image downloader", "Could not save downloader settings. Check folder permissions.", parent=self.root)

    def update_row_count(self):
        self.row_count.set(f"{len(self.rows):,} searches")

    def add_row(self, source_value="Danbooru", tags_value="", limit_value="100", target_value=""):
        identifier = self.table.insert("", "end", values=(source_value, tags_value, limit_value, target_value))
        self.rows[identifier] = dict(source=source_value, tags=tags_value, limit=limit_value, target=target_value)
        return identifier

    def prepare_gap_download(self, tag):
        if self.running or self.closing:
            messagebox.showinfo('Image downloader', 'Wait for the current downloads to finish, then choose Download more again.', parent=self.root)
            return
        self.search_notebook.select(self.search_page)
        identifiers = []
        group = uuid.uuid4().hex
        for source, modifier, target in (('Danbooru', 'order:score', '200'), ('Gelbooru', 'sort:score', '250')):
            query = f'{tag} {modifier}'
            identifier = next((key for key, row in self.rows.items() if not row['tags'].strip()), None)
            if identifier is None:
                identifier = self.add_row(source, query, target_value=target)
            else:
                self.rows[identifier].update(source=source, tags=query, target=target)
                row = self.rows[identifier]
                self.table.item(identifier, values=(source, query, row['limit'], target))
            identifiers.append(identifier)
            self.rows[identifier]['gap_group'] = group
        identifier = identifiers[0]
        self.table.selection_set(identifier)
        self.table.see(identifier)
        self.select_row()
        self.auto_caption.set(True)
        self.update_batch_controls()
        self.update_row_count()
        self.status.set('Set the desired total in done/, then click Start downloads.')
        self.editor_widgets[3].focus_set()

    def new_row(self):
        if self.running or self.closing:
            return
        identifier = self.add_row()
        self.table.selection_set(identifier)
        self.table.see(identifier)
        self.select_row()
        self.editor_widgets[1].focus_set()
        self.update_row_count()

    def select_row(self, event=None):
        selected = self.table.selection()
        row = self.rows.get(selected[0]) if len(selected) == 1 else None
        self.loading_editor = True
        try:
            self.edit_source.set(row["source"] if row else "Danbooru")
            self.edit_tags.set(row["tags"] if row else "")
            self.edit_limit.set(row["limit"] if row else "")
            self.edit_target.set(row.get("target", "") if row else "")
        finally:
            self.loading_editor = False
        if not row:
            self.editor_hint.set("Select one search to edit." if not selected else
                                 f"{len(selected):,} searches selected. Choose one to edit, or remove the selection.")
        else:
            self.editor_hint.set("Target mode: post limit is ignored. Counts completed images in done/ and done1/." if self.edit_target.get().strip() else
                                 "Changes update this row immediately. Leave Target in done blank to use the post limit.")
        self.set_row_controls(not self.running and not self.closing)

    def update_selected_row(self, *args):
        if self.loading_editor or self.running or self.closing:
            return
        selected = self.table.selection()
        if len(selected) != 1 or selected[0] not in self.rows:
            return
        row = self.rows[selected[0]]
        row.update(source=self.edit_source.get(), tags=self.edit_tags.get(), limit=self.edit_limit.get(), target=self.edit_target.get())
        self.table.item(selected[0], values=(row["source"], row["tags"], row["limit"], row["target"]))
        self.editor_hint.set("Target mode: post limit is ignored. Counts completed images in done/ and done1/." if row["target"].strip() else
                             "Changes update this row immediately. Leave Target in done blank to use the post limit.")
        self.set_row_controls(True)

    def remove_selected(self):
        if self.running or self.closing:
            return
        selected = self.table.selection()
        for identifier in selected:
            self.rows.pop(identifier, None)
        if selected:
            self.table.delete(*selected)
            if self.rows:
                self.table.selection_set(next(iter(self.rows)))
        self.select_row()
        self.update_row_count()

    def set_row_controls(self, enabled):
        selected = self.table.selection()
        self.remove_button.configure(state="normal" if enabled and selected else "disabled")
        editable = enabled and len(selected) == 1
        for index, widget in enumerate(self.editor_widgets):
            active = editable and not (index == 2 and self.edit_target.get().strip())
            widget.configure(state=("readonly" if index == 0 else "normal") if active else "disabled")

    def start(self):
        if self.running or self.closing:
            return
        if not self.can_start(self.auto_caption.get()):
            self.status.set("Wait for captioning or copy / move to finish before downloading.")
            return
        try:
            size = int(self.batch_size.get()) if self.auto_caption.get() else 50
            if size < 1:
                raise ValueError()
        except ValueError:
            self.search_notebook.select(self.settings_page)
            self.batch_entry.focus_set()
            messagebox.showerror('Image downloader', 'Images per batch must be a positive whole number.', parent=self.root)
            return
        blacklist = parse_blacklist(self.blacklist.get())
        jobs = []
        for index, (identifier, row) in enumerate(self.rows.items(), 1):
            try:
                jobs.append(download_job_from_row(row, blacklist))
            except ValueError as error:
                self.search_notebook.select(self.search_page)
                self.table.selection_set(identifier)
                self.table.see(identifier)
                self.select_row()
                messagebox.showerror('Image downloader',
                                     f"Row {index} ({row['source']}): {row['tags']}\n\n{error}", parent=self.root)
                return
        if not jobs:
            messagebox.showerror('Image downloader', 'Add at least one download row.', parent=self.root)
            return
        self.save_settings()
        self.caption_batches = self.auto_caption.get()
        self.chunk_size = size
        self.caption_toggle.configure(state="disabled")
        self.batch_entry.configure(state="disabled")
        self.running = True
        self.blacklist_entry.configure(state="disabled")
        self.cancel.clear()
        self.add_button.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.set_row_controls(False)
        self.status.set("Downloading…")
        try:
            threading.Thread(target=self.work, args=(jobs,), daemon=True).start()
        except RuntimeError:
            self.events.put(("log", "Could not start the download worker. Try again."))
            self.events.put(("finished", "Could not start downloads"))

    def work(self, jobs):
        had_errors = False
        batch_id = None
        batch_count = 0
        batch_handle = None
        download_handle = None
        try:
            download_handle = caption_queue.downloader_lock()
            if download_handle is None:
                had_errors = True
                self.events.put(("log", "Another downloader is already running. Wait for it to finish, then start again."))
                return
            images, done = PROJECT_DIR / "images", PROJECT_DIR / "done"
            self.events.put(("log", "Scanning images/, done/, and done1/ for existing MD5 filenames…"))
            known = ImageIndex(images, done)
            self.events.put(("log", f"Found {len(known)} existing MD5 images in images/, done/, and done1/."))
            def received(path):
                nonlocal batch_id, batch_count, batch_handle
                batch_count += 1
                try:
                    caption_queue.update(batch_id, "Downloading", batch_count)
                    if batch_count >= self.chunk_size:
                        caption_queue.update(batch_id, "Waiting", batch_count)
                except (OSError, caption_queue.sqlite3.Error) as error:
                    raise caption_queue.QueueSubmissionError() from error
                if batch_count >= self.chunk_size:
                    batch_handle.close()
                    batch_handle = None
                    batch_id = None
                    batch_count = 0

            def destination():
                nonlocal batch_id, images, batch_handle
                if batch_id is None:
                    try:
                        batch_id, images, batch_handle = caption_queue.new_batch("Downloaded images", own_download=True)
                    except (OSError, caption_queue.sqlite3.Error) as error:
                        raise caption_queue.QueueSubmissionError() from error
                return images

            gap_progress = {}
            track_gap = any(job.target_count is not None for job in jobs)
            for index, job in enumerate(jobs, 1):
                if self.cancel.is_set():
                    break
                self.events.put(("log", f"Row {index}: {job.source} — {job.tags}"))
                try:
                    counts = run_job(job, images, done, known, self.cancel,
                                     lambda count, stats: self.events.put(("status", f"Row {index}: {count} checked; {stats['downloaded']} downloaded")),
                                     log=lambda message: self.events.put(("log", f"Row {index}: {message}")),
                                     on_download=received if self.caption_batches else None,
                                     destination=destination if self.caption_batches else None,
                                     **({'gap_progress': gap_progress} if track_gap else {}))
                    self.events.put(("log", f"Row {index}: " + ", ".join(f"{value} {key}" for key, value in counts.items())))
                    had_errors = had_errors or bool(counts["failed"])
                except Exception as error:
                    had_errors = True
                    if isinstance(error, DatasetCountError):
                        self.events.put(('log', f'Downloads stopped: {error}'))
                        break
                    if isinstance(error, caption_queue.QueueSubmissionError):
                        self.events.put(("log", "Downloads stopped because the caption queue could not be saved. "
                                         "Check disk space and folder permissions before starting again."))
                        break
                    # Network errors may include authenticated URLs. Never log them.
                    self.events.put(("log", f"Row {index} failed: {failure_reason(error)}"))
        except Exception:
            had_errors = True
            self.events.put(("log", "Could not scan image folders. Check folder permissions."))
        finally:
            try:
                if batch_id is not None:
                    caption_queue.update(batch_id, "Waiting" if batch_count else "Cancelled", batch_count)
            except (OSError, caption_queue.sqlite3.Error):
                had_errors = True
                self.events.put(("log", "Could not submit the final batch. Reopen the Caption GUI to recover its files."))
            finally:
                if batch_handle:
                    batch_handle.close()
                if download_handle:
                    download_handle.close()
            self.events.put(("finished", "Stopped" if self.cancel.is_set() else
                             "Finished with errors — see log" if had_errors else "Finished"))

    def stop(self):
        self.cancel.set()
        self.status.set("Stopping…")
        self.stop_button.configure(state="disabled")

    def poll(self):
        self.poll_job = None
        try:
            # Bound each batch so log traffic cannot starve Stop or window events.
            for _ in range(200):
                kind, value = self.events.get_nowait()
                if kind == "log":
                    follow = self.log.yview()[1] >= 0.99
                    self.log.configure(state="normal")
                    self.log.insert("end", value + "\n")
                    line_count = int(self.log.index("end-1c").split(".")[0])
                    if line_count > 3000:
                        self.log.delete("1.0", f"{line_count - 3000 + 1}.0")
                    if follow:
                        self.log.see("end")
                    self.log.configure(state="disabled")
                else:
                    self.status.set(value)
                if kind == "finished":
                    self.running = False
                    if self.closing:
                        continue
                    self.caption_toggle.configure(state="normal")
                    self.update_batch_controls()
                    self.blacklist_entry.configure(state="normal")
                    self.add_button.configure(state="normal")
                    self.start_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    self.set_row_controls(True)
        except queue.Empty:
            pass
        self.poll_job = self.root.after(25 if not self.events.empty() else 100, self.poll)

    def close(self):
        if self.closing:
            return
        self.closing = True
        self.add_button.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self.finish_close()

    def finish_close(self):
        self.close_job = None
        if self.running:
            self.stop()
            self.close_job = self.root.after(100, self.finish_close)
        else:
            self.save_settings()
            if self.poll_job is not None:
                self.root.after_cancel(self.poll_job)
                self.poll_job = None
            self.root.destroy()


def main():
    from gui import main as caption_main
    caption_main(initial_tool="download")


if __name__ == "__main__":
    main()

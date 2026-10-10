"""Small standalone, row-based booru download queue."""
import queue
import json
from pathlib import Path
import tempfile
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext

import ttkbootstrap as ttk

from booru_downloader import DownloadJob, ImageIndex, failure_reason, parse_blacklist, run_job
from runtime_config import PROJECT_DIR
import caption_queue

SETTINGS_FILE = PROJECT_DIR / "data" / "caches" / "downloader_settings.json"


class DownloaderGUI:
    def __init__(self, root, settings_file=SETTINGS_FILE, can_start=None):
        self.root = root
        self.settings_file = settings_file
        self.can_start = can_start or (lambda automatic: True)
        self.rows = []
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.running = False
        self.closing = False
        self.poll_job = None
        self.close_job = None
        root.title("VLCaptioner — Image downloader")
        root.geometry("880x860")
        root.minsize(760, 840)
        frame = ttk.Frame(root, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Download original images", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Save originals + TXT/CSV tags to images/ • Skip MD5 filenames in images/ and done/").pack(anchor="w", pady=(4, 12))
        ttk.Label(frame, text="Search tags: separate tags with spaces; keep underscores within a tag (example: blue_eyes long_hair).",
                  wraplength=720, justify="left").pack(anchor="w")
        ttk.Label(frame, text="Post limit = posts checked, including blacklisted posts, images already in images/ or done/, "
                  "unavailable files, and failed downloads. A limit of 100 can download fewer than 100 images.",
                  wraplength=720, justify="left").pack(anchor="w", pady=(4, 0))
        ttk.Label(frame, text="Tag blacklist — applies to all rows", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(10, 2))
        self.blacklist = tk.StringVar()
        self.blacklist_entry = ttk.Entry(frame, textvariable=self.blacklist)
        self.blacklist_entry.pack(fill="x")
        ttk.Label(frame, text="Separate blacklist tags with spaces, just like search tags (example: tall_image watermark comic). "
                  "Keep underscores: tall_image matches; tall image does not. Any matching tag skips the image. "
                  "Whole tags only; letter case and extra spaces are ignored. Leave blank to allow all tags.",
                  wraplength=720, justify="left").pack(anchor="w", pady=(4, 4))
        self.table = ttk.ScrolledFrame(frame, height=180, auto_hide=True)
        self.table.pack(fill="both", expand=True, pady=8)
        for column, label in enumerate(("Source", "Search tags", "Post limit", "")):
            ttk.Label(self.table, text=label).grid(row=0, column=column, sticky="w", padx=4)
        self.table.columnconfigure(1, weight=1)
        batching = ttk.Frame(frame)
        batching.pack(fill="x", pady=8)
        self.auto_caption = tk.BooleanVar(value=False)
        self.batch_size = tk.StringVar(value="50")
        self.caption_toggle = ttk.Checkbutton(batching, text="Caption automatically", variable=self.auto_caption, command=self.update_batch_controls)
        self.caption_toggle.pack(side="left")
        ttk.Label(batching, text="Images per batch").pack(side="left", padx=(18, 6))
        self.batch_entry = ttk.Entry(batching, textvariable=self.batch_size, width=7, state="disabled")
        self.batch_entry.pack(side="left")
        ttk.Label(frame, text="Caption automatically: caption downloaded images in batches while downloads continue.",
                  wraplength=720, justify="left").pack(anchor="w")
        ttk.Label(frame, text="Batch size: larger = fewer model switches; smaller = earlier results. Leftover images form the last batch.",
                  wraplength=720, justify="left").pack(anchor="w", pady=(4, 4))
        ttk.Label(frame, text="Keep the Caption GUI open. Settings: Options tab. Batch progress: Queue tab.",
                  wraplength=720, justify="left").pack(anchor="w")
        controls = ttk.Frame(frame)
        controls.pack(fill="x", pady=8)
        self.add_button = ttk.Button(controls, text="Add row", command=self.add_row)
        self.add_button.pack(side="left")
        self.start_button = ttk.Button(controls, text="Start downloads", command=self.start, bootstyle="success")
        self.start_button.pack(side="left", padx=8)
        self.stop_button = ttk.Button(controls, text="Stop", command=self.stop, state="disabled", bootstyle="danger-outline")
        self.stop_button.pack(side="left")
        self.status = tk.StringVar(value="Ready")
        ttk.Label(controls, textvariable=self.status).pack(side="right")
        self.log = scrolledtext.ScrolledText(frame, height=8, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True)
        ttk.Label(frame, text="Rows and blacklist are remembered on close. Reopening restores settings; downloads start only when you click Start downloads.",
                  wraplength=720, justify="left").pack(anchor="w", pady=(8, 0))
        self.restore_settings()
        self.update_batch_controls()
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.poll_job = root.after(100, self.poll)

    def update_batch_controls(self):
        self.batch_entry.configure(state="normal" if self.auto_caption.get() and not self.running else "disabled")

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
            self.add_row(row["source"], row["tags"], row["limit"])

    def save_settings(self):
        settings = {"auto_caption": self.auto_caption.get(), "batch_size": self.batch_size.get(), "blacklist_separator": "spaces", "blacklist": self.blacklist.get(), "rows": [
            {"source": source.get(), "tags": tags.get(), "limit": limit.get()}
            for source, tags, limit, _ in self.rows]}
        temporary = None
        try:
            self.settings_file.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.settings_file.parent,
                                             prefix=".downloader-settings-", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(json.dumps(settings, ensure_ascii=False, indent=2) + "\n")
            temporary.replace(self.settings_file)
        except OSError:
            messagebox.showwarning("Image downloader", "Could not save downloader settings. Check folder permissions.", parent=self.root)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass  # Cleanup failure must not prevent the window closing.

    def add_row(self, source_value="Danbooru", tags_value="", limit_value="100"):
        source = tk.StringVar(value=source_value)
        tags = tk.StringVar(value=tags_value)
        limit = tk.StringVar(value=limit_value)
        row = len(self.rows) + 1
        widgets = [ttk.Combobox(self.table, textvariable=source, values=("Danbooru", "Gelbooru"), state="readonly", width=12),
                   ttk.Entry(self.table, textvariable=tags), ttk.Entry(self.table, textvariable=limit, width=9)]
        item = (source, tags, limit, widgets)
        widgets.append(ttk.Button(self.table, text="Remove", command=lambda: self.remove_row(item)))
        for col, widget in enumerate(widgets):
            widget.grid(row=row, column=col, sticky="ew", padx=4, pady=4)
        self.rows.append(item)

    def remove_row(self, item):
        if self.running or self.closing:
            return
        for widget in item[3]:
            widget.destroy()
        self.rows.remove(item)
        for index, remaining in enumerate(self.rows, 1):
            for widget in remaining[3]:
                widget.grid_configure(row=index)

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
            blacklist = parse_blacklist(self.blacklist.get())
            jobs = [DownloadJob(source.get(), tags.get().strip(), int(limit.get()), blacklist)
                    for source, tags, limit, _ in self.rows]
            if not jobs or any(not job.tags or job.limit < 1 for job in jobs):
                raise ValueError()
        except ValueError:
            messagebox.showerror("Image downloader", "Enter tags and positive whole-number post limits and batch size.")
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
        for _, _, _, widgets in self.rows:
            for widget in widgets:
                widget.configure(state="disabled")
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
            self.events.put(("log", "Scanning images/ and done/ for existing MD5 filenames…"))
            known = ImageIndex(images, done)
            self.events.put(("log", f"Found {len(known)} existing MD5 images in images/ and done/."))
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

            for index, job in enumerate(jobs, 1):
                if self.cancel.is_set():
                    break
                self.events.put(("log", f"Row {index}: {job.source} — {job.tags}"))
                try:
                    counts = run_job(job, images, done, known, self.cancel,
                                     lambda count, stats: self.events.put(("status", f"Row {index}: {count}/{job.limit} checked; {stats['downloaded']} downloaded")),
                                     log=lambda message: self.events.put(("log", f"Row {index}: {message}")),
                                     on_download=received if self.caption_batches else None,
                                     destination=destination if self.caption_batches else None)
                    self.events.put(("log", f"Row {index}: " + ", ".join(f"{value} {key}" for key, value in counts.items())))
                    had_errors = had_errors or bool(counts["failed"])
                except Exception as error:
                    had_errors = True
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
                    self.log.configure(state="normal")
                    self.log.insert("end", value + "\n")
                    line_count = int(self.log.index("end-1c").split(".")[0])
                    if line_count > 3000:
                        self.log.delete("1.0", f"{line_count - 3000 + 1}.0")
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
                    for _, _, _, widgets in self.rows:
                        for index, widget in enumerate(widgets):
                            widget.configure(state="readonly" if index == 0 else "normal")
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

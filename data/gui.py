"""Cross-platform GUI launcher for the VLTagger pipeline."""
from dataset_files import IMAGE_EXTENSIONS

import crash_logging
crash_logging.install()

import os
import queue
import signal
import subprocess
import sys
from file_io import atomic_write_json
import threading
import tkinter as tk
import importlib.util
from importlib.machinery import SourceFileLoader
import json
import re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from urllib.request import urlopen
from tkinter import messagebox, scrolledtext
import ttkbootstrap as ttk

from pipeline import ROOT, STAGES, stage_command, stage_description, stage_skip_reason
from runtime_config import CONFIG_FILE, read_settings, refiner_defaults, merge_batch_options
from image_failures import ENV_KEY, reset_failures, failures
from booru_setup import check_booru_access
import caption_queue

SETTINGS_FILE = ROOT / "data" / "caches" / "captioner_settings.json"
OPTION_NAMES = ("add_wd14_tags", "overwrite_danbooru_txt", "overwrite_caption_cache",
                "overwrite_caption_files", "add_year_tag", "add_copyright_tags",
                "refiner_thinking", "refiner_json")


def read_captioner_settings(path):
    settings = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(settings, dict):
        raise ValueError("expected a settings object")
    options = settings.get("options", {})
    if not isinstance(options, dict):
        raise ValueError("expected an options object")
    for name in OPTION_NAMES:
        if name in options and not isinstance(options[name], bool):
            raise ValueError(f"{name} must be true or false")
    if "theme" in settings and settings["theme"] not in ("bootstrap-dark", "bootstrap-light"):
        raise ValueError("unrecognized theme")
    return settings


def image_count(folder):
    if not folder.exists():
        return 0
    with os.scandir(folder) as entries:
        return sum(Path(entry.name).suffix.lower() in IMAGE_EXTENSIONS and entry.is_file()
                   for entry in entries)


def check_setup(require_images=True):
    count = image_count(ROOT / "images")
    messages = [f"✓ {count} image(s) in images/" if count else
                "• Add images to images/, then check again." if require_images else
                "• No images for manual captioning yet. Enable Caption automatically in the Download window, or add images here."]
    try:
        settings = read_settings(CONFIG_FILE)
    except (OSError, ValueError) as error:
        return False, "\n".join(messages + [f"• {error}. Copy config.example.txt to config.txt and edit it."])
    required = ("OLLAMA_URL", "OLLAMA_MODEL", "TORII_OLLAMA_MODEL", "DANBOORU_LOGIN", "DANBOORU_API_KEY")
    missing = [key for key in required if not settings.get(key) or settings[key].startswith("your-")]
    messages.append("• Set " + ", ".join(missing) + " in config.txt." if missing else "✓ Required config settings are present")
    url = settings.get("OLLAMA_URL", "").rstrip("/")
    if url:
        try:
            with urlopen(f"{url}/api/tags", timeout=4) as response:
                models = json.load(response).get("models", [])
            installed = {(item.get("name") or item.get("model") or "").casefold() for item in models}
            absent = [settings[key] for key in ("TORII_OLLAMA_MODEL", "OLLAMA_MODEL") if settings.get(key) and settings[key].casefold() not in installed]
            messages.append(f"✓ Ollama is reachable at {url}")
            messages.append("• Models will download on first run: " + ", ".join(absent) if absent else "✓ Both vision models are installed")
        except Exception as error:
            messages.append(f"• Cannot reach Ollama at {url}: {error}. Start Ollama and check the URL.")
            missing.append("OLLAMA_URL")
    with ThreadPoolExecutor(max_workers=2) as checks:
        messages.extend(checks.map(lambda site: check_booru_access(site, settings),
                                   ("Danbooru", "Gelbooru")))
    return (count > 0 or not require_images) and not missing, "\n".join(messages)


class PipelineGUI:
    def __init__(self, root, settings_file=None):
        self.root = root
        self.settings_file = Path(settings_file) if settings_file is not None else SETTINGS_FILE
        self._settings_save_error = None
        self._settings_save_job = None
        self._settings_dirty = set()
        self._settings_missing = False
        self._restoring_settings = False
        self.downloader_app = None
        self.copy_move_app = None
        self.dataset_manager_app = None
        self.layout_viewer_app = None
        self._layout_viewer_module = None
        self.worker_handle = None
        self.manual_download_handle = None
        self.active_batch = None
        self.queue_paused = False
        self.cleanup_errors = {}
        self.run_images = ROOT / "images"
        self.process = None
        self.events = queue.Queue()
        self.cancel_requested = threading.Event()
        self.closing = False
        self.checking = False
        self.ready = False
        self.setup_ready = False
        self.before_counts = (0, 0)
        root.title("VLCaptioner")
        self.running = False
        self.last_outcome = False
        self.current_stage = (0, len(STAGES))
        self.option_widgets = []
        root.geometry("1040x880")
        root.minsize(900, 840)
        frame = ttk.Frame(root, padding=20)
        frame.pack(fill="both", expand=True)
        header = ttk.Frame(frame)
        header.pack(fill="x", pady=(0, 16))
        ttk.Label(header, text="VLCaptioner", font=("Segoe UI", 22, "bold")).pack(side="left")
        ttk.Button(header, text="Light / dark", bootstyle="secondary-outline", command=self.toggle_theme).pack(side="right")
        self.status = ttk.Label(header, text="Checking setup", bootstyle="info", padding=(12, 6))
        self.status.pack(side="right", padx=12)
        self.notebook = ttk.Notebook(frame)
        self.notebook.pack(fill="x")
        run_page = ttk.Frame(self.notebook, padding=16)
        options_page = ttk.Frame(self.notebook, padding=12)
        ttk.Label(options_page, text="Caption and tag options are saved automatically.",
                  bootstyle="secondary").pack(anchor="w", padx=8, pady=(0, 4))
        self.advanced = ttk.ScrolledFrame(options_page, height=260, auto_hide=True, padding=8)
        self.advanced.pack(fill="both", expand=True)
        tags_page = ttk.Frame(self.notebook, padding=12)
        self.tag_options = ttk.ScrolledFrame(tags_page, height=260, auto_hide=True, padding=8)
        self.tag_options.pack(fill="both", expand=True)
        self.notebook.add(run_page, text="Caption images")
        self.notebook.add(options_page, text="Options")
        self.notebook.add(tags_page, text="Tag options")
        ttk.Label(self.advanced, text="Caption refinement", font=("Segoe UI", 13, "bold")).pack(anchor="w", pady=(0, 12))
        ttk.Label(self.tag_options, text="Tag files and metadata", font=("Segoe UI", 13, "bold")).pack(anchor="w", pady=(0, 12))
        steps_page = self.steps_page = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(steps_page, text="Steps")
        self.steps = ttk.Treeview(steps_page, columns=("status", "detail"), height=9)
        self.steps.heading("#0", text="Step")
        self.steps.heading("status", text="Status")
        self.steps.heading("detail", text="Behavior for selected options")
        self.steps.column("#0", width=245, stretch=False)
        self.steps.column("status", width=85, stretch=False)
        self.steps.column("detail", width=550)
        self.steps.pack(fill="both", expand=True)
        queue_page = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(queue_page, text="Queue")
        ttk.Label(queue_page, text="Captioning queue for the Image downloader",
                  font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(queue_page, text="In the Download window, turn on ‘Caption automatically’ and choose ‘Images per batch’. "
                  "Downloaded images arrive here in batches and are captioned one batch at a time while downloads continue.",
                  wraplength=850, justify="left").pack(anchor="w", pady=(4, 4))
        ttk.Label(queue_page, text="Choose caption settings in Options before downloading. Each batch uses those settings when it starts. "
                  "Follow detailed progress in Steps and find finished files in Results below.",
                  wraplength=850, justify="left").pack(anchor="w")
        self.queue_tree = ttk.Treeview(queue_page, columns=("count", "status"), height=8)
        self.queue_tree.heading("#0", text="Batch")
        self.queue_tree.heading("count", text="Images")
        self.queue_tree.heading("status", text="Status")
        self.queue_tree.pack(fill="both", expand=True, pady=8)
        queue_controls = ttk.Frame(queue_page)
        queue_controls.pack(fill="x")
        self.queue_pause_button = ttk.Button(queue_controls, text="Pause queue", command=self.toggle_queue)
        self.queue_pause_button.pack(side="left")
        ttk.Button(queue_controls, text="Retry selected", command=lambda: self.change_batch("Waiting")).pack(side="left", padx=8)
        ttk.Button(queue_controls, text="Cancel selected", command=lambda: self.change_batch("Cancelled")).pack(side="left")
        ttk.Label(queue_page, text="Pause queue lets the current batch finish. Use Stop below to stop it sooner. "
                  "To continue a stopped or failed batch, select it, click Retry selected, then Resume queue. "
                  "Cancel selected returns the batch’s images and tag files to images/ and removes it from the queue.",
                  wraplength=850, justify="left").pack(anchor="w", pady=6)
        setup = ttk.Labelframe(run_page, text="1. Check setup", padding=12)
        setup.pack(fill="x", pady=(0, 12))
        self.setup_message = tk.StringVar(value="Checking images, configuration, and Ollama…")
        ttk.Label(setup, textvariable=self.setup_message, justify="left", wraplength=810).pack(side="left", fill="x", expand=True)
        self.check_button = ttk.Button(setup, text="Check again", bootstyle="secondary-outline", command=self.refresh_setup)
        self.check_button.pack(side="right", anchor="n", padx=(10, 0))
        downloads = ttk.Labelframe(run_page, text="2. Download images (optional)", padding=12)
        downloads.pack(fill="x", pady=(0, 12))
        ttk.Label(downloads, text="Get original images from Danbooru or Gelbooru using tag searches.",
                  wraplength=810, justify="left").pack(anchor="w")
        ttk.Label(downloads, text="Images and matching tag files are saved to images/. "
                  "Images already in images/, done/, or done1/ are skipped.",
                  wraplength=810, justify="left").pack(anchor="w", pady=(4, 8))
        download_tools = ttk.Frame(downloads)
        download_tools.pack(anchor="w")
        ttk.Button(download_tools, text="Open image downloader", bootstyle="primary",
                   command=self.open_downloader_gui).pack(side="left")
        ttk.Button(download_tools, text="Open dataset manager", bootstyle="secondary-outline",
                   command=self.open_dataset_manager_gui).pack(side="left", padx=(8, 0))
        images = ttk.Labelframe(run_page, text="3. Add images", padding=12)
        images.pack(fill="x", pady=(0, 12))
        ttk.Label(images, text="Place your images in the images folder. Matching tag files are optional.").pack(anchor="w")
        folders = ttk.Frame(images)
        folders.pack(fill="x", pady=(8, 0))
        ttk.Button(folders, text="Open images folder", bootstyle="primary-outline", command=lambda: self.open_folder("images")).pack(side="left")
        self.copy_move_button = ttk.Button(folders, text="Copy / move by tags", bootstyle="secondary-outline", command=self.open_copy_move_gui)
        self.copy_move_button.pack(side="left", padx=8)
        self.add_wd14_tags = tk.BooleanVar(value=False)
        self.add_advanced_option(
            "Add missing tags to existing tag files", self.add_wd14_tags,
            "Append missing tags with at least 0.91 confidence. Images without tags are always tagged.",
            parent=self.tag_options,
        )
        self.overwrite_danbooru_txt = tk.BooleanVar()
        self.add_advanced_option(
            "Replace existing tags with fresh Danbooru or Gelbooru tags", self.overwrite_danbooru_txt,
            "On: replace matching tag files with tags from a found post. Off: keep existing tag files and skip their lookup.",
            parent=self.tag_options,
        )
        defaults = refiner_defaults()
        self.refiner_thinking = tk.BooleanVar(value=defaults["refiner_thinking"])
        self.add_advanced_option("Enable refiner thinking", self.refiner_thinking,
                                 "Enable model reasoning for refinement and JSON. May take longer.")
        self.refiner_json = tk.BooleanVar(value=defaults["refiner_json"])
        self.add_advanced_option("Create .json files", self.refiner_json,
                                 "Generate MOSS layout JSON with scene summary, elements, text and bounding boxes. Cache bypass also regenerates existing JSON files.")
        self.overwrite_caption_cache = tk.BooleanVar()
        self.add_advanced_option(
            "Bypass refinement caption cache", self.overwrite_caption_cache,
            "Bypass .long, .short and JSON caches. With JSON creation enabled, also regenerate existing JSON files. To replace completed long/short captions, enable the option below. Torii reports are reused.",
        )
        self.overwrite_caption_files = tk.BooleanVar(value=False)
        self.add_advanced_option(
            "Overwrite existing .long and .short files", self.overwrite_caption_files,
            "Replace existing caption files. Enable cache bypass too for fresh model output; otherwise matching cached captions may be reused.",
        )
        self.add_year_tag = tk.BooleanVar(value=False)
        self.add_advanced_option(
            "Add upload year to .tag files", self.add_year_tag,
            "On: add year YYYY when a post date is available. Off: omit the year tag.",
            parent=self.tag_options,
        )
        self.add_copyright_tags = tk.BooleanVar(value=False)
        self.add_advanced_option(
            "Include copyright and series tags in .tag files", self.add_copyright_tags,
            "On: include them in .tag and .combined files. Off: omit them there; source tags stay available.",
            parent=self.tag_options,
        )
        controls = ttk.Frame(frame)
        controls.pack(fill="x", pady=(16, 8))
        self.start_button = ttk.Button(controls, text="Start captioning", bootstyle="success", command=self.start, state="disabled")
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(controls, text="Stop", bootstyle="danger-outline", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        ttk.Label(controls, text="You can stop and resume. Completed work is kept.", bootstyle="secondary").pack(side="left", padx=12)
        self.progress = tk.StringVar(value="Check setup, choose any options, then start captioning.")
        ttk.Label(frame, textvariable=self.progress, wraplength=950).pack(anchor="w", pady=(0, 8))
        self.progress_bar = ttk.Progressbar(frame, maximum=100, bootstyle="success-striped")
        self.progress_bar.pack(fill="x", pady=(0, 12))
        results = ttk.Labelframe(frame, text="Results", padding=12)
        results.pack(fill="x", pady=(0, 12))
        ttk.Button(results, text="Completed files", bootstyle="success-outline", command=lambda: self.open_folder("done")).pack(side="right")
        ttk.Button(results, text="Needs review", bootstyle="warning-outline", command=lambda: self.open_folder("captionReview")).pack(side="right", padx=8)
        ttk.Button(results, text="Layout viewer", bootstyle="info-outline", command=self.open_layout_viewer).pack(side="right", padx=8)
        self.result_message = tk.StringVar(value="Completed images and captions appear here after processing.")
        ttk.Label(results, textvariable=self.result_message, wraplength=570).pack(side="left", fill="x", expand=True)
        logs = ttk.Labelframe(frame, text="Activity", padding=8)
        logs.pack(fill="both", expand=True)
        self.log = scrolledtext.ScrolledText(logs, state="disabled", wrap="word", height=8, font=("Consolas", 10))
        self.log.pack(fill="both", expand=True)
        self.restore_settings()
        self.style_log()
        for stage in STAGES:
            self.steps.insert("", "end", iid=stage.script, text=stage.label, values=("Pending", ""))
        for name in OPTION_NAMES:
            getattr(self, name).trace_add("write", lambda *args, name=name: self.options_changed(name))
        self.update_step_plan()
        root.after(100, self.read_events)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh_setup()
        root.after(1000, self.poll_queue)

    def toggle_queue(self):
        self.queue_paused = not self.queue_paused
        self.queue_pause_button.configure(text="Resume queue" if self.queue_paused else "Pause queue")

    def change_batch(self, status):
        try:
            for identifier in self.queue_tree.selection():
                row = next((job for job in caption_queue.jobs() if job["id"] == identifier), None)
                if row and row["status"] in ("Waiting", "Failed", "Stopped", "Needs retry", "Cancelled"):
                    caption_queue.transition(identifier, (row["status"],), status)
        except (OSError, caption_queue.sqlite3.Error) as error:
            self.progress.set(f"Could not change the queued batch: {error}")

    def poll_queue(self):
        if self.closing:
            return
        try:
            caption_queue.recover_downloads()
            jobs = caption_queue.jobs()
            known_ids = {job["id"] for job in jobs}
            for identifier in self.queue_tree.get_children():
                if identifier not in known_ids:
                    self.queue_tree.delete(identifier)
                    self.cleanup_errors.pop(identifier, None)
            if not self.running and any(job["status"] == "Running" for job in jobs):
                handle = caption_queue.worker_lock()
                if handle:
                    try:
                        for job in jobs:
                            if job["status"] == "Running":
                                if caption_queue.transition(job["id"], ("Running",), "Stopped"):
                                    job["status"] = "Stopped"
                    finally:
                        handle.close()
            for job in jobs:
                identifier = job["id"]
                if caption_queue.remove_missing(identifier):
                    if self.queue_tree.exists(identifier):
                        self.queue_tree.delete(identifier)
                    continue
                display_status = job["status"]
                if job["status"] in ("Completed", "Cancelled"):
                    try:
                        cleaned = caption_queue.cleanup_batch(identifier, job["status"])
                        display_status += " — waiting for cleanup"
                    except (OSError, caption_queue.sqlite3.Error) as error:
                        cleaned = False
                        display_status += " — cleanup blocked"
                        if self.cleanup_errors.get(identifier) != str(error):
                            self.append(f"Batch {identifier[:6]} cleanup blocked: {error}\n")
                            self.cleanup_errors[identifier] = str(error)
                    if cleaned:
                        self.cleanup_errors.pop(identifier, None)
                        if self.queue_tree.exists(identifier):
                            self.queue_tree.delete(identifier)
                        continue
                values = (job["count"], display_status)
                if self.queue_tree.exists(identifier):
                    self.queue_tree.item(identifier, values=values)
                else:
                    self.queue_tree.insert("", "end", iid=identifier, text=job["label"] + " " + identifier[:6], values=values)
            if not self.running and not self.checking and self.setup_ready and not self.queue_paused:
                job = next((job for job in jobs if job["status"] == "Waiting"
                            and (caption_queue.QUEUE_DIR / job["id"]).exists()), None)
                if job:
                    self.start(job)
        except (OSError, caption_queue.sqlite3.Error) as error:
            self.progress.set(f"Queue unavailable: {error}")
        self.root.after(1000, self.poll_queue)

    def add_advanced_option(self, title, variable, explanation, parent=None):
        parent = self.advanced if parent is None else parent
        widget = ttk.Checkbutton(parent, text=title, variable=variable, bootstyle="primary-round-toggle")
        widget.pack(anchor="w", pady=(0, 3))
        self.option_widgets.append(widget)
        ttk.Label(parent, text=explanation, wraplength=850, bootstyle="secondary").pack(anchor="w", padx=(38, 0), pady=(0, 12))

    def style_log(self):
        colors = self.root.style.colors
        self.log.configure(background=colors.inputbg, foreground=colors.inputfg, insertbackground=colors.inputfg)

    def selected_options(self):
        return dict(overwrite_danbooru_txt=self.overwrite_danbooru_txt.get(),
                    overwrite_caption_cache=self.overwrite_caption_cache.get(),
                    skip_wd14_high_confidence=not self.add_wd14_tags.get(),
                    add_year_tag=self.add_year_tag.get(),
                    add_copyright_tags=self.add_copyright_tags.get(),
                    overwrite_caption_files=self.overwrite_caption_files.get(),
                    refiner_thinking=self.refiner_thinking.get(), refiner_json=self.refiner_json.get())

    def restore_settings(self):
        try:
            settings = read_captioner_settings(self.settings_file)
        except FileNotFoundError:
            self._settings_missing = True
            return
        except (OSError, ValueError, RecursionError) as error:
            self.append(f"Could not load saved captioner settings: {error}. Using defaults.\n")
            return
        # Validate the entire document before replacing any defaults.
        self._restoring_settings = True
        try:
            self.root.style.theme_use(settings.get("theme", self.root.style.theme_use()))
            for name, value in settings.get("options", {}).items():
                if name in OPTION_NAMES:
                    getattr(self, name).set(value)
        finally:
            self._restoring_settings = False

    def save_settings(self):
        if self._settings_save_job is not None:
            self.root.after_cancel(self._settings_save_job)
            self._settings_save_job = None
        if not self._settings_dirty and not self._settings_missing:
            return
        lock = None
        try:
            self.settings_file.parent.mkdir(parents=True, exist_ok=True)
            lock = caption_queue.file_lock(self.settings_file.with_suffix(".lock"))
            if lock is None:
                if not self.closing:
                    self._settings_save_job = self.root.after(100, self.save_settings)
                    return
                raise OSError("Another window is saving captioner settings; could not save before closing")
            current_options = {name: getattr(self, name).get() for name in OPTION_NAMES}
            current_theme = self.root.style.theme_use()
            try:
                settings = read_captioner_settings(self.settings_file)
            except FileNotFoundError:
                settings = {}
            except (ValueError, RecursionError):
                # An explicit change can repair invalid settings. Merely closing cannot.
                if not self._settings_dirty:
                    self._settings_missing = False
                    return
                settings = {}
            else:
                if not self._settings_dirty:
                    self._settings_missing = False
                    return
            settings["options"] = {**current_options, **settings.get("options", {})}
            settings.setdefault("theme", current_theme)
            for name in self._settings_dirty:
                if name == "theme":
                    settings["theme"] = current_theme
                else:
                    settings["options"][name] = current_options[name]
            atomic_write_json(self.settings_file, settings, indent=2, trailing_newline=True,
                              prefix=".captioner-settings-")
            self._settings_dirty.clear()
            self._settings_missing = False
            self._settings_save_error = None
        except (OSError, ValueError, RecursionError) as error:
            if str(error) != self._settings_save_error:
                self.append(f"Could not save captioner settings: {error}\n")
                self._settings_save_error = str(error)
        finally:
            if lock is not None:
                lock.close()

    def options_changed(self, name):
        if self._restoring_settings:
            return
        self._settings_dirty.add(name)
        self.update_step_plan()
        self.save_settings()

    def update_step_plan(self):
        if self.running:
            return
        options = self.selected_options()
        for stage in STAGES:
            self.steps.set(stage.script, "detail", stage_description(stage, **options))
            self.steps.set(stage.script, "status", "Pending")

    def toggle_theme(self):
        current = self.root.style.theme_use()
        self.root.style.theme_use("bootstrap-light" if current == "bootstrap-dark" else "bootstrap-dark")
        self.style_log()
        self._settings_dirty.add("theme")
        self.save_settings()
        if self.layout_viewer_app and self.layout_viewer_app.root.winfo_exists():
            self.layout_viewer_app.apply_theme()
        if self.downloader_app and self.downloader_app.root.winfo_exists():
            self.downloader_app.apply_theme()
        if self.dataset_manager_app and self.dataset_manager_app.root.winfo_exists():
            self.dataset_manager_app.apply_theme()

    def open_folder(self, name):
        folder = ROOT / name
        folder.mkdir(exist_ok=True)
        try:
            if os.name == "nt":
                os.startfile(folder)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except OSError as error:
            messagebox.showerror("VLCaptioner", f"Could not open {folder}: {error}")

    def open_layout_viewer(self):
        viewer = ROOT / "data" / "layout_viewer.pyw"
        try:
            if self._layout_viewer_module is None:
                loader = SourceFileLoader("vl_layout_viewer", str(viewer))
                spec = importlib.util.spec_from_loader(loader.name, loader)
                module = importlib.util.module_from_spec(spec)
                loader.exec_module(module)
                self._layout_viewer_module = module
            self.open_tool(
                "layout_viewer_app",
                lambda window: self._layout_viewer_module.LayoutViewer(window),
            )
        except Exception as error:
            messagebox.showerror("VLCaptioner", f"Could not open the layout viewer: {error}")

    def refresh_setup(self):
        if self.checking or self.running:
            return
        self.checking = True
        self.ready = False
        self.start_button.configure(state="disabled")
        self.check_button.configure(state="disabled")
        self.setup_message.set("Checking images, configuration, and Ollama…")
        try:
            threading.Thread(target=self._check_setup, daemon=True).start()
        except RuntimeError:
            self.events.put(("setup", False, "Could not start the setup check. Click Check again to retry."))

    def _check_setup(self):
        try:
            result = check_setup(require_images=False)
        except Exception as error:
            result = (False, f"Setup check failed: {error}")
        self.events.put(("setup", *result))

    def append(self, value):
        follow = self.log.yview()[1] >= 0.99
        self.log.configure(state="normal")
        self.log.insert("end", value)
        # Limit retained activity so long batches don't grow the Tk text widget indefinitely.
        line_count = int(self.log.index("end-1c").split(".")[0])
        if line_count > 3000:
            self.log.delete("1.0", f"{line_count - 3000 + 1}.0")
        if follow:
            self.log.see("end")
        self.log.configure(state="disabled")

    def open_tool(self, attribute, factory):
        if self.closing:
            return
        app = getattr(self, attribute)
        if app is not None and app.root.winfo_exists():
            app.root.deiconify()
            app.root.lift()
            app.root.focus_set()
            return
        window = ttk.Toplevel(master=self.root)
        window.transient(self.root)
        try:
            setattr(self, attribute, factory(window))
        except Exception:
            window.destroy()
            raise

    def open_downloader_gui(self):
        from downloader_gui import DownloaderGUI
        self.open_tool("downloader_app", lambda window: DownloaderGUI(
            window, can_start=lambda automatic: not self.closing
            and not (self.copy_move_app and self.copy_move_app.running)
            and (automatic or not self.running)))

    def open_dataset_manager_gui(self):
        from dataset_manager import DatasetManager
        self.open_tool("dataset_manager_app", lambda window: DatasetManager(window, download_more=self.download_more))

    def download_more(self, tag):
        self.open_downloader_gui()
        self.downloader_app.prepare_gap_download(tag)

    def open_copy_move_gui(self):
        from copy_move_images_GUI import CopyMoveApp
        self.open_tool("copy_move_app", lambda window: CopyMoveApp(
            window, can_start=lambda: not self.running and not self.closing
            and not (self.downloader_app and self.downloader_app.running)))

    def start(self, batch=None):
        try:
            self._start(batch)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, caption_queue.sqlite3.Error) as error:
            if self.active_batch:
                try:
                    caption_queue.transition(self.active_batch, ("Waiting", "Running"), "Failed")
                except (OSError, caption_queue.sqlite3.Error):
                    pass
            if self.worker_handle:
                self.worker_handle.close()
                self.worker_handle = None
            if self.manual_download_handle:
                self.manual_download_handle.close()
                self.manual_download_handle = None
            self.running = False
            self.active_batch = None
            batch_problem = batch is not None and isinstance(error, (ValueError, KeyError, TypeError))
            if not batch_problem:
                self.queue_paused = True
                self.queue_pause_button.configure(text="Resume queue")
            self.progress.set(f"Could not start captioning: {error}")
            for widget in self.option_widgets:
                widget.configure(state="normal")
            self.stop_button.configure(state="disabled")
            self.copy_move_button.configure(state="normal")
            self.refresh_setup()

    def _start(self, batch=None):
        if self.closing or self.running or self.checking or (batch is None and not self.ready) or (batch is not None and not self.setup_ready):
            return
        if self.copy_move_app is not None and self.copy_move_app.running:
            self.progress.set("Waiting for the copy / move operation to finish before captioning.")
            return
        self.worker_handle = caption_queue.worker_lock()
        if self.worker_handle is None:
            self.progress.set("Another caption window is processing images. Waiting for it to finish.")
            return
        if batch is None:
            self.manual_download_handle = caption_queue.downloader_lock()
            if self.manual_download_handle is None:
                self.worker_handle.close()
                self.worker_handle = None
                self.progress.set("Wait for downloads to finish before starting manual captioning.")
                return
        if batch:
            batch = next((job for job in caption_queue.jobs() if job["id"] == batch["id"]), None)
            if batch is None or batch["status"] != "Waiting":
                self.worker_handle.close()
                self.worker_handle = None
                return
        # A previously interrupted process leaves a durable batch for explicit retry.
        for job in caption_queue.jobs():
            if job["status"] == "Running":
                caption_queue.update(job["id"], "Stopped")
        self.active_batch = batch["id"] if batch else None
        self.run_images = caption_queue.QUEUE_DIR / self.active_batch if batch else ROOT / "images"
        if batch and not self.run_images.is_dir():
            if caption_queue.remove_missing(self.active_batch):
                if self.queue_tree.exists(self.active_batch):
                    self.queue_tree.delete(self.active_batch)
            self.active_batch = None
            self.worker_handle.close()
            self.worker_handle = None
            return
        self.run_images.mkdir(parents=True, exist_ok=True)
        selected = self.selected_options()
        saved = json.loads(batch["options"]) if batch and batch["options"] else selected
        self.run_options = merge_batch_options(saved, selected)
        self.failure_file = reset_failures()
        self.ready = False
        self.running = True
        self.last_outcome = False
        if batch:
            if not caption_queue.transition(self.active_batch, ("Waiting",), "Running", options=self.run_options):
                self.running = False
                self.active_batch = None
                self.worker_handle.close()
                self.worker_handle = None
                return
        for stage in STAGES:
            self.steps.set(stage.script, "status", "Pending")
        if not batch:
            self.notebook.select(self.steps_page)
        for widget in self.option_widgets:
            widget.configure(state="disabled")
        self.progress_bar.configure(value=0)
        self.before_counts = (image_count(ROOT / "done"), image_count(ROOT / "captionReview"))
        stages = list(STAGES)
        self.cancel_requested.clear()
        self.append("Starting VLCaptioner pipeline...\n\n")
        for stage in stages:
            self.append(f"{stage.label}: {stage_description(stage, **self.run_options)}\n")
        self.start_button.configure(state="disabled")
        self.check_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.copy_move_button.configure(state="disabled")
        self.status.configure(text="Running")
        self.progress.set("Starting pipeline…")
        self.save_settings()
        threading.Thread(
            target=self.run_pipeline,
            args=(
                stages,
                self.run_options["overwrite_danbooru_txt"],
                self.run_options["overwrite_caption_cache"],
                self.run_options["skip_wd14_high_confidence"],
                self.run_options["add_year_tag"],
                self.run_options["add_copyright_tags"],
                self.run_options["overwrite_caption_files"],
                self.run_options["refiner_thinking"], self.run_options["refiner_json"],
            ),
            daemon=True,
        ).start()

    def run_pipeline(
        self,
        stages,
        overwrite_danbooru_txt=False,
        overwrite_caption_cache=False,
        skip_wd14_high_confidence=False,
        add_year_tag=False,
        add_copyright_tags=False,
        overwrite_caption_files=False,
        refiner_thinking=None,
        refiner_json=None,
    ):
        code = 0
        shared_failure = False
        active_index = None
        run_options = dict(overwrite_danbooru_txt=overwrite_danbooru_txt,
                           overwrite_caption_cache=overwrite_caption_cache,
                           skip_wd14_high_confidence=skip_wd14_high_confidence,
                           add_year_tag=add_year_tag, add_copyright_tags=add_copyright_tags,
                           overwrite_caption_files=overwrite_caption_files,
                           refiner_thinking=refiner_thinking, refiner_json=refiner_json)
        try:
            for index, stage in enumerate(stages, 1):
                if self.cancel_requested.is_set():
                    code = 130
                    break
                active_index = index
                reason = stage_skip_reason(stage, self.run_images, **run_options)
                if reason:
                    self.events.put(("step_status", stage.script, "Skipped", reason))
                    self.events.put(f"Skipped {stage.label}: {reason}\n")
                    continue
                self.events.put(("step_status", stage.script, "Running", stage_description(stage, **run_options)))
                self.events.put(("stage", index, len(stages), stage.label))
                self.events.put(f"\n=== {stage.label} ===\n")
                options = ({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True})
                # pythonw lacks console streams; use python for captured worker logs.
                worker_python = sys.executable
                if os.name == "nt" and Path(worker_python).name.lower() == "pythonw.exe":
                    worker_python = str(Path(worker_python).with_name("python.exe"))
                self.process = subprocess.Popen(
                    stage_command(stage, worker_python, overwrite_danbooru_txt,
                                  overwrite_caption_cache, skip_wd14_high_confidence,
                                  add_year_tag, add_copyright_tags,
                                  overwrite_caption_files, refiner_thinking, refiner_json), cwd=ROOT,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                    encoding="utf-8", errors="replace",
                    env={**os.environ, "PYTHONUNBUFFERED": "1", ENV_KEY: self.failure_file, "VLCAPTIONER_IMAGES": str(self.run_images)}, **options,
                )
                if self.cancel_requested.is_set():
                    self.terminate_process_tree()
                with self.process.stdout:
                    for line in self.process.stdout:
                        if "OllamaUnavailableError:" in line:
                            shared_failure = True
                        self.events.put(line)
                code = self.process.wait()
                self.events.put(("step_status", stage.script,
                                 "Stopped" if self.cancel_requested.is_set() else ("Failed" if code else "Done"),
                                 stage_description(stage, **run_options)))
                if self.cancel_requested.is_set():
                    code = 130
                    break
                if code:
                    break
        except Exception as error:
            code = 1
            shared_failure = True
            process = getattr(self, "process", None)
            if process is not None and process.poll() is None:
                self.terminate_process_tree()
                process.wait()
            self.events.put(f"\nPipeline could not start: {error}\n")
            if active_index is not None:
                self.events.put(("step_status", stages[active_index - 1].script, "Failed", str(error)))
        self.events.put(("done", code, shared_failure))

    def read_events(self):
        try:
            for _ in range(200):
                event = self.events.get_nowait()
                if isinstance(event, tuple):
                    if event[0] == "step_status":
                        _, script, status, detail = event
                        self.steps.item(script, values=(status, detail))
                        if status == "Skipped":
                            step_index = next(i for i, stage in enumerate(STAGES, 1) if stage.script == script)
                            self.progress_bar.configure(value=100 * step_index / len(STAGES))
                        continue
                    if event[0] == "setup":
                        _, self.setup_ready, message = event
                        self.checking = False
                        self.ready = False
                        self.ready = self.setup_ready and image_count(ROOT / "images") > 0
                        self.setup_message.set(message)
                        self.check_button.configure(state="normal")
                        self.start_button.configure(state="normal" if self.ready else "disabled")
                        if not self.last_outcome:
                            self.status.configure(text="Ready" if self.ready else "Ready for downloads" if self.setup_ready else "Setup needed",
                                                  bootstyle="success" if self.setup_ready else "warning")
                        continue
                    if event[0] == "stage":
                        _, index, total, label = event
                        self.current_stage = (index, total)
                        self.progress_bar.configure(value=100 * (index - 1) / total)
                        self.progress.set(f"Stage {index} of {total}: {label}. Images remaining: {image_count(self.run_images)}; completed: {image_count(ROOT / 'done')}; needing review: {image_count(ROOT / 'captionReview')}.")
                        continue
                    code = event[1]
                    shared_failure = bool(event[2]) if len(event) > 2 else False
                    self.process = None
                    self.running = False
                    was_batch = bool(self.active_batch)
                    if self.active_batch:
                        try:
                            status = "Stopped" if code == 130 else "Failed" if code else "Needs retry" if image_count(self.run_images) else "Completed"
                            caption_queue.update(self.active_batch, status)
                        except (OSError, caption_queue.sqlite3.Error) as error:
                            status = "Failed"
                            self.append(f"Could not save batch status: {error}. The batch can be recovered on reopening.\n")
                            self.queue_paused = True
                        self.active_batch = None
                        if code == 130 or shared_failure:
                            self.queue_paused = True
                        if self.queue_paused:
                            self.queue_pause_button.configure(text="Resume queue")
                    if self.worker_handle:
                        self.worker_handle.close()
                        self.worker_handle = None
                    if self.manual_download_handle:
                        self.manual_download_handle.close()
                        self.manual_download_handle = None
                    self.last_outcome = True
                    for script in self.steps.get_children():
                        if self.steps.set(script, "status") == "Pending":
                            self.steps.set(script, "status", "Not run")
                    for widget in self.option_widgets:
                        widget.configure(state="normal")
                    if code == 0:
                        self.progress_bar.configure(value=100)
                    if self.closing:
                        return
                    self.stop_button.configure(state="disabled")
                    self.copy_move_button.configure(state="normal")
                    self.status.configure(text="Stopped" if code == 130 else ("Finished" if code == 0 else f"Failed ({code})"), bootstyle="warning" if code == 130 else ("success" if code == 0 else "danger"))
                    try:
                        skipped_count = len(failures(getattr(self, "failure_file", None)))
                    except (OSError, ValueError, TypeError) as error:
                        skipped_count = 0
                        self.append(f"Could not read skipped-image report: {error}\n")
                    if code == 0 and skipped_count:
                        self.status.configure(text=f"Finished; {skipped_count} skipped", bootstyle="warning")
                    done = image_count(ROOT / "done")
                    review = image_count(ROOT / "captionReview")
                    self.result_message.set(f"Completed: {done} total ({max(0, done - self.before_counts[0])} added this run). Need review: {review} total ({max(0, review - self.before_counts[1])} added this run).")
                    self.progress.set("Run stopped. Completed work is kept; start again to resume." if code == 130 else ("Run finished. Review the files below." if code == 0 else "Run failed. Check the log below, fix the issue, then start again."))
                    if code == 0 and skipped_count:
                        self.progress.set(f"Finished with {skipped_count} skipped image(s). Their originals are kept for retry; see the activity log.")
                    if code not in (0, 130) and (not was_batch or shared_failure):
                        messagebox.showerror("VLCaptioner", f"Pipeline failed with exit code {code}. See the log below.")
                    if was_batch and code not in (0, 130) and not shared_failure:
                        self.progress.set("Batch failed; its files are kept for retry. Continuing with other queued batches.")
                    if was_batch and code == 130:
                        self.progress.set("Batch stopped. Select it in Queue, click Retry selected, then Resume queue.")
                    self.refresh_setup()
                else:
                    self.append(event)
                    match = re.search(r"(?:Refining captions|Torii captions): ([\d,]+) of ([\d,]+) images processed", event)
                    if match:
                        processed, total = (int(value.replace(",", "")) for value in match.groups())
                        self.progress.set(event.strip())
                        if total:
                            stage_index, stage_total = self.current_stage
                            self.progress_bar.configure(value=100 * (stage_index - 1 + min(1, processed / total)) / stage_total)
        except queue.Empty:
            pass
        except (OSError, ValueError, TypeError, caption_queue.sqlite3.Error) as error:
            self.append(f"Could not update caption progress: {error}\n")
        self.root.after(20 if not self.events.empty() else 100, self.read_events)

    def terminate_process_tree(self):
        process = self.process
        if not process or process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass  # The worker finished between poll() and killpg().

    def stop(self):
        self.cancel_requested.set()
        self.stop_button.configure(state="disabled")
        self.status.configure(text="Stopping")
        self.progress.set("Stopping. Completed work is kept; start again to resume.")
        self.append("\nStopping pipeline...\n")
        self.terminate_process_tree()

    def close(self):
        if self.closing:
            return
        self.closing = True
        self.save_settings()
        self.queue_paused = True
        for app in (self.downloader_app, self.copy_move_app, self.dataset_manager_app):
            if app is not None and app.root.winfo_exists():
                app.close()
        if self.running:
            self.stop()
        self.finish_close()

    def finish_close(self):
        children_open = any(app is not None and app.root.winfo_exists()
                            for app in (self.downloader_app, self.copy_move_app, self.dataset_manager_app))
        if self.running or children_open:
            self.root.after(100, self.finish_close)
        else:
            self.root.destroy()


def main(initial_tool=None):
    root = ttk.Window(themename="bootstrap-dark")
    crash_logging.attach_tk(root)
    app = PipelineGUI(root)
    if initial_tool == "download":
        app.open_downloader_gui()
    elif initial_tool == "move":
        app.open_copy_move_gui()
    root.mainloop()


if __name__ == "__main__":
    main()

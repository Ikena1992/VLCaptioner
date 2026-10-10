"""Cross-platform GUI launcher for the VLTagger pipeline."""

import os
import queue
import signal
import subprocess
import sys
import threading
import tkinter as tk
import json
import re
from pathlib import Path
from urllib.request import urlopen
from tkinter import messagebox, scrolledtext
import ttkbootstrap as ttk

from pipeline import ROOT, STAGES, stage_command, stage_description, stage_skip_reason
from runtime_config import CONFIG_FILE, read_settings
from image_failures import ENV_KEY, reset_failures, failures

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".gif", ".webp"}


def image_count(folder):
    return sum(p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS for p in folder.iterdir()) if folder.exists() else 0


def check_setup():
    count = image_count(ROOT / "images")
    messages = [f"✓ {count} image(s) in images/" if count else "• Add images to images/, then check again."]
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
    return count > 0 and not missing, "\n".join(messages)


class PipelineGUI:
    def __init__(self, root):
        self.root = root
        self.process = None
        self.events = queue.Queue()
        self.cancel_requested = threading.Event()
        self.closing = False
        self.checking = False
        self.ready = False
        self.before_counts = (0, 0)
        root.title("VLCaptioner")
        self.running = False
        self.last_outcome = False
        self.current_stage = (0, len(STAGES))
        self.option_widgets = []
        root.geometry("1040x820")
        root.minsize(900, 760)
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
        self.advanced = ttk.ScrolledFrame(options_page, height=260, auto_hide=True, padding=8)
        self.advanced.pack(fill="both", expand=True)
        self.notebook.add(run_page, text="Caption images")
        self.notebook.add(options_page, text="Options")
        steps_page = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(steps_page, text="Steps")
        self.steps = ttk.Treeview(steps_page, columns=("status", "detail"), height=9)
        self.steps.heading("#0", text="Step")
        self.steps.heading("status", text="Status")
        self.steps.heading("detail", text="Behavior for selected options")
        self.steps.column("#0", width=245, stretch=False)
        self.steps.column("status", width=85, stretch=False)
        self.steps.column("detail", width=550)
        self.steps.pack(fill="both", expand=True)
        images = ttk.Labelframe(run_page, text="1. Add images", padding=12)
        images.pack(fill="x", pady=(0, 12))
        ttk.Label(images, text="Place your images in the images folder. Matching tag files are optional.").pack(anchor="w")
        folders = ttk.Frame(images)
        folders.pack(fill="x", pady=(8, 0))
        ttk.Button(folders, text="Open images folder", bootstyle="primary-outline", command=lambda: self.open_folder("images")).pack(side="left")
        self.copy_move_button = ttk.Button(folders, text="Copy / move by tags", bootstyle="secondary-outline", command=self.open_copy_move_gui)
        self.copy_move_button.pack(side="left", padx=8)
        setup = ttk.Labelframe(run_page, text="2. Check setup", padding=12)
        setup.pack(fill="x", pady=(0, 12))
        self.setup_message = tk.StringVar(value="Checking images, configuration, and Ollama…")
        ttk.Label(setup, textvariable=self.setup_message, justify="left", wraplength=810).pack(side="left", fill="x", expand=True)
        self.check_button = ttk.Button(setup, text="Check again", bootstyle="secondary-outline", command=self.refresh_setup)
        self.check_button.pack(side="right", anchor="n", padx=(10, 0))
        self.add_wd14_tags = tk.BooleanVar(value=False)
        self.add_advanced_option(
            "Add missing tags to existing tag files", self.add_wd14_tags,
            "Append missing tags with at least 0.91 confidence. Images without tags are always tagged.",
        )
        self.overwrite_danbooru_txt = tk.BooleanVar()
        self.add_advanced_option(
            "Replace existing tags with fresh Danbooru or Gelbooru tags", self.overwrite_danbooru_txt,
            "On: replace matching tag files with tags from a found post. Off: keep existing tag files and skip their lookup.",
        )
        self.overwrite_caption_cache = tk.BooleanVar()
        self.add_advanced_option(
            "Bypass refinement caption cache", self.overwrite_caption_cache,
            "Generate fresh refinement output when needed. To replace completed captions too, enable the option below. Torii reports are reused.",
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
        )
        self.add_copyright_tags = tk.BooleanVar(value=False)
        self.add_advanced_option(
            "Include copyright and series tags in .tag files", self.add_copyright_tags,
            "On: include them in .tag and .combined files. Off: omit them there; source tags stay available.",
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
        self.result_message = tk.StringVar(value="Completed images and captions appear here after processing.")
        ttk.Label(results, textvariable=self.result_message, wraplength=570).pack(side="left", fill="x", expand=True)
        logs = ttk.Labelframe(frame, text="Activity", padding=8)
        logs.pack(fill="both", expand=True)
        self.log = scrolledtext.ScrolledText(logs, state="disabled", wrap="word", height=8, font=("Consolas", 10))
        self.log.pack(fill="both", expand=True)
        self.style_log()
        for stage in STAGES:
            self.steps.insert("", "end", iid=stage.script, text=stage.label, values=("Pending", ""))
        for variable in (self.add_wd14_tags, self.overwrite_danbooru_txt,
                         self.overwrite_caption_cache, self.overwrite_caption_files,
                         self.add_year_tag, self.add_copyright_tags):
            variable.trace_add("write", lambda *args: self.update_step_plan())
        self.update_step_plan()
        root.after(100, self.read_events)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh_setup()

    def add_advanced_option(self, title, variable, explanation):
        widget = ttk.Checkbutton(self.advanced, text=title, variable=variable, bootstyle="primary-round-toggle")
        widget.pack(anchor="w", pady=(0, 3))
        self.option_widgets.append(widget)
        ttk.Label(self.advanced, text=explanation, wraplength=850, bootstyle="secondary").pack(anchor="w", padx=(38, 0), pady=(0, 12))

    def style_log(self):
        colors = self.root.style.colors
        self.log.configure(background=colors.inputbg, foreground=colors.inputfg, insertbackground=colors.inputfg)

    def selected_options(self):
        return dict(overwrite_danbooru_txt=self.overwrite_danbooru_txt.get(),
                    overwrite_caption_cache=self.overwrite_caption_cache.get(),
                    skip_wd14_high_confidence=not self.add_wd14_tags.get(),
                    add_year_tag=self.add_year_tag.get(),
                    add_copyright_tags=self.add_copyright_tags.get(),
                    overwrite_caption_files=self.overwrite_caption_files.get())

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

    def refresh_setup(self):
        if self.checking or self.running:
            return
        self.checking = True
        self.ready = False
        self.start_button.configure(state="disabled")
        self.check_button.configure(state="disabled")
        self.setup_message.set("Checking images, configuration, and Ollama…")
        threading.Thread(target=self._check_setup, daemon=True).start()

    def _check_setup(self):
        try:
            result = check_setup()
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

    def open_copy_move_gui(self):
        if self.running:
            return
        try:
            subprocess.Popen(
                [sys.executable, str(ROOT / "data" / "copy_move_images_GUI.py")],
                cwd=ROOT,
                **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
            )
        except OSError as error:
            messagebox.showerror("VLCaptioner", f"Could not open copy/move images GUI: {error}")

    def start(self):
        if not self.ready or self.running:
            return
        self.failure_file = reset_failures()
        self.ready = False
        self.running = True
        self.last_outcome = False
        self.run_options = self.selected_options()
        for stage in STAGES:
            self.steps.set(stage.script, "status", "Pending")
        self.notebook.select(0)
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
        threading.Thread(
            target=self.run_pipeline,
            args=(
                stages,
                self.overwrite_danbooru_txt.get(),
                self.overwrite_caption_cache.get(),
                not self.add_wd14_tags.get(),
                self.add_year_tag.get(),
                self.add_copyright_tags.get(),
                self.overwrite_caption_files.get(),
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
    ):
        code = 0
        active_index = None
        run_options = dict(overwrite_danbooru_txt=overwrite_danbooru_txt,
                           overwrite_caption_cache=overwrite_caption_cache,
                           skip_wd14_high_confidence=skip_wd14_high_confidence,
                           add_year_tag=add_year_tag, add_copyright_tags=add_copyright_tags,
                           overwrite_caption_files=overwrite_caption_files)
        try:
            for index, stage in enumerate(stages, 1):
                if self.cancel_requested.is_set():
                    code = 130
                    break
                active_index = index
                reason = stage_skip_reason(stage, ROOT / "images", **run_options)
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
                                  overwrite_caption_files), cwd=ROOT,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                    encoding="utf-8", errors="replace",
                    env={**os.environ, "PYTHONUNBUFFERED": "1", ENV_KEY: self.failure_file}, **options,
                )
                if self.cancel_requested.is_set():
                    self.terminate_process_tree()
                with self.process.stdout:
                    for line in self.process.stdout:
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
            process = getattr(self, "process", None)
            if process is not None and process.poll() is None:
                self.terminate_process_tree()
                process.wait()
            self.events.put(f"\nPipeline could not start: {error}\n")
            if active_index is not None:
                self.events.put(("step_status", stages[active_index - 1].script, "Failed", str(error)))
        self.events.put(("done", code))

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
                        _, self.ready, message = event
                        self.checking = False
                        self.setup_message.set(message)
                        self.check_button.configure(state="normal")
                        self.start_button.configure(state="normal" if self.ready else "disabled")
                        if not self.last_outcome:
                            self.status.configure(text="Ready" if self.ready else "Setup needed", bootstyle="success" if self.ready else "warning")
                        continue
                    if event[0] == "stage":
                        _, index, total, label = event
                        self.current_stage = (index, total)
                        self.progress_bar.configure(value=100 * (index - 1) / total)
                        self.progress.set(f"Stage {index} of {total}: {label}. Images remaining: {image_count(ROOT / 'images')}; completed: {image_count(ROOT / 'done')}; needing review: {image_count(ROOT / 'captionReview')}.")
                        continue
                    code = event[1]
                    self.process = None
                    self.running = False
                    self.last_outcome = True
                    for script in self.steps.get_children():
                        if self.steps.set(script, "status") == "Pending":
                            self.steps.set(script, "status", "Not run")
                    for widget in self.option_widgets:
                        widget.configure(state="normal")
                    if code == 0:
                        self.progress_bar.configure(value=100)
                    if self.closing:
                        self.root.destroy()
                        return
                    self.stop_button.configure(state="disabled")
                    self.copy_move_button.configure(state="normal")
                    self.status.configure(text="Stopped" if code == 130 else ("Finished" if code == 0 else f"Failed ({code})"), bootstyle="warning" if code == 130 else ("success" if code == 0 else "danger"))
                    skipped_count = len(failures(getattr(self, "failure_file", None)))
                    if code == 0 and skipped_count:
                        self.status.configure(text=f"Finished; {skipped_count} skipped", bootstyle="warning")
                    done = image_count(ROOT / "done")
                    review = image_count(ROOT / "captionReview")
                    self.result_message.set(f"Completed: {done} total ({max(0, done - self.before_counts[0])} added this run). Need review: {review} total ({max(0, review - self.before_counts[1])} added this run).")
                    self.progress.set("Run stopped. Completed work is kept; start again to resume." if code == 130 else ("Run finished. Review the files below." if code == 0 else "Run failed. Check the log below, fix the issue, then start again."))
                    if code == 0 and skipped_count:
                        self.progress.set(f"Finished with {skipped_count} skipped image(s). Their originals are kept for retry; see the activity log.")
                    if code not in (0, 130):
                        messagebox.showerror("VLCaptioner", f"Pipeline failed with exit code {code}. See the log below.")
                    self.refresh_setup()
                else:
                    self.append(event)
                    match = re.search(r"Refining captions: ([\d,]+) of ([\d,]+) images processed", event)
                    if match:
                        processed, total = (int(value.replace(",", "")) for value in match.groups())
                        self.progress.set(event.strip())
                        if total:
                            stage_index, stage_total = self.current_stage
                            self.progress_bar.configure(value=100 * (stage_index - 1 + min(1, processed / total)) / stage_total)
        except queue.Empty:
            pass
        self.root.after(20 if not self.events.empty() else 100, self.read_events)

    def terminate_process_tree(self):
        process = self.process
        if not process or process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            os.killpg(process.pid, signal.SIGTERM)

    def stop(self):
        self.cancel_requested.set()
        self.stop_button.configure(state="disabled")
        self.status.configure(text="Stopping")
        self.progress.set("Stopping. Completed work is kept; start again to resume.")
        self.append("\nStopping pipeline...\n")
        self.terminate_process_tree()

    def close(self):
        if not self.running:
            self.root.destroy()
            return
        self.closing = True
        self.stop()


def main():
    root = ttk.Window(themename="bootstrap-dark")
    PipelineGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()

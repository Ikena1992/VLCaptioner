"""Cross-platform GUI launcher for the VLTagger pipeline."""

import os
import queue
import signal
import subprocess
import sys
import threading
import tkinter as tk
import json
from urllib.request import urlopen
from tkinter import messagebox, scrolledtext, ttk

from pipeline import ROOT, STAGES, stage_command
from runtime_config import CONFIG_FILE, read_settings

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
        root.geometry("900x690")
        root.minsize(680, 580)
        frame = ttk.Frame(root, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="1. Add images", font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Put images in images/. Matching .txt tags are optional.").pack(anchor="w")
        folders = ttk.Frame(frame)
        folders.pack(fill="x", pady=(4, 12))
        ttk.Button(folders, text="Open images folder", command=lambda: self.open_folder("images")).pack(side="left")
        ttk.Button(folders, text="Copy or move images by tags", command=self.open_copy_move_gui).pack(side="left", padx=8)
        ttk.Label(frame, text="2. Check setup", font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        self.check_button = ttk.Button(frame, text="Check again", command=self.refresh_setup)
        self.check_button.pack(anchor="w", pady=(4, 4))
        self.setup_message = tk.StringVar(value="Checking setup…")
        ttk.Label(frame, textvariable=self.setup_message, justify="left", wraplength=820).pack(anchor="w", pady=(0, 12))
        ttk.Label(frame, text="3. Start captioning", font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        self.advanced_visible = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Advanced options", variable=self.advanced_visible, command=self.toggle_advanced).pack(anchor="w")
        self.advanced = ttk.Frame(frame, padding=(18, 2, 0, 6))
        self.add_wd14_tags = tk.BooleanVar(value=False)
        self.add_advanced_option(
            "Add high-confidence WD14 tags to existing tag files", self.add_wd14_tags,
            "On: append missing high-confidence tags. Off: leave existing tag files unchanged. Images without tags are tagged either way.",
        )
        self.overwrite_danbooru_txt = tk.BooleanVar()
        self.add_advanced_option(
            "Replace existing tags with fresh Danbooru or Gelbooru tags", self.overwrite_danbooru_txt,
            "On: replace matching tag files with tags from a found post. Off: keep existing tag files and skip their lookup.",
        )
        self.overwrite_caption_cache = tk.BooleanVar()
        self.add_advanced_option(
            "Regenerate cached captions", self.overwrite_caption_cache,
            "On: generate short and long captions again. Off: reuse cached captions when available.",
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
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(0, 8))
        self.start_button = ttk.Button(buttons, text="Start captioning", command=self.start, state="disabled")
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(buttons, text="Stop", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        self.status = ttk.Label(buttons, text="Checking setup")
        self.status.pack(side="right")
        self.progress = tk.StringVar(value="Completed work is kept if you stop. Start again to resume remaining images.")
        ttk.Label(frame, textvariable=self.progress, wraplength=820).pack(anchor="w", pady=(0, 10))
        ttk.Label(frame, text="4. Review results", font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        results = ttk.Frame(frame)
        results.pack(fill="x", pady=(4, 8))
        ttk.Button(results, text="Open completed files", command=lambda: self.open_folder("done")).pack(side="left")
        ttk.Button(results, text="Open files needing review", command=lambda: self.open_folder("captionReview")).pack(side="left", padx=8)
        self.result_message = tk.StringVar(value="Results appear in done/; flagged captions appear in captionReview/.")
        ttk.Label(frame, textvariable=self.result_message).pack(anchor="w")
        self.log_visible = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Show detailed log", variable=self.log_visible, command=self.toggle_log).pack(anchor="w", pady=(10, 2))
        self.log = scrolledtext.ScrolledText(frame, state="disabled", wrap="word")
        root.after(100, self.read_events)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh_setup()

    def add_advanced_option(self, title, variable, explanation):
        ttk.Checkbutton(self.advanced, text=title, variable=variable).pack(anchor="w")
        ttk.Label(self.advanced, text=explanation, wraplength=760).pack(anchor="w", padx=(22, 0), pady=(0, 5))

    def toggle_advanced(self):
        if self.advanced_visible.get():
            self.advanced.pack(fill="x", before=self.start_button.master)
        else:
            self.advanced.pack_forget()

    def toggle_log(self):
        if self.log_visible.get():
            self.log.pack(fill="both", expand=True)
        else:
            self.log.pack_forget()

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
        if self.checking or self.process:
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
        self.log.configure(state="normal")
        self.log.insert("end", value)
        self.log.see("end")
        self.log.configure(state="disabled")

    def open_copy_move_gui(self):
        try:
            subprocess.Popen(
                [sys.executable, str(ROOT / "data" / "copy_move_images_GUI.py")],
                cwd=ROOT,
            )
        except OSError as error:
            messagebox.showerror("VLCaptioner", f"Could not open copy/move images GUI: {error}")

    def start(self):
        if not self.ready or self.process:
            return
        self.ready = False
        self.before_counts = (image_count(ROOT / "done"), image_count(ROOT / "captionReview"))
        stages = list(STAGES)
        self.cancel_requested.clear()
        self.append("Starting VLCaptioner pipeline...\n\n")
        self.start_button.configure(state="disabled")
        self.check_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
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
    ):
        code = 0
        try:
            for index, stage in enumerate(stages, 1):
                if self.cancel_requested.is_set():
                    code = 130
                    break
                self.events.put(("stage", index, len(stages), stage.label))
                self.events.put(f"\n=== {stage.label} ===\n")
                options = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True})
                self.process = subprocess.Popen(
                    stage_command(stage, sys.executable, overwrite_danbooru_txt,
                                  overwrite_caption_cache, skip_wd14_high_confidence,
                                  add_year_tag, add_copyright_tags), cwd=ROOT,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                    encoding="utf-8", errors="replace", **options,
                )
                for line in self.process.stdout:
                    self.events.put(line)
                code = self.process.wait()
                if self.cancel_requested.is_set():
                    code = 130
                    break
                if code:
                    break
        except Exception as error:
            code = 1
            self.events.put(f"\nPipeline could not start: {error}\n")
        self.events.put(("done", code))

    def read_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                if isinstance(event, tuple):
                    if event[0] == "setup":
                        _, self.ready, message = event
                        self.checking = False
                        self.setup_message.set(message)
                        self.check_button.configure(state="normal")
                        self.start_button.configure(state="normal" if self.ready else "disabled")
                        self.status.configure(text="Ready" if self.ready else "Setup needed")
                        continue
                    if event[0] == "stage":
                        _, index, total, label = event
                        self.progress.set(f"Stage {index} of {total}: {label}. Images remaining: {image_count(ROOT / 'images')}; completed: {image_count(ROOT / 'done')}; needing review: {image_count(ROOT / 'captionReview')}.")
                        continue
                    code = event[1]
                    self.process = None
                    if self.closing:
                        self.root.destroy()
                        return
                    self.stop_button.configure(state="disabled")
                    self.status.configure(text="Stopped" if code == 130 else ("Finished" if code == 0 else f"Failed ({code})"))
                    done = image_count(ROOT / "done")
                    review = image_count(ROOT / "captionReview")
                    self.result_message.set(f"Completed: {done} total ({max(0, done - self.before_counts[0])} added this run). Need review: {review} total ({max(0, review - self.before_counts[1])} added this run).")
                    self.progress.set("Run stopped. Completed work is kept; start again to resume." if code == 130 else ("Run finished. Review the files below." if code == 0 else "Run failed. Open the detailed log, fix the issue, then start again."))
                    if code not in (0, 130):
                        self.log_visible.set(True)
                        self.toggle_log()
                        messagebox.showerror("VLCaptioner", f"Pipeline failed with exit code {code}. See the detailed log.")
                    self.refresh_setup()
                else:
                    self.append(event)
        except queue.Empty:
            pass
        self.root.after(100, self.read_events)

    def terminate_process_tree(self):
        process = self.process
        if not process or process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
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
        if not self.process:
            self.root.destroy()
            return
        self.closing = True
        self.stop()


def main():
    root = tk.Tk()
    PipelineGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()

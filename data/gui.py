"""Cross-platform GUI launcher for the VLTagger pipeline."""

import os
import queue
import signal
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from pipeline import ROOT, STAGES, stage_command


class PipelineGUI:
    def __init__(self, root):
        self.root = root
        self.process = None
        self.events = queue.Queue()
        self.cancel_requested = threading.Event()
        self.closing = False
        root.title("VLCaptioner")
        root.geometry("850x560")
        frame = ttk.Frame(root, padding=12)
        frame.pack(fill="both", expand=True)
        self.skip_wd14 = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="Skip WD14 high-confidence missing-tag step", variable=self.skip_wd14).pack(anchor="w", pady=(0, 8))
        self.overwrite_danbooru_txt = tk.BooleanVar()
        ttk.Checkbutton(frame, text="Overwrite existing TXT files with fresh Danbooru tags", variable=self.overwrite_danbooru_txt).pack(anchor="w", pady=(0, 8))
        self.overwrite_caption_cache = tk.BooleanVar()
        ttk.Checkbutton(frame, text="Overwrite cached short and long captions", variable=self.overwrite_caption_cache).pack(anchor="w", pady=(0, 8))
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(0, 8))
        self.start_button = ttk.Button(buttons, text="Start pipeline", command=self.start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(buttons, text="Stop", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        self.status = ttk.Label(buttons, text="Ready")
        self.status.pack(side="right")
        self.log = scrolledtext.ScrolledText(frame, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True)
        root.after(100, self.read_events)
        root.protocol("WM_DELETE_WINDOW", self.close)

    def append(self, value):
        self.log.configure(state="normal")
        self.log.insert("end", value)
        self.log.see("end")
        self.log.configure(state="disabled")

    def start(self):
        stages = list(STAGES)
        self.cancel_requested.clear()
        self.append("Starting VLTagger pipeline...\n\n")
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status.configure(text="Running")
        threading.Thread(
            target=self.run_pipeline,
            args=(
                stages,
                self.overwrite_danbooru_txt.get(),
                self.overwrite_caption_cache.get(),
                self.skip_wd14.get(),
            ),
            daemon=True,
        ).start()

    def run_pipeline(
        self,
        stages,
        overwrite_danbooru_txt=False,
        overwrite_caption_cache=False,
        skip_wd14_high_confidence=False,
    ):
        code = 0
        for stage in stages:
            if self.cancel_requested.is_set():
                code = 130
                break
            self.events.put(f"\n=== {stage.label} ===\n")
            options = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True})
            self.process = subprocess.Popen(
                stage_command(
                    stage,
                    sys.executable,
                    overwrite_danbooru_txt,
                    overwrite_caption_cache,
                    skip_wd14_high_confidence,
                ), cwd=ROOT,
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
        self.events.put(("done", code))

    def read_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                if isinstance(event, tuple):
                    code = event[1]
                    self.process = None
                    if self.closing:
                        self.root.destroy()
                        return
                    self.start_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    self.status.configure(text="Stopped" if code == 130 else ("Finished" if code == 0 else f"Failed ({code})"))
                    if code not in (0, 130):
                        messagebox.showerror("VLTagger", f"Pipeline failed with exit code {code}.")
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

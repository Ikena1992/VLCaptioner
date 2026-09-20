import os
import shutil
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".avif")
SIDECAR_EXTENSIONS = (".combined", ".long", ".short", ".tag")

# --- PATH SETUP ---
SCRIPT_DIR = Path(__file__).parent.resolve()
GIF_PATH = SCRIPT_DIR / "dance.gif"

# --- GLOBALS ---
stop_requested = False
gif_running = False
gif_frames = []
gif_index = 0


def select_source():
    folder = filedialog.askdirectory()
    source_entry.delete(0, tk.END)
    source_entry.insert(0, folder)


def select_destination():
    folder = filedialog.askdirectory()
    dest_entry.delete(0, tk.END)
    dest_entry.insert(0, folder)


def set_buttons_state(running):
    if running:
        copy_btn.config(state="disabled")
        move_btn.config(state="disabled")
        cancel_btn.config(state="normal")
        start_gif()
    else:
        copy_btn.config(state="normal")
        move_btn.config(state="normal")
        cancel_btn.config(state="disabled")
        stop_gif()


def request_cancel():
    global stop_requested
    stop_requested = True
    status_label.config(text="Cancelling...")


def safe_transfer(src, dst, mode):
    try:
        if mode == "copy":
            shutil.copy2(src, dst)
        else:
            shutil.move(src, dst)
        return True
    except Exception as e:
        print(f"Error processing {src}: {e}")
        return False


def transfer_files(mode="copy"):
    global stop_requested
    stop_requested = False

    source = source_entry.get()
    dest = dest_entry.get()
    user_input = search_entry.get()
    contains_input = contains_entry.get().lower().strip()
    exclude_input = exclude_entry.get()

    if not source or not dest:
        messagebox.showerror("Error", "Fill source and destination.")
        return

    set_buttons_state(True)

    keywords = [k.strip().lower() for k in user_input.split(",") if k.strip()]
    exclude_keywords = [k.strip().lower() for k in exclude_input.split(",") if k.strip()]

    txt_files = [f for f in os.listdir(source) if f.lower().endswith(".txt")]

    progress["maximum"] = len(txt_files)
    progress["value"] = 0
    status_label.config(text=f"0 / {len(txt_files)}")

    copied = 0

    for i, file in enumerate(txt_files):

        if stop_requested:
            status_label.config(text="Cancelled")
            break

        txt_path = os.path.join(source, file)
        base = os.path.splitext(file)[0]

        try:
            with open(txt_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read().lower()
        except:
            continue

        tags = {t.strip() for t in content.replace("\n", ",").split(",") if t.strip()}

        if keywords:
            if search_mode.get() == "AND":
                tag_match = all(keyword in tags for keyword in keywords)
            else:
                tag_match = any(keyword in tags for keyword in keywords)
        else:
            tag_match = False

        if exclude_keywords:
            exclude_match = any(keyword in tags for keyword in exclude_keywords)
        else:
            exclude_match = False

        if contains_mode.get() and contains_input:
            contains_match = contains_input in content
        else:
            contains_match = False

        match = (tag_match or contains_match) and not exclude_match

        if match:
            if safe_transfer(txt_path, os.path.join(dest, file), mode):
                copied += 1

            nl_file = base + ".naturalLanguage"
            nl_path = os.path.join(source, nl_file)
            if os.path.exists(nl_path):
                if safe_transfer(nl_path, os.path.join(dest, nl_file), mode):
                    copied += 1

            for ext in IMAGE_EXTENSIONS:
                img_path = os.path.join(source, base + ext)
                if os.path.exists(img_path):
                    if safe_transfer(img_path, os.path.join(dest, base + ext), mode):
                        copied += 1
                    break

            for ext in SIDECAR_EXTENSIONS:
                sidecar = base + ext
                sidecar_path = os.path.join(source, sidecar)
                if os.path.exists(sidecar_path):
                    if safe_transfer(sidecar_path, os.path.join(dest, sidecar), mode):
                        copied += 1

        progress["value"] = i + 1
        status_label.config(text=f"{i+1} / {len(txt_files)}")
        root.update_idletasks()

    else:
        status_label.config(text="Done")
        messagebox.showinfo("Done", f"{copied} files processed.")

    set_buttons_state(False)


def start_transfer(mode):
    threading.Thread(target=transfer_files, args=(mode,), daemon=True).start()


# --- GIF HANDLING ---
def load_gif():
    global gif_frames

    gif_frames.clear()

    if not GIF_PATH.exists():
        print(f"GIF not found: {GIF_PATH}")
        return

    try:
        i = 0
        while True:
            frame = tk.PhotoImage(file=str(GIF_PATH), format=f"gif -index {i}")

            try:
                frame = frame.copy()
            except:
                pass

            gif_frames.append(frame)
            i += 1

    except tk.TclError:
        pass

    if not gif_frames:
        print("No GIF frames loaded!")
    else:
        print(f"Loaded {len(gif_frames)} frames from {GIF_PATH}")
        gif_label.config(image=gif_frames[0])
        gif_label.image = gif_frames[0]


def animate_gif():
    global gif_index, gif_running

    if not gif_running or not gif_frames:
        return

    frame = gif_frames[gif_index]

    gif_label.config(image=frame)
    gif_label.image = frame

    gif_index = (gif_index + 1) % len(gif_frames)

    root.after(50, animate_gif)


def start_gif():
    global gif_running
    gif_running = True
    animate_gif()


def stop_gif():
    global gif_running, gif_frames

    gif_running = False

    # Keep visible frame instead of clearing
    if gif_frames:
        gif_label.config(image=gif_frames[0])
        gif_label.image = gif_frames[0]


# --- GUI ---
root = tk.Tk()
root.title("Momiji is a virus! she mines crypto!")
root.geometry("500x600")

tk.Label(root, text="Image Folder").pack()
source_entry = tk.Entry(root, width=50)
source_entry.pack()
tk.Button(root, text="Browse", command=select_source).pack(pady=5)

tk.Label(root, text="Destination Folder").pack()
dest_entry = tk.Entry(root, width=50)
dest_entry.pack()
tk.Button(root, text="Browse", command=select_destination).pack(pady=5)

tk.Label(root, text="Search Tags").pack()
tk.Label(root, text="Example: 1girl, blonde hair").pack()
search_entry = tk.Entry(root, width=55)
search_entry.pack(pady=5)

search_mode = tk.StringVar(value="OR")
mode_frame = tk.Frame(root)
mode_frame.pack()

tk.Radiobutton(mode_frame, text="OR", variable=search_mode, value="OR").pack(side=tk.LEFT, padx=10)
tk.Radiobutton(mode_frame, text="AND", variable=search_mode, value="AND").pack(side=tk.LEFT, padx=10)

tk.Label(root, text="Exclude Tags").pack()
exclude_entry = tk.Entry(root, width=55)
exclude_entry.pack(pady=5)

tk.Label(root, text="Contains Text (optional)").pack()
contains_entry = tk.Entry(root, width=55)
contains_entry.pack(pady=5)

contains_mode = tk.BooleanVar(value=False)
tk.Checkbutton(root, text="Enable contains search", variable=contains_mode).pack()

# --- BUTTON ROW WITH GIF ---
action_frame = tk.Frame(root)
action_frame.pack(pady=10)

copy_btn = tk.Button(action_frame, text="Copy Files", command=lambda: start_transfer("copy"))
copy_btn.grid(row=0, column=0, padx=5)

move_btn = tk.Button(action_frame, text="Move Files", command=lambda: start_transfer("move"))
move_btn.grid(row=0, column=1, padx=5)

cancel_btn = tk.Button(action_frame, text="Cancel", command=request_cancel, state="disabled")
cancel_btn.grid(row=0, column=2, padx=5)

gif_label = tk.Label(action_frame)
gif_label.grid(row=0, column=3, padx=10)

# --- PROGRESS ---
progress = ttk.Progressbar(root, orient="horizontal", length=320, mode="determinate")
progress.pack(pady=5)

status_label = tk.Label(root, text="Idle")
status_label.pack()

# Load GIF
load_gif()

root.mainloop()

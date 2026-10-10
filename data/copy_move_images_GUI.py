import queue
import re
import shutil
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

import ttkbootstrap as ttk

IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.avif', '.tif', '.tiff')
SIDECAR_EXTENSIONS = ('.txt', '.combined', '.long', '.short', '.tag', '.naturallanguage')
GIF_PATH = Path(__file__).resolve().parent / 'dance.gif'


def normalize_target_extension(value):
    extension = value.strip().lower()
    if extension and not extension.startswith('.'):
        extension = '.' + extension
    if not re.fullmatch(r'\.[a-z0-9]+', extension):
        raise ValueError('Enter one file extension, such as .txt or .tag.')
    return extension


def matches(content, keywords, excluded, search_mode, contains):
    content = content.lower()
    tags = {tag.strip() for tag in content.replace('\n', ',').split(',') if tag.strip()}
    tag_match = bool(keywords) and (all(word in tags for word in keywords) if search_mode == 'AND'
                                   else any(word in tags for word in keywords))
    return (tag_match or bool(contains and contains in content)) and not any(word in tags for word in excluded)


def transfer(settings, cancel, emit):
    """Perform filesystem work only; report results to the UI through its queue."""
    scanned = matched = transferred = skipped = errors = 0
    try:
        source, destination = settings['source'], settings['destination']
        files = sorted((path for path in source.iterdir() if path.is_file()), key=lambda path: path.name.lower())
        candidates = [path for path in files if path.suffix.lower() == settings['extension']]
        groups = {}
        for path in files:
            groups.setdefault(path.stem.casefold(), []).append(path)
        emit(('progress', 0, len(candidates), 'Scanning caption files…'))
        for caption in candidates:
            if cancel.is_set():
                break
            scanned += 1
            try:
                content = caption.read_text(encoding='utf-8-sig')
                if matches(content, settings['keywords'], settings['excluded'], settings['mode'], settings['contains']):
                    matched += 1
                    bundle = [path for path in groups[caption.stem.casefold()]
                              if path == caption or path.suffix.lower() in IMAGE_EXTENSIONS + SIDECAR_EXTENSIONS]
                    # Check the whole group before moving anything. Never overwrite a destination file.
                    conflicts = [path.name for path in bundle if (destination / path.name).exists()]
                    if conflicts:
                        skipped += 1
                        emit(('log', f'Skipped {caption.stem}: destination already contains {", ".join(conflicts)}'))
                    else:
                        # Copy/move images first so a failed image transfer leaves its captions in place.
                        bundle.sort(key=lambda path: path.suffix.lower() not in IMAGE_EXTENSIONS)
                        for path in bundle:
                            target = destination / path.name
                            if settings['action'] == 'copy':
                                # Exclusive creation also protects against files created after the preflight check.
                                with path.open('rb') as reader, target.open('xb') as writer:
                                    try:
                                        shutil.copyfileobj(reader, writer)
                                    except Exception:
                                        writer.close()
                                        target.unlink(missing_ok=True)
                                        raise
                                shutil.copystat(path, target)
                            else:
                                # Copy first, then remove the source only after the complete file is saved.
                                with path.open('rb') as reader, target.open('xb') as writer:
                                    try:
                                        shutil.copyfileobj(reader, writer)
                                    except Exception:
                                        writer.close()
                                        target.unlink(missing_ok=True)
                                        raise
                                shutil.copystat(path, target)
                                path.unlink()
                            transferred += 1
                        emit(('log', f'{"Copied" if settings["action"] == "copy" else "Moved"}: {caption.stem} ({len(bundle)} files)'))
            except Exception as error:
                errors += 1
                emit(('log', f'Error for {caption.name}: {error}. Continuing with the next caption.'))
            emit(('progress', scanned, len(candidates), caption.name))
    except Exception as error:
        errors += 1
        emit(('log', f'Transfer could not continue: {error}'))
    finally:
        emit(('done', cancel.is_set(), scanned, matched, transferred, skipped, errors))


class CopyMoveApp:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.running = False
        self.closing = False
        self.gif_frames = []
        self.gif_index = 0
        self.gif_job = None
        self.controls = []
        root.title('Copy / move images by tags')
        root.geometry('880x760')
        root.minsize(760, 680)
        root.protocol('WM_DELETE_WINDOW', self.close)
        panel = ttk.Frame(root, padding=20)
        panel.pack(fill='both', expand=True)
        ttk.Label(panel, text='Copy / move images', font=('', 20, 'bold')).pack(anchor='w')
        ttk.Label(panel, text='Find matching captions and transfer their images and companion files.').pack(anchor='w', pady=(4, 16))
        folders = ttk.Labelframe(panel, text='Folders', padding=12)
        folders.pack(fill='x')
        folders.columnconfigure(1, weight=1)
        self.source = tk.StringVar(value=str(Path(__file__).resolve().parent.parent / 'done'))
        self.destination = tk.StringVar()
        for row, (label, variable) in enumerate((('Source', self.source), ('Destination', self.destination))):
            ttk.Label(folders, text=label).grid(row=row, column=0, sticky='w', padx=(0, 12), pady=4)
            entry = ttk.Entry(folders, textvariable=variable)
            entry.grid(row=row, column=1, sticky='ew', pady=4)
            button = ttk.Button(folders, text='Browse…', bootstyle='secondary-outline', command=lambda v=variable: self.browse(v))
            button.grid(row=row, column=2, padx=(8, 0), pady=4)
            self.controls.extend((entry, button))
        filters = ttk.Labelframe(panel, text='Match captions', padding=12)
        filters.pack(fill='x', pady=12)
        filters.columnconfigure(1, weight=1)
        self.extension = tk.StringVar(value='.txt')
        self.tags = tk.StringVar()
        self.excluded = tk.StringVar()
        self.contains = tk.StringVar()
        for row, (label, variable) in enumerate((('Caption extension', self.extension), ('Include tags', self.tags), ('Exclude tags', self.excluded), ('Text contains', self.contains))):
            ttk.Label(filters, text=label).grid(row=row, column=0, sticky='w', padx=(0, 12), pady=4)
            entry = ttk.Entry(filters, textvariable=variable)
            entry.grid(row=row, column=1, sticky='ew', pady=4)
            self.controls.append(entry)
        self.mode = tk.StringVar(value='OR')
        modes = ttk.Frame(filters)
        modes.grid(row=4, column=1, sticky='w', pady=6)
        for label, value in (('Any included tag (OR)', 'OR'), ('All included tags (AND)', 'AND')):
            control = ttk.Radiobutton(modes, text=label, variable=self.mode, value=value)
            control.pack(side='left', padx=(0, 16))
            self.controls.append(control)
        ttk.Label(filters, text='OR: match at least one included tag. AND: match every included tag.\nSeparate tags with commas. Text matches also qualify; any excluded tag rejects a match.', wraplength=680).grid(row=5, column=0, columnspan=2, sticky='w')
        actions = ttk.Frame(panel)
        actions.pack(fill='x')
        for label, action, style in (('Copy matching files', 'copy', 'primary'), ('Move matching files', 'move', 'warning')):
            button = ttk.Button(actions, text=label, bootstyle=style, command=lambda a=action: self.start(a))
            button.pack(side='left', padx=(0, 8))
            self.controls.append(button)
        self.cancel_button = ttk.Button(actions, text='Cancel', bootstyle='secondary-outline', state='disabled', command=self.request_cancel)
        self.cancel_button.pack(side='left')
        self.gif_label = ttk.Label(actions)
        self.gif_label.pack(side='right')
        self.progress = ttk.Progressbar(panel, mode='determinate')
        self.progress.pack(fill='x', pady=(12, 6))
        self.status = ttk.Label(panel, text='Ready — existing destination files will be skipped.', wraplength=800)
        self.status.pack(anchor='w')
        self.log = tk.Text(panel, height=7, wrap='word', state='disabled', background='#20252b', foreground='#eeeeee', relief='flat')
        self.log.pack(fill='both', expand=True, pady=(10, 0))
        self.load_gif()
        root.after(50, self.poll)

    def browse(self, variable):
        folder = filedialog.askdirectory(parent=self.root, initialdir=variable.get() or None)
        if folder:
            variable.set(folder)

    def start(self, action):
        if self.running:
            return
        try:
            if not self.source.get().strip() or not self.destination.get().strip():
                raise ValueError('Choose both source and destination folders.')
            source = Path(self.source.get().strip()).resolve()
            destination = Path(self.destination.get().strip()).resolve()
            if not source.is_dir():
                raise ValueError('The source folder does not exist.')
            if source == destination:
                raise ValueError('Source and destination must be different folders.')
            extension = normalize_target_extension(self.extension.get())
            keywords = [tag.strip().lower() for tag in self.tags.get().split(',') if tag.strip()]
            contains = self.contains.get().strip().lower()
            if not keywords and not contains:
                raise ValueError('Enter at least one included tag or some text to search for.')
            destination.mkdir(parents=True, exist_ok=True)
            settings = dict(source=source, destination=destination, extension=extension, keywords=keywords,
                            excluded=[tag.strip().lower() for tag in self.excluded.get().split(',') if tag.strip()],
                            contains=contains, mode=self.mode.get(), action=action)
        except (ValueError, OSError) as error:
            messagebox.showerror('Cannot start transfer', str(error), parent=self.root)
            return
        self.running = True
        self.cancel.clear()
        for control in self.controls:
            control.configure(state='disabled')
        self.cancel_button.configure(state='normal')
        self.progress.configure(value=0)
        self.status.configure(text='Starting transfer…')
        self.animate_gif()
        threading.Thread(target=transfer, args=(settings, self.cancel, self.events.put), daemon=True).start()

    def request_cancel(self):
        self.cancel.set()
        self.cancel_button.configure(state='disabled')
        self.status.configure(text='Cancelling after the current image and its companion files…')

    def poll(self):
        for _ in range(100):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if event[0] == 'progress':
                _, current, total, name = event
                self.progress.configure(maximum=max(total, 1), value=current)
                if not self.cancel.is_set():
                    self.status.configure(text=f'{current:,} / {total:,} captions checked — {name}')
            elif event[0] == 'log':
                self.log.configure(state='normal')
                self.log.insert('end', event[1] + '\n')
                if int(self.log.index('end-1c').split('.')[0]) > 1000:
                    self.log.delete('1.0', '2.0')
                self.log.see('end')
                self.log.configure(state='disabled')
            elif event[0] == 'done':
                _, cancelled, scanned, matched, transferred, skipped, errors = event
                self.running = False
                self.stop_gif()
                for control in self.controls:
                    control.configure(state='normal')
                self.cancel_button.configure(state='disabled')
                self.status.configure(text=f'{"Cancelled" if cancelled else "Finished"}: {scanned:,} captions checked, {matched:,} matched, {transferred:,} files transferred, {skipped:,} groups skipped, {errors:,} errors.')
                if self.closing:
                    self.root.destroy()
                    return
        self.root.after(50, self.poll)

    def load_gif(self):
        if not GIF_PATH.exists():
            return
        index = 0
        try:
            while True:
                self.gif_frames.append(tk.PhotoImage(master=self.root, file=str(GIF_PATH), format=f'gif -index {index}'))
                index += 1
        except tk.TclError:
            pass
        if self.gif_frames:
            self.gif_label.configure(image=self.gif_frames[0])

    def animate_gif(self):
        if not self.running or not self.gif_frames:
            return
        self.gif_label.configure(image=self.gif_frames[self.gif_index])
        self.gif_index = (self.gif_index + 1) % len(self.gif_frames)
        self.gif_job = self.root.after(50, self.animate_gif)

    def stop_gif(self):
        if self.gif_job is not None:
            self.root.after_cancel(self.gif_job)
            self.gif_job = None
        self.gif_index = 0
        if self.gif_frames:
            self.gif_label.configure(image=self.gif_frames[0])

    def close(self):
        if self.running:
            self.closing = True
            self.request_cancel()
        else:
            self.root.destroy()


if __name__ == '__main__':
    app_root = ttk.Window(themename='bootstrap-dark')
    CopyMoveApp(app_root)
    app_root.mainloop()

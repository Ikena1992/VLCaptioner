"""Read-only local image/tag dataset browser. Run with Python (Tk + Pillow)."""
from __future__ import annotations

import csv
from contextlib import closing
import subprocess
import sys
import os
import queue
import re
import sqlite3
import threading
import tkinter as tk
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT
EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tif', '.tiff', '.avif'}
CATEGORIES = {0: 'General', 1: 'Artist', 3: 'Copyright', 4: 'Character', 5: 'Meta'}


def normalize(tag):
    return re.sub(r'\s+', ' ', tag.strip().lower().replace(r'\(', '(').replace(r'\)', ')').replace('_', ' '))


def load_categories(paths, cancelled=lambda: False):
    categories, warnings = {}, []
    for path in paths:
        if cancelled():
            break
        if not path.is_file():
            warnings.append(f'Category database missing: {path}')
            continue
        try:
            with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as database:
                for tag, category in database.execute('SELECT tag, category FROM tag_categories'):
                    if cancelled():
                        break
                    key = normalize(tag)
                    if key not in categories or category == 4:
                        categories[key] = CATEGORIES.get(category, 'Unknown')
        except sqlite3.Error as error:
            warnings.append(f'{path}: {error}')
    return categories, warnings


@dataclass
class Dataset:
    counts: Counter = field(default_factory=Counter)
    matches: dict = field(default_factory=lambda: defaultdict(list))
    folder_counts: dict = field(default_factory=lambda: defaultdict(Counter))
    folder_matches: dict = field(default_factory=lambda: defaultdict(lambda: defaultdict(list)))
    caption_paths: dict = field(default_factory=dict)
    folder_images: Counter = field(default_factory=Counter)
    images: int = 0
    captions: int = 0
    missing: int = 0
    empty: int = 0
    orphan: int = 0
    warnings: list = field(default_factory=list)


def scan_dataset(folders, progress=lambda value: None, cancelled=lambda: False):
    result = Dataset()
    # Cache parsed captions once, but count membership in each selected folder.
    scanned = {}
    seen_directories = set()
    for folder in dict.fromkeys(folders):
        if cancelled():
            return None
        if not folder.is_dir():
            result.warnings.append(f'Folder missing: {folder}')
            continue
        progress(f'Scanning {folder}…')
        for parent, dirs, names in os.walk(folder, onerror=lambda error: result.warnings.append(str(error))):
            if cancelled():
                return None
            dirs.sort()
            parent = Path(parent)
            txt = {Path(name).stem.casefold(): parent / name for name in sorted(names)
                   if Path(name).suffix.lower() == '.txt'
                   and not Path(name).stem.lower().endswith(('.tag', '.combined', '.long', '.short'))}
            image_stems = set()
            for name in sorted(names):
                if cancelled():
                    return None
                image = parent / name
                if image.suffix.lower() not in EXTENSIONS:
                    continue
                stem = image.stem.casefold()
                image_stems.add(stem)
                identity = os.path.normcase(str(image.resolve()))
                result.folder_images[str(folder)] += 1
                if identity in scanned:
                    tags = scanned[identity]
                else:
                    result.images += 1
                    tags = set()
                    caption = txt.get(stem)
                    if caption is None:
                        result.missing += 1
                    else:
                        result.caption_paths[image] = caption
                        try:
                            content = caption.read_text(encoding='utf-8-sig')
                            lines = []
                            for line in content.splitlines():
                                if line.strip().startswith('# VLTagger metadata:'):
                                    break
                                if not line.lstrip().startswith('#'):
                                    lines.append(line)
                            tags = {normalize(tag) for tag in re.split(r'[,\r\n]+', '\n'.join(lines)) if normalize(tag)}
                            result.captions += 1
                            result.empty += not bool(tags)
                        except (OSError, UnicodeError) as error:
                            result.warnings.append(f'{caption}: {error}')
                    scanned[identity] = tags
                    result.counts.update(tags)
                    for tag in tags:
                        result.matches[tag].append(image)
                    if result.images % 500 == 0:
                        progress(f'{result.images:,} images scanned…')
                result.folder_counts[str(folder)].update(tags)
                for tag in tags:
                    result.folder_matches[str(folder)][tag].append(image)
            directory_identity = os.path.normcase(str(parent.resolve()))
            if directory_identity not in seen_directories:
                result.orphan += len(set(txt) - image_stems)
                seen_directories.add(directory_identity)
    return result


class DatasetManager(ttk.Frame):
    def __init__(self, root, download_more=None):
        super().__init__(root)
        self.root = root
        self.download_more_callback = download_more
        self.pack(fill="both", expand=True)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.title('Dataset Manager — Tag Statistics')
        self.root.geometry('1250x800')
        self.root.minsize(850, 550)
        self.dataset = Dataset()
        self.categories = {}
        self.events = queue.Queue()
        self.running = False
        self.closing = False
        self.render_timer = None
        self.cancel_requested = threading.Event()
        self.visible_rows = []
        self.image_paths = []
        self.preview_image = None
        self.sort_column, self.descending = 'images', True
        self.folder_vars = [tk.StringVar(value=str(SOURCE / 'done')), tk.StringVar(value='')]
        controls = ttk.Frame(self, padding=12)
        controls.pack(fill='x')
        for i, var in enumerate(self.folder_vars):
            ttk.Label(controls, text=f'Dataset {i + 1}').grid(row=i, column=0, padx=5, pady=3)
            ttk.Entry(controls, textvariable=var).grid(row=i, column=1, sticky='ew')
            ttk.Button(controls, text='Browse…', command=lambda v=var: self.browse(v)).grid(row=i, column=2, padx=5)
        controls.columnconfigure(1, weight=1)
        self.scan_button = ttk.Button(controls, text='Scan / Refresh', command=self.scan)
        self.scan_button.grid(row=0, column=3, rowspan=2, padx=10)
        self.cancel_button = ttk.Button(controls, text='Cancel scan', command=self.cancel_scan, state='disabled')
        self.cancel_button.grid(row=0, column=4, rowspan=2, padx=5)
        self.status = tk.StringVar(value='Choose folders, then click Scan / Refresh to load the dataset.')
        ttk.Label(self, textvariable=self.status, padding=(15, 5), wraplength=1200).pack(fill='x')
        filters = ttk.Frame(self, padding=(12, 5))
        filters.pack(fill='x')
        self.search = tk.StringVar()
        self.category = tk.StringVar(value='All')
        self.scope = tk.StringVar(value='Both datasets')
        self.minimum = tk.StringVar(value='1')
        self.maximum = tk.StringVar(value='')
        filters.columnconfigure(1, weight=1)
        ttk.Label(filters, text='Search tag').grid(row=0, column=0, sticky='w', padx=(0, 6))
        ttk.Entry(filters, textvariable=self.search).grid(row=0, column=1, sticky='ew', padx=(0, 10))
        for column, (var, choices) in enumerate(((self.category, ['All', 'Character', 'General', 'Artist', 'Copyright', 'Meta', 'Unknown']), (self.scope, ['Both datasets', 'Dataset 1', 'Dataset 2'])), start=2):
            ttk.Combobox(filters, textvariable=var, values=choices, state='readonly', width=17).grid(row=0, column=column, padx=5)
        limits = ttk.Frame(filters)
        limits.grid(row=1, column=0, columnspan=4, sticky='w', pady=(10, 0))
        ttk.Label(limits, text='Minimum images').pack(side='left', padx=(0, 6))
        self.minimum_entry = ttk.Entry(limits, textvariable=self.minimum, width=10)
        self.minimum_entry.pack(side='left')
        ttk.Label(limits, text='Maximum images').pack(side='left', padx=(16, 6))
        self.maximum_entry = ttk.Entry(limits, textvariable=self.maximum, width=10)
        self.maximum_entry.pack(side='left')
        ttk.Label(limits, text='(blank = unlimited)').pack(side='left', padx=6)
        ttk.Button(filters, text='Export filtered CSV', command=self.export).grid(row=2, column=0, columnspan=4, sticky='w', pady=(10, 0))
        self.filter_message = tk.StringVar()
        ttk.Label(filters, textvariable=self.filter_message).grid(row=3, column=0, columnspan=4, sticky='w')
        panes = ttk.Panedwindow(self, orient='horizontal')
        panes.pack(fill='both', expand=True, padx=12, pady=8)
        left, right = ttk.Frame(panes), ttk.Frame(panes)
        panes.add(left, weight=3)
        panes.add(right, weight=2)
        columns = ('tag', 'category', 'images', 'percent', 'first', 'second')
        self.table = ttk.Treeview(left, columns=columns, show='headings', selectmode='browse')
        for column, label, width in zip(columns, ('Tag', 'Category', 'Images', '% of images', 'Dataset 1', 'Dataset 2'), (260, 95, 70, 85, 75, 75)):
            self.table.heading(column, text=label, command=lambda c=column: self.sort(c))
            self.table.column(column, width=width, anchor='w' if column in ('tag', 'category') else 'e')
        scrollbar = ttk.Scrollbar(left, orient='vertical', command=self.table.yview)
        self.table.configure(yscrollcommand=scrollbar.set)
        horizontal = ttk.Scrollbar(left, orient='horizontal', command=self.table.xview)
        self.table.configure(xscrollcommand=horizontal.set)
        horizontal.pack(side='bottom', fill='x')
        scrollbar.pack(side='right', fill='y')
        self.table.pack(fill='both', expand=True)
        self.table.bind('<<TreeviewSelect>>', self.select_tag)
        ttk.Button(right, text='Download more…', command=self.download_more,
                   state='normal' if download_more else 'disabled').pack(anchor='w')
        self.details = tk.StringVar(value='Select a tag to inspect matching images.')
        ttk.Label(right, textvariable=self.details, wraplength=430).pack(fill='x', pady=5)
        self.files = tk.Listbox(right, height=9, exportselection=False)
        self.files.pack(fill='x')
        self.files.bind('<<ListboxSelect>>', self.preview)
        self.files.bind('<Double-Button-1>', self.open_image)
        self.preview_label = ttk.Label(right, anchor='center')
        self.preview_label.pack(fill='both', expand=True)
        self.caption = tk.Text(right, height=7, wrap='word', state='disabled')
        self.caption.pack(fill='x')
        ttk.Label(self, text='Counts = images containing a tag (duplicates within a TXT count once). Categories use local caches; unclassified tags appear as Unknown. Double-click an image to open it.', padding=(12, 5), wraplength=1200).pack(fill='x')
        self.filter_timer = None
        for var in (self.search, self.category, self.scope, self.minimum, self.maximum):
            var.trace_add('write', self.schedule_filter)
        self.poll_timer = self.after(100, self.poll)

    def close(self):
        if self.closing:
            return
        self.closing = True
        self.cancel_requested.set()
        for timer in (self.filter_timer, self.poll_timer, self.render_timer):
            if timer:
                self.after_cancel(timer)
        self.root.destroy()

    def browse(self, var):
        path = filedialog.askdirectory(parent=self.root, initialdir=var.get())
        if path:
            var.set(path)

    def cancel_scan(self):
        self.cancel_requested.set()
        self.cancel_button.configure(state='disabled')
        self.status.set('Cancelling scan…')

    def scan(self):
        if self.running or self.closing:
            return
        try:
            folders = [Path(v.get()).expanduser().resolve() if v.get().strip() else None for v in self.folder_vars]
        except (OSError, ValueError, RuntimeError) as error:
            self.status.set(f'Invalid dataset folder: {error}')
            return
        if not any(folders):
            self.status.set('Choose at least one dataset folder before scanning.')
            return
        self.running = True
        self.cancel_requested.clear()
        self.cancel_button.configure(state='normal')
        self.scan_button.configure(state='disabled')
        self.status.set('Loading local tag categories…')
        def work():
            try:
                categories, warnings = load_categories([SOURCE / 'data/caches/gelbooru_tag_categories.sqlite3', SOURCE / 'data/caches/cache_tags.sqlite3'], self.cancel_requested.is_set)
                dataset = scan_dataset([folder for folder in folders if folder is not None],
                                       lambda s: self.events.put(('progress', s)), self.cancel_requested.is_set)
                if dataset is None or self.cancel_requested.is_set():
                    self.events.put(('cancelled', None))
                    return
                dataset.warnings.extend(warnings)
                self.events.put(('result', (dataset, categories, folders)))
            except Exception as error:
                self.events.put(('error', str(error)))
        try:
            threading.Thread(target=work, daemon=True).start()
        except RuntimeError as error:
            self.events.put(('error', str(error)))

    def poll(self):
        try:
            while True:
                event, value = self.events.get_nowait()
                if event == 'progress':
                    if not self.cancel_requested.is_set():
                        self.status.set(value)
                else:
                    self.running = False
                    self.scan_button.configure(state='normal')
                    self.cancel_button.configure(state='disabled')
                    if event == 'cancelled' or (event == 'result' and self.cancel_requested.is_set()):
                        self.status.set('Scan cancelled. Previous results are kept.')
                    elif event == 'error':
                        self.status.set(f'Scan failed: {value}')
                        messagebox.showerror('Scan failed', value, parent=self.root)
                    else:
                        self.dataset, self.categories, self.folders = value
                        d = self.dataset
                        types = Counter(self.categories.get(tag, 'Unknown') for tag in d.counts)
                        self.status.set(f'{d.images:,} images · {d.captions:,} readable TXT pairs · {len(d.counts):,} unique tags ({types["Character"]:,} character / {types["General"]:,} general / {types["Unknown"]:,} unknown) · {d.missing:,} missing TXT · {d.empty:,} empty TXT · {d.orphan:,} orphan TXT · {len(d.warnings):,} warnings')
                        self.refresh()
                        if d.warnings:
                            messagebox.showwarning('Scan warnings', '\n'.join(d.warnings[:15]), parent=self.root)
        except queue.Empty:
            pass
        if not self.closing:
            self.poll_timer = self.after(100, self.poll)

    def schedule_filter(self, *args):
        if self.filter_timer:
            self.after_cancel(self.filter_timer)
        self.filter_timer = self.after(180, self.refresh)

    def sort(self, column):
        self.descending = not self.descending if self.sort_column == column else column not in ('tag', 'category')
        self.sort_column = column
        self.refresh()

    def refresh(self):
        if self.filter_timer:
            self.after_cancel(self.filter_timer)
            self.filter_timer = None
        try:
            minimum = int(self.minimum.get())
            maximum = int(self.maximum.get()) if self.maximum.get().strip() else float('inf')
        except ValueError:
            self.filter_message.set('Enter whole numbers for the image limits.')
            return False
        if minimum < 1 or maximum < minimum:
            self.filter_message.set('Minimum must be at least 1; maximum must be at least minimum.')
            return False
        self.filter_message.set('')
        folders = getattr(self, 'folders', [])
        first = self.dataset.folder_counts.get(str(folders[0]), Counter()) if folders else Counter()
        second = self.dataset.folder_counts.get(str(folders[1]), Counter()) if len(folders) > 1 else Counter()
        counts = {'Dataset 1': first, 'Dataset 2': second}.get(self.scope.get(), self.dataset.counts)
        # Denominator includes images without captions.
        total = self.dataset.images
        if self.scope.get() != 'Both datasets':
            index = 0 if self.scope.get() == 'Dataset 1' else 1
            total = self.dataset.folder_images[str(folders[index])] if index < len(folders) else 0
        search = normalize(self.search.get())
        selected_category = self.category.get()
        rows = []
        for tag, count in counts.items():
            category = self.categories.get(tag, 'Unknown')
            if minimum <= count <= maximum and search in tag and selected_category in ('All', category):
                rows.append((tag, category, count, round(count / total * 100, 2) if total else 0, first[tag], second[tag]))
        col = ('tag', 'category', 'images', 'percent', 'first', 'second').index(self.sort_column)
        rows.sort(key=lambda row: (row[col], row[0]), reverse=self.descending)
        self.visible_rows = rows
        if self.render_timer:
            self.after_cancel(self.render_timer)
            self.render_timer = None
        children = self.table.get_children()
        if children:
            self.table.delete(*children)
        self.clear_preview()
        self.render_rows(rows)
        return True

    def render_rows(self, rows, offset=0):
        self.render_timer = None
        for row in rows[offset:offset + 200]:
            self.table.insert('', 'end', values=row)
        if offset + 200 < len(rows):
            self.render_timer = self.after(1, lambda: self.render_rows(rows, offset + 200))

    def clear_preview(self):
        self.image_paths = []
        self.preview_image = None
        self.files.delete(0, 'end')
        self.preview_label.configure(image='', text='')
        self.details.set('Select a tag to inspect matching images.')
        self.caption.configure(state='normal')
        self.caption.delete('1.0', 'end')
        self.caption.configure(state='disabled')

    def download_more(self):
        selected = self.table.selection()
        if selected and self.download_more_callback:
            tag = self.table.item(selected[0], 'values')[0]
            self.download_more_callback(tag.replace(' ', '_'))

    def select_tag(self, event=None):
        selected = self.table.selection()
        if not selected:
            self.clear_preview()
            return
        tag = self.table.item(selected[0], 'values')[0]
        self.image_paths = self.dataset.matches[tag]
        if self.scope.get() != 'Both datasets':
            index = 0 if self.scope.get() == 'Dataset 1' else 1
            self.image_paths = self.dataset.folder_matches.get(str(self.folders[index]), {}).get(tag, []) if index < len(self.folders) else []
        self.details.set(f'{tag} — {len(self.image_paths):,} matching images')
        self.files.delete(0, 'end')
        for path in self.image_paths:
            self.files.insert('end', str(path))
        if self.image_paths:
            self.files.selection_set(0)
            self.preview()

    def preview(self, event=None):
        selected = self.files.curselection()
        if not selected:
            return
        path = self.image_paths[selected[0]]
        try:
            from PIL import Image, ImageOps, ImageTk
            with Image.open(path) as image:
                image = ImageOps.exif_transpose(image).convert('RGB')
                image.thumbnail((max(200, self.preview_label.winfo_width()), 340))
                self.preview_image = ImageTk.PhotoImage(image, master=self.root)
            self.preview_label.configure(image=self.preview_image, text='')
        except Exception as error:
            self.preview_label.configure(image='', text=f'Preview unavailable: {error}')
        try:
            text = self.dataset.caption_paths.get(path, path.with_suffix('.txt')).read_text(encoding='utf-8-sig')
        except (OSError, UnicodeError) as error:
            text = str(error)
        self.caption.configure(state='normal')
        self.caption.delete('1.0', 'end')
        self.caption.insert('1.0', text)
        self.caption.configure(state='disabled')

    def open_image(self, event=None):
        if self.files.curselection():
            path = self.image_paths[self.files.curselection()[0]]
            try:
                if os.name == 'nt':
                    os.startfile(path)
                else:
                    subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', str(path)])
            except OSError as error:
                messagebox.showerror('Could not open image', str(error), parent=self.root)

    def export(self):
        if not self.refresh():
            return
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension='.csv', initialfile='dataset_tag_counts.csv', filetypes=[('CSV', '*.csv')])
        if path:
            try:
                with open(path, 'w', newline='', encoding='utf-8-sig') as handle:
                    writer = csv.writer(handle)
                    writer.writerow(('Tag', 'Category', 'Images', 'Percent', 'Dataset 1', 'Dataset 2'))
                    writer.writerows(self.visible_rows)
            except OSError as error:
                messagebox.showerror('Export failed', str(error), parent=self.root)


if __name__ == '__main__':
    root = tk.Tk()
    DatasetManager(root)
    root.mainloop()

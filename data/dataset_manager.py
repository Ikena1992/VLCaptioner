"""Read-only local image/tag dataset browser. Run with Python (Tk + Pillow)."""
from __future__ import annotations

import csv
import json
import tempfile
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
from tkinter import filedialog, messagebox
import ttkbootstrap as ttk

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT
EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tif', '.tiff', '.avif'}
SETTINGS_FILE = ROOT / 'data' / 'caches' / 'dataset_manager_settings.json'
CATEGORIES = {0: 'General', 1: 'Artist', 3: 'Copyright', 4: 'Character', 5: 'Meta'}


def folder_key(path):
    return os.path.normcase(str(Path(path).expanduser().resolve()))


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
                invalid_tags = 0
                for tag, category in database.execute('SELECT tag, category FROM tag_categories'):
                    if cancelled():
                        break
                    if not isinstance(tag, str) or not tag.strip():
                        invalid_tags += 1
                        continue
                    key = normalize(tag)
                    if key not in categories or category == 4:
                        categories[key] = CATEGORIES.get(category, 'Unknown')
                if invalid_tags:
                    warnings.append(f'{path}: skipped {invalid_tags:,} invalid category tags')
        except (sqlite3.Error, OSError, RuntimeError) as error:
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
        folder_seen = set()
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
                try:
                    identity = os.path.normcase(str(image.resolve()))
                except (OSError, RuntimeError) as error:
                    result.warnings.append(f'{image}: {error}')
                    continue
                if identity in folder_seen:
                    continue
                folder_seen.add(identity)
                result.folder_images[str(folder)] += 1
                if identity in scanned:
                    tags, caption = scanned[identity]
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
                    scanned[identity] = (tags, caption)
                    result.counts.update(tags)
                    for tag in tags:
                        result.matches[tag].append(image)
                    if result.images % 500 == 0:
                        progress(f'{result.images:,} images scanned…')
                if caption is not None:
                    result.caption_paths[image] = caption
                result.folder_counts[str(folder)].update(tags)
                for tag in tags:
                    result.folder_matches[str(folder)][tag].append(image)
            try:
                directory_identity = os.path.normcase(str(parent.resolve()))
            except (OSError, RuntimeError) as error:
                result.warnings.append(f'{parent}: {error}')
                directory_identity = os.path.normcase(str(parent.absolute()))
            if directory_identity not in seen_directories:
                result.orphan += len(set(txt) - image_stems)
                seen_directories.add(directory_identity)
    return result


class DatasetManager(ttk.Frame):
    def __init__(self, root, download_more=None, settings_file=SETTINGS_FILE):
        super().__init__(root)
        self.root = root
        self.settings_file = Path(settings_file)
        self.folder_paths = [str(SOURCE / name) for name in ('done', 'images')]
        self.folders = []
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
        self.files_render_timer = None
        self.display_scope = 'All selected folders'
        self.cancel_requested = threading.Event()
        self.visible_rows = []
        self.image_paths = []
        self.preview_image = None
        self.preview_timer = None
        self.caption_path = None
        self.valid_count_limits = ('1', '')
        self.sort_column, self.descending = 'images', True
        header = ttk.Frame(self, padding=(16, 14, 16, 6))
        header.pack(fill='x')
        ttk.Label(header, text='Dataset manager', font=('Segoe UI', 21, 'bold')).pack(anchor='w')
        ttk.Label(header, text='Explore tags, compare datasets and inspect matching images.', bootstyle='secondary').pack(anchor='w', pady=(4, 0))
        controls = ttk.Labelframe(self, text='Dataset folders', padding=10)
        controls.pack(fill='x', padx=16, pady=8)
        controls.columnconfigure(0, weight=1)
        self.folder_list = tk.Listbox(controls, height=3, selectmode='multiple', exportselection=False, relief='flat', highlightthickness=0)
        self.folder_list.grid(row=0, column=0, rowspan=3, sticky='nsew')
        folder_scroll = ttk.Scrollbar(controls, command=self.folder_list.yview)
        self.folder_list.configure(yscrollcommand=folder_scroll.set)
        folder_scroll.grid(row=0, column=1, rowspan=3, sticky='ns')
        ttk.Button(controls, text='Add folder…', command=self.add_folder).grid(row=0, column=2, padx=8, pady=2, sticky='ew')
        ttk.Button(controls, text='Remove selected', command=self.remove_folders).grid(row=1, column=2, padx=8, pady=2, sticky='ew')
        ttk.Button(controls, text='Save settings', command=self.save_settings).grid(row=2, column=2, padx=8, pady=2, sticky='ew')
        self.scan_button = ttk.Button(controls, text='Scan selected', command=self.scan, bootstyle='primary')
        self.scan_button.grid(row=0, column=3, padx=5, pady=2, sticky='ew')
        self.cancel_button = ttk.Button(controls, text='Cancel scan', command=self.cancel_scan, state='disabled', bootstyle='danger-outline')
        self.cancel_button.grid(row=1, column=3, padx=5, pady=2, sticky='ew')
        ttk.Label(controls, text='Click folders to toggle inclusion. Each selected folder gets its own count column.', bootstyle='secondary').grid(row=3, column=0, columnspan=4, sticky='w', pady=(6, 0))
        self.status = tk.StringVar(value='Choose folders, then click Scan / Refresh to load the dataset.')
        ttk.Label(self, textvariable=self.status, padding=(15, 5), wraplength=1200).pack(fill='x')
        filters = ttk.Labelframe(self, text='Filter tags', padding=10)
        filters.pack(fill='x', padx=16, pady=(6, 8))
        self.search = tk.StringVar()
        self.category = tk.StringVar(value='All')
        self.scope = tk.StringVar(value='All selected folders')
        self.minimum = tk.StringVar(value='1')
        self.maximum = tk.StringVar(value='')
        filters.columnconfigure(1, weight=1)
        ttk.Label(filters, text='Search tag').grid(row=0, column=0, sticky='w', padx=(0, 6))
        ttk.Entry(filters, textvariable=self.search).grid(row=0, column=1, sticky='ew', padx=(0, 10))
        for column, (var, choices) in enumerate(((self.category, ['All', 'Character', 'General', 'Artist', 'Copyright', 'Meta', 'Unknown']), (self.scope, ['All selected folders'])), start=2):
            group = ttk.Frame(filters)
            group.grid(row=0, column=column, padx=5, sticky='ew')
            ttk.Label(group, text='Category' if column == 2 else 'Count scope', bootstyle='secondary').pack(anchor='w')
            combo = ttk.Combobox(group, textvariable=var, values=choices, state='readonly', width=22)
            combo.pack(fill='x')
            if column == 3:
                self.scope_combo = combo
        limits = ttk.Frame(filters)
        limits.grid(row=1, column=0, columnspan=4, sticky='w', pady=(10, 0))
        ttk.Label(limits, text='Minimum images').pack(side='left', padx=(0, 6))
        self.minimum_entry = ttk.Entry(limits, textvariable=self.minimum, width=10)
        self.minimum_entry.pack(side='left')
        ttk.Label(limits, text='Maximum images').pack(side='left', padx=(16, 6))
        self.maximum_entry = ttk.Entry(limits, textvariable=self.maximum, width=10)
        self.maximum_entry.pack(side='left')
        ttk.Label(limits, text='(blank = unlimited)').pack(side='left', padx=6)
        ttk.Button(limits, text='Export filtered CSV', command=self.export, bootstyle='secondary-outline').pack(side='left', padx=(20, 0))
        self.filter_message = tk.StringVar()
        ttk.Label(filters, textvariable=self.filter_message).grid(row=3, column=0, columnspan=4, sticky='w')
        panes = ttk.Panedwindow(self, orient='horizontal')
        panes.pack(fill='both', expand=True, padx=16, pady=8)
        left, right = ttk.Labelframe(panes, text='Tag statistics', padding=6), ttk.Labelframe(panes, text='Image inspector', padding=8)
        panes.add(left, weight=3)
        panes.add(right, weight=2)
        columns = ('tag', 'category', 'images', 'percent')
        self.table = ttk.Treeview(left, columns=columns, show='headings', selectmode='extended')
        for column, label, width in zip(columns, ('Tag', 'Category', 'Images', '% of images'), (260, 95, 70, 85)):
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
        self.download_button = ttk.Button(right, text='Download more for selected tags', command=self.download_more,
                   bootstyle='primary-outline', state='disabled')
        self.download_button.pack(anchor='w')
        self.details = tk.StringVar(value='Select a tag to inspect matching images.')
        ttk.Label(right, textvariable=self.details, wraplength=430).pack(fill='x', pady=5)
        files_panel = ttk.Frame(right)
        files_panel.pack(fill='x', pady=(4, 8))
        self.files = tk.Listbox(files_panel, height=5, exportselection=False, relief='flat', highlightthickness=0)
        file_scroll = ttk.Scrollbar(files_panel, command=self.files.yview)
        file_horizontal = ttk.Scrollbar(files_panel, orient='horizontal', command=self.files.xview)
        self.files.configure(yscrollcommand=file_scroll.set, xscrollcommand=file_horizontal.set)
        file_horizontal.pack(side='bottom', fill='x')
        file_scroll.pack(side='right', fill='y')
        self.files.pack(fill='both', expand=True)
        self.files.bind('<<ListboxSelect>>', self.preview)
        self.files.bind('<Double-Button-1>', self.open_image)
        inspector_tabs = ttk.Notebook(right)
        inspector_tabs.pack(fill='both', expand=True)
        image_tab, caption_tab = ttk.Frame(inspector_tabs), ttk.Frame(inspector_tabs, padding=4)
        inspector_tabs.add(image_tab, text='Image')
        inspector_tabs.add(caption_tab, text='Full tag text')
        self.preview_label = ttk.Label(image_tab, anchor='center', text='Select a tag and an image to preview.')
        self.preview_label.pack(fill='both', expand=True)
        self.preview_label.bind('<Configure>', self.schedule_preview)
        self.caption = tk.Text(caption_tab, height=7, wrap='word', state='disabled', relief='flat', padx=8, pady=8)
        caption_scroll = ttk.Scrollbar(caption_tab, command=self.caption.yview)
        self.caption.configure(yscrollcommand=caption_scroll.set)
        caption_scroll.pack(side='right', fill='y')
        self.caption.pack(fill='both', expand=True)
        self.apply_theme()
        ttk.Label(self, text='Counts = images containing a tag (duplicates within a TXT count once). Categories use local caches; unclassified tags appear as Unknown. Double-click an image to open it.', padding=(12, 5), wraplength=1200).pack(fill='x')
        self.filter_timer = None
        self.restore_settings()
        for var in (self.search, self.category, self.scope, self.minimum, self.maximum):
            var.trace_add('write', self.schedule_filter)
        self.poll_timer = self.after(100, self.poll)

    def apply_theme(self):
        colors = ttk.Style().colors
        for widget in (self.files, self.caption, self.folder_list):
            widget.configure(background=colors.inputbg, foreground=colors.inputfg)
        for widget in (self.files, self.folder_list):
            widget.configure(selectbackground=colors.primary, selectforeground='white')

    def close(self):
        if self.closing:
            return
        self.save_settings(notify=False)
        self.closing = True
        self.cancel_requested.set()
        for timer in (self.filter_timer, self.poll_timer, self.render_timer, self.preview_timer, self.files_render_timer):
            if timer:
                self.after_cancel(timer)
        self.root.destroy()

    def show_folders(self, selected):
        self.folder_list.delete(0, 'end')
        for index, path in enumerate(self.folder_paths):
            self.folder_list.insert('end', path)
            if path in selected:
                self.folder_list.selection_set(index)

    def add_folder(self):
        path = filedialog.askdirectory(parent=self.root)
        if path:
            path = str(Path(path).resolve())
            selected = self.selected_folders()
            existing = next((p for p in self.folder_paths if folder_key(p) == folder_key(path)), None)
            if existing is None:
                self.folder_paths.append(path)
            selected.append(existing or path)
            self.show_folders(selected)

    def selected_folders(self):
        return [self.folder_paths[i] for i in self.folder_list.curselection()]

    def remove_folders(self):
        selected = set(self.selected_folders())
        self.folder_paths = [path for path in self.folder_paths if path not in selected]
        self.show_folders([])

    def restore_settings(self):
        selected = list(self.folder_paths)
        try:
            settings = json.loads(self.settings_file.read_text(encoding='utf-8'))
            if not isinstance(settings, dict):
                raise ValueError('Invalid settings object')
            paths, selected = settings['folders'], settings['selected']
            if not isinstance(paths, list) or not isinstance(selected, list) or not all(isinstance(p, str) and p.strip() for p in paths + selected):
                raise ValueError('Invalid folders')
            paths_by_key = {folder_key(p): str(Path(p).expanduser().resolve()) for p in paths}
            selected_keys = {folder_key(p) for p in selected}
            filters = {}
            for name in ('search', 'category', 'scope', 'minimum', 'maximum'):
                value = settings.get(name, getattr(self, name).get())
                if not isinstance(value, str):
                    raise ValueError('Invalid filter')
                filters[name] = value
            if filters['category'] not in ('All', *CATEGORIES.values(), 'Unknown'):
                raise ValueError('Invalid category')
            minimum = int(filters['minimum'])
            maximum = int(filters['maximum']) if filters['maximum'].strip() else float('inf')
            if minimum < 1 or maximum < minimum:
                raise ValueError('Invalid count limits')
            selected = [path for key, path in paths_by_key.items() if key in selected_keys]
            if filters['scope'] != 'All selected folders':
                filters['scope'] = paths_by_key.get(folder_key(filters['scope']), 'All selected folders')
                if filters['scope'] not in selected:
                    filters['scope'] = 'All selected folders'
            self.folder_paths = list(paths_by_key.values())
            self.scope_combo.configure(values=['All selected folders'] + selected)
            for name, value in filters.items():
                getattr(self, name).set(value)
            self.valid_count_limits = (filters['minimum'], filters['maximum'])
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, KeyError, RuntimeError):
            selected = list(self.folder_paths)
            self.status.set('Saved settings unavailable; using defaults.')
        self.show_folders(selected)

    def save_settings(self, notify=True):
        settings = {'folders': self.folder_paths, 'selected': self.selected_folders()}
        settings.update({name: getattr(self, name).get() for name in ('search', 'category', 'scope', 'minimum', 'maximum')})
        try:
            minimum = int(settings['minimum'])
            maximum = int(settings['maximum']) if settings['maximum'].strip() else float('inf')
            if minimum < 1 or maximum < minimum:
                raise ValueError('Invalid count limits')
            self.valid_count_limits = (settings['minimum'], settings['maximum'])
        except ValueError:
            settings['minimum'], settings['maximum'] = self.valid_count_limits
        temporary = None
        try:
            self.settings_file.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.settings_file.parent, prefix='.dataset-settings-', suffix='.tmp', delete=False) as output:
                temporary = Path(output.name)
                json.dump(settings, output, ensure_ascii=False, indent=2)
            temporary.replace(self.settings_file)
            if notify:
                self.status.set('Settings saved.')
        except OSError as error:
            messagebox.showwarning('Settings not saved', str(error), parent=self.root)
        finally:
            if temporary:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def configure_dataset_columns(self):
        columns = ('tag', 'category', 'images', 'percent') + tuple(f'folder{i}' for i in range(len(self.folders)))
        self.table.configure(columns=columns)
        labels = ('Tag', 'Category', 'Images', '% of images') + tuple(str(path) for path in self.folders)
        for column, label in zip(columns, labels):
            self.table.heading(column, text=label, command=lambda c=column: self.sort(c))
            self.table.column(column, width=240 if column == 'tag' else 120, anchor='w' if column in ('tag', 'category') else 'e')
        self.scope_combo.configure(values=['All selected folders'] + [str(path) for path in self.folders])
        if self.scope.get() not in self.scope_combo['values']:
            self.scope.set('All selected folders')
        if self.sort_column not in columns:
            self.sort_column = 'images'

    def cancel_scan(self):
        self.cancel_requested.set()
        self.cancel_button.configure(state='disabled')
        self.status.set('Cancelling scan…')

    def scan(self):
        if self.running or self.closing:
            return
        try:
            folders = list(dict.fromkeys(Path(path).expanduser().resolve() for path in self.selected_folders()))
        except (OSError, ValueError, RuntimeError) as error:
            self.status.set(f'Invalid dataset folder: {error}')
            return
        if not any(folders):
            self.status.set('Choose at least one dataset folder before scanning.')
            return
        self.save_settings(notify=False)
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
            for _ in range(200):
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
                        if self.render_timer:
                            self.after_cancel(self.render_timer)
                            self.render_timer = None
                        self.visible_rows = []
                        children = self.table.get_children()
                        if children:
                            self.table.delete(*children)
                        self.clear_preview()
                        self.dataset, self.categories, self.folders = value
                        self.configure_dataset_columns()
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
        self.valid_count_limits = (self.minimum.get(), self.maximum.get())
        self.filter_message.set('')
        folders = getattr(self, 'folders', [])
        folder_counts = [self.dataset.folder_counts.get(str(folder), Counter()) for folder in folders]
        scope = self.scope.get()
        counts = self.dataset.counts if scope == 'All selected folders' else self.dataset.folder_counts.get(scope, Counter())
        total = self.dataset.images if scope == 'All selected folders' else self.dataset.folder_images[scope]
        search = normalize(self.search.get())
        selected_category = self.category.get()
        rows = []
        for tag, count in counts.items():
            category = self.categories.get(tag, 'Unknown')
            if minimum <= count <= maximum and search in tag and selected_category in ('All', category):
                rows.append((tag, category, count, round(count / total * 100, 2) if total else 0, *(counter[tag] for counter in folder_counts)))
        col = (('tag', 'category', 'images', 'percent') + tuple(f'folder{i}' for i in range(len(folders)))).index(self.sort_column)
        rows.sort(key=lambda row: (row[col], row[0]), reverse=self.descending)
        self.display_scope = scope
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
        if self.files_render_timer:
            self.after_cancel(self.files_render_timer)
            self.files_render_timer = None
        if self.preview_timer:
            self.after_cancel(self.preview_timer)
            self.preview_timer = None
        self.image_paths = []
        self.preview_image = None
        self.caption_path = None
        self.files.delete(0, 'end')
        self.preview_label.configure(image='', text='')
        self.details.set('Select a tag to inspect matching images.')
        self.download_button.configure(state='disabled')
        self.caption.configure(state='normal')
        self.caption.delete('1.0', 'end')
        self.caption.configure(state='disabled')

    def download_more(self):
        selected = self.table.selection()
        if selected and self.download_more_callback:
            for identifier in selected:
                tag = self.table.item(identifier, 'values')[0]
                self.download_more_callback(tag.replace(' ', '_'))

    def select_tag(self, event=None):
        selected = self.table.selection()
        if not selected:
            self.clear_preview()
            return
        tag = self.table.item(selected[0], 'values')[0]
        self.clear_preview()
        self.download_button.configure(state='normal' if self.download_more_callback else 'disabled')
        self.image_paths = self.dataset.matches[tag]
        if self.display_scope != 'All selected folders':
            self.image_paths = self.dataset.folder_matches.get(self.display_scope, {}).get(tag, [])
        self.details.set(f'{tag} — {len(self.image_paths):,} matching images')
        self.render_files()
        if self.image_paths:
            self.files.selection_set(0)
            self.preview()

    def render_files(self, offset=0):
        self.files_render_timer = None
        batch = self.image_paths[offset:offset + 200]
        if batch:
            self.files.insert('end', *(str(path) for path in batch))
        if offset + 200 < len(self.image_paths):
            self.files_render_timer = self.after(1, lambda: self.render_files(offset + 200))

    def schedule_preview(self, event=None):
        if self.preview_timer:
            self.after_cancel(self.preview_timer)
        self.preview_timer = self.after(100, self.preview)

    def preview(self, event=None):
        if self.preview_timer:
            self.after_cancel(self.preview_timer)
            self.preview_timer = None
        selected = self.files.curselection()
        if not selected or selected[0] >= len(self.image_paths):
            return
        path = self.image_paths[selected[0]]
        try:
            from PIL import Image, ImageOps, ImageTk
            with Image.open(path) as image:
                image = ImageOps.exif_transpose(image).convert('RGB')
                width, height = self.preview_label.winfo_width(), self.preview_label.winfo_height()
                image.thumbnail((max(1, width - 16) if width > 1 else 400,
                                 max(1, height - 16) if height > 1 else 260))
                self.preview_image = ImageTk.PhotoImage(image, master=self.root)
            self.preview_label.configure(image=self.preview_image, text='')
        except Exception as error:
            self.preview_image = None
            self.preview_label.configure(image='', text=f'Preview unavailable: {error}')
        if self.caption_path == path and event is None:
            return
        try:
            text = self.dataset.caption_paths.get(path, path.with_suffix('.txt')).read_text(encoding='utf-8-sig')
        except (OSError, UnicodeError) as error:
            text = str(error)
        self.caption.configure(state='normal')
        self.caption.delete('1.0', 'end')
        self.caption.insert('1.0', text)
        self.caption.yview_moveto(0)
        self.caption.configure(state='disabled')
        self.caption_path = path

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
        # Native save dialogs run a nested event loop: scan results and filter
        # callbacks can arrive while the dialog is open. Freeze both together.
        headers = ('Tag', 'Category', 'Images', 'Percent') + tuple(str(folder) for folder in self.folders)
        rows = list(self.visible_rows)
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension='.csv', initialfile='dataset_tag_counts.csv', filetypes=[('CSV', '*.csv')])
        if path:
            temporary = None
            try:
                destination = Path(path)
                with tempfile.NamedTemporaryFile(mode='w', newline='', encoding='utf-8-sig',
                                                 dir=destination.parent, prefix='.dataset-export-',
                                                 suffix='.tmp', delete=False) as handle:
                    temporary = Path(handle.name)
                    writer = csv.writer(handle)
                    writer.writerow(headers)
                    writer.writerows(rows)
                temporary.replace(destination)
            except OSError as error:
                messagebox.showerror('Export failed', str(error), parent=self.root)
            finally:
                if temporary:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass


if __name__ == '__main__':
    root = ttk.Window(themename='flatly')
    DatasetManager(root)
    root.mainloop()

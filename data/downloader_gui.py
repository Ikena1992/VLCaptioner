"""Small standalone, row-based booru download queue."""
import queue
import json
from pathlib import Path
from file_io import atomic_write_json, atomic_write_text
import threading
import uuid
import tkinter as tk
from tkinter import messagebox, scrolledtext, filedialog
from tkinter import font as tkfont

import ttkbootstrap as ttk

from booru_downloader import DatasetCountError, DownloadJob, ImageIndex, failure_reason, gap_tag, parse_blacklist, run_job, query_rating, SCORE_FILTER
from runtime_config import PROJECT_DIR
import caption_queue
from character_discovery import CharacterOptions, discover, search_rows, character_tags_text

SETTINGS_FILE = PROJECT_DIR / "data" / "caches" / "downloader_settings.json"


def minimum_score_filter(value):
    """Turn an optional minimum-score number into a search metatag."""
    value = value.strip()
    if not value:
        return ''
    try:
        return f'score:>={int(value)}'
    except ValueError:
        raise ValueError('Minimum score must be a whole number, or blank.') from None


def search_identity(row):
    """Compare equivalent single-character searches regardless of token order."""
    source, query = row['source'], row['tags']
    try:
        tag = gap_tag(source, query).casefold()
        score = tuple(sorted(token.casefold() for token in query.split() if SCORE_FILTER.fullmatch(token)))
        return source, tag, query_rating(query), score
    except ValueError:
        return source, ' '.join(query.casefold().split())


def download_job_from_row(row, blacklist):
    tags = row['tags'].strip()
    if not tags:
        raise ValueError('Search tags are empty.')
    if row['source'] not in ('Danbooru', 'Gelbooru'):
        raise ValueError('Choose Danbooru or Gelbooru.')
    try:
        target = int(row.get('target', '').strip())
        if target < 1:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('Target image count must be a positive whole number.') from None
    gap_tag(row['source'], tags)
    return DownloadJob(row['source'], tags, blacklist=blacklist, target_count=target)



class DownloaderGUI:
    def __init__(self, root, settings_file=SETTINGS_FILE, can_start=None):
        self.root = root
        self.settings_file = Path(settings_file)
        self.can_start = can_start or (lambda automatic: True)
        self.rows = {}
        self.loading_editor = False
        self.cell_edit = None
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.running = False
        self.closing = False
        self.poll_job = None
        self.close_job = None
        self.discovery_running = False
        self.discovery_cancel = threading.Event()
        self.character_results = []
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
        self.build_character_tab(notebook)

        row_tools = ttk.Frame(search_page)
        row_tools.pack(fill="x", pady=(0, 8))
        self.add_button = ttk.Button(row_tools, text="Add search", command=self.new_row, bootstyle="primary")
        self.add_button.pack(side="left")
        self.remove_button = ttk.Button(row_tools, text="Remove selected", command=self.remove_selected, bootstyle="secondary-outline")
        self.remove_button.pack(side="left", padx=8)
        self.remove_all_button = ttk.Button(row_tools, text='Remove all', command=self.remove_all, bootstyle='danger-outline')
        self.remove_all_button.pack(side='left')
        self.row_count = tk.StringVar()
        ttk.Label(row_tools, textvariable=self.row_count, bootstyle="secondary").pack(side="right")
        table_panel = ttk.Frame(search_page)
        table_panel.pack(fill="both", expand=True)
        table_panel.rowconfigure(0, weight=1)
        table_panel.columnconfigure(0, weight=1)
        ttk.Style().configure("DownloadRows.Treeview",
                              rowheight=max(30, tkfont.nametofont("TkDefaultFont").metrics("linespace") + 10))
        self.table = ttk.Treeview(table_panel, columns=("source", "tags", "target"),
                                 show="headings", selectmode="extended", height=7,
                                 style="DownloadRows.Treeview")
        for column, title, width in (("source", "Source", 110), ("tags", "Search tags", 430), ("target", "Target image count", 160)):
            self.table.heading(column, text=title)
            self.table.column(column, width=width, minwidth=80, stretch=column == "tags")
        self.table.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(table_panel, orient="vertical", command=lambda *args: (self.finish_cell_edit(), self.table.yview(*args)))
        horizontal = ttk.Scrollbar(table_panel, orient="horizontal", command=lambda *args: (self.finish_cell_edit(), self.table.xview(*args)))
        self.table.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal.set)
        scrollbar.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        self.table.bind("<<TreeviewSelect>>", self.select_row)
        self.table.bind("<Delete>", lambda event: self.remove_selected())
        self.table.bind('<Double-1>', self.edit_clicked_cell)
        self.table.bind('<Button-1>', lambda event: self.finish_cell_edit_on_navigation(event))
        self.table.bind('<F2>', self.edit_focused_cell)
        self.table.bind('<Configure>', lambda event: self.finish_cell_edit())
        self.table.bind('<MouseWheel>', self.finish_cell_edit_on_navigation)
        self.edit_source = tk.StringVar(value="Danbooru")
        self.edit_tags = tk.StringVar()
        self.edit_target = tk.StringVar()
        self.editor_widgets = [
            ttk.Combobox(self.table, textvariable=self.edit_source, values=("Danbooru", "Gelbooru"), state="readonly", width=12),
            ttk.Entry(self.table, textvariable=self.edit_tags),
            ttk.Entry(self.table, textvariable=self.edit_target, width=12),
        ]
        for widget in self.editor_widgets:
            widget.bind('<Return>', lambda event: self.finish_cell_key())
            widget.bind('<Escape>', lambda event: self.finish_cell_key(cancel=True))
            widget.bind('<Tab>', lambda event: self.next_edit_cell(1))
            widget.bind('<Shift-Tab>', lambda event: self.next_edit_cell(-1))
            widget.bind('<FocusOut>', self.finish_unfocused_cell)
        self.editor_widgets[0].bind('<<ComboboxSelected>>', lambda event: self.finish_cell_edit())
        self.editor_hint = tk.StringVar(value="Select one search to edit. Changes are saved immediately to the row.")
        ttk.Label(search_page, textvariable=self.editor_hint, bootstyle="secondary", wraplength=800).pack(anchor='w', pady=(8, 0))
        for variable in (self.edit_source, self.edit_tags, self.edit_target):
            variable.trace_add("write", self.update_selected_row)
        ttk.Label(search_page, text="Double-click a cell or press F2 to edit. Enter saves; Esc cancels; Tab moves between cells. Use spaces between tags and underscores within tags. Ctrl / Shift selects multiple rows.",
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
        help_panel = ttk.Labelframe(settings_page, text="How download targets work", padding=12)
        help_panel.pack(fill="x")
        ttk.Label(help_panel, text="Target image count is the desired total of matching images in images/ and done/. Downloads fill the missing amount and stop when the target is covered or results run out. Duplicates, blacklisted posts and failed downloads do not consume the target. Use one character/tag per row, optionally filtered by rating and minimum score. Enable automatic captioning to send downloads through to done/.",
                  wraplength=780, justify="left").pack(anchor="w")

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

    def build_character_tab(self, notebook):
        page = ttk.Frame(notebook, padding=16)
        notebook.add(page, text='Missing characters')
        ttk.Label(page, text='Find new character tags', font=('Segoe UI', 16, 'bold')).pack(anchor='w')
        ttk.Label(page, text='Discover Danbooru character tags created on or after the date (UTC). '
                  'Compare unique images in images/ and done/. Add searches without starting downloads.',
                  wraplength=780).pack(anchor='w', pady=(8, 16))
        fields = ttk.Frame(page)
        fields.pack(fill='x')
        defaults = {'since': '2025-01-01', 'min_posts': '100',
                    'source': 'Both', 'scope': 'Below target', 'score_filter': '',
                    'rating_g': '50', 'rating_s': '50', 'rating_q': '50', 'rating_e': '50'}
        self.character_fields = {key: tk.StringVar(value=value) for key, value in defaults.items()}
        self.character_widgets = []
        for index, (key, title, choices) in enumerate((
                ('since', 'Tag created on/after (YYYY-MM-DD)', None),
                ('min_posts', 'Minimum posts on Danbooru', None),
                ('score_filter', 'Minimum score (optional number, e.g. 5)', None),
                ('source', 'Queue download sources', ('Danbooru', 'Gelbooru', 'Both')),
                ('scope', 'Characters to include', ('Absent only', 'Below target')))):
            ttk.Label(fields, text=title).grid(row=index, column=0, sticky='w', pady=6, padx=(0, 18))
            widget = (ttk.Combobox(fields, textvariable=self.character_fields[key], values=choices, state='readonly')
                      if choices else ttk.Entry(fields, textvariable=self.character_fields[key]))
            widget.grid(row=index, column=1, sticky='ew', pady=6)
            self.character_widgets.append(widget)
        fields.columnconfigure(1, weight=1)
        ratings = ttk.Labelframe(page, text='Target images per character and rating (0 skips that rating)', padding=8)
        ratings.pack(fill='x', pady=(10, 0))
        for index, (rating, label) in enumerate(zip('gsqe', ('General', 'Sensitive', 'Questionable', 'Explicit'))):
            ttk.Label(ratings, text=label).grid(row=0, column=index, sticky='w', padx=6)
            widget = ttk.Entry(ratings, textvariable=self.character_fields[f'rating_{rating}'], width=10)
            widget.grid(row=1, column=index, sticky='ew', padx=6)
            ratings.columnconfigure(index, weight=1)
            self.character_widgets.append(widget)
        ttk.Label(page, text='Minimum posts measures total Danbooru availability, before blacklist filtering. '
                  'Below target includes partially filled characters. Both sources share the same total target; '
                  'Gelbooru fills any gap left by Danbooru. Score filters restrict downloads, not the total post-count threshold '
                  'or existing dataset counts. Enable automatic captioning to move downloads into done/.',
                  wraplength=780, bootstyle='secondary').pack(anchor='w', pady=16)
        actions = ttk.Frame(page)
        actions.pack(fill='x')
        self.discover_button = ttk.Button(actions, text='Find and add missing characters', command=self.start_discovery)
        self.discover_button.pack(side='left')
        self.discovery_stop = ttk.Button(actions, text='Cancel search', command=self.discovery_cancel.set, state='disabled')
        self.discovery_stop.pack(side='left', padx=8)
        exports = ttk.Frame(page)
        exports.pack(fill='x', pady=(8, 0))
        ttk.Button(exports, text='Export last results as JSON', command=self.export_characters).pack(side='left')
        ttk.Button(exports, text='Export character tags as TXT', command=self.export_character_tags).pack(side='left', padx=8)
        self.discovery_status = tk.StringVar(value='Ready. Existing queue rows are kept; duplicate searches are skipped.')
        ttk.Label(page, textvariable=self.discovery_status, wraplength=780).pack(anchor='w', pady=16)

    def start_discovery(self):
        if self.running or self.discovery_running or self.closing:
            return
        try:
            values = {key: value.get().strip() for key, value in self.character_fields.items()}
            options = CharacterOptions(values['since'], int(values['min_posts']), sum(int(values[f'rating_{r}']) for r in 'gsqe'),
                                       values['source'], values['scope'], minimum_score_filter(values['score_filter']),
                                       tuple(int(values[f'rating_{r}']) for r in 'gsqe'))
        except (ValueError, TypeError):
            messagebox.showerror('Missing characters', 'Enter a valid YYYY-MM-DD date, positive minimum posts, nonnegative rating targets (at least one above zero), and an optional whole-number minimum score such as 5.', parent=self.root)
            return
        self.save_settings()
        self.discovery_cancel.clear()
        self.discovery_running = True
        self.discover_button.configure(state='disabled')
        self.discovery_stop.configure(state='normal')
        self.start_button.configure(state='disabled')
        self.discovery_status.set('Searching Danbooru API character tags…')
        def work():
            try:
                results = discover(options, PROJECT_DIR, self.discovery_cancel,
                                   lambda text: self.events.put(('character_status', text)))
                if results is not None and not self.discovery_cancel.is_set():
                    self.events.put(('characters', (results, options.source)))
                else:
                    self.events.put(('character_status', 'Search cancelled; queue unchanged.'))
            except Exception as error:
                message = str(error) if isinstance(error, DatasetCountError) else failure_reason(error)
                self.events.put(('character_status', 'Search failed; queue unchanged. ' + message))
            finally:
                self.events.put(('character_finished', None))
        try:
            threading.Thread(target=work, daemon=True).start()
        except RuntimeError:
            self.events.put(('character_status', 'Could not start character search.'))
            self.events.put(('character_finished', None))

    def add_character_results(self, results, source):
        if self.closing or self.discovery_cancel.is_set():
            return
        self.finish_cell_edit()
        self.character_results = results
        existing = {search_identity(row): identifier for identifier, row in self.rows.items()}
        blank_rows = iter(identifier for identifier, row in self.rows.items() if not row['tags'].strip())
        added = updated = 0
        for row in search_rows(results, source):
            key = search_identity(row)
            if key in existing:
                identifier = existing[key]
                previous = self.rows[identifier]
                # Refresh targets of searches owned by this feature, while
                # preserving manually entered rows and their custom targets.
                if previous.get('character_generated') and previous['target'] != row['target']:
                    previous['target'] = row['target']
                    self.table.item(identifier, values=tuple(previous[k] for k in ('source', 'tags', 'target')))
                    updated += 1
                continue
            blank = next(blank_rows, None)
            if blank is None:
                identifier = self.add_row(row['source'], row['tags'], row['target'])
            else:
                identifier = blank
                self.rows[blank] = row
                self.table.item(blank, values=tuple(row[k] for k in ('source', 'tags', 'target')))
            self.rows[identifier]['character_generated'] = True
            existing[key] = identifier
            added += 1
        self.update_row_count()
        self.select_row()
        self.save_settings()
        self.discovery_status.set(f'{len(results):,} eligible characters; added {added:,} searches, updated {updated:,} targets. Review Search queue, then start downloads.')

    def export_characters(self):
        if not self.character_results:
            messagebox.showinfo('Missing characters', 'Complete a character search first.', parent=self.root)
            return
        results = list(self.character_results)
        filename = filedialog.asksaveasfilename(parent=self.root, defaultextension='.json',
                                              filetypes=[('JSON', '*.json')], initialfile='missing_characters.json')
        if filename:
            try:
                atomic_write_json(Path(filename), results, ensure_ascii=False, indent=2, trailing_newline=True)
                self.discovery_status.set(f'Exported {len(results):,} character results to {filename}')
            except OSError:
                messagebox.showerror('Missing characters', 'Could not save the JSON file.', parent=self.root)

    def update_batch_controls(self):
        self.batch_entry.configure(state="normal" if self.auto_caption.get() and not self.running else "disabled")

    def export_character_tags(self):
        if not self.character_results:
            messagebox.showinfo('Missing characters', 'Complete a character search first.', parent=self.root)
            return
        # Native file dialogs run a nested event loop. Capture this result set
        # before opening one, so a finishing search cannot change the export.
        results = list(self.character_results)
        text = character_tags_text(results)
        count = len({result['name'] for result in results})
        filename = filedialog.asksaveasfilename(parent=self.root, defaultextension='.txt',
                                              filetypes=[('Text files', '*.txt')], initialfile='character_tags.txt')
        if filename:
            try:
                atomic_write_text(Path(filename), text)
                self.discovery_status.set(f'Exported {count:,} character tags to {filename}')
            except OSError:
                messagebox.showerror('Missing characters', 'Could not save the TXT file.', parent=self.root)

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
                        or not isinstance(row.get("tags"), str)):
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
        saved_characters = settings.get('character_discovery', {})
        if isinstance(saved_characters, dict):
            for key, variable in self.character_fields.items():
                if isinstance(saved_characters.get(key), str):
                    value = saved_characters[key]
                    if key == 'score_filter' and value.lower().startswith('score:>='):
                        value = value[len('score:>='):]
                    variable.set(value)
        for row in rows:
            target = str(row.get('target', '')).strip()
            if not target:
                legacy = str(row.get('limit', '')).strip()
                target = legacy if legacy.isdecimal() and int(legacy) > 0 else '200'
            identifier = self.add_row(row["source"], row["tags"], target)
            if isinstance(row.get('gap_group'), str):
                self.rows[identifier]['gap_group'] = row['gap_group']
            if row.get('character_generated') is True:
                self.rows[identifier]['character_generated'] = True

    def save_settings(self):
        settings = {"auto_caption": self.auto_caption.get(), "batch_size": self.batch_size.get(), "blacklist_separator": "spaces", "blacklist": self.blacklist.get(), "rows": list(self.rows.values())}
        settings['character_discovery'] = {key: value.get() for key, value in self.character_fields.items()}
        try:
            self.settings_file.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(self.settings_file, settings, ensure_ascii=False, indent=2,
                              trailing_newline=True, prefix=".downloader-settings-")
            return True
        except OSError:
            messagebox.showwarning("Image downloader", "Could not save downloader settings. Check folder permissions.", parent=self.root)
            return False

    def update_row_count(self):
        self.row_count.set(f"{len(self.rows):,} searches")

    def add_row(self, source_value="Danbooru", tags_value="", target_value="200"):
        identifier = self.table.insert("", "end", values=(source_value, tags_value, target_value))
        self.rows[identifier] = dict(source=source_value, tags=tags_value, target=target_value)
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
                self.table.item(identifier, values=(source, query, target))
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
        self.begin_cell_edit(identifier, 2)

    def new_row(self):
        if self.running or self.closing:
            return
        identifier = self.add_row()
        self.table.selection_set(identifier)
        self.table.see(identifier)
        self.select_row()
        self.begin_cell_edit(identifier, 1)
        self.update_row_count()

    def edit_clicked_cell(self, event):
        if self.table.identify_region(event.x, event.y) != 'cell':
            return
        identifier = self.table.identify_row(event.y)
        column = int(self.table.identify_column(event.x)[1:]) - 1
        self.begin_cell_edit(identifier, column)
        return 'break'

    def edit_focused_cell(self, event=None):
        selected = self.table.selection()
        if len(selected) == 1:
            self.begin_cell_edit(selected[0], 1)
        return 'break'

    def begin_cell_edit(self, identifier, column):
        self.finish_cell_edit()
        if self.running or self.closing or identifier not in self.rows or column not in range(3):
            return False
        self.table.selection_set(identifier)
        self.table.focus(identifier)
        self.select_row()
        self.table.see(identifier)
        self.root.update_idletasks()
        bounds = self.table.bbox(identifier, ('source', 'tags', 'target')[column])
        if not bounds:
            return False
        self.cell_edit = (identifier, column, dict(self.rows[identifier]))
        widget = self.editor_widgets[column]
        x, y, width, height = bounds
        widget.place(x=x, y=y, width=width, height=height)
        widget.focus_set()
        widget.selection_range(0, 'end')
        return True

    def finish_cell_edit(self, cancel=False):
        if self.cell_edit is None:
            return 'break'
        identifier, column, original = self.cell_edit
        self.cell_edit = None
        self.editor_widgets[column].place_forget()
        if cancel and identifier in self.rows:
            self.rows[identifier] = original
            self.table.item(identifier, values=tuple(original[k] for k in ('source', 'tags', 'target')))
            self.select_row()
        elif identifier in self.rows and self.rows[identifier] != original:
            self.save_settings()
        return 'break'

    def finish_unfocused_cell(self, event):
        if self.cell_edit and self.editor_widgets[self.cell_edit[1]] is event.widget:
            self.finish_cell_edit()

    def finish_cell_edit_on_navigation(self, event):
        self.finish_cell_edit()
        # Let the Treeview class binding select rows, resize columns or scroll.

    def finish_cell_key(self, cancel=False):
        self.finish_cell_edit(cancel=cancel)
        self.table.focus_set()
        return 'break'

    def next_edit_cell(self, direction):
        if self.cell_edit is None:
            return 'break'
        identifier, column, _ = self.cell_edit
        self.finish_cell_edit()
        for offset in range(1, 4):
            if self.begin_cell_edit(identifier, (column + direction * offset) % 3):
                break
        return 'break'

    def select_row(self, event=None):
        selected = self.table.selection()
        if self.cell_edit and selected != (self.cell_edit[0],):
            self.finish_cell_edit()
        row = self.rows.get(selected[0]) if len(selected) == 1 else None
        self.loading_editor = True
        try:
            self.edit_source.set(row["source"] if row else "Danbooru")
            self.edit_tags.set(row["tags"] if row else "")
            self.edit_target.set(row.get("target", "") if row else "")
        finally:
            self.loading_editor = False
        if not row:
            self.editor_hint.set("Select one search to edit." if not selected else
                                 f"{len(selected):,} searches selected. Choose one to edit, or remove the selection.")
        else:
            self.editor_hint.set("Target image count is the desired total of matching images in images/ and done/." if self.edit_target.get().strip() else
                                 "Enter a positive target before starting downloads.")
        self.set_row_controls(not self.running and not self.closing)

    def update_selected_row(self, *args):
        if self.loading_editor or self.running or self.closing:
            return
        selected = self.table.selection()
        if len(selected) != 1 or selected[0] not in self.rows:
            return
        row = self.rows[selected[0]]
        row.pop('character_generated', None)
        row.update(source=self.edit_source.get(), tags=self.edit_tags.get(), target=self.edit_target.get())
        self.table.item(selected[0], values=(row["source"], row["tags"], row["target"]))
        self.editor_hint.set("Target image count is the desired total of matching images in images/ and done/." if row["target"].strip() else
                             "Enter a positive target before starting downloads.")
        self.set_row_controls(True)

    def remove_selected(self):
        if self.running or self.closing:
            return
        self.finish_cell_edit()
        selected = self.table.selection()
        for identifier in selected:
            self.rows.pop(identifier, None)
        if selected:
            self.table.delete(*selected)
            if self.rows:
                self.table.selection_set(next(iter(self.rows)))
        self.select_row()
        self.update_row_count()

    def remove_all(self):
        if self.running or self.closing or not self.rows:
            return
        self.finish_cell_edit()
        self.table.delete(*self.table.get_children())
        self.rows.clear()
        self.select_row()
        self.update_row_count()
        self.save_settings()

    def set_row_controls(self, enabled):
        selected = self.table.selection()
        self.remove_button.configure(state="normal" if enabled and selected else "disabled")
        self.remove_all_button.configure(state='normal' if enabled and self.rows else 'disabled')
        editable = enabled and len(selected) == 1
        for index, widget in enumerate(self.editor_widgets):
            active = editable
            widget.configure(state=("readonly" if index == 0 else "normal") if active else "disabled")

    def start(self):
        if self.running or self.discovery_running or self.closing:
            return
        self.finish_cell_edit()
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
        batch_folder = None
        download_handle = None
        try:
            download_handle = caption_queue.downloader_lock()
            if download_handle is None:
                had_errors = True
                self.events.put(("log", "Another downloader is already running. Wait for it to finish, then start again."))
                return
            images, done = PROJECT_DIR / "images", PROJECT_DIR / "done"
            self.events.put(("log", "Scanning local image folders for existing MD5 filenames…"))
            known = ImageIndex(images, done)
            self.events.put(("log", f"Found {len(known)} existing MD5 images in local image folders."))
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
                nonlocal batch_id, batch_folder, batch_handle
                if batch_id is None:
                    try:
                        batch_id, batch_folder, batch_handle = caption_queue.new_batch("Downloaded images", own_download=True)
                    except (OSError, caption_queue.sqlite3.Error) as error:
                        raise caption_queue.QueueSubmissionError() from error
                return batch_folder

            gap_progress = {}
            track_gap = any(job.target_count is not None for job in jobs)
            for index, job in enumerate(jobs, 1):
                if self.cancel.is_set():
                    break
                self.events.put(("log", f"Row {index}: {job.source} — {job.tags}"))
                try:
                    counts = run_job(job, images, done, known, self.cancel,
                                     lambda count, stats: self.events.put(("status", f"Row {index}: {count} API posts checked; {stats['downloaded']} downloaded")),
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
                if kind == 'character_status':
                    self.discovery_status.set(value)
                    continue
                if kind == 'characters':
                    self.add_character_results(*value)
                    continue
                if kind == 'character_finished':
                    self.discovery_running = False
                    self.discovery_stop.configure(state='disabled')
                    self.discover_button.configure(state='normal')
                    if not self.closing:
                        self.start_button.configure(state='normal')
                    continue
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
        self.finish_cell_edit()
        self.closing = True
        self.add_button.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self.finish_close()

    def finish_close(self):
        self.close_job = None
        if self.running or self.discovery_running:
            if self.running:
                self.stop()
            self.discovery_cancel.set()
            self.close_job = self.root.after(100, self.finish_close)
        else:
            if self.save_settings() is False:
                # Keep the editable state available so a failed save can be
                # retried instead of silently losing the queue on destruction.
                self.closing = False
                self.add_button.configure(state='normal')
                self.start_button.configure(state='normal')
                self.select_row()
                self.status.set('Settings could not be saved. Fix permissions or disk space, then close again.')
                return
            if self.poll_job is not None:
                self.root.after_cancel(self.poll_job)
                self.poll_job = None
            self.root.destroy()


def main():
    from gui import main as caption_main
    caption_main(initial_tool="download")


if __name__ == "__main__":
    main()

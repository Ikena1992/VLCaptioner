"""Launch the standalone image downloader with visible startup errors."""
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from downloader_gui import main
    main()
except Exception:
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror("Image downloader could not start", traceback.format_exc(), parent=root)
    root.destroy()

"""Launch the GUI without a Windows console, with visible startup errors."""
import os
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "data"))

try:
    from gui import main
    main()
except Exception:
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror("VLCaptioner could not start", traceback.format_exc(), parent=root)
    root.destroy()

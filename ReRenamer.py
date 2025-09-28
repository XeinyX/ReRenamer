#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ReRenamer (Tkinter)
- Config constants for colors, fonts, debounce, history, start-on-top, toast duration, uniform type
- History & Favorites
- Non-blocking toast message for mixed file/folder attempts
- Multi-step Undo (rename batches, add items, remove items)
- Regex vs Literal, scope (name/ext/both), OS-specific validation, DnD via tkinterdnd2
- AutoSort option; when off, preserve insertion order and enable drag-and-drop reordering in the table
- Multi-select drag reorder with exact "insert-above-target-row" indicator and autoscroll
"""

import re
import json
import platform
from pathlib import Path
from collections import Counter
from urllib.parse import urlparse, unquote

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from tkinter import font as tkfont

# --- Optional DnD ---
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    DND_AVAILABLE = True
except Exception:
    DND_AVAILABLE = False

# --- Optional natural sort ---
try:
    from natsort import natsorted

    NATSORT_AVAILABLE = True
except Exception:
    NATSORT_AVAILABLE = False


# =========================
# Configuration constants
# =========================

APP_NAME = "ReRenamer"

# Colors (hex, e.g. "#ffaa00")
COLOR_SAME = "#aaaaaa"  # unchanged
COLOR_OK = "#2afabb"  # valid to rename
COLOR_CONFLICT = "#fd4040"  # invalid/conflict

# Fonts: sizes apply even when families are None
UI_FONT_FAMILY = None  # e.g. "Segoe UI", "Helvetica", "Arial"; None = system default
UI_FONT_SIZE = 12
TABLE_FONT_FAMILY = None  # e.g. "Consolas"; None = system default
TABLE_FONT_SIZE = 12

# Debounce for live preview (ms)
DEBOUNCE_MS = 160

# History limit
HISTORY_LIMIT = 15

# Enforce uniform type: either all files or all folders
ENFORCE_UNIFORM_TYPE = True

# Start window on top
START_ON_TOP = True

# Toast (non-blocking info) duration (ms)
TOAST_DURATION_MS = 3000

# Config directory/files
CONFIG_DIR = Path.home() / ".rerename"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
FAV_FILE = CONFIG_DIR / "favorites.json"
HIST_FILE = CONFIG_DIR / "history.json"


# ================
# OS-specific rules
# ================

WIN_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
WIN_INVALID_CHARS_RE = re.compile(r'[\x00-\x1f<>:"/\\|?*]')
LINUX_MAC_INVALID_CHARS_RE = re.compile(r"[\x00/]")  # NUL and slash

# Templates
NUMBERING_RE = re.compile(r"<(#+)(?::(-?\d+))?(?::(-?\d+))?>")
PARENT_RE = re.compile(r"<p(?::(\d+))?>", re.IGNORECASE)
PARENT_RE_OLD = re.compile(r"<parent(?::(\d+))?>", re.IGNORECASE)


def safe_natsort(data, key):
    """Natural sort if `natsort` is available, otherwise fallback to Python sort."""
    if NATSORT_AVAILABLE:
        return natsorted(data, key=key)
    return sorted(data, key=key)


def normalize_path_for_dupe(p: Path) -> str:
    """
    Normalize path for duplicate detection:
    - Case-insensitive on Windows and macOS (default HFS+),
    - Exact match on Linux/other Unix.
    """
    if platform.system() in ("Windows", "Darwin"):
        return str(p).casefold()
    return str(p)


def os_is_case_insensitive() -> bool:
    return platform.system() in ("Windows", "Darwin")


def sanitize_component(name: str, is_dir: bool, for_ext: bool = False) -> str:
    """Sanitize a single path component according to OS rules."""
    sysname = platform.system()
    if sysname == "Windows":
        name = WIN_INVALID_CHARS_RE.sub("_", name)
        name = name.rstrip(" .")
        base = name.split(".")[0].upper()
        if base in WIN_RESERVED or name in (".", ".."):
            name = f"_{name}" if name else "_"
        if not name:
            name = "_"
    else:
        name = LINUX_MAC_INVALID_CHARS_RE.sub("_", name)
        if name in (".", ".."):
            name = f"_{name}"
        if not name:
            name = "_"
    if for_ext:
        name = name.lstrip(".")
        name = name.replace("/", "_")
    return name


def is_valid_component(
    name: str, is_dir: bool, for_ext: bool = False
) -> tuple[bool, str]:
    """Validate a single path component according to OS rules."""
    sysname = platform.system()
    if not name and not for_ext:
        return False, "Empty name"
    if sysname == "Windows":
        if WIN_INVALID_CHARS_RE.search(name):
            return False, 'Contains forbidden characters <>:"/\\|?* or control chars'
        if name.rstrip(" .") != name:
            return False, "Trailing spaces or dots are not allowed on Windows"
        base = name.split(".")[0].upper()
        if base in WIN_RESERVED or name in (".", ".."):
            return False, f"Reserved name on Windows: {name}"
    else:
        if "\x00" in name or "/" in name:
            return False, "Contains NUL or /"
        if name in (".", ".."):
            return False, "Invalid name: . or .."
    return True, ""


def parse_numbering(repl: str):
    """Parse numbering placeholder like <##:start:step>."""
    m = NUMBERING_RE.search(repl)
    if not m:
        return None
    hashes, start, step = m.groups()
    return {
        "hashes": hashes,
        "start": int(start) if start else 1,
        "step": int(step) if step else 1,
    }


def expand_templates(
    repl: str, item_dir: Path, number_cfg: dict | None, current_number: int | None
) -> tuple[str, bool]:
    """
    Expand templates inside replacement text:
      - <p:n> (and legacy <parent:n>) for parent directory name
      - <##:start:step> numbering placeholders (use current_number)
    Returns (expanded_text, numbering_used_bool).
    """

    def parent_repl(m: re.Match) -> str:
        n_str = m.group(1)
        n = int(n_str) if n_str else 1
        if n <= 0:
            return ""
        if n == 1:
            return item_dir.name
        idx = n - 2
        parents = item_dir.parents
        if idx >= len(parents):
            return ""
        return parents[idx].name

    out = PARENT_RE.sub(parent_repl, repl)
    out = PARENT_RE_OLD.sub(parent_repl, out)

    used_numbering = False
    if number_cfg is not None and current_number is not None:
        width = len(number_cfg["hashes"])
        fmt = f"{{:0{width}d}}"
        out = NUMBERING_RE.sub(lambda _: fmt.format(current_number), out)
        used_numbering = True

    return out, used_numbering


class ReRenamerApp(TkinterDnD.Tk if DND_AVAILABLE else tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} (Tkinter)")
        self.geometry("1120x760+120+80")
        self.minsize(800, 500)

        # Data
        self.table_data: list[dict] = []
        self.duple = set()
        self.collection_type: str | None = None  # 'file' or 'dir'
        self._uid_seq = 1  # stable IDs for rows (for DnD reordering)

        # Multi-step undo stack
        self.undo_stack: list[dict] = []

        # UI state
        self.case_sensitive = tk.BooleanVar(value=True)
        self.regex_on = tk.BooleanVar(value=True)
        self.autosort = tk.BooleanVar(value=True)  # AutoSort toggle
        self.scope_var = tk.StringVar(value="name")  # 'name' | 'ext' | 'both'

        self.find_text = tk.StringVar()
        self.repl_text = tk.StringVar()

        # Debounce
        self._after_id = None

        # Favorites / History
        self.favorites: list[dict] = self._load_json(FAV_FILE, default=[])
        self.history: list[dict] = self._load_json(HIST_FILE, default=[])

        # Toast holder
        self._toast_label: tk.Label | None = None

        # Auto-scroll timer during drag
        self._auto_scroll_job = None

        # Drag-reorder state
        self._drag_selection: list[str] | None = None
        self._press_row: str | None = None
        self._hover_target = None

        # Init UI
        self._build_ui()
        self._apply_fonts()
        self._bind_events()

        if not DND_AVAILABLE:
            messagebox.showinfo(
                "Info",
                "Drag & drop is disabled (missing 'tkinterdnd2').\n\nInstall: pip install tkinterdnd2",
            )

        # Bring to front on start
        if START_ON_TOP:
            self.after(80, self._bring_to_front)

    # -------------
    # UI Building
    # -------------
    def _build_ui(self):
        root = self

        # Top area: left (fields + buttons), right (Options + Scope)
        top = ttk.Frame(root, padding=(20, 8))
        top.pack(side=tk.TOP, fill=tk.X)
        top.grid_columnconfigure(0, weight=1)
        top.grid_columnconfigure(1, weight=0)

        # LEFT
        left = ttk.Frame(top)
        left.grid(row=0, column=0, sticky="nsew")
        left.grid_columnconfigure(1, weight=1)

        # Row 0: Find
        ttk.Label(left, text="Find:").grid(
            row=0, column=0, sticky="w", padx=(0, 6), pady=(15, 3)
        )
        self.find_entry = ttk.Entry(left, textvariable=self.find_text)
        self.find_entry.grid(row=0, column=1, columnspan=2, sticky="ew", pady=(15, 3))
        # Counters block (top-right)
        counters = ttk.Frame(left)
        counters.grid(row=2, column=2, sticky="e", padx=(8, 0))
        self.lbl_same = ttk.Label(counters, text="0", foreground=COLOR_SAME)
        self.lbl_ok = ttk.Label(counters, text=" / 0", foreground=COLOR_OK)
        self.lbl_conflict = ttk.Label(counters, text=" / 0", foreground=COLOR_CONFLICT)
        self.lbl_same.pack(side=tk.LEFT)
        self.lbl_ok.pack(side=tk.LEFT)
        self.lbl_conflict.pack(side=tk.LEFT)

        # Row 1: Replace
        ttk.Label(left, text="Replace:").grid(
            row=1, column=0, sticky="w", padx=(0, 6), pady=3
        )
        self.repl_entry = ttk.Entry(left, textvariable=self.repl_text)
        self.repl_entry.grid(row=1, column=1, columnspan=2, sticky="ew", pady=3)

        # RIGHT: Options and Scope
        right = ttk.Frame(top)
        right.grid(row=0, column=1, sticky="ne", padx=(12, 0))
        opt_group = ttk.Labelframe(right, text="Options", padding=6)
        opt_group.pack(side=tk.LEFT, fill=tk.Y, anchor="w")
        ttk.Checkbutton(
            opt_group,
            text="Case Sensitive",
            variable=self.case_sensitive,
            command=self.handle_input,
        ).pack(anchor="w")
        ttk.Checkbutton(
            opt_group, text="Regex", variable=self.regex_on, command=self.handle_input
        ).pack(anchor="w", pady=(2, 2))
        ttk.Checkbutton(
            opt_group,
            text="AutoSort",
            variable=self.autosort,
            command=self.on_autosort_toggle,
        ).pack(anchor="w")

        scope_group = ttk.Labelframe(right, text="Scope", padding=6)
        scope_group.pack(side=tk.LEFT, fill=tk.Y, anchor="w", padx=(10, 0))
        ttk.Radiobutton(
            scope_group,
            text="Name only",
            value="name",
            variable=self.scope_var,
            command=self.handle_input,
        ).pack(anchor="w")
        ttk.Radiobutton(
            scope_group,
            text="Extension only",
            value="ext",
            variable=self.scope_var,
            command=self.handle_input,
        ).pack(anchor="w")
        ttk.Radiobutton(
            scope_group,
            text="Name + Extension",
            value="both",
            variable=self.scope_var,
            command=self.handle_input,
        ).pack(anchor="w")

        # Row 2: Context label
        self.context_label = ttk.Label(
            left, text="Add files or folders", foreground="#999999"
        )
        self.context_label.grid(row=2, column=1, columnspan=2, sticky="w", pady=(2, 0))

        # Row 3: buttons

        # Buttons Top
        # Row: buttons (left group + right group)
        buttons = ttk.Frame(root, padding=(20, 8))
        buttons.pack(side=tk.TOP, fill=tk.X)
        buttons.grid_columnconfigure(0, weight=1)
        buttons.grid_columnconfigure(1, weight=0)

        row = ttk.Frame(buttons)
        row.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        row.grid_columnconfigure(0, weight=1)
        row.grid_columnconfigure(1, weight=1)
        left_btns = ttk.Frame(row)
        left_btns.grid(row=0, column=0, sticky="w")
        ttk.Button(left_btns, text="Add Files…", command=self.add_files_dialog).pack(
            side=tk.LEFT
        )
        ttk.Button(left_btns, text="Add Folders…", command=self.add_dirs_dialog).pack(
            side=tk.LEFT, padx=(6, 0)
        )
        ttk.Label(
            left_btns, text="[ or use drag and drop ]", foreground="#999999"
        ).pack(side=tk.LEFT, padx=(6, 0))
        right_btns = ttk.Frame(row)
        right_btns.grid(row=0, column=1, sticky="e")
        ttk.Button(right_btns, text="Save Favorite", command=self.save_favorite).pack(
            side=tk.LEFT
        )
        ttk.Button(right_btns, text="Favorites…", command=self.favorites_dialog).pack(
            side=tk.LEFT, padx=(6, 0)
        )
        ttk.Button(right_btns, text="History…", command=self.history_dialog).pack(
            side=tk.LEFT, padx=(6, 0)
        )
        # Table
        table_frame = ttk.Frame(root, padding=(20, 8))
        table_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        columns = ("old", "new")
        self.tree = ttk.Treeview(
            table_frame, columns=columns, show="headings", selectmode="extended"
        )  # extended = multi-select
        self.tree.heading("old", text="Original Name")
        self.tree.heading("new", text="Transformed Name")
        self.tree.column("old", anchor="w", width=520)
        self.tree.column("new", anchor="w", width=520)

        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        # Row tag colors
        self.tree.tag_configure("same", foreground=COLOR_SAME)
        self.tree.tag_configure("ok", foreground=COLOR_OK)
        self.tree.tag_configure("conflict", foreground=COLOR_CONFLICT)

        # Insertion indicator line (hidden by default) — child of tree (coordinates relative to tree)
        self._ins_line = tk.Frame(self.tree, height=2, bg="#1e90ff")
        self._ins_line.place_forget()

        # Bottom buttons
        bottom = ttk.Frame(root, padding=(20, 8))
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Button(bottom, text="Remove Selected", command=self.remove_selected).pack(
            side=tk.LEFT
        )
        ttk.Button(bottom, text="Clear Table", command=self.clear_table).pack(
            side=tk.LEFT, padx=(6, 0)
        )
        ttk.Button(bottom, text="Undo", command=self.undo).pack(side=tk.RIGHT)
        ttk.Button(bottom, text="Apply Rules", command=self.apply_rules).pack(
            side=tk.RIGHT, padx=(0, 6)
        )

        # DnD
        if DND_AVAILABLE:
            self.drop_target_register(DND_FILES)
            self.dnd_bind("<<Drop>>", self.on_drop)

    def _apply_fonts(self):
        """Apply UI and Table fonts according to constants."""
        style = ttk.Style(self)

        # Update Tk default UI fonts size/family
        base_ui = tkfont.nametofont("TkDefaultFont")
        ui_family = UI_FONT_FAMILY or base_ui.cget("family")
        ui_size = UI_FONT_SIZE or base_ui.cget("size")
        base_ui.configure(family=ui_family, size=ui_size)
        style.configure(".", font=(ui_family, ui_size))
        style.configure("Treeview.Heading", font=(ui_family, ui_size))

        # Table rows font
        base_tbl = tkfont.nametofont("TkDefaultFont")
        tbl_family = TABLE_FONT_FAMILY or base_tbl.cget("family")
        tbl_size = TABLE_FONT_SIZE or base_tbl.cget("size")
        style.configure("Treeview", font=(tbl_family, tbl_size))

    def _bind_events(self):
        # Live preview with debounce
        self.find_entry.bind("<KeyRelease>", self._schedule_handle_input)
        self.repl_entry.bind("<KeyRelease>", self._schedule_handle_input)
        # Enter = immediate recompute
        self.find_entry.bind("<Return>", lambda e: self.handle_input())
        self.repl_entry.bind("<Return>", lambda e: self.handle_input())
        # Delete = remove selection
        self.tree.bind("<Delete>", lambda e: self.remove_selected())

        # Drag reorder handlers (active only when AutoSort is off)
        self.tree.bind("<ButtonPress-1>", self._on_tree_press)
        self.tree.bind("<B1-Motion>", self._on_tree_drag)
        self.tree.bind("<ButtonRelease-1>", self._on_tree_release)

        # Optional: hide indicator when pointer leaves the tree area
        self.tree.bind("<Leave>", lambda e: self._hide_insert_indicator())

    def _bring_to_front(self):
        try:
            self.lift()
            self.attributes("-topmost", True)
            self.after(200, lambda: self.attributes("-topmost", False))
            self.focus_force()
        except Exception:
            pass

    # -------------------------
    # Favorites / History I/O
    # -------------------------
    @staticmethod
    def _load_json(path: Path, default):
        try:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
        return default

    @staticmethod
    def _save_json(path: Path, data):
        try:
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            messagebox.showerror("Save error", f"Cannot save {path}:\n{e}")

    def current_preset(self) -> dict:
        return {
            "find": self.find_text.get(),
            "replace": self.repl_text.get(),
            "case": bool(self.case_sensitive.get()),
            "regex": bool(self.regex_on.get()),
            "scope": self.scope_var.get(),
        }

    def apply_preset(self, p: dict):
        self.find_text.set(p.get("find", ""))
        self.repl_text.set(p.get("replace", ""))
        self.case_sensitive.set(bool(p.get("case", True)))
        self.regex_on.set(bool(p.get("regex", True)))
        self.scope_var.set(p.get("scope", "name"))
        self.handle_input()

    def save_favorite(self):
        p = self.current_preset()
        key = (p["find"], p["replace"], p["case"], p["regex"], p["scope"])
        # newest-first (insert at front), de-dup
        self.favorites = [
            x
            for x in self.favorites
            if (
                x.get("find"),
                x.get("replace"),
                x.get("case"),
                x.get("regex"),
                x.get("scope"),
            )
            != key
        ]
        self.favorites.insert(0, p)
        self._save_json(FAV_FILE, self.favorites)
        self._toast("Favorite saved.")

    def favorites_dialog(self):
        top = tk.Toplevel(self)
        top.title("Favorites")
        top.geometry("940x380+200+120")

        cols = ("find", "replace")
        tv = ttk.Treeview(top, columns=cols, show="headings", selectmode="browse")
        for c in cols:
            tv.heading(c, text=c.capitalize())
        tv.column("find", width=290, anchor="w")
        tv.column("replace", width=290, anchor="w")

        vsb = ttk.Scrollbar(top, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=vsb.set)
        tv.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0), pady=8)
        vsb.pack(side=tk.LEFT, fill=tk.Y, pady=8)

        def refresh():
            tv.delete(*tv.get_children())
            for i, p in enumerate(self.favorites):
                tv.insert(
                    "",
                    tk.END,
                    iid=str(i),
                    values=(p.get("find", ""), p.get("replace", "")),
                )

        def do_load():
            sel = tv.selection()
            if not sel:
                return
            idx = int(sel[0])
            self.apply_preset(self.favorites[idx])
            top.destroy()

        def do_delete():
            sel = tv.selection()
            if not sel:
                return
            idx = int(sel[0])
            del self.favorites[idx]
            self._save_json(FAV_FILE, self.favorites)
            refresh()

        tv.bind("<Double-1>", lambda e: do_load())
        tv.bind("<Return>", lambda e: do_load())

        btns = ttk.Frame(top)
        btns.pack(side=tk.RIGHT, fill=tk.Y, padx=8, pady=8)
        ttk.Button(btns, text="Load", command=do_load).pack(fill=tk.X)
        ttk.Button(btns, text="Delete", command=do_delete).pack(fill=tk.X, pady=(6, 0))
        ttk.Button(btns, text="Close", command=top.destroy).pack(
            fill=tk.X, pady=(20, 0)
        )

        refresh()

    def history_dialog(self):
        top = tk.Toplevel(self)
        top.title("History")
        top.geometry("940x380+210+140")

        cols = ("find", "replace")
        tv = ttk.Treeview(top, columns=cols, show="headings", selectmode="browse")
        for c in cols:
            tv.heading(c, text=c.capitalize())
        tv.column("find", width=290, anchor="w")
        tv.column("replace", width=290, anchor="w")

        vsb = ttk.Scrollbar(top, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=vsb.set)
        tv.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0), pady=8)
        vsb.pack(side=tk.LEFT, fill=tk.Y, pady=8)

        def refresh():
            tv.delete(*tv.get_children())
            for i, p in enumerate(reversed(self.history[-HISTORY_LIMIT:])):
                tv.insert(
                    "",
                    tk.END,
                    iid=str(i),
                    values=(p.get("find", ""), p.get("replace", "")),
                )

        def do_load():
            sel = tv.selection()
            if not sel:
                return
            idx = int(sel[0])
            slice_list = list(reversed(self.history[-HISTORY_LIMIT:]))
            self.apply_preset(slice_list[idx])
            top.destroy()

        tv.bind("<Double-1>", lambda e: do_load())
        tv.bind("<Return>", lambda e: do_load())

        btns = ttk.Frame(top)
        btns.pack(side=tk.RIGHT, fill=tk.Y, padx=8, pady=8)
        ttk.Button(btns, text="Load", command=do_load).pack(fill=tk.X)
        ttk.Button(btns, text="Close", command=top.destroy).pack(
            fill=tk.X, pady=(20, 0)
        )

        refresh()

    def add_to_history(self):
        p = self.current_preset()
        key = (p["find"], p["replace"], p["case"], p["regex"], p["scope"])
        self.history = [
            h
            for h in self.history
            if (
                h.get("find"),
                h.get("replace"),
                h.get("case"),
                h.get("regex"),
                h.get("scope"),
            )
            != key
        ]
        self.history.append(p)
        if len(self.history) > HISTORY_LIMIT:
            self.history = self.history[-HISTORY_LIMIT:]
        self._save_json(HIST_FILE, self.history)

    # -----------------------
    # DnD and File selection
    # -----------------------
    @staticmethod
    def _split_dnd_paths(data):
        out = []
        token = []
        in_brace = False
        for ch in data:
            if ch == "{":
                in_brace = True
                token = []
                continue
            if ch == "}":
                in_brace = False
                out.append("".join(token))
                token = []
                continue
            if ch == " " and not in_brace:
                if token:
                    out.append("".join(token))
                    token = []
                continue
            token.append(ch)
        if token:
            out.append("".join(token))
        cleaned = []
        for p in out:
            if p.startswith("file://"):
                u = urlparse(p)
                path = unquote(u.path)
                if platform.system() == "Windows" and path.startswith("/"):
                    path = path[1:]
                cleaned.append(path)
            else:
                cleaned.append(p)
        return cleaned

    def on_drop(self, event):
        paths = self._split_dnd_paths(event.data)
        self._add_paths(paths)

    def add_files_dialog(self):
        files = filedialog.askopenfilenames(title="Select files to add")
        if files:
            self._add_paths(files)

    def add_dirs_dialog(self):
        directory = filedialog.askdirectory(title="Select folder to add")
        if directory:
            self._add_paths([directory])

    # ----------------
    # Toast (non-block)
    # ----------------
    def _toast(self, text: str):
        """Show a transient toast at the bottom center."""
        try:
            if self._toast_label and self._toast_label.winfo_exists():
                self._toast_label.destroy()
        except Exception:
            pass
        lbl = tk.Label(
            self,
            text=text,
            bg="#333333",
            fg=COLOR_OK,
            bd=1,
            relief="solid",
            padx=10,
            pady=6,
        )
        self._toast_label = lbl
        self.update_idletasks()
        lbl.update_idletasks()
        w = lbl.winfo_reqwidth()
        x = (self.winfo_width() - w) // 2
        lbl.place(x=max(10, x), rely=1.0, anchor="s", y=-12)
        self.after(
            TOAST_DURATION_MS, lambda: lbl.destroy() if lbl.winfo_exists() else None
        )

    # -----------------
    # Data manipulation
    # -----------------
    def _update_context_label(self):
        if not self.table_data:
            text = "Add files or folders"
        else:
            text = (
                "Renaming files..."
                if self.collection_type == "file"
                else "Renaming folders..."
            )
        self.context_label.configure(text=text)

    def _add_paths(self, paths):
        """
        Add files/folders to the table.
        - Enforces uniform type if enabled.
        - Skips duplicates (case-insensitive on Windows/macOS).
        """
        # Build normalized set of existing full paths for duplicate check
        existing_norm = {
            normalize_path_for_dupe(self._file_path(item, "old_name"))
            for item in self.table_data
        }

        skipped_type = 0
        added_items = []

        for raw in paths:
            try:
                p = Path(raw)
            except Exception:
                continue
            if not (p.is_file() or p.is_dir()):
                continue

            new_type = "file" if p.is_file() else "dir"
            if ENFORCE_UNIFORM_TYPE:
                if self.collection_type is None:
                    self.collection_type = new_type
                elif new_type != self.collection_type:
                    skipped_type += 1
                    continue

            # Skip duplicates (normalized)
            if normalize_path_for_dupe(p) in existing_norm:
                continue

            parent = p.parent
            if p.is_file():
                stem, suffix = p.stem, p.suffix
            else:
                stem, suffix = p.name, ""

            item = {
                "uid": self._uid_seq,  # stable ID for DnD
                "index": 0,
                "type": new_type,
                "path": str(parent),
                "old_name": stem,
                "new_name": stem,
                "extension": suffix,
                "status": 0,
            }
            self._uid_seq += 1
            self.table_data.append(item)
            added_items.append(item)
            existing_norm.add(
                normalize_path_for_dupe(self._file_path(item, "old_name"))
            )

        if skipped_type:
            self._toast(
                f"Skipped {skipped_type} item(s): cannot mix files and folders."
            )

        if added_items:
            self.undo_stack.append(
                {"type": "add_items", "items": [dict(x) for x in added_items]}
            )
            self.update_table()

        self._update_context_label()

    def clear_table(self):
        if self.table_data:
            removed_copy = [dict(x) for x in self.table_data]
            self.undo_stack.append({"type": "remove_items", "items": removed_copy})
        self.table_data = []
        self.collection_type = None
        self.duple.clear()
        self._refresh_tree()
        self._update_counters([0, 0, 0])
        self._update_context_label()

    def remove_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        uids_to_remove = {int(iid) for iid in sel}
        removed_items = [
            dict(item) for item in self.table_data if item["uid"] in uids_to_remove
        ]
        if not removed_items:
            return
        self.undo_stack.append({"type": "remove_items", "items": removed_items})
        self.table_data = [
            item for item in self.table_data if item["uid"] not in uids_to_remove
        ]
        if not self.table_data:
            self.collection_type = None
        self.update_table()
        self._update_context_label()

    # -----------------------
    # Preview & status update
    # -----------------------
    def _schedule_handle_input(self, *_):
        if self._after_id:
            self.after_cancel(self._after_id)
        self._after_id = self.after(DEBOUNCE_MS, self.handle_input)

    def on_autosort_toggle(self):
        # If turning AutoSort off, preserve current visual order
        if not self.autosort.get():
            order_iids = list(self.tree.get_children())
            uid_to_item = {str(it["uid"]): it for it in self.table_data}
            self.table_data = [
                uid_to_item[iid] for iid in order_iids if iid in uid_to_item
            ]
        self.update_table()

    def update_table(self):
        if self.table_data:
            if self.autosort.get():
                self.table_data = safe_natsort(
                    self.table_data, key=lambda x: x["old_name"] + x["extension"]
                )
            for i, item in enumerate(self.table_data):
                item["index"] = i
        self.handle_input()

    def handle_input(self):
        self._after_id = None

        find_text = self.find_text.get()
        repl_text = self.repl_text.get()
        flags = 0 if self.case_sensitive.get() else re.IGNORECASE
        regex_on = bool(self.regex_on.get())
        pattern = (
            find_text
            if (find_text and regex_on)
            else (re.escape(find_text) if find_text else "")
        )

        number_cfg = parse_numbering(repl_text)
        number_current = number_cfg["start"] if number_cfg else None

        for item in self.table_data:
            typ = item["type"]
            old_name = item["old_name"]
            ext = item["extension"]

            ext_base = ext[1:] if ext.startswith(".") else ext
            scope = self.scope_var.get()
            if scope == "ext":
                subject = ext_base
            elif scope == "both" and typ == "file":
                subject = old_name + ext
            else:
                subject = old_name

            if not pattern:
                new_subject = subject
                replaced = 0
            else:
                item_dir = Path(item["path"])
                expanded_repl, _ = expand_templates(
                    repl_text, item_dir, number_cfg, number_current
                )
                try:
                    new_subject, replaced = re.subn(
                        pattern, expanded_repl, subject, flags=flags
                    )
                except re.error:
                    new_subject, replaced = subject, 0
                if number_cfg and replaced > 0:
                    number_current += number_cfg["step"]

            # Split & sanitize back
            if scope == "ext":
                new_ext_base = sanitize_component(
                    new_subject, is_dir=False, for_ext=True
                )
                new_name = old_name
                new_ext = ("." + new_ext_base) if new_ext_base else ""
            elif scope == "both" and typ == "file":
                base = new_subject
                if base.startswith("."):
                    stem = base
                    suff = ""
                else:
                    if "." in base:
                        stem, suff = base.rsplit(".", 1)
                        suff = "." + suff if suff else ""
                    else:
                        stem, suff = base, ""
                new_name = sanitize_component(stem, is_dir=False, for_ext=False)
                new_ext = sanitize_component(suff, is_dir=False, for_ext=False)
                if new_ext and not new_ext.startswith("."):
                    new_ext = "." + new_ext
            else:
                new_name = sanitize_component(
                    new_subject, is_dir=(typ == "dir"), for_ext=False
                )
                new_ext = ext

            item["new_name"] = new_name or old_name
            item["new_ext"] = new_ext  # preview ext for status/display

        # Batch duplicates
        targets = [self._target_path(xx) for xx in self.table_data]
        norm_list = [
            normalize_path_for_dupe(p) if os_is_case_insensitive() else str(p)
            for p in targets
        ]
        cnt = Counter(norm_list)
        self.duple = {k for k, v in cnt.items() if v > 1}

        # Status & counters
        counts = [0, 0, 0]
        for item in self.table_data:
            a = self._check_status(item)
            item["status"] = a
            counts[a] += 1

        self._update_counters(counts)
        self._refresh_tree()
        self._update_context_label()

    def _target_path(self, item: dict) -> Path:
        ext = item.get("new_ext", item["extension"])
        return Path(item["path"]) / (item["new_name"] + ext)

    def _file_path(self, item, name_key) -> Path:
        return Path(item["path"]) / (item[name_key] + item["extension"])

    def _depth(self, item):
        return len(self._file_path(item, "old_name").parts)

    def _check_status(self, item) -> int:
        """
        Return 0 (same), 1 (ok/changed), 2 (conflict).
        Conflict reasons: invalid component, in-batch duplicate, or target exists on disk.
        """
        old = self._file_path(item, "old_name")
        new = self._target_path(item)
        typ = item["type"]

        ok_name, _ = is_valid_component(item["new_name"], is_dir=(typ == "dir"))
        if not ok_name:
            return 2
        if typ == "file":
            ext_base = item.get("new_ext", item["extension"])
            ext_base = ext_base[1:] if ext_base.startswith(".") else ext_base
            if ext_base:
                ok_ext, _ = is_valid_component(ext_base, is_dir=False, for_ext=True)
                if not ok_ext:
                    return 2

        same = (
            normalize_path_for_dupe(old) == normalize_path_for_dupe(new)
            if os_is_case_insensitive()
            else (old == new)
        )

        # Duplicate in-batch
        if os_is_case_insensitive():
            if normalize_path_for_dupe(new) in self.duple and not same:
                return 2
        else:
            if str(new) in self.duple and not same:
                return 2

        # Existing on disk
        if not same and new.exists():
            return 2

        return 0 if same else 1

    # -------------
    # Apply & Undo
    # -------------
    def apply_rules(self):
        if any(i["status"] == 2 for i in self.table_data):
            messagebox.showwarning(
                "Conflicts", "There are conflicts. Adjust rules so no rows are red."
            )
            return

        renamed_ops = []
        affected_entries = []

        for item in sorted(self.table_data, key=self._depth, reverse=True):
            old = self._file_path(item, "old_name")
            new = self._target_path(item)
            same = (
                normalize_path_for_dupe(old) == normalize_path_for_dupe(new)
                if os_is_case_insensitive()
                else (old == new)
            )
            if same:
                item["extension"] = item.get("new_ext", item["extension"])
                continue
            try:
                new.parent.mkdir(parents=True, exist_ok=True)
                old.rename(new)
                renamed_ops.append((old, new))
                affected_entries.append((item, item["old_name"], item["extension"]))
                item["old_name"] = item["new_name"]
                item["extension"] = item.get("new_ext", item["extension"])
            except Exception as e:
                messagebox.showerror("Rename error", f"Failed: {old} → {new}\n{e}")

        if renamed_ops:
            self.undo_stack.append(
                {
                    "type": "rename_batch",
                    "ops": renamed_ops,
                    "affected": affected_entries,
                }
            )

        self.add_to_history()
        self.update_table()

    def undo(self):
        if not self.undo_stack:
            messagebox.showinfo("Undo", "Nothing to undo.")
            return

        action = self.undo_stack.pop()

        if action["type"] == "add_items":
            ids_to_remove = {x["uid"] for x in action["items"]}
            self.table_data = [
                it for it in self.table_data if it["uid"] not in ids_to_remove
            ]
            if not self.table_data:
                self.collection_type = None
            self.update_table()
            self._toast("Undid: add items")

        elif action["type"] == "remove_items":
            self.table_data.extend(action["items"])
            if self.collection_type is None and self.table_data:
                self.collection_type = self.table_data[0]["type"]
            self.update_table()
            self._toast("Undid: remove items")

        elif action["type"] == "rename_batch":
            errors = []
            for old, new in reversed(action["ops"]):
                try:
                    if new.exists():
                        new.rename(old)
                except Exception as e:
                    errors.append(f"{new} → {old}: {e}")
            for item, old_name, extension in action["affected"]:
                item["old_name"] = old_name
                item["extension"] = extension
            if errors:
                messagebox.showerror(
                    "Undo errors",
                    "Some items failed to revert:\n\n"
                    + "\n".join(errors[:10])
                    + (
                        f"\n... and more ({len(errors)-10})" if len(errors) > 10 else ""
                    ),
                )
            self.update_table()
            self._toast("Undid: rename batch")

        else:
            self._toast("Unknown undo action type")

    # ----------------
    # Treeview refresh
    # ----------------
    def _refresh_tree(self):
        """Full repaint of the tree while preserving selection."""
        selected = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        for item in self.table_data:
            disp_ext = item.get("new_ext", item["extension"])
            old_full = item["old_name"] + item["extension"]
            new_full = item["new_name"] + disp_ext
            tag = (
                "same"
                if item["status"] == 0
                else ("ok" if item["status"] == 1 else "conflict")
            )
            iid = str(item["uid"])
            self.tree.insert(
                "", tk.END, iid=iid, values=(old_full, new_full), tags=(tag,)
            )
        # Re-apply selection (by uid)
        keep = [str(it["uid"]) for it in self.table_data if str(it["uid"]) in selected]
        if keep:
            self.tree.selection_set(keep)

    def _update_counters(self, counts):
        same, ok, conflict = counts
        self.lbl_same.configure(text=str(same))
        self.lbl_ok.configure(text=f" / {ok}")
        self.lbl_conflict.configure(text=f" / {conflict}")

    # -------------------------
    # Tree drag-and-drop reorder (multi-select + exact insertion indicator)
    # -------------------------
    def _on_tree_press(self, event):
        if self.autosort.get():
            self._drag_selection = None
            self._press_row = None
            return

        # Ignore presses in the heading/separator
        region = self.tree.identify_region(event.x, event.y)
        if region in ("heading", "separator"):
            self._drag_selection = None
            self._press_row = None
            self._hide_insert_indicator()
            return

        row = self.tree.identify_row(event.y)
        self._press_row = row

        if not row:
            self._drag_selection = None
            self._hide_insert_indicator()
            return

        current_sel = tuple(self.tree.selection())

        if row in current_sel:
            # Keep multi-selection intact; prevent Tk from collapsing selection.
            order = list(self.tree.get_children())
            self._drag_selection = [iid for iid in order if iid in current_sel]
            # Show initial indicator
            self._show_insert_indicator(event, self._drag_selection)
            self._start_autoscroll()
            return "break"
        else:
            # Let Tk update selection normally; we will capture it on first motion.
            self._drag_selection = None
            self._hide_insert_indicator()
            self._start_autoscroll()
            return

    def _on_tree_drag(self, event):
        if self.autosort.get():
            return

        # Ignore motions in the heading/separator
        region = self.tree.identify_region(event.x, event.y)
        if region in ("heading", "separator"):
            self._hide_insert_indicator()
            return

        # Capture selection if not yet captured (click outside prior selection)
        if self._drag_selection is None:
            order = list(self.tree.get_children())
            selset = set(self.tree.selection())
            if not selset:
                return
            self._drag_selection = [iid for iid in order if iid in selset]

        # Update indicator only if row under cursor changed
        target = self.tree.identify_row(event.y)
        if target == self._hover_target and not (
            target is None and self._hover_target is None
        ):
            return
        self._hover_target = target

        # Live indicator — always "above the row under cursor"
        self._show_insert_indicator(event, self._drag_selection)

    def _on_tree_release(self, event):
        # Always stop autoscroll and hide indicator
        self._stop_autoscroll()
        self._hide_insert_indicator()

        if self.autosort.get() or not self._drag_selection:
            self._drag_selection = None
            self._press_row = None
            return

        sel = list(self._drag_selection)
        self._drag_selection = None
        self._press_row = None

        children = list(self.tree.get_children())
        if not children:
            return

        others = [iid for iid in children if iid not in sel]

        # Always insert BEFORE the row under cursor
        region = self.tree.identify_region(event.x, event.y)
        target = (
            self.tree.identify_row(event.y)
            if region not in ("heading", "separator")
            else ""
        )

        if target:
            # Dest index = count of "others" with index less than target index among all children
            tgt_idx_all = children.index(target)
            dest_index = sum(1 for iid in others if children.index(iid) < tgt_idx_all)
        else:
            # Not over a row -> decide top/bottom by visible rows
            first_vis = None
            last_vis = None
            for iid in children:
                bbox = self.tree.bbox(iid)
                if bbox:
                    if first_vis is None:
                        first_vis = iid
                    last_vis = iid
            if not first_vis or not last_vis:
                return

            bbox_first = self.tree.bbox(first_vis)
            bbox_last = self.tree.bbox(last_vis)
            top_first = bbox_first[1]
            bottom_last = bbox_last[1] + bbox_last[3]

            if event.y <= top_first:
                # before first visible
                tgt_idx_all = children.index(first_vis)
                dest_index = sum(
                    1 for iid in others if children.index(iid) < tgt_idx_all
                )
            elif event.y >= bottom_last:
                # after last visible
                dest_index = len(others)
            else:
                # fallback: end
                dest_index = len(others)

        # New order: others[:dest] + sel + others[dest:]
        new_order = others[:dest_index] + sel + others[dest_index:]

        # Apply order to Treeview
        for idx, iid in enumerate(new_order):
            self.tree.move(iid, "", idx)
        self.tree.selection_set(sel)  # preserve selection

        # Sync order to model and recompute preview
        uid_to_item = {str(it["uid"]): it for it in self.table_data}
        self.table_data = [uid_to_item[iid] for iid in new_order if iid in uid_to_item]
        for i, item in enumerate(self.table_data):
            item["index"] = i

        self.handle_input()

    def _place_indicator_at_tree_y(self, y_rel_to_tree: int):
        """Place the insertion line at a vertical position relative to the Treeview widget."""
        try:
            self._ins_line.place(x=0, y=y_rel_to_tree, relwidth=1, height=2)
            self._ins_line.lift()
        except Exception:
            pass

    def _hide_insert_indicator(self):
        if getattr(self, "_ins_line", None):
            self._ins_line.place_forget()

    def _show_insert_indicator(self, event, sel_iids):
        """
        Draw a horizontal insertion line exactly above the row under cursor.
        If cursor is not over a row: show above first visible row or below last visible row.
        If over the heading, hide the indicator.
        """
        # Hide if over heading/separator (when event has x,y)
        if hasattr(event, "x") and hasattr(event, "y"):
            region = self.tree.identify_region(event.x, event.y)
            if region in ("heading", "separator"):
                self._hide_insert_indicator()
                return

        children = list(self.tree.get_children())
        if not children:
            self._hide_insert_indicator()
            return

        # Row under the cursor (Y is relative to Treeview)
        target = self.tree.identify_row(event.y)

        if target:
            bbox = self.tree.bbox(target)
            if not bbox:
                self._hide_insert_indicator()
                return
            y_line = bbox[1]  # top edge of target row
            self._place_indicator_at_tree_y(y_line)
            return

        # Not over any row -> decide top/bottom by visible rows
        first_vis = None
        last_vis = None
        for iid in children:
            bbox = self.tree.bbox(iid)
            if bbox:
                if first_vis is None:
                    first_vis = iid
                last_vis = iid
        if not first_vis or not last_vis:
            self._hide_insert_indicator()
            return

        bbox_first = self.tree.bbox(first_vis)
        bbox_last = self.tree.bbox(last_vis)
        top_first = bbox_first[1]
        bottom_last = bbox_last[1] + bbox_last[3]

        if event.y <= top_first:
            self._place_indicator_at_tree_y(top_first)
        elif event.y >= bottom_last:
            self._place_indicator_at_tree_y(bottom_last)
        else:
            # In the middle of a gap — hide to avoid misleading visuals
            self._hide_insert_indicator()

    def _start_autoscroll(self):
        if self._auto_scroll_job:
            return
        self._auto_scroll_job = self.after(30, self._autoscroll_tick)

    def _stop_autoscroll(self):
        if self._auto_scroll_job:
            try:
                self.after_cancel(self._auto_scroll_job)
            except Exception:
                pass
            self._auto_scroll_job = None

    def _autoscroll_tick(self):
        """Scroll when dragging near top/bottom edges and update the indicator accordingly."""
        if self.autosort.get() or not self._drag_selection:
            self._auto_scroll_job = None
            return
        try:
            y_rel = self.tree.winfo_pointery() - self.tree.winfo_rooty()
            height = self.tree.winfo_height()
        except Exception:
            self._auto_scroll_job = None
            return

        border = 18
        scrolled = False
        if y_rel < border:
            self.tree.yview_scroll(-1, "units")
            scrolled = True
        elif y_rel > height - border:
            self.tree.yview_scroll(1, "units")
            scrolled = True

        if scrolled:
            # After scrolling, recompute indicator using current pointer Y
            fake_event = type("E", (), {"y": y_rel})()
            self._show_insert_indicator(fake_event, self._drag_selection or [])

        self._auto_scroll_job = self.after(30, self._autoscroll_tick)


# ----------------
# Main loop
# ----------------


def main():
    app = ReRenamerApp()
    app.mainloop()


if __name__ == "__main__":
    main()

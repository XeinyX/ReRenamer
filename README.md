
# ReRenamer (Tkinter)

ReRenamer is a simple, cross‑platform desktop app for batch renaming files and folders. It supports classic Find/Replace, regular expressions, numbering templates, and parent-folder placeholders. You can keep items AutoSorted or manually reorder them via drag‑and‑drop to create precise sequences.

- GUI: Tkinter (Python standard library)
- Cross‑platform name validation for Windows/macOS/Linux
- Favorites and History for quick reuse
- Multi‑step Undo for safety

---

## Table of Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Running](#running)
- [Usage](#usage)
  - [Scope: Name, Extension, Both](#scope-name-extension-both)
  - [AutoSort vs. Manual Reordering](#autosort-vs-manual-reordering)
  - [Templates: Numbering and Parent Names](#templates-numbering-and-parent-names)
- [Regex Examples](#regex-examples)
- [OS Notes (Windows/macOS/Linux)](#os-notes-windowsmacoslinux)
- [Configuration and Persistence](#configuration-and-persistence)
- [License](#license)

---

## Features

- Find/Replace with:
  - Regular expressions or literal matching
  - Case sensitivity toggle
  - Scope: Name only, Extension only, or Name + Extension
- Templates:
  - Numbering with zero padding: `<###:start:step>` (e.g., `<001:1:1>`, `<###:10:5>`)
  - Parent folder names: `<p:n>` (or legacy `<parent:n>`), where `n` is the depth
- Preview table with color status:
  - Gray = unchanged, Green = OK to rename, Red = conflict
- Ordering modes:
  - AutoSort (natural sorting, if available)
  - Manual reordering by drag‑and‑drop with precise insert indicator
- Batch rename with multi‑step Undo
- Drag & drop to add items (when `tkinterdnd2` is installed)

---

## Requirements

- **Python 3.10+** (3.11 recommended)
  - Reason: the code uses PEP 604 union types with the `|` operator in type hints, which requires Python 3.10 or newer.
- Tkinter:
  - Windows: included with standard Python from python.org
  - macOS: included with python.org installer; with Homebrew Python you may need `brew install tcl-tk`
  - Linux: install system package (e.g., `sudo apt-get install python3-tk`)
- Optional Python modules:
  - `tkinterdnd2` for drag & drop
  - `natsort` for natural sorting


---

## Installation

1. Ensure Python (3.10+) is installed and Tkinter is available (see Requirements).
2. Clone or download this repository.
3. (Optional) Create and activate a virtual environment:
   - macOS/Linux:
     ```bash
     python3 -m venv .venv
     source .venv/bin/activate
     ```
   - Windows (PowerShell):
     ```powershell
     py -m venv .venv
     .\.venv\Scripts\Activate.ps1
     ```
4. Install optional modules:
   ```bash
   pip install tkinterdnd2 natsort
   ```

---

## Running

Run the app from the project directory:
```bash
python ReRenamer.py
```
or
```bash
python -m ReRenamer
```

---

## Usage

### Add Items
- Use buttons:
  - “Add Files…”
  - “Add Folders…”
- Or drag and drop files/folders into the window (requires `tkinterdnd2`)

The app enforces uniform type by default: you cannot mix files and folders in a single batch (to prevent ambiguous renames). You can adjust this behavior in code if needed.

### Options
- Case Sensitive: toggles case sensitivity for matching
- Regex: switches between regular expression matching and literal matching
- AutoSort:
  - On: keeps the table auto‑sorted (natural sort if `natsort` is installed)
  - Off: preserves your custom order and enables manual drag‑and‑drop reordering

### Scope: Name, Extension, Both
- Name only: apply Find/Replace to the base name (without the dot and extension)
- Extension only: apply Find/Replace to the extension part
- Name + Extension: treat the entire file name (including extension) as a single string

Notes:
- When “Extension only” is selected, the app normalizes the dot and extension correctly.
- When “Name + Extension” is selected, the app splits the result back into name and extension safely, following OS rules.

### AutoSort vs. Manual Reordering
- AutoSort ON:
  - Items are sorted automatically and continuously.
  - Drag‑and‑drop reordering is disabled.
- AutoSort OFF:
  - Your table order is preserved.
  - Drag‑and‑drop reordering is enabled; use it to create precise sequences (e.g., when building numbered series). A blue horizontal line shows the exact insertion point. Multi‑selection is respected.

### Apply and Undo
- “Apply Rules” performs the actual renaming on disk.
- Conflicts are highlighted in red and must be resolved (invalid names, duplicates, or existing targets).
- “Undo” supports:
  - Reverting the latest rename batch
  - Restoring recently removed or added items

### Favorites and History
- Save your current Find/Replace options as a Favorite.
- Quickly recall recent configurations from History (most recent preserved up to a limit).

### Templates: Numbering and Parent Names
You can use templates inside the Replace field. Templates are expanded per item during preview/rename.

- Numbering: `<###:start:step>`
  - Number of `#` defines zero‑padding width.
  - `start` is the initial number (default: 1).
  - `step` is the increment (default: 1).
  - Examples:
    - Replace: `photo_<###:1:1>` → `photo_001`, `photo_002`, …
    - Replace: `ep-<##:10:5>` → `ep-10`, `ep-15`, `ep-20`, …

- Parent folder name: `<p:n>` (or `<parent:n>`)
  - `n = 1` uses the immediate parent folder’s name
  - `n = 2` uses the grandparent, etc.
  - Examples:
    - Replace: `<p:1>_#<###:1:1>` → `Album_#001`, `Album_#002`, …
    - Replace: `<p:2>-<p:1>_<##>` → `Project-Album_01`, …

You can combine templates with Regex or literal replacement. Templates are expanded before applying numbering increments, and numbering increases only for items where a replacement occurred.

---

## Regex Examples

Assume the following sample inputs to illustrate the results.

1) Remove a fixed prefix:
- Find (Regex off): `prefix_`
- Replace: (empty)
- From: `prefix_report.txt` → `report.txt`

2) Swap date order from `YYYY-MM-DD` to `DD-MM-YYYY`:
- Find (Regex on): `(\d{4})-(\d{2})-(\d{2})`
- Replace: `\3-\2-\1`
- From: `2023-07-14_notes.txt` → `14-07-2023_notes.txt`

3) Keep only digits from a part:
- Scope: Name only
- Find (Regex on): `\D+`
- Replace: (empty)
- From: `item-12a-34b` → `1234`

4) Change extension from `.jpeg` or `.jpg` to `.jpg` consistently:
- Scope: Extension only
- Find (Regex on): `jpe?g`
- Replace: `jpg`
- From: `image.JPEG` → `image.jpg` (case‑sensitivity depends on the toggle)

5) Insert numbering and keep the original name:
- Replace: `<###:1:1>_<p:1>_<p:2>_<###:1:1>`  
  Example result: `001_Album_Project_002_filename.ext`  
  Note: numbering increases only where a substitution matched.

6) Extract and reorder parts with groups:
- Find (Regex on): `(\w+)-(\d+)`
- Replace: `\2_\1`
- From: `alpha-42.txt` → `42_alpha.txt`

Tips:
- Use `^` and `$` anchors for start/end of the string.
- Use lookaheads/lookbehinds for context without capturing, e.g., `(?<=prefix)_` or `foo(?=bar)`.

---

## OS Notes (Windows/macOS/Linux)

- Invalid characters and reserved names:
  - Windows: forbids `<>:"/\|?*` and control chars; reserved names like `CON`, `PRN`, `AUX`, `NUL`, `COM1…`, `LPT1…`
  - Linux/macOS: `/` and NUL are invalid in names
- Trailing spaces/dots:
  - Not allowed on Windows
- Case sensitivity:
  - The app detects duplicates case‑insensitively on Windows/macOS (by default) and case‑sensitively on Linux
- Existing targets:
  - Renames that would overwrite existing paths are flagged as conflicts
- Cross‑volume rename:
  - `Path.rename` may fail when moving between different drives/volumes on Windows. If needed, implement a copy‑and‑delete fallback.
- Case‑only renames (e.g., `file.txt` → `File.txt`) on case‑insensitive file systems can be tricky; the app tries to handle it, but a two‑step rename may be required in some edge cases.

---

## Configuration and Persistence

- Config directory: `~/.rerename/`
  - `favorites.json` — saved presets (Find/Replace + options)
  - `history.json` — recent configurations (newest last)
- These files are written in UTF‑8 JSON.

---

## License

MIT License. See LICENSE.

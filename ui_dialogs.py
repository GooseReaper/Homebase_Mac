"""Item, location, and review windows."""
from __future__ import annotations

import sqlite3
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from ai import AIError
from inventory import CATEGORIES, LOCATION_TYPES, InventoryError
from themes import T
from ui_common import (IS_MAC, MOD, PHOTO_TYPES, dnd_available, paint_photo, photo_from_clipboard, read_photo,
                       register_drop, scroll_frame)


class ItemDialog:
    """Add/edit form; all input is validated by the Inventory class."""

    def __init__(self, app, item_id=None, photo=None, autofill=False, on_close=None):
        self.app, self.item_id, self.on_close = app, item_id, on_close
        self.item = app.inventory.get_item(item_id) if item_id else None
        self.photo = self.item["photo"] if self.item else b""
        self.photo_name = self.item["photo_name"] if self.item else ""
        if photo:
            self.photo, self.photo_name = photo
        font = app.font
        self.window = tk.Toplevel(app.root)
        self.window.title("Edit item" if self.item else "Add item")
        self.window.transient(app.root)
        self.window.resizable(False, True)
        self.window.configure(bg=T["bg"])
        self.window.grab_set()
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        outer, form = scroll_frame(self.window, width=600, padding=24)
        outer.pack(fill="both", expand=True)
        form.columnconfigure(0, weight=1)
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="Edit item" if self.item else "Add an item", style="Title.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 15))
        photo_row = ttk.Frame(form)
        photo_row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        self.preview = tk.Canvas(photo_row, width=150, height=130, bg=T["photo_bg"], highlightthickness=1,
                                 highlightbackground=T["border"], cursor="hand2")
        self.preview.pack(side="left", padx=(0, 15))
        self.preview.bind("<Button-1>", lambda event: self.choose_photo())
        buttons = ttk.Frame(photo_row)
        buttons.pack(side="left", fill="y")
        ttk.Button(buttons, text="Choose photo…", command=self.choose_photo).pack(anchor="w", pady=2, fill="x")
        ttk.Button(buttons, text="Paste photo", command=self.paste_photo).pack(anchor="w", pady=2, fill="x")
        ttk.Button(buttons, text="Remove photo", command=self.remove_photo).pack(anchor="w", pady=2, fill="x")
        self.ai_button = ttk.Button(buttons, text="✦ Fill in with AI", style="Primary.TButton", command=self.ai_fill)
        self.ai_button.pack(anchor="w", pady=(8, 2), fill="x")
        drop = "Drag a photo here, click the box, or paste one. " if dnd_available(self.window) else "Click the box or paste a photo. "
        ttk.Label(buttons, text=drop + "Up to 10 MB;\nstored inside your encrypted inventory.", style="Hint.TLabel").pack(anchor="w", pady=(6, 0))
        self.paint()
        self.values = {}
        initial = self.item or {"name": "", "category": CATEGORIES[0], "quantity": 1, "value": 0, "low_stock": 0}
        for key in ("name", "category", "quantity", "value", "low_stock"):
            self.values[key] = tk.StringVar(value=f"{initial[key]:.2f}" if key == "value" and self.item else str(initial[key]))
        self.values["tags"] = tk.StringVar(value=", ".join(self.item["tags"]) if self.item else "")
        ttk.Label(form, text="Item name *").grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 4))
        self.name_entry = ttk.Entry(form, textvariable=self.values["name"], width=63)
        self.name_entry.grid(row=3, column=0, columnspan=2, sticky="ew")
        ttk.Label(form, text="Category *").grid(row=4, column=0, sticky="w", pady=(12, 4))
        ttk.Label(form, text="Quantity *").grid(row=4, column=1, sticky="w", padx=(14, 0), pady=(12, 4))
        ttk.Combobox(form, textvariable=self.values["category"], values=CATEGORIES, state="readonly", width=25).grid(row=5, column=0, sticky="ew")
        ttk.Spinbox(form, textvariable=self.values["quantity"], from_=0, to=999999, width=25).grid(row=5, column=1, sticky="ew", padx=(14, 0))
        ttk.Label(form, text="Location *").grid(row=6, column=0, columnspan=2, sticky="w", pady=(12, 4))
        locations = app.inventory.locations()
        location_map = {loc["id"]: loc for loc in locations}
        self.location_ids = {app.inventory.path_text(loc["id"], location_map): loc["id"] for loc in locations}
        location_id = self.item["location_id"] if self.item else app.current_location() or (locations[0]["id"] if locations else "")
        self.location_value = tk.StringVar(value=app.inventory.path_text(location_id, location_map) if location_id else "")
        self.location_combo = ttk.Combobox(form, textvariable=self.location_value, values=sorted(self.location_ids), state="readonly")
        self.location_combo.grid(row=7, column=0, columnspan=2, sticky="ew")
        ttk.Label(form, text="Tags (separate with commas)").grid(row=8, column=0, columnspan=2, sticky="w", pady=(12, 4))
        ttk.Entry(form, textvariable=self.values["tags"]).grid(row=9, column=0, columnspan=2, sticky="ew")
        ttk.Label(form, text="Unit value ($)").grid(row=10, column=0, sticky="w", pady=(12, 4))
        ttk.Label(form, text="Low stock at").grid(row=10, column=1, sticky="w", padx=(14, 0), pady=(12, 4))
        ttk.Entry(form, textvariable=self.values["value"]).grid(row=11, column=0, sticky="ew")
        ttk.Spinbox(form, textvariable=self.values["low_stock"], from_=0, to=999999).grid(row=11, column=1, sticky="ew", padx=(14, 0))
        ttk.Label(form, text="Notes").grid(row=12, column=0, columnspan=2, sticky="w", pady=(12, 4))
        self.notes = tk.Text(form, height=4, width=58, font=font(10), wrap="word", relief="sunken" if T["bevel"] else "flat",
                             bd=2 if T["bevel"] else 0, highlightthickness=0 if T["bevel"] else 1, highlightbackground=T["border"],
                             highlightcolor=T["accent"], bg=T["field"], fg=T["ink"], insertbackground=T["ink"], undo=True, padx=6, pady=4)
        self.notes.grid(row=13, column=0, columnspan=2, sticky="ew")
        self.notes.insert("1.0", self.item["notes"] if self.item else "")
        self.notes.edit_reset()
        self.error = tk.StringVar()
        ttk.Label(form, textvariable=self.error, style="Error.TLabel", wraplength=540).grid(row=14, column=0, columnspan=2, sticky="w", pady=(10, 0))
        footer = ttk.Frame(form)
        footer.grid(row=15, column=0, columnspan=2, sticky="e", pady=(15, 0))
        ttk.Button(footer, text="Cancel", command=self.close).pack(side="left", padx=8)
        ttk.Button(footer, text="Save changes" if self.item else "Add item", style="Primary.TButton", command=self.save).pack(side="left")
        self.window.bind("<Escape>", lambda event: self.close())
        self.window.bind("<Control-Return>", lambda event: self.save())
        if IS_MAC:
            self.window.bind("<Command-Return>", lambda event: self.save())
            self.window.bind("<Command-v>", self.paste_shortcut)
        self.window.bind("<Control-v>", self.paste_shortcut)
        for target in (self.window, self.preview):
            register_drop(target, self.dropped)
        self.window.update_idletasks()
        height = min(form.winfo_reqheight() + 10, self.window.winfo_screenheight() - 120)
        self.window.geometry(f"{max(650, form.winfo_reqwidth() + 40)}x{max(460, height)}")
        app.center_dialog(self.window)
        self.name_entry.focus_set()
        if autofill and self.photo and app.ai_ready():
            self.window.after(200, self.ai_fill)

    # ------------------------------------------------------------------ photos
    def paint(self):
        paint_photo(self.preview, self.photo, title="Drop or click for a photo" if not self.photo else "", font=self.app.font(9))

    def set_photo(self, data, name):
        self.photo, self.photo_name = data, name
        self.paint()
        self.error.set("")

    def choose_photo(self):
        filename = filedialog.askopenfilename(parent=self.window, title="Choose item photo",
                                              filetypes=[("Photos", " ".join(f"*{ext}" for ext in PHOTO_TYPES)), ("All files", "*.*")])
        if filename:
            self.load_file(filename)

    def load_file(self, filename):
        try:
            self.set_photo(*read_photo(filename))
        except Exception as error:
            self.error.set(f"Could not use this photo: {error}")

    def dropped(self, paths):
        photos = [p for p in paths if Path(p).suffix.casefold() in PHOTO_TYPES]
        if not photos:
            self.error.set("That isn't a photo. Drop a PNG, GIF, JPG, or WebP file.")
            return
        self.load_file(photos[0])

    def paste_shortcut(self, event):
        if isinstance(event.widget, (tk.Entry, ttk.Entry, tk.Text, ttk.Combobox, ttk.Spinbox)):
            return None   # normal text paste
        self.paste_photo()
        return "break"

    def paste_photo(self):
        try:
            found = photo_from_clipboard()
        except InventoryError as error:
            self.error.set(str(error))
            return
        if found:
            self.set_photo(*found)
        else:
            self.error.set("There's no photo on the clipboard. Copy an image or image file first.")

    def remove_photo(self):
        self.photo, self.photo_name = b"", ""
        self.paint()

    # ---------------------------------------------------------------------- AI
    def ai_fill(self):
        if not self.photo:
            self.error.set("Add a photo first, then AI can fill in the details.")
            return
        try:
            assistant = self.app.ai()
        except AIError as error:
            self.error.set(str(error))
            return
        self.ai_button.configure(state="disabled", text="✦ Looking at the photo…")
        self.error.set("")
        paths = sorted(self.location_ids)
        photo = self.photo
        self.app.background.run(lambda: assistant.describe_photo(photo, CATEGORIES, paths), self.ai_done, self.ai_failed)

    def ai_done(self, result):
        if not self.window.winfo_exists():
            return
        self.ai_button.configure(state="normal", text="✦ Fill in with AI")
        new = self.item is None
        if new or not self.values["name"].get().strip():
            self.values["name"].set(result["name"])
        self.values["category"].set(result["category"])
        if new:
            self.values["quantity"].set(str(result["quantity"] or 1))
        tags = [t.strip() for t in self.values["tags"].get().split(",") if t.strip()]
        self.values["tags"].set(", ".join(dict.fromkeys(tags + result["tags"])))
        if not self.notes.get("1.0", "end-1c").strip():
            self.notes.insert("1.0", result["notes"])
        if result["value"] and self.values["value"].get().strip() in ("", "0", "0.0", "0.00"):
            self.values["value"].set(f"{result['value']:.2f}")
        match = next((path for path in self.location_ids if path.casefold() == result["location"].casefold()), None)
        if match and new:
            self.location_value.set(match)
        self.error.set("")
        self.app.set_status("AI filled in the form. Check it, then save.")

    def ai_failed(self, error):
        if self.window.winfo_exists():
            self.ai_button.configure(state="normal", text="✦ Fill in with AI")
            self.error.set(str(error))

    # -------------------------------------------------------------------- save
    def close(self):
        self.window.destroy()
        if self.on_close:
            self.on_close()

    def save(self):
        data = {key: value.get() for key, value in self.values.items()}
        data.update(location_id=self.location_ids.get(self.location_value.get(), ""), notes=self.notes.get("1.0", "end-1c"), photo=self.photo, photo_name=self.photo_name)
        try:
            saved = self.app.inventory.save_item(data, self.item_id)
        except (InventoryError, sqlite3.Error) as error:
            self.error.set(str(error))
            return
        self.window.destroy()
        self.app.after_item_saved(saved)
        if self.on_close:
            self.on_close()


class LocationDialog:
    def __init__(self, app, location_id=None, parent_id=None):
        self.app, self.location_id = app, location_id
        old = app.inventory.get_location(location_id) if location_id else None
        self.window = tk.Toplevel(app.root)
        self.window.title("Edit location" if old else "Add location")
        self.window.transient(app.root)
        self.window.resizable(False, False)
        self.window.configure(bg=T["bg"])
        self.window.grab_set()
        frame = ttk.Frame(self.window, padding=24)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Edit location" if old else "Add a location", style="Title.TLabel").pack(anchor="w", pady=(0, 20))
        self.name = tk.StringVar(value=old["name"] if old else "")
        self.kind = tk.StringVar(value=old["kind"] if old else "Storage" if parent_id else "House")
        ttk.Label(frame, text="Location name *").pack(anchor="w", pady=(0, 4))
        entry = ttk.Entry(frame, textvariable=self.name, width=55)
        entry.pack(fill="x")
        ttk.Label(frame, text="Type").pack(anchor="w", pady=(14, 4))
        ttk.Combobox(frame, textvariable=self.kind, values=LOCATION_TYPES, state="readonly").pack(fill="x")
        location_map = {loc["id"]: loc for loc in app.inventory.locations()}
        excluded = app.inventory.descendants(location_id, location_map) if location_id else set()
        self.parents = {"No parent (top level)": None}
        self.parents.update(sorted(((app.inventory.path_text(loc_id, location_map), loc_id) for loc_id in location_map if loc_id not in excluded)))
        parent = old["parent_id"] if old else parent_id
        self.parent = tk.StringVar(value=app.inventory.path_text(parent, location_map) if parent else "No parent (top level)")
        ttk.Label(frame, text="Inside of").pack(anchor="w", pady=(14, 4))
        ttk.Combobox(frame, textvariable=self.parent, values=list(self.parents), state="readonly").pack(fill="x")
        self.path_preview = tk.StringVar()
        ttk.Label(frame, textvariable=self.path_preview, wraplength=440, style="Hint.TLabel").pack(anchor="w", pady=15)
        self.error = tk.StringVar()
        ttk.Label(frame, textvariable=self.error, wraplength=440, style="Error.TLabel").pack(anchor="w")
        footer = ttk.Frame(frame)
        footer.pack(anchor="e", pady=(16, 0))
        ttk.Button(footer, text="Cancel", command=self.window.destroy).pack(side="left", padx=8)
        ttk.Button(footer, text="Save location", style="Primary.TButton", command=self.save).pack(side="left")
        self.name.trace_add("write", self.update_path)
        self.parent.trace_add("write", self.update_path)
        self.update_path()
        self.window.bind("<Escape>", lambda event: self.window.destroy())
        self.window.bind("<Return>", lambda event: self.save())
        app.center_dialog(self.window)
        entry.focus_set()

    def update_path(self, *_):
        parent = "" if self.parents.get(self.parent.get()) is None else self.parent.get() + " → "
        self.path_preview.set(parent + (self.name.get().strip() or "New location"))

    def save(self):
        try:
            loc = self.app.inventory.save_location({"name": self.name.get(), "kind": self.kind.get(), "parent_id": self.parents.get(self.parent.get())}, self.location_id)
        except (InventoryError, sqlite3.Error) as error:
            self.error.set(str(error))
            return
        self.window.destroy()
        self.app.refresh()
        self.app.set_status(f"Saved location: {loc['name']}  ·  {MOD}Z to undo")


class ReviewDialog:
    """A checklist of proposed changes (from AI). Only ticked rows are applied, as one undo step."""

    def __init__(self, app, title, intro, rows, on_apply):
        """rows: [(label, payload)]; on_apply([payload, ...])."""
        self.app, self.rows, self.on_apply = app, rows, on_apply
        self.window = tk.Toplevel(app.root)
        self.window.title(title)
        self.window.transient(app.root)
        self.window.configure(bg=T["bg"])
        self.window.grab_set()
        frame = ttk.Frame(self.window, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=title, style="Title.TLabel").pack(anchor="w")
        ttk.Label(frame, text=intro, style="Hint.TLabel", wraplength=640).pack(anchor="w", pady=(4, 12))
        outer, inner = scroll_frame(frame, width=640)
        outer.pack(fill="both", expand=True)
        self.checks = []
        for label, _payload in rows:
            var = tk.BooleanVar(value=True)
            ttk.Checkbutton(inner, text=label, variable=var).pack(anchor="w", pady=3)
            self.checks.append(var)
        footer = ttk.Frame(frame)
        footer.pack(fill="x", pady=(14, 0))
        ttk.Button(footer, text="Select none", command=lambda: [v.set(False) for v in self.checks]).pack(side="left")
        ttk.Button(footer, text="Select all", command=lambda: [v.set(True) for v in self.checks]).pack(side="left", padx=8)
        ttk.Button(footer, text="Apply selected", style="Primary.TButton", command=self.apply).pack(side="right")
        ttk.Button(footer, text="Cancel", command=self.window.destroy).pack(side="right", padx=8)
        self.window.bind("<Escape>", lambda event: self.window.destroy())
        self.window.geometry(f"720x{min(620, 200 + 30 * len(rows))}")
        app.center_dialog(self.window)

    def apply(self):
        chosen = [payload for (label, payload), var in zip(self.rows, self.checks) if var.get()]
        self.window.destroy()
        if chosen:
            self.on_apply(chosen)

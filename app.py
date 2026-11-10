"""Homebase: a local, encrypted, multi-user home inventory for the desktop.

Run: python app.py                 (accounts, encryption, sign-in)
     python app.py --db FILE       (open an unencrypted database directly: developer mode)
     python app.py --check         (check a database without opening a window)
"""
from __future__ import annotations

import argparse
import copy
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import ai
from ai import AIError, PROVIDERS
from inventory import CATEGORIES, SORTS, Inventory, InventoryError
from themes import DENSITY, THEMES, T, apply_theme, font_families

try:
    import tkinter as tk
    from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk
except ImportError:
    tk = colorchooser = filedialog = messagebox = simpledialog = ttk = None

if tk is not None:
    import security
    from ui_auth import AuthScreen, PasswordForm, TwoFactorSetup
    from ui_common import (CONTEXT_BUTTONS, IS_MAC, MIDDLE_BUTTON, MOD, NEW_TAB_CLICK, PHOTO_TYPES, Background, accel,
                           currency, install_wheel, make_root, paint_photo, read_photo, register_drop, scroll_frame)
    from ui_dialogs import ItemDialog, LocationDialog, ReviewDialog
else:   # tests and --check still work without Tkinter
    IS_MAC = sys.platform == "darwin"
    MOD = "⌘" if IS_MAC else "Ctrl+"

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
LEGACY_DB = DATA / "homebase.sqlite3"

DEFAULT_SETTINGS = {
    "theme": "modern", "text_scale": 100, "font_body": "", "font_display": "", "accent": "", "density": "Comfortable",
    "sidebar": "Left", "detail": "Right", "tabbar": "Top", "show_stats": True, "open_items_in": "New tab",
    "restore_tabs": True, "confirm_delete": True, "default_sort": SORTS[0], "auto_lock_minutes": 10,
    "ai_provider": "claude", "ai_model_claude": "", "ai_model_openai": "", "ai_key_claude": "", "ai_key_openai": "",
    "ai_send_notes": False, "ai_send_values": False, "ai_autofill_photos": True,
    "open_tabs": [], "active_tab": 0,
}
LEGACY_SIZES = {"Small": 90, "Normal": 100, "Large": 115, "Extra large": 130}
PAGE_NAMES = {"items": "Items", "item": "Item", "locations": "Locations", "activity": "Activity", "settings": "Settings", "assistant": "AI assistant"}
PAGE_ICONS = {"items": "▤", "item": "◆", "locations": "⌂", "activity": "≡", "settings": "⚙", "assistant": "✦"}
CHOICES = {"sidebar": ("Left", "Right", "Hidden"), "detail": ("Right", "Left", "Hidden"), "tabbar": ("Top", "Bottom"),
           "open_items_in": ("New tab", "Same tab"), "density": tuple(DENSITY)}
LOCK_CHOICES = {"Never": 0, "5 minutes": 5, "10 minutes": 10, "15 minutes": 15, "30 minutes": 30, "1 hour": 60}


def blank_items_state(sort=SORTS[0], location_id=None, query=""):
    return {"query": query, "category": "", "tag": "", "low_only": False, "sort": sort, "location_id": location_id, "selected_id": None}


class HomebaseApp:
    def __init__(self, root, inventory, session=None, store=None, on_sign_out=None):
        self.root, self.inventory, self.session, self.store, self.on_sign_out = root, inventory, session, store, on_sign_out
        self.settings = {key: inventory.get_setting(key, copy.deepcopy(default)) for key, default in DEFAULT_SETTINGS.items()}
        legacy = inventory.get_setting("text_size")
        if legacy in LEGACY_SIZES and inventory.get_setting("text_scale") is None:
            self.settings["text_scale"] = LEGACY_SIZES[legacy]
        for key, options in CHOICES.items():
            if self.settings[key] not in options:
                self.settings[key] = DEFAULT_SETTINGS[key]
        if self.settings["theme"] not in THEMES:
            self.settings["theme"] = "modern"
        if self.settings["default_sort"] not in SORTS:
            self.settings["default_sort"] = SORTS[0]
        self.selected_id = None
        self.filtered_items = []
        self.closed_tabs = []
        self.chat_history = []
        self._jobs = {}
        self._loading = False
        self._last_shortcut = {}
        self._last_activity = time.monotonic()
        self._photo_queue = []
        self.shell = None
        self.closed = False
        self.background = Background(root)
        if session is not None:
            inventory.on_change = self.schedule_save
        self.tabs, self.active = [], 0
        self.restore_tabs()
        root.title("Homebase" + (f" · {session.username}" if session else " · Local inventory"))
        if not getattr(root, "_homebase_sized", False):
            root.geometry("1400x880")
            root.minsize(1060, 680)
            root._homebase_sized = True
        root.protocol("WM_DELETE_WINDOW", self.close)
        install_wheel(root)
        root._homebase_app = self
        if not getattr(root, "_homebase_activity", False):
            root._homebase_activity = True
            for sequence in ("<Any-KeyPress>", "<Motion>", "<Any-Button>"):
                root.bind_all(sequence, lambda event: root._homebase_app and setattr(root._homebase_app, "_last_activity", time.monotonic()), add="+")
        self.bind_shortcuts()
        self.build_ui()
        self.refresh()
        self.later("autolock", 15000, self.check_autolock)

    # ---------------------------------------------------------------- utilities
    def later(self, name, ms, callback):
        """Run callback once after ms (replacing any pending job with the same name)."""
        if self._jobs.get(name):
            try:
                self.root.after_cancel(self._jobs[name])
            except Exception:
                pass

        def run():
            self._jobs[name] = None
            if not self.closed:
                callback()
        self._jobs[name] = self.root.after(ms, run)

    def cancel_jobs(self):
        for job in self._jobs.values():
            if job:
                try:
                    self.root.after_cancel(job)
                except Exception:
                    pass
        self._jobs.clear()

    def center_dialog(self, window):
        window.update_idletasks()
        x = max(0, self.root.winfo_rootx() + (self.root.winfo_width() - window.winfo_width()) // 2)
        y = max(0, self.root.winfo_rooty() + (self.root.winfo_height() - window.winfo_height()) // 2)
        window.geometry(f"+{x}+{y}")

    def set_status(self, text):
        self.status.set(f"{text}  ·  Saved {'encrypted' if self.session else 'locally'}")

    def busy(self):
        return bool(self.root.grab_current())

    # ----------------------------------------------------------------- saving
    def schedule_save(self):
        self.later("save", 700, self.save_now)

    def save_now(self):
        if self.session is not None:
            try:
                self.session.save()
            except Exception as error:
                messagebox.showerror("Couldn't save", f"Your changes could not be written to disk:\n{error}", parent=self.root)

    # --------------------------------------------------------------- settings
    def change_setting(self, key, value, rebuild=None):
        if self.settings.get(key) == value:
            return
        self.settings[key] = value
        self.inventory.set_setting(key, value)
        if self.store is not None and key in ("theme", "text_scale"):
            self.store.set_pref(key, value)
        looks = ("theme", "text_scale", "font_body", "font_display", "accent", "density", "sidebar", "detail", "tabbar", "show_stats")
        if rebuild or (rebuild is None and key in looks):
            self.later("rebuild", 10, self.rebuild)
        self.set_status("Setting saved")

    def rebuild(self):
        """Apply a new look or layout by rebuilding the window; tabs and filters are kept."""
        self.capture_items_state()
        status = self.status.get()
        self.shell.destroy()
        self.build_ui()
        self.refresh()
        self.status.set(status)

    # --------------------------------------------------------------------- tabs
    def restore_tabs(self):
        saved = self.settings.get("open_tabs") if self.settings.get("restore_tabs") else []
        for entry in saved or []:
            if isinstance(entry, dict) and entry.get("page") in PAGE_NAMES:
                state = entry.get("state") or {}
                if entry["page"] == "items":
                    state = {**blank_items_state(self.settings["default_sort"]), **state}
                self.tabs.append({"page": entry["page"], "state": state, "back": [], "fwd": []})
        if not self.tabs:
            self.tabs.append({"page": "items", "state": blank_items_state(self.settings["default_sort"]), "back": [], "fwd": []})
        self.active = min(max(0, int(self.settings.get("active_tab") or 0)), len(self.tabs) - 1)

    def save_tabs(self):
        self.capture_items_state()
        data = [{"page": tab["page"], "state": {k: v for k, v in tab["state"].items() if k != "selected_id"}} for tab in self.tabs[:30]]
        if data != self.settings.get("open_tabs") or self.active != self.settings.get("active_tab"):
            self.settings["open_tabs"], self.settings["active_tab"] = data, self.active
            self.inventory.set_setting("open_tabs", data)
            self.inventory.set_setting("active_tab", self.active)

    @property
    def tab(self):
        return self.tabs[self.active]

    def tab_title(self, tab):
        page, state = tab["page"], tab["state"]
        if page == "items":
            if state.get("query"):
                return f"Search: {state['query']}"
            if state.get("location_id"):
                try:
                    return self.inventory.get_location(state["location_id"])["name"]
                except InventoryError:
                    return "Missing location"
            if state.get("low_only"):
                return "Low stock"
            return state.get("category") or "All items"
        if page == "item":
            try:
                return self.inventory.get_item(state.get("item_id"))["name"]
            except InventoryError:
                return "Deleted item"
        return PAGE_NAMES[page]

    def new_tab(self, page="items", state=None, activate=True):
        if self.busy():
            return
        self.capture_items_state()
        if page == "items":
            state = {**blank_items_state(self.settings["default_sort"]), **(state or {})}
        self.tabs.insert(self.active + 1, {"page": page, "state": dict(state or {}), "back": [], "fwd": []})
        if activate:
            self.active += 1
        self.show_tab()

    def open_or_focus(self, page):
        """Pages like Settings open once; asking again switches to that tab."""
        for index, tab in enumerate(self.tabs):
            if tab["page"] == page:
                self.activate(index)
                return
        self.new_tab(page)

    def close_tab(self, index=None):
        if self.busy():
            return
        index = self.active if index is None else index
        self.capture_items_state()
        self.closed_tabs.append(self.tabs.pop(index))
        del self.closed_tabs[:-20]
        if not self.tabs:
            self.tabs.append({"page": "items", "state": blank_items_state(self.settings["default_sort"]), "back": [], "fwd": []})
        if index < self.active or self.active >= len(self.tabs):
            self.active = max(0, self.active - 1)
        self.show_tab()

    def reopen_tab(self):
        if self.closed_tabs and not self.busy():
            self.capture_items_state()
            self.tabs.insert(self.active + 1, self.closed_tabs.pop())
            self.active += 1
            self.show_tab()

    def activate(self, index):
        if 0 <= index < len(self.tabs) and not self.busy():
            self.capture_items_state()
            self.active = index
            self.show_tab()

    def cycle_tab(self, step):
        self.activate((self.active + step) % len(self.tabs))

    def navigate(self, page, state=None, new_tab=False):
        """Go somewhere in the current tab (with Back), or in a new tab."""
        if self.busy():
            return
        if page == "items":
            state = {**blank_items_state(self.settings["default_sort"]), **(state or {})}
        if new_tab:
            self.new_tab(page, state or {})
            return
        self.capture_items_state()
        tab = self.tab
        tab["back"].append((tab["page"], copy.deepcopy(tab["state"])))
        del tab["back"][:-50]
        tab["fwd"].clear()
        tab["page"], tab["state"] = page, dict(state or {})
        self.show_tab()

    def go_back(self):
        tab = self.tab
        if tab["back"] and not self.busy():
            self.capture_items_state()
            tab["fwd"].append((tab["page"], copy.deepcopy(tab["state"])))
            tab["page"], tab["state"] = tab["back"].pop()
            self.show_tab()

    def go_forward(self):
        tab = self.tab
        if tab["fwd"] and not self.busy():
            self.capture_items_state()
            tab["back"].append((tab["page"], copy.deepcopy(tab["state"])))
            tab["page"], tab["state"] = tab["fwd"].pop()
            self.show_tab()

    def show_tab(self):
        """Draw the tab strip and show the active tab's page."""
        tab = self.tab
        self.render_tab_strip()
        self.pages[tab["page"]].tkraise()
        if tab["page"] == "items":
            self.load_items_state()
            self.refresh_items()
        elif tab["page"] == "item":
            self.show_item_page()
        elif tab["page"] == "locations":
            self.refresh_locations()
        elif tab["page"] == "assistant":
            self.refresh_assistant()
        self.update_nav()
        self.later("save_tabs", 600, self.save_tabs)

    def render_tab_strip(self):
        for child in self.strip_tabs.winfo_children():
            child.destroy()
        width = max(8, min(26, 150 // max(1, len(self.tabs))))
        for index, tab in enumerate(self.tabs):
            active = index == self.active
            bg = T["tab_selected"] if active else T["tab_bg"]
            fg = T["accent"] if active else T["muted"]
            box = tk.Frame(self.strip_tabs, bg=bg, padx=10, pady=5, relief="raised" if T["bevel"] else "flat",
                           bd=2 if T["bevel"] else 0, highlightthickness=0 if T["bevel"] else 1,
                           highlightbackground=T["accent"] if active and T["is_dark"] else T["border"])
            box.pack(side="left", padx=(0, 2), pady=(5, 0) if self.settings["tabbar"] == "Top" else (0, 5), fill="y")
            title = self.tab_title(tab)
            title = title if len(title) <= width else title[:width - 1] + "…"
            label = tk.Label(box, text=f"{PAGE_ICONS[tab['page']]}  {title.upper() if T['uppercase'] else title}", bg=bg, fg=fg,
                             font=self.font(10, "bold" if active else "normal"), cursor="hand2")
            label.pack(side="left")
            close = tk.Label(box, text="✕", bg=bg, fg=T["muted"], font=self.font(9), cursor="hand2", padx=3)
            close.pack(side="left", padx=(8, 0))
            for widget in (box, label):
                widget.bind("<Button-1>", lambda event, i=index: self.activate(i))
                widget.bind(MIDDLE_BUTTON, lambda event, i=index: self.close_tab(i))
            close.bind("<Button-1>", lambda event, i=index: self.close_tab(i))
        plus = tk.Label(self.strip_tabs, text=" + ", bg=T["tab_strip"], fg=T["accent"], font=self.font(14, "bold"), cursor="hand2")
        plus.pack(side="left", padx=6)
        plus.bind("<Button-1>", lambda event: self.new_tab())

    def update_nav(self):
        tab = self.tab
        self.back_button.configure(state="normal" if tab["back"] else "disabled")
        self.fwd_button.configure(state="normal" if tab["fwd"] else "disabled")
        for child in self.crumbs.winfo_children():
            child.destroy()
        parts = []
        if tab["page"] in ("items", "item"):
            location_id = tab["state"].get("location_id") if tab["page"] == "items" else None
            item = None
            if tab["page"] == "item":
                try:
                    item = self.inventory.get_item(tab["state"].get("item_id"))
                    location_id = item["location_id"]
                except InventoryError:
                    item = None
            parts.append(("All items", lambda: self.navigate("items")))
            if location_id:
                try:
                    chain, current = [], location_id
                    while current:
                        loc = self.inventory.get_location(current)
                        chain.insert(0, loc)
                        current = loc["parent_id"]
                    for loc in chain:
                        parts.append((loc["name"], lambda l=loc["id"]: self.navigate("items", {"location_id": l})))
                except InventoryError:
                    pass
            if item:
                parts.append((item["name"], None))
            if tab["page"] == "items" and tab["state"].get("query"):
                parts.append((f"search “{tab['state']['query']}”", None))
        else:
            parts.append((PAGE_NAMES[tab["page"]], None))
        for index, (text, command) in enumerate(parts):
            if index:
                ttk.Label(self.crumbs, text="›", style="Hint.TLabel").pack(side="left", padx=5)
            clickable = command is not None and index < len(parts) - 1
            label = ttk.Label(self.crumbs, text=text, style="Link.TLabel" if clickable else "TLabel", cursor="hand2" if clickable else "")
            label.pack(side="left")
            if clickable:
                label.bind("<Button-1>", lambda event, c=command: c())
        self._loading = True
        self.search_var.set(tab["state"].get("query", "") if tab["page"] == "items" else "")
        self._loading = False

    # --------------------------------------------------------------- shortcuts
    def bind_shortcuts(self):
        keys = {"n": self.add_item, "f": self.focus_search, "l": self.focus_search, "z": self.undo, "y": self.redo, "Z": self.redo,
                "t": lambda: self.new_tab(), "w": lambda: self.close_tab(), "T": self.reopen_tab,
                "bracketleft": self.go_back, "bracketright": self.go_forward, "comma": lambda: self.open_or_focus("settings"),
                "L": self.lock}
        mods = ("Control", "Command") if IS_MAC else ("Control",)
        for key, command in keys.items():
            for mod in mods:
                self.root.bind(f"<{mod}-{key}>", lambda event, c=command, k=key: self.shortcut(k, c))
        for mod in mods:
            for number in range(1, 10):
                self.root.bind(f"<{mod}-Key-{number}>", lambda event, n=number: self.shortcut(f"#{n}", lambda: self.activate(min(n, len(self.tabs)) - 1)))
        self.root.bind("<Control-Tab>", lambda event: self.shortcut("tab", lambda: self.cycle_tab(1)))
        self.root.bind("<Control-Shift-Tab>", lambda event: self.shortcut("stab", lambda: self.cycle_tab(-1)))
        if not IS_MAC:
            self.root.bind("<Control-q>", lambda event: self.close())
            self.root.bind("<Alt-Left>", lambda event: self.go_back())
            self.root.bind("<Alt-Right>", lambda event: self.go_forward())

    def shortcut(self, key, command):
        """Run a keyboard shortcut once (macOS can deliver both the menu and the key binding)."""
        moment = time.monotonic()
        if moment - self._last_shortcut.get(key, 0) < 0.25:
            return "break"
        self._last_shortcut[key] = moment
        try:
            focused = self.root.focus_get()
        except (KeyError, tk.TclError):
            focused = None
        if isinstance(focused, tk.Text) and key in "zZy":
            return None   # let a text box undo its own typing
        if self.busy():
            return "break"
        command()
        return "break"

    # ------------------------------------------------------------------- layout
    def build_ui(self):
        self.font = apply_theme(self.root, self.settings)
        self.shell = ttk.Frame(self.root)
        self.shell.pack(fill="both", expand=True)
        self.build_menu()
        self.build_header()
        footer = ttk.Frame(self.shell, padding=(22, 6, 22, 10))
        footer.pack(side="bottom", fill="x")
        self.status = tk.StringVar(value=f"Signed in as {self.session.username} · your inventory is decrypted in memory only" if self.session
                                   else f"Unencrypted database: {self.inventory.path}")
        ttk.Label(footer, textvariable=self.status, style="Hint.TLabel").pack(side="left", fill="x", expand=True)
        self.undo_hint = tk.StringVar()
        ttk.Label(footer, textvariable=self.undo_hint, style="Hint.TLabel").pack(side="right")
        chrome = ttk.Frame(self.shell, style="Strip.TFrame")
        chrome.pack(side="top" if self.settings["tabbar"] == "Top" else "bottom", fill="x")
        self.build_chrome(chrome)
        body = ttk.Panedwindow(self.shell, orient="horizontal")
        body.pack(fill="both", expand=True, padx=16, pady=(10, 4))
        side = self.settings["sidebar"]
        sidebar = self.build_sidebar(body) if side != "Hidden" else None
        main = ttk.Frame(body, padding=(14 if side == "Left" else 0, 0, 14 if side == "Right" else 0, 0))
        if sidebar is not None and side == "Left":
            body.add(sidebar, weight=0)
        body.add(main, weight=1)
        if sidebar is not None and side == "Right":
            body.add(sidebar, weight=0)
        self.stat_values = {}
        if self.settings["show_stats"]:
            self.build_stats(main)
        stack = ttk.Frame(main)
        stack.pack(fill="both", expand=True, pady=(12 if self.settings["show_stats"] else 0, 0))
        stack.rowconfigure(0, weight=1)
        stack.columnconfigure(0, weight=1)
        self.pages = {}
        for page in PAGE_NAMES:
            frame = ttk.Frame(stack, padding=(4, 4))
            frame.grid(row=0, column=0, sticky="nsew")
            self.pages[page] = frame
        self.build_items_page(self.pages["items"])
        self.build_item_page(self.pages["item"])
        self.build_locations_page(self.pages["locations"])
        self.build_activity_page(self.pages["activity"])
        self.build_settings_page(self.pages["settings"])
        self.build_assistant_page(self.pages["assistant"])
        register_drop(self.pages["items"], self.dropped_on_items)
        self.show_tab()

    def build_menu(self):
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="Add item…", accelerator=accel("N"), command=self.add_item)
        file_menu.add_command(label="Add items from photos…", command=self.add_items_from_photos)
        file_menu.add_command(label="Add location…", command=self.add_location)
        file_menu.add_separator()
        file_menu.add_command(label="New tab", accelerator=accel("T"), command=lambda: self.new_tab())
        file_menu.add_command(label="Close tab", accelerator=accel("W"), command=lambda: self.close_tab())
        file_menu.add_command(label="Reopen closed tab", accelerator=accel("T", shift=True), command=self.reopen_tab)
        file_menu.add_separator()
        file_menu.add_command(label="Back up…", command=self.backup)
        file_menu.add_command(label="Export shown items (CSV)…", command=self.export_csv)
        if self.session:
            file_menu.add_separator()
            file_menu.add_command(label="Lock", accelerator=accel("L", shift=True), command=self.lock)
            file_menu.add_command(label="Sign out", command=self.sign_out)
        if not IS_MAC:
            file_menu.add_separator()
            file_menu.add_command(label="Quit", accelerator="Ctrl+Q", command=self.close)
        menubar.add_cascade(label="File", menu=file_menu)
        self.edit_menu = tk.Menu(menubar, tearoff=False)
        self.edit_menu.add_command(label="Undo", accelerator=accel("Z"), command=self.undo)
        self.edit_menu.add_command(label="Redo", accelerator=accel("Z", shift=True) if IS_MAC else "Ctrl+Y", command=self.redo)
        self.edit_menu.add_separator()
        self.edit_menu.add_command(label="Find…", accelerator=accel("F"), command=self.focus_search)
        self.edit_menu.add_command(label="Edit selected item…", command=self.edit_item)
        self.edit_menu.add_command(label="Set quantity…", command=self.set_quantity_prompt)
        self.edit_menu.add_command(label="Delete selected item", command=self.delete_item)
        menubar.add_cascade(label="Edit", menu=self.edit_menu)
        view_menu = tk.Menu(menubar, tearoff=False)
        view_menu.add_command(label="Back", accelerator=accel("[") if IS_MAC else "Alt+Left", command=self.go_back)
        view_menu.add_command(label="Forward", accelerator=accel("]") if IS_MAC else "Alt+Right", command=self.go_forward)
        view_menu.add_separator()
        for page in ("locations", "activity", "assistant", "settings"):
            view_menu.add_command(label=PAGE_NAMES[page], command=lambda p=page: self.open_or_focus(p))
        view_menu.add_separator()
        self.theme_choice = tk.StringVar(value=self.settings["theme"])
        for key, theme in THEMES.items():
            view_menu.add_radiobutton(label=f"{theme['name']} style", value=key, variable=self.theme_choice,
                                      command=lambda: self.change_setting("theme", self.theme_choice.get()))
        menubar.add_cascade(label="View", menu=view_menu)
        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="About Homebase", command=self.about)
        menubar.add_cascade(label="Help", menu=help_menu)
        self.root.configure(menu=menubar)

    def build_header(self):
        band = T["header_band"]
        bg = band or T["bg"]
        frame = tk.Frame(self.shell, bg=bg, padx=20, pady=12 if band else 14)
        frame.pack(fill="x", padx=8 if T["bevel"] else 0, pady=(8 if T["bevel"] else 0, 0))
        left = tk.Frame(frame, bg=bg)
        left.pack(side="left")
        tk.Label(left, text=T["title_prefix"] + T["title"], bg=bg, fg=T["header_fg"] if band else T["accent"],
                 font=self.font(T["title_size"], "bold", title=True)).pack(anchor="w")
        tk.Label(left, text=T["subtitle"], bg=bg, fg=T["header_sub_fg"] if band else T["muted"], font=self.font(9, display=T["bevel"])).pack(anchor="w")
        ttk.Button(frame, text="+ Add item", style="Primary.TButton", command=self.add_item).pack(side="right", padx=(8, 0))
        ttk.Button(frame, text="✦ Assistant", command=lambda: self.open_or_focus("assistant")).pack(side="right", padx=4)
        if self.session:
            ttk.Button(frame, text="Lock", command=self.lock).pack(side="right", padx=4)
        self.redo_button = ttk.Button(frame, text="↷ Redo", command=self.redo, state="disabled")
        self.redo_button.pack(side="right", padx=(4, 8))
        self.undo_button = ttk.Button(frame, text="↶ Undo", command=self.undo, state="disabled")
        self.undo_button.pack(side="right", padx=4)
        if self.session:
            tk.Label(frame, text=f"● {self.session.username}", bg=bg, fg=T["header_sub_fg"] if band else T["muted"],
                     font=self.font(10, "bold")).pack(side="right", padx=12)
        if T["stripe"]:
            tk.Frame(self.shell, bg=T["stripe"], height=3).pack(fill="x", padx=8 if T["bevel"] else 0)

    def build_chrome(self, chrome):
        """The browser-like part: a tab strip, then back/forward, the path, and search."""
        strip = tk.Frame(chrome, bg=T["tab_strip"], padx=12)
        nav = ttk.Frame(chrome, padding=(16, 8, 16, 6))
        for part in ((strip, nav) if self.settings["tabbar"] == "Top" else (nav, strip)):
            part.pack(fill="x")
        self.strip_tabs = tk.Frame(strip, bg=T["tab_strip"])
        self.strip_tabs.pack(side="left", fill="x", expand=True)
        self.back_button = ttk.Button(nav, text="◀", width=3, style="Small.TButton", command=self.go_back)
        self.back_button.pack(side="left")
        self.fwd_button = ttk.Button(nav, text="▶", width=3, style="Small.TButton", command=self.go_forward)
        self.fwd_button.pack(side="left", padx=(4, 12))
        self.crumbs = ttk.Frame(nav)
        self.crumbs.pack(side="left", fill="x", expand=True)
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(nav, textvariable=self.search_var, width=34)
        self.search_entry.pack(side="right")
        ttk.Label(nav, text="Search", style="Hint.TLabel").pack(side="right", padx=(0, 8))
        self.search_var.trace_add("write", lambda *_: self.on_search_typed())
        self.search_entry.bind("<Return>", lambda event: self.on_search_typed(force=True))

    def build_sidebar(self, parent):
        sidebar = ttk.Frame(parent, style="Card.TFrame", padding=14, width=230)
        ttk.Label(sidebar, text="MY LOCATIONS", style="Eyebrow.TLabel", background=T["surface"]).pack(anchor="w", pady=(3, 10))
        ttk.Button(sidebar, text="All items", command=lambda: self.navigate("items")).pack(fill="x", pady=(0, 8))
        ttk.Button(sidebar, text="Edit selected location", command=self.edit_location).pack(side="bottom", fill="x", pady=3)
        ttk.Button(sidebar, text="+ Add location", command=self.add_location).pack(side="bottom", fill="x", pady=3)
        ttk.Label(sidebar, text=f"Click: open here. {'⌘' if IS_MAC else 'Ctrl'}-click or\nmiddle-click: open in a new tab.",
                  style="Hint.TLabel", background=T["surface"]).pack(side="bottom", anchor="w", pady=10)
        area = ttk.Frame(sidebar)
        area.pack(fill="both", expand=True)
        self.location_tree = ttk.Treeview(area, show="tree", selectmode="browse", height=6)
        self.location_tree.column("#0", width=200, minwidth=150, stretch=True)
        scroll = ttk.Scrollbar(area, orient="vertical", command=self.location_tree.yview)
        self.location_tree.configure(yscrollcommand=scroll.set)
        self.location_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.location_tree.bind("<ButtonRelease-1>", lambda event: self.location_clicked(event, new_tab=False))
        self.location_tree.bind(NEW_TAB_CLICK, lambda event: self.location_clicked(event, new_tab=True))
        self.location_tree.bind(MIDDLE_BUTTON, lambda event: self.location_clicked(event, new_tab=True))
        for button in CONTEXT_BUTTONS:
            self.location_tree.bind(button, lambda event: self.location_menu(event, self.location_tree))
        return sidebar

    def build_stats(self, parent):
        frame = ttk.Frame(parent)
        frame.pack(fill="x")
        for number, (key, title) in enumerate((("items", "UNIQUE ITEMS"), ("units", "TOTAL UNITS"), ("locations", "LOCATIONS"), ("low", "LOW STOCK"))):
            frame.columnconfigure(number, weight=1, uniform="stats")
            card = tk.Frame(frame, bg=T["field"] if T["bevel"] else T["surface"], highlightbackground=T["accent"] if T["is_dark"] else T["border"],
                            highlightthickness=T["card_highlight"], relief=T["card_relief"], bd=T["card_bd"], padx=17, pady=10, cursor="hand2" if key == "low" else "")
            card.grid(row=0, column=number, sticky="ew", padx=(0 if number == 0 else 10, 0))
            card_bg = card.cget("bg")
            tk.Label(card, text=title, bg=card_bg, fg=T["muted"], font=self.font(8, "bold")).pack(anchor="w")
            self.stat_values[key] = tk.StringVar(value="0")
            value = tk.Label(card, textvariable=self.stat_values[key], bg=card_bg, fg=T["stat_warning"] if key == "low" else T["ink"],
                             font=self.font(24, "bold", display=True))
            value.pack(anchor="w", pady=(2, 0))
            if key == "low":
                for widget in (card, value):
                    widget.bind("<Button-1>", lambda event: self.navigate("items", {"low_only": True}))

    # ---------------------------------------------------------------- items page
    def build_items_page(self, page):
        filters = ttk.Frame(page)
        filters.pack(fill="x", pady=(0, 8))
        self.category = tk.StringVar(value="All categories")
        self.tag = tk.StringVar(value="All tags")
        self.low_only = tk.BooleanVar(value=False)
        self.sort = tk.StringVar(value=self.settings["default_sort"])
        category_combo = ttk.Combobox(filters, textvariable=self.category, values=("All categories", *CATEGORIES), state="readonly", width=16)
        category_combo.pack(side="left", padx=(0, 8))
        self.tag_combo = ttk.Combobox(filters, textvariable=self.tag, values=("All tags",), state="readonly", width=16)
        self.tag_combo.pack(side="left", padx=(0, 8))
        ttk.Checkbutton(filters, text="Low stock", variable=self.low_only, command=self.filters_changed).pack(side="left", padx=7)
        ttk.Button(filters, text="Clear filters", style="Small.TButton", command=self.clear_filters).pack(side="left", padx=8)
        sort_combo = ttk.Combobox(filters, textvariable=self.sort, values=SORTS, state="readonly", width=19)
        sort_combo.pack(side="right")
        ttk.Label(filters, text="Sort", style="Hint.TLabel").pack(side="right", padx=6)
        for combo in (category_combo, self.tag_combo, sort_combo):
            combo.bind("<<ComboboxSelected>>", lambda event: self.filters_changed())
        actions = ttk.Frame(page)
        actions.pack(side="bottom", fill="x", pady=(10, 0))
        self.results = tk.StringVar()
        ttk.Label(actions, textvariable=self.results, style="Hint.TLabel").pack(side="left")
        ttk.Button(actions, text="Export shown items…", style="Small.TButton", command=self.export_csv).pack(side="right")
        ttk.Button(actions, text="Add from photos…", style="Small.TButton", command=self.add_items_from_photos).pack(side="right", padx=8)
        content = ttk.Panedwindow(page, orient="horizontal")
        content.pack(fill="both", expand=True)
        table_frame = ttk.Frame(content)
        detail = self.build_detail(content) if self.settings["detail"] != "Hidden" else None
        self.has_detail = detail is not None
        if detail is not None and self.settings["detail"] == "Left":
            content.add(detail, weight=0)
        content.add(table_frame, weight=1)
        if detail is not None and self.settings["detail"] == "Right":
            content.add(detail, weight=0)
        columns = ("name", "category", "quantity", "location", "tags", "value")
        self.item_tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        for key, title, width in zip(columns, ("Item name", "Category", "Qty", "Location", "Tags", "Total value"), (220, 105, 55, 220, 150, 95)):
            self.item_tree.heading(key, text=title.upper() if T["uppercase"] else title)
            self.item_tree.column(key, width=width, minwidth=50, stretch=key in ("name", "location"), anchor="e" if key in ("quantity", "value") else "w")
        vertical = ttk.Scrollbar(table_frame, orient="vertical", command=self.item_tree.yview)
        horizontal = ttk.Scrollbar(table_frame, orient="horizontal", command=self.item_tree.xview)
        self.item_tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.item_tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.item_tree.tag_configure("alternate", background=T["row_alt"])
        self.item_tree.tag_configure("low", foreground=T["warning"])
        self.item_tree.bind("<<TreeviewSelect>>", self.on_item_select)
        self.item_tree.bind("<Double-1>", lambda event: self.open_item(new_tab=self.settings["open_items_in"] == "New tab"))
        self.item_tree.bind(NEW_TAB_CLICK, self.item_clicked_new_tab)
        self.item_tree.bind(MIDDLE_BUTTON, self.item_clicked_new_tab)
        self.item_tree.bind("<Return>", lambda event: self.edit_item())
        self.item_tree.bind("<Delete>", lambda event: self.delete_item())
        self.item_tree.bind("<BackSpace>", lambda event: self.delete_item())
        self.item_tree.bind("<plus>", lambda event: self.adjust_quantity(1))
        self.item_tree.bind("<equal>", lambda event: self.adjust_quantity(1))
        self.item_tree.bind("<minus>", lambda event: self.adjust_quantity(-1))
        for button in CONTEXT_BUTTONS:
            self.item_tree.bind(button, self.item_menu)
        register_drop(self.item_tree, self.dropped_on_items)

    def build_detail(self, parent):
        outer, frame = scroll_frame(parent, width=290, padding=(14, 0, 14, 8))
        self.photo_canvas = tk.Canvas(frame, width=255, height=175, bg=T["photo_bg"], highlightthickness=0,
                                      relief="sunken" if T["bevel"] else "flat", bd=2 if T["bevel"] else 0, cursor="hand2")
        self.photo_canvas.pack(fill="x", pady=(0, 12))
        self.photo_canvas.bind("<Button-1>", lambda event: self.change_photo())
        register_drop(self.photo_canvas, lambda paths: self.dropped_on_item(paths, self.selected_id))
        self.detail_name = tk.StringVar(value="Select an item")
        ttk.Label(frame, textvariable=self.detail_name, font=self.font(16, "bold", display=True), wraplength=255).pack(anchor="w", pady=(0, 6))
        self.detail_category = tk.StringVar()
        ttk.Label(frame, textvariable=self.detail_category, style="Hint.TLabel").pack(anchor="w")
        quantity = ttk.Frame(frame)
        quantity.pack(fill="x", pady=12)
        self.minus_button = ttk.Button(quantity, text="−", width=3, command=lambda: self.adjust_quantity(-1))
        self.minus_button.pack(side="left")
        self.detail_qty = tk.StringVar(value="—")
        qty_label = ttk.Label(quantity, textvariable=self.detail_qty, font=self.font(14, "bold"), cursor="hand2")
        qty_label.pack(side="left", padx=12)
        qty_label.bind("<Button-1>", lambda event: self.set_quantity_prompt())
        self.plus_button = ttk.Button(quantity, text="+", width=3, command=lambda: self.adjust_quantity(1))
        self.plus_button.pack(side="left")
        self.detail_values = tk.StringVar()
        ttk.Label(frame, textvariable=self.detail_values, style="Hint.TLabel", wraplength=255).pack(anchor="w", pady=(0, 12))
        ttk.Label(frame, text="LOCATION", style="Eyebrow.TLabel").pack(anchor="w", pady=(2, 4))
        self.detail_location = tk.StringVar()
        ttk.Label(frame, textvariable=self.detail_location, wraplength=255, style="Accent.TLabel").pack(anchor="w", pady=(0, 10))
        ttk.Label(frame, text="TAGS", style="Eyebrow.TLabel").pack(anchor="w", pady=(2, 4))
        self.detail_tags = tk.StringVar()
        ttk.Label(frame, textvariable=self.detail_tags, wraplength=255, style="Hint.TLabel").pack(anchor="w", pady=(0, 10))
        ttk.Label(frame, text="NOTES", style="Eyebrow.TLabel").pack(anchor="w", pady=(2, 4))
        self.detail_notes = tk.Text(frame, height=4, width=28, font=self.font(9), bg=T["bg"], fg=T["muted"], wrap="word", bd=0, highlightthickness=0)
        self.detail_notes.pack(fill="both", expand=True, pady=(0, 8))
        self.detail_notes.configure(state="disabled")
        self.open_button = ttk.Button(frame, text="Open in new tab", command=lambda: self.open_item(new_tab=True))
        self.open_button.pack(fill="x", pady=3)
        self.edit_button = ttk.Button(frame, text="Edit / move item", command=self.edit_item, style="Primary.TButton")
        self.edit_button.pack(fill="x", pady=3)
        self.delete_button = ttk.Button(frame, text="Delete item…", command=self.delete_item, style="Danger.TButton")
        self.delete_button.pack(fill="x", pady=3)
        return outer

    # ---------------------------------------------------------- item page (tab)
    def build_item_page(self, page):
        top = ttk.Frame(page)
        top.pack(fill="both", expand=True)
        left = ttk.Frame(top)
        left.pack(side="left", fill="y", padx=(0, 24))
        self.page_photo = tk.Canvas(left, width=380, height=300, bg=T["photo_bg"], highlightthickness=1, highlightbackground=T["border"], cursor="hand2")
        self.page_photo.pack()
        self.page_photo.bind("<Button-1>", lambda event: self.change_photo(self.tab["state"].get("item_id")))
        register_drop(self.page_photo, lambda paths: self.dropped_on_item(paths, self.tab["state"].get("item_id")))
        ttk.Label(left, text="Click or drop a photo to replace it.", style="Hint.TLabel").pack(anchor="w", pady=(6, 0))
        right = ttk.Frame(top)
        right.pack(side="left", fill="both", expand=True)
        self.page_name = tk.StringVar()
        ttk.Label(right, textvariable=self.page_name, font=self.font(24, "bold", display=True), wraplength=520).pack(anchor="w")
        self.page_category = tk.StringVar()
        ttk.Label(right, textvariable=self.page_category, style="Hint.TLabel").pack(anchor="w", pady=(2, 12))
        qty = ttk.Frame(right)
        qty.pack(anchor="w", pady=(0, 12))
        ttk.Button(qty, text="−", width=3, command=lambda: self.adjust_quantity(-1, self.tab["state"].get("item_id"))).pack(side="left")
        self.page_qty = tk.StringVar()
        ttk.Label(qty, textvariable=self.page_qty, font=self.font(18, "bold", display=True)).pack(side="left", padx=14)
        ttk.Button(qty, text="+", width=3, command=lambda: self.adjust_quantity(1, self.tab["state"].get("item_id"))).pack(side="left")
        ttk.Button(qty, text="Set…", style="Small.TButton", command=lambda: self.set_quantity_prompt(self.tab["state"].get("item_id"))).pack(side="left", padx=12)
        self.page_fields = {}
        grid = ttk.Frame(right)
        grid.pack(fill="x")
        for row, (key, title) in enumerate((("location", "LOCATION"), ("value", "VALUE"), ("tags", "TAGS"), ("dates", "ADDED / UPDATED"), ("notes", "NOTES"))):
            ttk.Label(grid, text=title, style="Eyebrow.TLabel").grid(row=row, column=0, sticky="nw", pady=6, padx=(0, 18))
            self.page_fields[key] = tk.StringVar()
            label = ttk.Label(grid, textvariable=self.page_fields[key], wraplength=520, justify="left",
                              style="Link.TLabel" if key == "location" else "TLabel", cursor="hand2" if key == "location" else "")
            label.grid(row=row, column=1, sticky="w", pady=6)
            if key == "location":
                label.bind("<Button-1>", lambda event: self.open_item_location())
        buttons = ttk.Frame(right)
        buttons.pack(anchor="w", pady=(18, 0))
        ttk.Button(buttons, text="Edit / move…", style="Primary.TButton", command=lambda: self.edit_item(self.tab["state"].get("item_id"))).pack(side="left")
        ttk.Button(buttons, text="Delete…", style="Danger.TButton", command=lambda: self.delete_item(self.tab["state"].get("item_id"))).pack(side="left", padx=8)
        ttk.Button(buttons, text="Show in its location", command=self.open_item_location).pack(side="left")

    def show_item_page(self):
        item_id = self.tab["state"].get("item_id")
        try:
            item = self.inventory.get_item(item_id)
        except InventoryError:
            self.page_name.set("This item was deleted")
            self.page_category.set(f"Use Undo ({MOD}Z) to bring it back, or close this tab.")
            self.page_qty.set("—")
            for var in self.page_fields.values():
                var.set("")
            paint_photo(self.page_photo, b"", font=self.font(9))
            return
        self.selected_id = item_id
        self.page_name.set(item["name"])
        self.page_category.set(item["category"] + ("  ·  LOW STOCK" if item["quantity"] <= item["low_stock"] else ""))
        self.page_qty.set(f"{item['quantity']} units")
        self.page_fields["location"].set(self.inventory.path_text(item["location_id"]))
        self.page_fields["value"].set(f"{currency(item['value'])} each  ·  {currency(item['value'] * item['quantity'])} total  ·  low stock at {item['low_stock']}")
        self.page_fields["tags"].set(", ".join(item["tags"]) or "No tags")
        fmt = lambda stamp: datetime.fromisoformat(stamp).astimezone().strftime("%b %d, %Y %I:%M %p")
        self.page_fields["dates"].set(f"{fmt(item['created_at'])}  /  {fmt(item['updated_at'])}")
        self.page_fields["notes"].set(item["notes"] or "No notes added.")
        paint_photo(self.page_photo, item["photo"], title="Click or drop a photo", font=self.font(9))

    def open_item_location(self):
        try:
            item = self.inventory.get_item(self.tab["state"].get("item_id"))
        except InventoryError:
            return
        self.navigate("items", {"location_id": item["location_id"], "selected_id": item["id"]})

    # -------------------------------------------------------- other pages
    def build_locations_page(self, page):
        ttk.Label(page, text="Organize from your house to your smallest drawer. Right-click for more.", style="Hint.TLabel").pack(anchor="w", pady=(0, 10))
        controls = ttk.Frame(page)
        controls.pack(side="bottom", fill="x", pady=(12, 0))
        area = ttk.Frame(page)
        area.pack(fill="both", expand=True)
        self.manage_tree = ttk.Treeview(area, columns=("type", "path", "count"), show="tree headings", selectmode="browse")
        self.manage_tree.heading("#0", text="Location")
        self.manage_tree.column("#0", width=240)
        for key, text, width in (("type", "Type", 90), ("path", "Full path", 450), ("count", "Items inside", 100)):
            self.manage_tree.heading(key, text=text)
            self.manage_tree.column(key, width=width)
        scroll = ttk.Scrollbar(area, orient="vertical", command=self.manage_tree.yview)
        self.manage_tree.configure(yscrollcommand=scroll.set)
        self.manage_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.manage_tree.bind("<Double-1>", lambda event: self.open_location(self.location_selection(manage=True)))
        self.manage_tree.bind("<Delete>", lambda event: self.delete_location())
        self.manage_tree.bind("<BackSpace>", lambda event: self.delete_location())
        for button in CONTEXT_BUTTONS:
            self.manage_tree.bind(button, lambda event: self.location_menu(event, self.manage_tree))
        ttk.Button(controls, text="+ Add location", style="Primary.TButton", command=lambda: self.add_location(manage=True)).pack(side="left", padx=(0, 8))
        ttk.Button(controls, text="Edit / move selected", command=lambda: self.edit_location(manage=True)).pack(side="left", padx=(0, 8))
        ttk.Button(controls, text="Delete empty location…", command=self.delete_location).pack(side="left")
        ttk.Label(controls, text="Double-click a location to open it in this tab.", style="Hint.TLabel").pack(side="right")

    def build_activity_page(self, page):
        ttk.Label(page, text="The most recent 200 changes, kept inside your encrypted inventory." if self.session
                  else "The most recent 200 changes.", style="Hint.TLabel").pack(side="bottom", anchor="w", pady=(10, 0))
        frame = ttk.Frame(page)
        frame.pack(fill="both", expand=True)
        self.activity_tree = ttk.Treeview(frame, columns=("when", "what"), show="headings", selectmode="browse")
        self.activity_tree.heading("when", text="Date and time")
        self.activity_tree.heading("what", text="Activity")
        self.activity_tree.column("when", width=175, stretch=False)
        self.activity_tree.column("what", width=650)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.activity_tree.yview)
        self.activity_tree.configure(yscrollcommand=scroll.set)
        self.activity_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    # ------------------------------------------------------------- settings page
    def build_settings_page(self, page):
        outer, body = scroll_frame(page, width=900, padding=(18, 12, 22, 22))
        outer.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        row = 0

        def section(title, column=0, span=2):
            frame = ttk.Labelframe(body, text=f" {title} ", padding=16)
            frame.grid(row=row, column=column, columnspan=span, sticky="nsew", pady=(0, 16), padx=(0 if column == 0 else 8, 8 if span == 1 and column == 0 else 0))
            return frame

        def choice(parent, label, key, options, width=16, hint=""):
            line = ttk.Frame(parent)
            line.pack(fill="x", pady=4)
            ttk.Label(line, text=label, width=22).pack(side="left")
            var = tk.StringVar(value=self.settings[key])
            combo = ttk.Combobox(line, textvariable=var, values=list(options), state="readonly", width=width)
            combo.pack(side="left")
            combo.bind("<<ComboboxSelected>>", lambda event: self.change_setting(key, var.get()))
            if hint:
                ttk.Label(line, text="  " + hint, style="Hint.TLabel").pack(side="left")
            return var

        def toggle(parent, label, key, hint=""):
            var = tk.BooleanVar(value=bool(self.settings[key]))
            ttk.Checkbutton(parent, text=label, variable=var, command=lambda: self.change_setting(key, var.get())).pack(anchor="w", pady=(4, 0))
            if hint:
                ttk.Label(parent, text=hint, style="Hint.TLabel", wraplength=380).pack(anchor="w", padx=(24, 0))
            return var

        # Account
        account = section("Account and security")
        if self.session:
            two = "on" if self.session.totp_enabled else "off"
            ttk.Label(account, text=f"Signed in as {self.session.username}", font=self.font(13, "bold", display=True)).pack(anchor="w")
            ttk.Label(account, text=f"Your inventory is encrypted with AES-256-GCM. Two-factor sign-in is {two}.\n"
                                    f"Encrypted file: {self.session.vault.path}", style="Hint.TLabel", wraplength=820, justify="left").pack(anchor="w", pady=(2, 10))
            buttons = ttk.Frame(account)
            buttons.pack(fill="x")
            ttk.Button(buttons, text="Turn off two-factor…" if self.session.totp_enabled else "Turn on two-factor…",
                       style="Primary.TButton" if not self.session.totp_enabled else "TButton", command=self.toggle_two_factor).pack(side="left", padx=(0, 8))
            ttk.Button(buttons, text="Change password…", command=self.change_password).pack(side="left", padx=(0, 8))
            ttk.Button(buttons, text="Change recovery phrase…", command=self.change_phrase).pack(side="left", padx=(0, 8))
            ttk.Button(buttons, text="Lock now", command=self.lock).pack(side="left", padx=(0, 8))
            ttk.Button(buttons, text="Sign out", command=self.sign_out).pack(side="left", padx=(0, 8))
            ttk.Button(buttons, text="Delete account…", style="Danger.TButton", command=self.delete_account).pack(side="right")
            lock_line = ttk.Frame(account)
            lock_line.pack(fill="x", pady=(12, 0))
            ttk.Label(lock_line, text="Lock automatically after", width=22).pack(side="left")
            names = {v: k for k, v in LOCK_CHOICES.items()}
            lock_var = tk.StringVar(value=names.get(self.settings["auto_lock_minutes"], "10 minutes"))
            lock_combo = ttk.Combobox(lock_line, textvariable=lock_var, values=list(LOCK_CHOICES), state="readonly", width=16)
            lock_combo.pack(side="left")
            lock_combo.bind("<<ComboboxSelected>>", lambda event: self.change_setting("auto_lock_minutes", LOCK_CHOICES[lock_var.get()]))
            ttk.Label(lock_line, text="  of no mouse or keyboard use", style="Hint.TLabel").pack(side="left")
        else:
            ttk.Label(account, text="Developer mode: this database is NOT encrypted and has no sign-in. "
                                    "Run Homebase without --db to use accounts and encryption.", style="Error.TLabel", wraplength=820).pack(anchor="w")
        row += 1

        # Appearance
        look = section("Appearance")
        ttk.Label(look, text="Style", style="Eyebrow.TLabel").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))
        self.theme_var = tk.StringVar(value=self.settings["theme"])
        for index, (key, theme) in enumerate(THEMES.items()):
            look.columnconfigure(index, weight=1, uniform="theme")
            chosen = key == self.settings["theme"]
            card = tk.Frame(look, bg=T["surface"], highlightthickness=3 if chosen else 1,
                            highlightbackground=T["accent"] if chosen else T["dark"], padx=8, pady=8, cursor="hand2")
            card.grid(row=1, column=index, sticky="nsew", padx=(0 if index == 0 else 8, 0))
            preview = tk.Canvas(card, width=190, height=112, highlightthickness=0, bg=theme["bg"], cursor="hand2")
            preview.pack(fill="x")
            preview.bind("<Configure>", lambda event, c=preview, t=theme: self.draw_theme_preview(c, t, event.width))
            ttk.Radiobutton(card, text=theme["name"], value=key, variable=self.theme_var, style="Card.TRadiobutton",
                            command=lambda: self.change_setting("theme", self.theme_var.get())).pack(anchor="w", pady=(8, 2))
            tk.Label(card, text=theme["description"], bg=T["surface"], fg=T["muted"], font=self.font(9), wraplength=180, justify="left").pack(anchor="w")
            for widget in (card, preview):
                widget.bind("<Button-1>", lambda event, k=key: self.change_setting("theme", k))
        fonts = ttk.Frame(look)
        fonts.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(16, 0))
        families = ["Theme default", *font_families(self.root)]
        for col, (label, key) in enumerate((("Text font", "font_body"), ("Heading font", "font_display"))):
            box = ttk.Frame(fonts)
            box.pack(side="left", padx=(0, 24))
            ttk.Label(box, text=label, style="Eyebrow.TLabel").pack(anchor="w")
            var = tk.StringVar(value=self.settings[key] or "Theme default")
            combo = ttk.Combobox(box, textvariable=var, values=families, width=24)
            combo.pack(anchor="w", pady=(4, 0))
            combo.bind("<<ComboboxSelected>>", lambda event, v=var, k=key: self.change_setting(k, "" if v.get() == "Theme default" else v.get()))
            combo.bind("<Return>", lambda event, v=var, k=key: self.change_setting(k, "" if v.get() in ("", "Theme default") else v.get()))
        size_box = ttk.Frame(fonts)
        size_box.pack(side="left", padx=(0, 24))
        ttk.Label(size_box, text="Text size", style="Eyebrow.TLabel").pack(anchor="w")
        size_line = ttk.Frame(size_box)
        size_line.pack(anchor="w", pady=(4, 0))
        self.size_var = tk.IntVar(value=int(self.settings["text_scale"]))
        size_label = ttk.Label(size_line, text=f"{self.size_var.get()}%", width=5)
        scale = ttk.Scale(size_line, from_=70, to=200, orient="horizontal", length=170,
                          command=lambda value: (self.size_var.set(int(float(value)) // 5 * 5), size_label.configure(text=f"{self.size_var.get()}%")))
        scale.set(self.size_var.get())
        scale.pack(side="left")
        size_label.pack(side="left", padx=6)
        scale.bind("<ButtonRelease-1>", lambda event: self.change_setting("text_scale", self.size_var.get()))
        accent_box = ttk.Frame(fonts)
        accent_box.pack(side="left")
        ttk.Label(accent_box, text="Accent color", style="Eyebrow.TLabel").pack(anchor="w")
        accent_line = ttk.Frame(accent_box)
        accent_line.pack(anchor="w", pady=(4, 0))
        tk.Label(accent_line, text="     ", bg=T["accent"], relief="solid", bd=1).pack(side="left", padx=(0, 8))
        ttk.Button(accent_line, text="Choose…", style="Small.TButton", command=self.choose_accent).pack(side="left")
        ttk.Button(accent_line, text="Reset", style="Small.TButton", command=lambda: self.change_setting("accent", "")).pack(side="left", padx=4)
        row += 1

        # Layout
        layout = section("Layout", column=0, span=1)
        choice(layout, "Locations sidebar", "sidebar", CHOICES["sidebar"])
        choice(layout, "Item details panel", "detail", CHOICES["detail"])
        choice(layout, "Tab bar", "tabbar", CHOICES["tabbar"])
        choice(layout, "Spacing", "density", CHOICES["density"])
        choice(layout, "Double-click opens item in", "open_items_in", CHOICES["open_items_in"])
        toggle(layout, "Show the summary cards", "show_stats")
        toggle(layout, "Reopen my tabs when I sign in", "restore_tabs")
        behavior = section("Behavior", column=1, span=1)
        toggle(behavior, "Ask before deleting an item", "confirm_delete", "Deletes can always be undone.")
        choice(behavior, "Default sort", "default_sort", SORTS, width=20)
        ttk.Label(behavior, text="Undo history (newest first)", style="Eyebrow.TLabel").pack(anchor="w", pady=(14, 4))
        self.history_list = tk.Listbox(behavior, height=6, activestyle="none", font=self.font(10), bg=T["field"], fg=T["ink"],
                                       selectbackground=T["select_bg"], selectforeground=T["select_fg"], highlightthickness=1,
                                       highlightbackground=T["border"], relief="sunken" if T["bevel"] else "flat", bd=2 if T["bevel"] else 0)
        self.history_list.pack(fill="both", expand=True)
        undo_row = ttk.Frame(behavior)
        undo_row.pack(fill="x", pady=(8, 0))
        self.settings_undo = ttk.Button(undo_row, text="↶ Undo", command=self.undo)
        self.settings_undo.pack(side="left")
        self.settings_redo = ttk.Button(undo_row, text="↷ Redo", command=self.redo)
        self.settings_redo.pack(side="left", padx=8)
        ttk.Button(undo_row, text="Clear history", command=self.clear_history).pack(side="right")
        row += 1

        # AI
        assistant = section("AI assistant (ChatGPT or Claude)")
        ttk.Label(assistant, text="Optional. Uses your own API key. Nothing is sent until you use an AI feature. Then only your item list "
                                  "(names, categories, quantities, locations, tags) goes out, plus a photo when you ask AI to read one.",
                  style="Hint.TLabel", wraplength=820, justify="left").pack(anchor="w", pady=(0, 10))
        if not self.session:
            ttk.Label(assistant, text="Warning: in developer mode, API keys are saved in an unencrypted file.", style="Error.TLabel").pack(anchor="w", pady=(0, 8))
        provider_line = ttk.Frame(assistant)
        provider_line.pack(fill="x")
        ttk.Label(provider_line, text="Use", width=22).pack(side="left")
        self.provider_var = tk.StringVar(value=self.settings["ai_provider"])
        for key, info in PROVIDERS.items():
            ttk.Radiobutton(provider_line, text=info["name"], value=key, variable=self.provider_var,
                            command=lambda: self.change_setting("ai_provider", self.provider_var.get())).pack(side="left", padx=(0, 16))
        self.key_vars = {}
        for key, info in PROVIDERS.items():
            line = ttk.Frame(assistant)
            line.pack(fill="x", pady=(8, 0))
            ttk.Label(line, text=f"{info['name']} key", width=22).pack(side="left")
            var = tk.StringVar(value=self.settings[f"ai_key_{key}"])
            entry = ttk.Entry(line, textvariable=var, show="•", width=40)
            entry.pack(side="left")
            self.key_vars[key] = var
            ttk.Button(line, text="Show", style="Small.TButton", command=lambda e=entry: e.configure(show="" if e.cget("show") else "•")).pack(side="left", padx=6)
            ttk.Label(line, text="Model", style="Hint.TLabel").pack(side="left", padx=(12, 6))
            model_var = tk.StringVar(value=self.settings[f"ai_model_{key}"] or info["default_model"])
            model = ttk.Combobox(line, textvariable=model_var, values=info["models"], width=20)
            model.pack(side="left")
            model.bind("<<ComboboxSelected>>", lambda event, v=model_var, k=key: self.change_setting(f"ai_model_{k}", v.get().strip(), rebuild=False))
            model.bind("<FocusOut>", lambda event, v=model_var, k=key: self.change_setting(f"ai_model_{k}", v.get().strip(), rebuild=False))
        key_buttons = ttk.Frame(assistant)
        key_buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(key_buttons, text="Save keys", style="Primary.TButton", command=self.save_ai_keys).pack(side="left")
        ttk.Button(key_buttons, text="Test connection", command=self.test_ai).pack(side="left", padx=8)
        self.ai_test_result = tk.StringVar()
        ttk.Label(key_buttons, textvariable=self.ai_test_result, style="Hint.TLabel").pack(side="left", padx=8)
        privacy = ttk.Frame(assistant)
        privacy.pack(fill="x", pady=(10, 0))
        toggle(privacy, "Also send item notes to the AI", "ai_send_notes")
        toggle(privacy, "Also send item values to the AI", "ai_send_values")
        toggle(privacy, "Fill in new items from their photo automatically", "ai_autofill_photos")
        row += 1

        # Data
        data = section("Your data")
        where = f"Encrypted file: {self.session.vault.path}" if self.session else f"Database: {self.inventory.path}"
        ttk.Label(data, text=where, style="Hint.TLabel", wraplength=820).pack(anchor="w", pady=(0, 10))
        buttons = ttk.Frame(data)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Back up…", style="Primary.TButton", command=self.backup).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Export all items (CSV)…", command=lambda: self.export_csv(everything=True)).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Import an old Homebase database…", command=self.import_database).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Show data folder", command=self.show_data_folder).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Reset to samples…", style="Danger.TButton", command=self.reset_samples).pack(side="right")
        ttk.Label(data, text="Backups are encrypted: they open only with your password or recovery phrase, on any computer "
                             "(Sign-in screen → Restore an account from a backup file)." if self.session else "",
                  style="Hint.TLabel", wraplength=820).pack(anchor="w", pady=(8, 0))
        row += 1

        keys = section("Keyboard shortcuts")
        shortcuts = ((f"{MOD}N", "Add item"), (f"{MOD}F", "Search"), (f"{MOD}Z", "Undo"), (f"{MOD}⇧Z" if IS_MAC else "Ctrl+Y", "Redo"),
                     (f"{MOD}T", "New tab"), (f"{MOD}W", "Close tab"), (f"{MOD}⇧T", "Reopen closed tab"), ("Ctrl+Tab", "Next tab"),
                     (f"{MOD}1–9", "Go to tab"), (f"{MOD}[  {MOD}]" if IS_MAC else "Alt+←  Alt+→", "Back / forward"),
                     ("Double-click", "Open item"), ("Return", "Edit item"), ("+  /  −", "Change quantity"), ("Delete", "Delete item"),
                     (f"{MOD}⇧L", "Lock"), (f"{MOD},", "Settings"))
        for index, (key, action) in enumerate(shortcuts):
            ttk.Label(keys, text=key, font=self.font(10, "bold", display=True)).grid(row=index // 4, column=(index % 4) * 2, sticky="w", padx=(0, 8), pady=3)
            ttk.Label(keys, text=action, style="Hint.TLabel").grid(row=index // 4, column=(index % 4) * 2 + 1, sticky="w", padx=(0, 26), pady=3)

    def draw_theme_preview(self, canvas, theme, width=190):
        """A tiny drawing of each style so you can see it before choosing it."""
        canvas.delete("all")
        w, h = max(150, width), 112
        canvas.create_rectangle(0, 0, w, h, fill=theme["bg"], outline=theme["dark"])
        band = theme["header_band"]
        if band:
            canvas.create_rectangle(4, 4, w - 4, 24, fill=band, outline="")
            canvas.create_text(10, 14, text=theme["title_prefix"] + theme["title"], anchor="w", fill=theme["header_fg"], font=("Helvetica", 9, "bold"))
        else:
            canvas.create_text(10, 15, text=theme["title"], anchor="w", fill=theme["accent"], font=("Helvetica", 11, "bold"))
        if theme["stripe"]:
            canvas.create_rectangle(4, 24, w - 4, 26, fill=theme["stripe"], outline="")
        canvas.create_rectangle(w - 58, 7, w - 10, 21, fill=theme["primary"], outline="#000000" if theme["bevel"] else "")
        canvas.create_text(w - 34, 14, text="+ Add", fill=theme["primary_text"], font=("Helvetica", 7, "bold"))
        y = 34
        for index, name in enumerate(("Headphones", "Cable drawer", "Flashlight")):
            fill = theme["select_bg"] if index == 0 else theme["row_alt"] if index % 2 else theme["field"]
            canvas.create_rectangle(8, y, w - 8, y + 20, fill=fill, outline=theme["dark"] if theme["bevel"] else "")
            canvas.create_text(14, y + 10, text=name.upper() if theme["uppercase"] else name, anchor="w",
                               fill=theme["select_fg"] if index == 0 else theme["ink"], font=("Helvetica", 8))
            y += 24

    def choose_accent(self):
        color = colorchooser.askcolor(color=T["accent"], parent=self.root, title="Choose an accent color")[1]
        if color:
            self.change_setting("accent", color)

    # ----------------------------------------------------------- assistant page
    def build_assistant_page(self, page):
        top = ttk.Frame(page)
        top.pack(fill="x", pady=(0, 8))
        self.ai_title = tk.StringVar()
        ttk.Label(top, textvariable=self.ai_title, font=self.font(15, "bold", display=True)).pack(side="left")
        ttk.Button(top, text="Settings…", style="Small.TButton", command=lambda: self.open_or_focus("settings")).pack(side="right")
        ttk.Button(top, text="Clear chat", style="Small.TButton", command=self.clear_chat).pack(side="right", padx=6)
        tools = ttk.Frame(page)
        tools.pack(fill="x", pady=(0, 8))
        ttk.Button(tools, text="✦ Suggest tags and better places", command=self.suggest_organization).pack(side="left")
        ttk.Button(tools, text="✦ Add items from photos…", command=self.add_items_from_photos).pack(side="left", padx=8)
        for prompt in ("What's running low?", "Summarize my inventory by room", "What's worth the most?"):
            ttk.Button(tools, text=prompt, style="Small.TButton", command=lambda p=prompt: self.send_chat(p)).pack(side="left", padx=4)
        entry_row = ttk.Frame(page)
        entry_row.pack(side="bottom", fill="x", pady=(8, 0))
        self.chat_var = tk.StringVar()
        entry = ttk.Entry(entry_row, textvariable=self.chat_var)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda event: self.send_chat())
        self.chat_send = ttk.Button(entry_row, text="Send", style="Primary.TButton", command=self.send_chat)
        self.chat_send.pack(side="left", padx=(8, 0))
        frame = ttk.Frame(page)
        frame.pack(fill="both", expand=True)
        self.chat_view = tk.Text(frame, wrap="word", font=self.font(11), bg=T["field"], fg=T["ink"], padx=14, pady=12,
                                 relief="sunken" if T["bevel"] else "flat", bd=2 if T["bevel"] else 0, highlightthickness=1,
                                 highlightbackground=T["border"], state="disabled")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.chat_view.yview)
        self.chat_view.configure(yscrollcommand=scroll.set)
        self.chat_view.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.chat_view.tag_configure("you", foreground=T["accent"], font=self.font(11, "bold"), spacing1=10)
        self.chat_view.tag_configure("ai", foreground=T["ink"], spacing1=4, lmargin1=12, lmargin2=12)
        self.chat_view.tag_configure("note", foreground=T["muted"], font=self.font(9), spacing1=6)
        for role, text in self.chat_history:
            self.chat_line(role, text, remember=False)
        if not self.chat_history:
            self.chat_line("note", "Ask anything about your inventory, like “Where are my AA batteries?” or “Move all the kitchen "
                                   "tools to the pantry.” Changes the assistant suggests are shown for your OK first, and you can undo them.",
                           remember=False)

    def refresh_assistant(self):
        provider = PROVIDERS[self.settings["ai_provider"]]
        model = self.settings[f"ai_model_{self.settings['ai_provider']}"] or provider["default_model"]
        ready = "" if self.ai_ready() else "  ·  add an API key in Settings"
        self.ai_title.set(f"✦ Assistant  ·  {provider['name']}  ·  {model}{ready}")

    def chat_line(self, role, text, remember=True):
        self.chat_view.configure(state="normal")
        prefix = {"you": "You:  ", "ai": "", "note": ""}[role]
        self.chat_view.insert("end", prefix + text.strip() + "\n", role)
        self.chat_view.configure(state="disabled")
        self.chat_view.see("end")
        if remember and role in ("you", "ai"):
            self.chat_history.append(("user" if role == "you" else "assistant", text))

    def clear_chat(self):
        self.chat_history.clear()
        self.chat_view.configure(state="normal")
        self.chat_view.delete("1.0", "end")
        self.chat_view.configure(state="disabled")

    # ------------------------------------------------------------------- AI glue
    def ai_ready(self):
        return bool(str(self.settings.get(f"ai_key_{self.settings['ai_provider']}") or "").strip())

    def ai(self):
        provider = self.settings["ai_provider"]
        return ai.Assistant(provider, self.settings.get(f"ai_key_{provider}"), self.settings.get(f"ai_model_{provider}"))

    def ai_context(self):
        return ai.inventory_context(self.inventory, self.settings["ai_send_notes"], self.settings["ai_send_values"])

    def save_ai_keys(self):
        for key, var in self.key_vars.items():
            self.change_setting(f"ai_key_{key}", var.get().strip(), rebuild=False)
        self.ai_test_result.set("Keys saved" + (" inside your encrypted inventory." if self.session else "."))
        self.refresh_assistant()

    def test_ai(self):
        self.save_ai_keys()
        try:
            assistant = self.ai()
        except AIError as error:
            self.ai_test_result.set(str(error))
            return
        self.ai_test_result.set("Testing…")
        self.background.run(assistant.test_connection, lambda reply: self.ai_test_result.set(f"Connected ✓  ({assistant.model})"),
                            lambda error: self.ai_test_result.set(str(error)))

    def send_chat(self, text=None):
        message = (text or self.chat_var.get()).strip()
        if not message:
            return
        try:
            assistant = self.ai()
        except AIError as error:
            self.chat_line("note", str(error), remember=False)
            return
        self.chat_var.set("")
        self.chat_line("you", message, remember=False)
        self.chat_line("note", "Thinking…", remember=False)
        self.chat_send.configure(state="disabled")
        history = list(self.chat_history)
        context = self.ai_context()

        def done(result):
            reply, actions = result
            self.chat_history.extend([("user", message), ("assistant", reply)])
            if self.chat_view.winfo_exists():
                self.chat_send.configure(state="normal")
                self.chat_line("ai", reply, remember=False)
            if actions:
                self.review_actions(actions, f"AI: {message[:40]}")

        def failed(error):
            if self.chat_view.winfo_exists():
                self.chat_send.configure(state="normal")
                self.chat_line("note", f"⚠ {error}", remember=False)
        self.background.run(lambda: assistant.chat(history, message, context), done, failed)

    def review_actions(self, actions, label):
        rows = [(ai.describe_action(self.inventory, action), action) for action in actions]
        ReviewDialog(self, "Apply these changes?", "The assistant suggested these changes. Untick any you don't want. "
                     f"Everything you apply counts as one step you can undo ({MOD}Z).", rows,
                     lambda chosen: self.apply_ai_actions(chosen, label))

    def apply_ai_actions(self, actions, label):
        changes, locations, deletions, problems = ai.actions_to_batch(self.inventory, actions)
        try:
            if changes or locations or deletions:
                self.inventory.apply_batch(label, changes, locations, deletions)
        except (InventoryError, sqlite3.Error) as error:
            messagebox.showerror("Changes not applied", f"Nothing was changed: {error}", parent=self.root)
            return
        self.refresh()
        message = f"Applied {len(actions) - len(problems)} change(s)  ·  {MOD}Z to undo"
        if problems:
            messagebox.showwarning("Some changes were skipped", "\n".join(problems), parent=self.root)
        self.set_status(message)
        if hasattr(self, "chat_view") and self.chat_view.winfo_exists():
            self.chat_line("note", message, remember=False)

    def suggest_organization(self):
        try:
            assistant = self.ai()
        except AIError as error:
            messagebox.showinfo("AI isn't set up", str(error), parent=self.root)
            return
        self.open_or_focus("assistant")
        self.chat_line("note", "Looking for better tags, categories, and places…", remember=False)
        location_map = {loc["id"]: loc for loc in self.inventory.locations()}
        paths = [self.inventory.path_text(loc_id, location_map) for loc_id in location_map]
        context = self.ai_context()

        def done(suggestions):
            actions = []
            for entry in suggestions:
                changes = {}
                if isinstance(entry.get("tags"), list):
                    changes["tags"] = entry["tags"]
                if entry.get("category") in CATEGORIES:
                    changes["category"] = entry["category"]
                if entry.get("location"):
                    changes["location"] = entry["location"]
                if changes:
                    actions.append({"type": "update_item", "item_id": entry["item_id"], "changes": changes, "reason": entry.get("reason", "")})
            if not actions:
                self.chat_line("note", "No changes suggested. Your inventory already looks tidy.", remember=False)
                return
            self.chat_line("note", f"{len(actions)} suggestion(s) ready for review.", remember=False)
            self.review_actions(actions, "AI: tidy tags and places")
        self.background.run(lambda: assistant.suggest_organization(context, CATEGORIES, paths), done,
                            lambda error: self.chat_line("note", f"⚠ {error}", remember=False))

    # --------------------------------------------------------- items state/view
    def capture_items_state(self):
        if not self.tabs or self.tab["page"] != "items" or not hasattr(self, "category") or self._loading:
            return
        state = self.tab["state"]
        state.update(category="" if self.category.get() == "All categories" else self.category.get(),
                     tag="" if self.tag.get() == "All tags" else self.tag.get(),
                     low_only=bool(self.low_only.get()), sort=self.sort.get(), selected_id=self.selected_id)

    def load_items_state(self):
        state = self.tab["state"]
        self._loading = True
        self.category.set(state.get("category") or "All categories")
        self.tag.set(state.get("tag") or "All tags")
        self.low_only.set(bool(state.get("low_only")))
        self.sort.set(state.get("sort") or self.settings["default_sort"])
        if state.get("selected_id"):
            self.selected_id = state["selected_id"]
        self._loading = False

    def filters_changed(self):
        if self._loading:
            return
        self.capture_items_state()
        self.refresh_items()
        self.render_tab_strip()
        self.later("save_tabs", 600, self.save_tabs)

    def on_search_typed(self, force=False):
        if self._loading:
            return
        query = self.search_var.get()
        if self.tab["page"] != "items":
            if force and query.strip():
                self.navigate("items", {"query": query.strip()})
            return
        self.tab["state"]["query"] = query

        def run():
            self.refresh_items()
            self.render_tab_strip()
            self.update_nav_crumbs_only()
        self.later("search", 0 if force else 180, run)

    def update_nav_crumbs_only(self):
        self._loading = True
        try:
            self.update_nav()
        finally:
            self._loading = False

    def current_filters(self):
        state = self.tab["state"] if self.tabs and self.tab["page"] == "items" else blank_items_state(self.settings["default_sort"])
        return {"query": state.get("query", ""), "location_id": state.get("location_id"), "category": state.get("category", ""),
                "tag": state.get("tag", ""), "low_only": bool(state.get("low_only")), "sort": state.get("sort") or SORTS[0],
                "include_photos": False}

    def current_location(self):
        if self.tabs and self.tab["page"] == "items":
            return self.tab["state"].get("location_id")
        return None

    def clear_filters(self, refresh=True):
        if self.tab["page"] == "items":
            self.tab["state"].update(blank_items_state(self.settings["default_sort"]))
            self.tab["state"]["selected_id"] = self.selected_id
            self.load_items_state()
            self.update_nav_crumbs_only()
        if refresh:
            self.refresh_items()
            self.render_tab_strip()

    def focus_search(self, _event=None):
        if not self.busy():
            self.search_entry.focus_set()
            self.search_entry.selection_range(0, "end")
        return "break"

    # ------------------------------------------------------------------ refresh
    def refresh(self):
        totals = self.inventory.stats()
        for key, var in self.stat_values.items():
            var.set(f"{totals[key]:,}")
        all_tags = self.inventory.all_tags()
        if self.tag.get() not in ("All tags", *all_tags):
            self.tag.set("All tags")
        self.tag_combo.configure(values=("All tags", *all_tags))
        self.refresh_locations()
        page = self.tab["page"]
        if page == "items":
            self.refresh_items()
        elif page == "item":
            self.show_item_page()
        self.activity_tree.delete(*self.activity_tree.get_children())
        for entry in self.inventory.activity():
            when = datetime.fromisoformat(entry["created_at"]).astimezone().strftime("%b %d, %Y  %I:%M %p")
            self.activity_tree.insert("", "end", iid=str(entry["id"]), values=(when, entry["message"]))
        self.render_tab_strip()
        self.update_nav_crumbs_only()
        self.update_undo_state()

    def update_undo_state(self):
        can_undo, can_redo = self.inventory.can_undo(), self.inventory.can_redo()
        undo_label, redo_label = self.inventory.undo_label(), self.inventory.redo_label()
        for button in (getattr(self, "undo_button", None), getattr(self, "settings_undo", None)):
            if button is not None:
                button.configure(state="normal" if can_undo else "disabled")
        for button in (getattr(self, "redo_button", None), getattr(self, "settings_redo", None)):
            if button is not None:
                button.configure(state="normal" if can_redo else "disabled")
        if getattr(self, "edit_menu", None) is not None:
            self.edit_menu.entryconfigure(0, label=f"Undo {undo_label}" if can_undo else "Undo", state="normal" if can_undo else "disabled")
            self.edit_menu.entryconfigure(1, label=f"Redo {redo_label}" if can_redo else "Redo", state="normal" if can_redo else "disabled")
        if getattr(self, "undo_hint", None) is not None:
            self.undo_hint.set(f"{MOD}Z undoes: {undo_label}" if can_undo else "Nothing to undo")
        if getattr(self, "history_list", None) is not None and self.history_list.winfo_exists():
            self.history_list.delete(0, "end")
            steps = self.inventory.history()
            for index, label in enumerate(steps):
                self.history_list.insert("end", f"{'→ ' if index == 0 else '   '}{label}")
            if not steps:
                self.history_list.insert("end", "   No changes yet in this session.")

    def refresh_locations(self):
        locations = self.inventory.locations()
        location_map = {loc["id"]: loc for loc in locations}
        counts = self.inventory.location_counts()
        trees = [tree for tree in (getattr(self, "location_tree", None), self.manage_tree) if tree is not None and tree.winfo_exists()]
        old = {tree: tree.selection() for tree in trees}
        for tree in trees:
            tree.delete(*tree.get_children())
        remaining = locations[:]
        while remaining:
            progress = False
            for loc in remaining[:]:
                parent = loc["parent_id"] or ""
                if parent and not self.manage_tree.exists(parent):
                    continue
                count = counts.get(loc["id"], 0)
                for tree in trees:
                    if tree is self.manage_tree:
                        tree.insert(parent, "end", iid=loc["id"], text=loc["name"], values=(loc["kind"], self.inventory.path_text(loc["id"], location_map), count), open=True)
                    else:
                        tree.insert(parent, "end", iid=loc["id"], text=f"{loc['name']}  ({count})", open=True)
                remaining.remove(loc)
                progress = True
            if not progress:
                raise InventoryError("A location hierarchy is invalid. Restore a backup.")
        current = self.current_location()
        for tree in trees:
            keep = current if tree is not self.manage_tree else (old[tree][0] if old[tree] else None)
            if keep and tree.exists(keep):
                tree.selection_set(keep)
                tree.see(keep)

    def refresh_items(self):
        filters = self.current_filters()
        if filters["location_id"]:
            try:
                self.inventory.get_location(filters["location_id"])
            except InventoryError:
                self.tab["state"]["location_id"] = filters["location_id"] = None
        self.filtered_items = self.inventory.items(**filters)
        location_map = {loc["id"]: loc for loc in self.inventory.locations()}
        self.item_tree.delete(*self.item_tree.get_children())
        for index, item in enumerate(self.filtered_items):
            tags = ["alternate"] if index % 2 else []
            if item["quantity"] <= item["low_stock"]:
                tags.append("low")
            self.item_tree.insert("", "end", iid=item["id"], values=(item["name"], item["category"], item["quantity"],
                                  self.inventory.path_text(item["location_id"], location_map), ", ".join(item["tags"]),
                                  currency(item["quantity"] * item["value"])), tags=tags)
        visible = {item["id"] for item in self.filtered_items}
        if self.selected_id not in visible:
            self.selected_id = self.filtered_items[0]["id"] if self.filtered_items else None
        if self.selected_id:
            self.item_tree.selection_set(self.selected_id)
            self.item_tree.focus(self.selected_id)
            self.item_tree.see(self.selected_id)
        self.show_detail()
        count = len(self.filtered_items)
        hint = " · No matches. Try clearing your filters." if not count else " · Double-click to open · right-click for more"
        drop = " · drop photos here to add items" if hasattr(self, "pages") and self.pages["items"].tk.call("info", "commands", "tkdnd::drop_target") else ""
        self.results.set(f"{count} item{'s' if count != 1 else ''} shown{hint}{drop}")

    def show_detail(self):
        if not getattr(self, "has_detail", False):
            return
        try:
            item = self.inventory.get_item(self.selected_id) if self.selected_id else None
        except InventoryError:
            item = None
        self.detail_name.set(item["name"] if item else "Select an item")
        self.detail_category.set((item["category"] + ("  ·  LOW STOCK" if item["quantity"] <= item["low_stock"] else "")) if item else "Add an item or clear your filters.")
        self.detail_qty.set(f"{item['quantity']} units" if item else "—")
        self.detail_values.set(f"Unit value {currency(item['value'])}  ·  Low stock at {item['low_stock']}" if item else "")
        self.detail_location.set(self.inventory.path_text(item["location_id"]) if item else "—")
        self.detail_tags.set((", ".join(item["tags"]) or "No tags") if item else "—")
        self.detail_notes.configure(state="normal")
        self.detail_notes.delete("1.0", "end")
        self.detail_notes.insert("1.0", (item["notes"] or "No notes added.") if item else "")
        self.detail_notes.configure(state="disabled")
        paint_photo(self.photo_canvas, item["photo"] if item else b"", title="Click or drop a photo" if item else "No item selected", font=self.font(9))
        for button in (self.edit_button, self.delete_button, self.open_button):
            button.configure(state="normal" if item else "disabled")
        self.minus_button.configure(state="normal" if item and item["quantity"] > 0 else "disabled")
        self.plus_button.configure(state="normal" if item and item["quantity"] < 999999 else "disabled")

    def on_item_select(self, _event=None):
        selected = self.item_tree.selection()
        if selected:
            self.selected_id = selected[0]
            if self.tab["page"] == "items":
                self.tab["state"]["selected_id"] = self.selected_id
            self.show_detail()

    def open_item(self, new_tab=False, item_id=None):
        item_id = item_id or self.selected_id
        if item_id:
            self.navigate("item", {"item_id": item_id}, new_tab=new_tab)

    def item_clicked_new_tab(self, event):
        row = self.item_tree.identify_row(event.y)
        if row:
            self.open_item(new_tab=True, item_id=row)
        return "break"

    def location_clicked(self, event, new_tab=False):
        row = self.location_tree.identify_row(event.y)
        if row and self.location_tree.identify_element(event.x, event.y) != "Treeitem.indicator":
            self.open_location(row, new_tab)
        return "break" if new_tab else None

    def open_location(self, location_id, new_tab=False):
        if location_id:
            state = {"location_id": location_id, "sort": self.tab["state"].get("sort", self.settings["default_sort"]) if self.tab["page"] == "items" else self.settings["default_sort"]}
            self.navigate("items", state, new_tab=new_tab)

    def item_menu(self, event):
        row = self.item_tree.identify_row(event.y)
        if not row:
            return
        self.item_tree.selection_set(row)
        self.selected_id = row
        menu = tk.Menu(self.root, tearoff=False)
        menu.add_command(label="Open", command=lambda: self.open_item(False, row))
        menu.add_command(label="Open in new tab", command=lambda: self.open_item(True, row))
        menu.add_separator()
        menu.add_command(label="Edit / move…", command=lambda: self.edit_item(row))
        menu.add_command(label="Set quantity…", command=lambda: self.set_quantity_prompt(row))
        menu.add_command(label="Change photo…", command=lambda: self.change_photo(row))
        menu.add_separator()
        menu.add_command(label="Delete…", command=lambda: self.delete_item(row))
        menu.tk_popup(event.x_root, event.y_root)
        return "break"

    def location_menu(self, event, tree):
        row = tree.identify_row(event.y)
        if not row:
            return
        tree.selection_set(row)
        menu = tk.Menu(self.root, tearoff=False)
        menu.add_command(label="Open", command=lambda: self.open_location(row))
        menu.add_command(label="Open in new tab", command=lambda: self.open_location(row, True))
        menu.add_separator()
        menu.add_command(label="Add item here…", command=lambda: self.add_item(location_id=row))
        menu.add_command(label="Add location inside…", command=lambda: LocationDialog(self, parent_id=row) if not self.busy() else None)
        menu.add_command(label="Edit / move…", command=lambda: LocationDialog(self, row) if not self.busy() else None)
        menu.add_command(label="Delete (if empty)…", command=lambda: self.delete_location(row))
        menu.tk_popup(event.x_root, event.y_root)
        return "break"

    # ------------------------------------------------------------------ actions
    def add_item(self, photo=None, autofill=False, on_close=None, location_id=None):
        if self.busy():
            return
        if not self.inventory.locations():
            messagebox.showinfo("Add a location first", "Create a home, room, shelf, or drawer before adding an item.", parent=self.root)
            self.add_location()
            return
        if location_id:
            self.navigate("items", {"location_id": location_id})
        ItemDialog(self, photo=photo, autofill=autofill, on_close=on_close)

    def edit_item(self, item_id=None):
        item_id = item_id or self.selected_id
        if item_id and not self.busy():
            try:
                ItemDialog(self, item_id)
            except InventoryError as error:
                messagebox.showinfo("Item not found", str(error), parent=self.root)

    def after_item_saved(self, saved):
        self.selected_id = saved["id"]
        if getattr(self, "tabs", None) and self.tab["page"] == "items":
            visible = {item["id"] for item in self.inventory.items(**self.current_filters())}
            if saved["id"] not in visible:
                self.tab["state"].update(blank_items_state(self.tab["state"].get("sort", SORTS[0])))
                self.load_items_state()
            self.tab["state"]["selected_id"] = saved["id"]
        self.refresh()
        self.set_status(f"Saved {saved['name']}  ·  {MOD}Z to undo")

    def adjust_quantity(self, delta, item_id=None):
        item_id = item_id or self.selected_id
        if item_id and not self.busy():
            try:
                item = self.inventory.adjust_quantity(item_id, delta)
                self.refresh()
                self.set_status(f"{item['name']}: {item['quantity']} units")
            except (InventoryError, sqlite3.Error) as error:
                messagebox.showerror("Quantity not changed", str(error), parent=self.root)

    def set_quantity_prompt(self, item_id=None):
        item_id = item_id or self.selected_id
        if not item_id or self.busy():
            return
        try:
            item = self.inventory.get_item(item_id)
        except InventoryError:
            return
        value = simpledialog.askinteger("Set quantity", f"How many “{item['name']}” do you have?", parent=self.root,
                                        initialvalue=item["quantity"], minvalue=0, maxvalue=999999)
        if value is not None:
            try:
                self.inventory.set_quantity(item_id, value)
                self.refresh()
                self.set_status(f"{item['name']}: {value} units  ·  {MOD}Z to undo")
            except (InventoryError, sqlite3.Error) as error:
                messagebox.showerror("Quantity not changed", str(error), parent=self.root)

    def delete_item(self, item_id=None):
        item_id = item_id or self.selected_id
        if not item_id or self.busy():
            return
        try:
            item = self.inventory.get_item(item_id)
        except InventoryError:
            return
        if self.settings["confirm_delete"] and not messagebox.askyesno(
                "Delete item?", f"Delete {item['name']} and its {item['quantity']} units?\n\nYou can undo this with Undo ({MOD}Z).", parent=self.root):
            return
        try:
            self.inventory.delete_item(item["id"])
            if self.selected_id == item["id"]:
                self.selected_id = None
            self.refresh()
            self.set_status(f"Deleted {item['name']}  ·  {MOD}Z to undo")
        except (InventoryError, sqlite3.Error) as error:
            messagebox.showerror("Item not deleted", str(error), parent=self.root)

    # -------------------------------------------------------------------- photos
    def change_photo(self, item_id=None):
        item_id = item_id or self.selected_id
        if not item_id or self.busy():
            return
        filename = filedialog.askopenfilename(parent=self.root, title="Choose a photo",
                                              filetypes=[("Photos", " ".join(f"*{ext}" for ext in PHOTO_TYPES)), ("All files", "*.*")])
        if filename:
            self.dropped_on_item([filename], item_id)

    def dropped_on_item(self, paths, item_id):
        photos = [p for p in paths if Path(p).suffix.casefold() in PHOTO_TYPES]
        if not item_id or not photos or self.busy():
            return
        try:
            data, name = read_photo(photos[0])
            item = self.inventory.get_item(item_id)
            self.inventory.save_item({**item, "photo": data, "photo_name": name, "value": item["value"],
                                      "quantity": str(item["quantity"]), "low_stock": str(item["low_stock"])}, item_id)
        except (InventoryError, OSError, sqlite3.Error) as error:
            messagebox.showerror("Photo not changed", str(error), parent=self.root)
            return
        self.refresh()
        self.set_status(f"New photo for {item['name']}  ·  {MOD}Z to undo")

    def dropped_on_items(self, paths):
        photos = [p for p in paths if Path(p).suffix.casefold() in PHOTO_TYPES]
        if not photos:
            messagebox.showinfo("Not a photo", "Drop PNG, GIF, JPG, or WebP photos to add items.", parent=self.root)
            return
        self.add_items_from_photos(photos)

    def add_items_from_photos(self, paths=None):
        if self.busy():
            return
        if paths is None:
            paths = filedialog.askopenfilenames(parent=self.root, title="Choose photos of items to add",
                                                filetypes=[("Photos", " ".join(f"*{ext}" for ext in PHOTO_TYPES))])
            if not paths:
                return
        photos = []
        for path in paths:
            try:
                photos.append(read_photo(path))
            except (InventoryError, OSError) as error:
                messagebox.showwarning("Photo skipped", str(error), parent=self.root)
        if not photos:
            return
        if len(photos) > 1 and self.ai_ready():
            self.describe_many(photos)
            return
        self._photo_queue = list(photos)
        self.next_photo_dialog()

    def next_photo_dialog(self):
        if self._photo_queue and not self.busy():
            photo = self._photo_queue.pop(0)
            self.add_item(photo=photo, autofill=self.settings["ai_autofill_photos"], on_close=lambda: self.root.after(150, self.next_photo_dialog))

    def describe_many(self, photos):
        """Ask AI about several photos, then show one checklist to add them all as one undo step."""
        try:
            assistant = self.ai()
        except AIError as error:
            messagebox.showinfo("AI isn't set up", str(error), parent=self.root)
            return
        location_map = {loc["id"]: loc for loc in self.inventory.locations()}
        paths = {self.inventory.path_text(loc_id, location_map): loc_id for loc_id in location_map}
        fallback = self.current_location() or next(iter(location_map), None)
        self.set_status(f"AI is looking at {len(photos)} photos…")

        def work():
            results = []
            for data, name in photos:
                try:
                    results.append((data, name, assistant.describe_photo(data, CATEGORIES, sorted(paths)), None))
                except AIError as error:
                    results.append((data, name, None, error))
            return results

        def done(results):
            rows, failures = [], []
            for data, name, info, error in results:
                if info is None:
                    failures.append(f"{name}: {error}")
                    continue
                location_id = next((paths[p] for p in paths if p.casefold() == info["location"].casefold()), fallback)
                fields = {"name": info["name"] or Path(name).stem, "category": info["category"], "quantity": str(info["quantity"] or 1),
                          "location_id": location_id, "tags": info["tags"], "notes": info["notes"], "value": info["value"],
                          "low_stock": "0", "photo": data, "photo_name": name}
                label = f"Add “{fields['name']}” × {fields['quantity']} ({fields['category']}) in {self.inventory.path_text(location_id, location_map)}"
                rows.append((label, fields))
            if failures:
                messagebox.showwarning("Some photos couldn't be read", "\n".join(failures), parent=self.root)
            if rows:
                ReviewDialog(self, "Add these items?", f"AI read {len(rows)} photo(s). Untick any you don't want; you can edit items after adding. "
                             f"Adding them all is one undo step.", rows, self.add_reviewed_items)
        self.background.run(work, done, lambda error: messagebox.showerror("AI error", str(error), parent=self.root))

    def add_reviewed_items(self, fields_list):
        try:
            self.inventory.apply_batch(f"Add {len(fields_list)} item(s) from photos", [(None, fields) for fields in fields_list])
        except (InventoryError, sqlite3.Error) as error:
            messagebox.showerror("Items not added", str(error), parent=self.root)
            return
        self.refresh()
        self.set_status(f"Added {len(fields_list)} item(s) from photos  ·  {MOD}Z to undo")

    # -------------------------------------------------------------- undo/redo
    def undo(self):
        if self.busy() or not self.inventory.can_undo():
            return
        try:
            label, focus = self.inventory.undo()
        except (InventoryError, sqlite3.Error) as error:
            messagebox.showerror("Can't undo", str(error), parent=self.root)
            return
        self.after_history_step(focus)
        self.set_status(f"Undid: {label}")

    def redo(self):
        if self.busy() or not self.inventory.can_redo():
            return
        try:
            label, focus = self.inventory.redo()
        except (InventoryError, sqlite3.Error) as error:
            messagebox.showerror("Can't redo", str(error), parent=self.root)
            return
        self.after_history_step(focus)
        self.set_status(f"Redid: {label}")

    def after_history_step(self, focus):
        """After undo/redo, show the item that changed if it still exists."""
        if focus:
            try:
                self.inventory.get_item(focus)
                self.selected_id = focus
                if getattr(self, "tabs", None) and self.tab["page"] == "items":
                    visible = {item["id"] for item in self.inventory.items(**self.current_filters())}
                    if focus not in visible:
                        self.tab["state"].update(blank_items_state(self.tab["state"].get("sort", SORTS[0])))
                        self.load_items_state()
                    self.tab["state"]["selected_id"] = focus
            except InventoryError:
                pass
        self.refresh()

    def undo_delete(self):
        """Older name for Undo, kept so existing scripts keep working."""
        self.undo()

    def clear_history(self):
        if not self.inventory.can_undo() and not self.inventory.can_redo():
            return
        if messagebox.askyesno("Clear undo history?", "Forget every undo and redo step from this session?\n\nYour inventory itself is not changed.", parent=self.root):
            self.inventory.clear_history()
            self.update_undo_state()
            self.set_status("Undo history cleared")

    # --------------------------------------------------------------- locations
    def location_selection(self, manage=False):
        tree = self.manage_tree if manage or not hasattr(self, "location_tree") else self.location_tree
        selected = tree.selection()
        return selected[0] if selected else None

    def add_location(self, manage=False):
        if not self.busy():
            LocationDialog(self, parent_id=self.location_selection(manage) or self.current_location())

    def edit_location(self, manage=False):
        location_id = self.location_selection(manage) or (None if manage else self.current_location())
        if location_id and not self.busy():
            LocationDialog(self, location_id)
        elif not self.busy():
            messagebox.showinfo("Select a location", "Select a location to edit it.", parent=self.root)

    def delete_location(self, location_id=None):
        location_id = location_id or self.location_selection(manage=True)
        if not location_id:
            messagebox.showinfo("Select a location", "Select an empty location to delete it.", parent=self.root)
            return
        location = self.inventory.get_location(location_id)
        if not messagebox.askyesno("Delete empty location?", f"Delete {location['name']}?\n\nLocations containing items or other locations can't be deleted. You can undo this.", parent=self.root):
            return
        try:
            self.inventory.delete_location(location_id)
            self.refresh()
            self.set_status(f"Location deleted  ·  {MOD}Z to undo")
        except (InventoryError, sqlite3.Error) as error:
            messagebox.showerror("Location not deleted", str(error), parent=self.root)

    # -------------------------------------------------------------------- data
    def backup(self):
        stamp = f"{datetime.now():%Y-%m-%d}"
        if self.session:
            filename = filedialog.asksaveasfilename(parent=self.root, title="Save an encrypted backup", defaultextension=".hbbackup",
                                                    initialfile=f"Homebase_{self.session.username}_{stamp}.hbbackup",
                                                    filetypes=[("Homebase backup", "*.hbbackup")])
            if filename:
                try:
                    self.store.export_backup(self.session, filename)
                    self.set_status("Encrypted backup saved (opens only with your password or recovery phrase)")
                except (OSError, security.SecurityError) as error:
                    messagebox.showerror("Backup failed", str(error), parent=self.root)
            return
        filename = filedialog.asksaveasfilename(parent=self.root, title="Back up inventory and photos", defaultextension=".sqlite3",
                                                initialfile=f"Homebase_Backup_{stamp}.sqlite3", filetypes=[("SQLite database", "*.sqlite3")])
        if filename:
            try:
                self.inventory.backup(filename)
                self.set_status("Database backup saved, including all photos")
            except (OSError, InventoryError, sqlite3.Error) as error:
                messagebox.showerror("Backup failed", str(error), parent=self.root)

    def export_csv(self, everything=False):
        rows = self.inventory.items(include_photos=False) if everything or self.tab["page"] != "items" else self.filtered_items
        if self.session and not messagebox.askokcancel("Export without encryption?", "A CSV file is plain text: anyone who opens it can read "
                                                      "these items. Continue?", parent=self.root):
            return
        filename = filedialog.asksaveasfilename(parent=self.root, title="Export items", defaultextension=".csv",
                                                initialfile="Homebase_Inventory.csv", filetypes=[("CSV spreadsheet", "*.csv")])
        if filename:
            try:
                self.inventory.export_csv(filename, rows)
                self.set_status(f"Exported {len(rows)} items")
            except (OSError, InventoryError) as error:
                messagebox.showerror("Export failed", str(error), parent=self.root)

    def import_database(self):
        filename = filedialog.askopenfilename(parent=self.root, title="Choose an old Homebase database",
                                              filetypes=[("Homebase database", "*.sqlite3"), ("All files", "*.*")])
        if not filename:
            return
        if not messagebox.askyesno("Replace this inventory?", "This replaces everything in this inventory with the chosen file's items and "
                                   "locations. You can undo it while Homebase stays open. Continue?", parent=self.root):
            return
        try:
            self.inventory.replace_with(filename)
        except (InventoryError, sqlite3.Error, OSError) as error:
            messagebox.showerror("Import failed", str(error), parent=self.root)
            return
        self.selected_id = None
        self.refresh()
        self.set_status(f"Imported {Path(filename).name}  ·  {MOD}Z to undo")

    def show_data_folder(self):
        path = self.session.vault.path if self.session else self.inventory.path
        try:
            if IS_MAC:
                subprocess.Popen(["open", "-R", str(path)])
            elif sys.platform.startswith("win"):
                subprocess.Popen(["explorer", "/select,", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path.parent)])
        except OSError as error:
            messagebox.showerror("Can't open folder", f"{error}\n\nThe file is at:\n{path}", parent=self.root)

    def reset_samples(self):
        if not messagebox.askyesno("Replace inventory with samples?", "This replaces every item, location, and photo with the sample inventory.\n\n"
                                   "You can undo this while Homebase stays open, but a backup is safer. Continue?", icon="warning", parent=self.root):
            return
        try:
            self.inventory.reset_samples()
            self.selected_id = None
            self.clear_filters(refresh=False)
            self.refresh()
            self.set_status(f"Sample inventory restored  ·  {MOD}Z to undo")
        except (InventoryError, sqlite3.Error) as error:
            messagebox.showerror("Reset failed", str(error), parent=self.root)

    # ----------------------------------------------------------------- account
    def toggle_two_factor(self):
        if not self.session.totp_enabled:
            TwoFactorSetup(self)
            return
        PasswordForm(self, "Turn off two-factor", [("password", "Your password", "secret")],
                     lambda v: self.store.disable_two_factor(self.session, v["password"]), "Two-factor sign-in is off.")

    def change_password(self):
        PasswordForm(self, "Change password", [("old", "Current password", "secret"), ("new", "New password", "secret"), ("new2", "New password again", "secret")],
                     lambda v: self.store.change_password(self.session, v["old"], v["new"]), "Password changed.")

    def change_phrase(self):
        PasswordForm(self, "Change recovery phrase", [("password", "Your password", "secret"), ("phrase", "New recovery phrase (12+ words)", "phrase")],
                     lambda v: self.store.change_seed_phrase(self.session, v["password"], v["phrase"]),
                     "Recovery phrase changed. Your old phrase no longer works.", intro="Write the new phrase down before saving.")

    def delete_account(self):
        def remove(values):
            self.store.delete_account(self.session, values["password"])
            self.root.after(50, lambda: self.teardown(f"Deleted the account “{self.session.username}”.", saved=False))
        PasswordForm(self, "Delete this account", [("password", "Type your password to confirm", "secret")], remove, None,
                     intro="This permanently erases this account and its encrypted inventory from this computer. Backups you saved are not touched.")

    def after_account_change(self, message):
        self.set_status(message)
        self.rebuild()
        self.set_status(message)

    def check_autolock(self):
        minutes = int(self.settings.get("auto_lock_minutes") or 0)
        if self.session and minutes and time.monotonic() - self._last_activity > minutes * 60 and not self.busy() and not self.background.busy:
            self.lock(auto=True)
            return
        self.later("autolock", 15000, self.check_autolock)

    def lock(self, auto=False):
        if self.session:
            self.teardown("Locked after inactivity. Sign in to continue." if auto else "Locked. Sign in to continue.")

    def sign_out(self):
        if self.session:
            self.teardown("Signed out.")

    def teardown(self, message, saved=True):
        """Save, forget the decrypted data, and go back to the sign-in screen."""
        if self.closed:
            return
        username = self.session.username if self.session else None
        try:
            if saved:
                self.save_tabs()
        except Exception:
            pass
        for window in self.root.winfo_children():
            if isinstance(window, tk.Toplevel):
                window.destroy()
        self.closed = True
        self.cancel_jobs()
        if self.session is not None and saved:
            self.session.lock()
        self.shell.destroy()
        self.root._homebase_app = None
        if self.on_sign_out:
            self.on_sign_out(username, message)

    def about(self):
        messagebox.showinfo("About Homebase", "Homebase · Universal Home/Locational Inventory\n\nPython + Tkinter + SQLite. Runs on your computer with no account "
                            "server. Each user's inventory is encrypted with AES-256-GCM; keys are protected with scrypt and can be recovered with your "
                            "recovery phrase. Optional two-factor sign-in uses standard authenticator codes.\n\n"
                            "AI features (ChatGPT or Claude) are optional and use your own API key.\n\n"
                            "Created by Gregory Edison", parent=self.root)

    def close(self):
        if self.closed:
            self.root.destroy()
            return
        try:
            self.save_tabs()
        except Exception:
            pass
        self.closed = True
        self.cancel_jobs()
        if self.session is not None:
            self.session.lock()
        else:
            self.inventory.close()
        self.root.destroy()


# --------------------------------------------------------------------------- start
def run_accounts(root, data_folder):
    store = security.AccountStore(data_folder)
    legacy = data_folder / "homebase.sqlite3"
    holder = {}

    def open_plain():
        holder["auth"].destroy()
        HomebaseApp(root, Inventory(legacy))

    def show_auth(username=None, message=None):
        holder["auth"] = AuthScreen(root, store, signed_in, legacy_db=legacy, username=username, message=message, open_plain=open_plain)

    def signed_in(session, setup_2fa=False):
        holder["auth"].destroy()
        try:
            inventory = session.open_inventory()
        except security.SecurityError as error:
            messagebox.showerror("Can't open your inventory", f"{error}\n\nIf you have a backup, restore it from the sign-in screen.", parent=root)
            show_auth(session.username)
            return
        app = HomebaseApp(root, inventory, session=session, store=store, on_sign_out=signed_out)
        if setup_2fa:
            root.after(300, lambda: TwoFactorSetup(app))

    def signed_out(username, message):
        show_auth(username, message)

    show_auth()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Homebase: local, encrypted home inventory")
    parser.add_argument("--db", type=Path, help="open this unencrypted SQLite database directly (developer mode, no sign-in)")
    parser.add_argument("--data", type=Path, default=DATA, help="folder for accounts and encrypted inventories")
    parser.add_argument("--check", action="store_true", help="check a database without opening a window")
    args = parser.parse_args(argv)
    try:
        if args.check:
            path = args.db or LEGACY_DB
            inventory = Inventory(path)
            check = inventory.db.execute("PRAGMA integrity_check").fetchone()[0]
            totals = inventory.stats()
            inventory.close()
            if check != "ok":
                raise InventoryError(f"Database integrity check failed: {check}")
            print(f"Database OK: {totals['items']} items, {totals['units']} units, {totals['locations']} locations.")
            return 0
        if tk is None:
            print("Tkinter is missing. On macOS, use a Python.org installer. On Windows, enable Tcl/Tk in the installer. "
                  "On Ubuntu/Debian, install python3-tk.", file=sys.stderr)
            return 1
        root = make_root()
        width, height = min(1400, root.winfo_screenwidth() - 60), min(880, root.winfo_screenheight() - 90)
        root.geometry(f"{width}x{height}+{max(0, (root.winfo_screenwidth() - width) // 2)}+{max(0, (root.winfo_screenheight() - height) // 3)}")
        root.minsize(1000, 640)
        root._homebase_sized = True
        if args.db:
            HomebaseApp(root, Inventory(args.db))
        else:
            run_accounts(root, args.data)
        root.mainloop()
        return 0
    except Exception as error:
        print(f"Homebase could not start: {error}", file=sys.stderr)
        if tk is not None and isinstance(error, tk.TclError):
            print("A desktop display is required. Run python app.py on your Windows, Mac, or Linux desktop.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Theme creator: pick colors, see a live preview, save as a custom theme."""
import re
import tkinter as tk
import uuid
from tkinter import colorchooser, messagebox, ttk

from themes import CUSTOM_THEMES, EDITABLE, THEMES, T, build_custom, clean_hex, contrast, set_custom_themes


class ThemeEditor:
    """Create a new theme, or edit one you made before (theme_id)."""

    def __init__(self, app, theme_id=None):
        self.app, self.theme_id = app, theme_id
        saved = dict(app.inventory.get_setting("custom_themes", {}) or {})
        data = saved.get(theme_id, {}) if theme_id else {}
        self.colors = dict(data.get("colors", {}))           # only what the user changed
        self.window = tk.Toplevel(app.root)                   # a pop-up, not a second app
        self.window.title("Edit theme" if theme_id else "Create theme")
        self.window.transient(app.root)
        self.window.configure(bg=T["bg"])
        self.window.grab_set()
        frame = ttk.Frame(self.window, padding=20)
        frame.pack(fill="both", expand=True)
        left = ttk.Frame(frame)
        left.pack(side="left", fill="y")
        right = ttk.Frame(frame)
        right.pack(side="left", fill="both", expand=True, padx=(24, 0))

        ttk.Label(left, text="Edit theme" if theme_id else "Create a theme", style="Title.TLabel").pack(anchor="w", pady=(0, 12))
        self.name = tk.StringVar(value=data.get("name", "My theme"))
        self.base = tk.StringVar(value=data.get("base", "modern"))
        options = data.get("options", {})
        self.title = tk.StringVar(value=options.get("title", ""))
        self.uppercase = tk.BooleanVar(value=bool(options.get("uppercase", False)))
        self.bevel = tk.BooleanVar(value=bool(options.get("bevel", False)))
        has_band = self.colors["header_band"] is not None if "header_band" in self.colors else bool(THEMES.get(self.base.get(), THEMES["modern"])["header_band"])
        self.header_on = tk.BooleanVar(value=has_band)
        grid = ttk.Frame(left)
        grid.pack(fill="x")
        ttk.Label(grid, text="Name").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(grid, textvariable=self.name, width=22).grid(row=0, column=1, columnspan=2, sticky="ew", pady=4)
        ttk.Label(grid, text="Start from").grid(row=1, column=0, sticky="w", pady=4)
        base_box = ttk.Combobox(grid, values=[t["name"] for t in THEMES.values()], state="readonly", width=20)
        base_box.set(THEMES[self.base.get()]["name"] if self.base.get() in THEMES else "Modern")
        base_box.grid(row=1, column=1, columnspan=2, sticky="ew", pady=4)
        base_box.bind("<<ComboboxSelected>>", lambda e: self.set_base(base_box.get()))
        ttk.Label(grid, text="Title text").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(grid, textvariable=self.title, width=22).grid(row=2, column=1, columnspan=2, sticky="ew", pady=4)

        ttk.Label(left, text="COLORS", style="Eyebrow.TLabel").pack(anchor="w", pady=(14, 4))
        self.swatches = {}
        colors = ttk.Frame(left)
        colors.pack(fill="x")
        for row, (key, label) in enumerate(EDITABLE):
            ttk.Label(colors, text=label).grid(row=row, column=0, sticky="w", pady=3)
            swatch = tk.Label(colors, width=4, relief="solid", bd=1, cursor="hand2")
            swatch.grid(row=row, column=1, padx=8, pady=3)
            swatch.bind("<Button-1>", lambda e, k=key: self.pick(k))
            ttk.Button(colors, text="Change…", style="Small.TButton", command=lambda k=key: self.pick(k)).grid(row=row, column=2, pady=3)
            ttk.Button(colors, text="Reset", style="Small.TButton", command=lambda k=key: self.reset(k)).grid(row=row, column=3, padx=(4, 0), pady=3)
            self.swatches[key] = swatch

        ttk.Label(left, text="STYLE", style="Eyebrow.TLabel").pack(anchor="w", pady=(14, 4))
        for text, var in (("Colored header bar", self.header_on), ("ALL-CAPS tabs and headings", self.uppercase),
                          ("Retro 3-D bevels", self.bevel)):
            ttk.Checkbutton(left, text=text, variable=var, command=self.redraw).pack(anchor="w")

        ttk.Label(right, text="PREVIEW", style="Eyebrow.TLabel").pack(anchor="w")
        self.preview = tk.Canvas(right, width=340, height=112, highlightthickness=1, highlightbackground=T["border"])
        self.preview.pack(anchor="w", pady=(4, 8))
        self.warning = tk.StringVar()
        ttk.Label(right, textvariable=self.warning, style="Error.TLabel", wraplength=340, justify="left").pack(anchor="w")
        buttons = ttk.Frame(right)
        buttons.pack(side="bottom", fill="x", pady=(16, 0))
        ttk.Button(buttons, text="Save and use", style="Primary.TButton", command=self.save).pack(side="right")
        ttk.Button(buttons, text="Cancel", command=self.window.destroy).pack(side="right", padx=8)
        if theme_id:
            ttk.Button(buttons, text="Delete", style="Danger.TButton", command=self.delete).pack(side="left")
        self.window.bind("<Escape>", lambda e: self.window.destroy())
        self.redraw()
        app.center_dialog(self.window)

    # ------------------------------------------------------------ editing
    def set_base(self, name):
        self.base.set(next((key for key, t in THEMES.items() if t["name"] == name), "modern"))
        self.bevel.set(THEMES[self.base.get()]["bevel"])
        self.uppercase.set(THEMES[self.base.get()]["uppercase"])
        if "header_band" not in self.colors:
            self.header_on.set(bool(THEMES[self.base.get()]["header_band"]))
        self.redraw()

    def current_colors(self):
        colors = dict(self.colors)
        if not self.header_on.get():
            colors["header_band"] = None
        elif colors.get("header_band", "unset") is None:
            colors.pop("header_band")
        return colors

    def current_options(self):
        return {"title": self.title.get().strip(), "uppercase": self.uppercase.get(), "bevel": self.bevel.get()}

    def current_theme(self):
        theme = build_custom(self.base.get(), self.current_colors(), self.current_options())
        if self.header_on.get() and not theme["header_band"]:
            theme = build_custom(self.base.get(), {**self.current_colors(), "header_band": theme["accent"]}, self.current_options())
        return theme

    def pick(self, key):
        current = self.current_theme().get(key) or "#000000"
        color = colorchooser.askcolor(color=current, parent=self.window, title="Choose a color")[1]
        if color:
            self.colors[key] = clean_hex(color)
            if key == "header_band":
                self.header_on.set(True)
            self.redraw()                                     # live preview after every change

    def reset(self, key):
        self.colors.pop(key, None)
        self.redraw()

    def redraw(self):
        theme = self.current_theme()
        for key, swatch in self.swatches.items():
            swatch.configure(bg=theme.get(key) or T["bg"], text="" if theme.get(key) else "off")
        self.app.draw_theme_preview(self.preview, theme, 340)
        problems = []
        if contrast(theme["ink"], theme["bg"]) < 4.5:
            problems.append("Text is hard to read on the background")
        if contrast(theme["ink"], theme["surface"]) < 4.5:
            problems.append("Text is hard to read on cards")
        if contrast(theme["primary_text"], theme["primary"]) < 3:
            problems.append("Button text is hard to read")
        self.warning.set(("⚠ " + "; ".join(problems) + ".") if problems else "")

    # ------------------------------------------------------------- saving
    def save(self):
        name = self.name.get().strip()
        if not name:
            self.warning.set("Give your theme a name.")
            return
        saved = dict(self.app.inventory.get_setting("custom_themes", {}) or {})
        if any(data.get("name", "").casefold() == name.casefold() and key != self.theme_id for key, data in saved.items()) \
                or name.casefold() in (t["name"].casefold() for t in THEMES.values()):
            self.warning.set("That name is already used. Choose another.")
            return
        key = self.theme_id or "custom_" + (re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_")[:20] or "theme") + "_" + uuid.uuid4().hex[:6]
        saved[key] = {"name": name[:24], "base": self.base.get(), "colors": self.current_colors(), "options": self.current_options()}
        self.app.inventory.set_setting("custom_themes", saved)
        set_custom_themes(saved)
        self.window.destroy()
        self.app.use_custom_theme(key)

    def delete(self):
        if not messagebox.askyesno("Delete theme?", f"Delete the theme “{self.name.get()}”?", parent=self.window):
            return
        saved = dict(self.app.inventory.get_setting("custom_themes", {}) or {})
        saved.pop(self.theme_id, None)
        self.app.inventory.set_setting("custom_themes", saved)
        set_custom_themes(saved)
        self.window.destroy()
        self.app.use_custom_theme(None)


__all__ = ["ThemeEditor", "CUSTOM_THEMES"]

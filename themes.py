"""Homebase looks: four built-in themes plus your own font, size, color, and density choices.

Every color, font, and border the interface uses comes from the active theme dictionary T,
so adding a theme means adding one dictionary here.
"""
from __future__ import annotations

import sys

try:
    from tkinter import font as tkfont
    from tkinter import ttk
except ImportError:
    tkfont = ttk = None

IS_MAC = sys.platform == "darwin"

_BASE = dict(
    bevel=False, is_dark=False, header_band=None, header_fg="#ffffff", header_sub_fg="#dfe6ff", stripe=None,
    card_relief="flat", card_bd=0, card_highlight=1, uppercase=False, title_prefix="",
    button_padding=(12, 8), primary_padding=(15, 9), row_height=36, title_size=26,
)

THEMES = {
    "modern": dict(_BASE,
        name="Modern", description="Clean and calm: soft greens, roomy rows. The original Homebase look.",
        body_fonts=("Helvetica Neue", "SF Pro Text", "Segoe UI", "Inter", "Ubuntu", "DejaVu Sans", "Helvetica", "Arial"),
        display_fonts=("Helvetica Neue", "SF Pro Display", "Segoe UI Semibold", "Segoe UI", "Inter", "Ubuntu", "DejaVu Sans", "Helvetica"),
        title_fonts=("Helvetica Neue", "Segoe UI Semibold", "Segoe UI", "DejaVu Sans", "Helvetica"),
        title="homebase.", subtitle="Universal home / locational inventory · Local and encrypted",
        bg="#f4f6f0", surface="#ffffff", ink="#243b30", muted="#748275", eyebrow="#7d9275",
        accent="#175b4c", accent_hover="#104737", accent_disabled="#adc3b8",
        primary="#175b4c", primary_hover="#104737", primary_text="#ffffff",
        button="#e7eee1", button_hover="#dbe8d3", button_text="#243b30",
        field="#ffffff", border="#dfe7d8", light="#e7eee1", dark="#c9d6c0",
        select_bg="#dcebd5", select_fg="#175b4c", row_alt="#f8faf5",
        heading_bg="#e9eee3", heading_fg="#6e8062", tab_bg="#e3eadc", tab_selected="#ffffff", tab_strip="#e9eee3",
        warning="#a77b3d", stat_warning="#9c7843", error="#ab443b", danger="#a65245",
        photo_bg="#eaf0e3", placeholder=("#d7e1cc", "#bdcdb0", "#a4bd98", "#8da07e", "#7c936e", "#87967b"),
    ),
    "retro": dict(_BASE,
        name="Retro", description="1990s desktop: grey bevels, navy title bar, pixel-style type.",
        body_fonts=("Geneva", "Tahoma", "MS Sans Serif", "Verdana", "DejaVu Sans", "Arial", "Helvetica"),
        display_fonts=("Monaco", "Lucida Console", "Courier New", "DejaVu Sans Mono", "Courier", "Helvetica"),
        title_fonts=("Monaco", "Lucida Console", "Courier New", "DejaVu Sans Mono", "Helvetica"),
        title="HOMEBASE", subtitle="Home Inventory System  v3.0  ·  Local disk only", title_prefix="▣ ",
        bg="#c0c0c0", surface="#c0c0c0", ink="#000000", muted="#3c3c3c", eyebrow="#000080",
        accent="#000080", accent_hover="#1084d0", accent_disabled="#808080",
        primary="#c0c0c0", primary_hover="#d4d0c8", primary_text="#000000",
        button="#c0c0c0", button_hover="#d4d0c8", button_text="#000000",
        field="#ffffff", border="#000000", light="#ffffff", dark="#808080",
        select_bg="#000080", select_fg="#ffffff", row_alt="#efefef",
        heading_bg="#c0c0c0", heading_fg="#000000", tab_bg="#a8a8a8", tab_selected="#c0c0c0", tab_strip="#808080",
        warning="#a00000", stat_warning="#a00000", error="#a00000", danger="#a00000",
        photo_bg="#008080", placeholder=("#ffff80", "#c0c000", "#808000", "#000000", "#000000", "#ffffff"),
        bevel=True, header_band="#000080", card_relief="sunken", card_bd=2, card_highlight=0, uppercase=True,
        button_padding=(10, 5), primary_padding=(12, 6), row_height=26, title_size=22,
    ),
    "blossom": dict(_BASE,
        name="Blossom", description="Soft pinks and lavender, rounded type, little hearts. Sweet and cozy.",
        body_fonts=("Avenir Next", "Avenir", "Nunito", "Quicksand", "Segoe UI", "Trebuchet MS", "Helvetica Neue", "DejaVu Sans", "Helvetica"),
        display_fonts=("Avenir Next Demi Bold", "Avenir Next", "Nunito", "Segoe UI Semibold", "Trebuchet MS", "DejaVu Sans", "Helvetica"),
        title_fonts=("Snell Roundhand", "Savoye LET", "Segoe Script", "Lucida Handwriting", "Brush Script MT", "URW Chancery L", "Z003", "DejaVu Serif", "Helvetica"),
        title="Homebase", subtitle="✿  my cozy little inventory  ✿", title_prefix="♡ ",
        bg="#fff5f9", surface="#ffffff", ink="#5a2a4a", muted="#a1708f", eyebrow="#c45a92",
        accent="#e75a9b", accent_hover="#cf3f83", accent_disabled="#f3b9d3",
        primary="#e75a9b", primary_hover="#cf3f83", primary_text="#ffffff",
        button="#ffe4ef", button_hover="#ffd3e6", button_text="#5a2a4a",
        field="#ffffff", border="#f6c6dc", light="#fff0f6", dark="#f2b8d2",
        select_bg="#ffd6e8", select_fg="#8a1f55", row_alt="#fff7fb",
        heading_bg="#fbe3f0", heading_fg="#9b4d7a", tab_bg="#f9d9e9", tab_selected="#ffffff", tab_strip="#f7e1f3",
        warning="#d9822b", stat_warning="#b07ad8", error="#c2335f", danger="#c2335f",
        photo_bg="#fbe7f3", placeholder=("#ffd1e6", "#f7b5d5", "#d8b4f0", "#e58bb8", "#c98ae8", "#b77aa0"),
        header_band="#fde3f0", header_fg="#d94f8f", header_sub_fg="#a1708f", stripe="#d8b4f0",
        title_size=32, row_height=38,
    ),
    "voltage": dict(_BASE,
        name="Voltage", description="Black and neon green, heavy condensed type. Garage, gym, and energy-drink vibes.",
        body_fonts=("Avenir Next Condensed", "DIN Condensed", "Bahnschrift", "Roboto Condensed", "Arial Narrow", "DejaVu Sans Condensed", "Helvetica Neue", "Helvetica"),
        display_fonts=("Impact", "Haettenschweiler", "Anton", "Bebas Neue", "DIN Condensed", "Avenir Next Condensed", "DejaVu Sans Condensed", "Helvetica"),
        title_fonts=("Impact", "Haettenschweiler", "Anton", "Bebas Neue", "DejaVu Sans Condensed", "Helvetica"),
        title="HOMEBASE", subtitle="///  GEAR. STASH. LOCKED DOWN.  ///", title_prefix="⚡ ",
        bg="#0b0b0b", surface="#151515", ink="#f1f1f1", muted="#9b9b9b", eyebrow="#7cff00",
        accent="#7cff00", accent_hover="#a8ff4f", accent_disabled="#3b5a1a",
        primary="#7cff00", primary_hover="#a8ff4f", primary_text="#000000",
        button="#222222", button_hover="#2e2e2e", button_text="#f1f1f1",
        field="#1a1a1a", border="#2c2c2c", light="#2a2a2a", dark="#000000",
        select_bg="#7cff00", select_fg="#000000", row_alt="#121212",
        heading_bg="#000000", heading_fg="#7cff00", tab_bg="#1c1c1c", tab_selected="#0b0b0b", tab_strip="#000000",
        warning="#ffd400", stat_warning="#ffd400", error="#ff4d4d", danger="#ff4d4d",
        photo_bg="#000000", placeholder=("#7cff00", "#4fa800", "#2e6600", "#000000", "#000000", "#7cff00"),
        is_dark=True, header_band="#000000", header_fg="#7cff00", header_sub_fg="#9b9b9b", stripe="#7cff00",
        uppercase=True, title_size=34, row_height=34,
    ),
}
DENSITY = {"Compact": 0.8, "Comfortable": 1.0, "Spacious": 1.25}
T = dict(THEMES["modern"])   # the active theme, shared by every window
_font_cache = {}


def pick_font(root, candidates):
    key = tuple(candidates)
    if key not in _font_cache:
        available = {name.casefold(): name for name in tkfont.families(root)}
        fallback = tkfont.nametofont("TkDefaultFont", root=root).actual("family")
        _font_cache[key] = next((available[c.casefold()] for c in candidates if c.casefold() in available), fallback)
    return _font_cache[key]


def font_families(root):
    return sorted({name for name in tkfont.families(root) if not name.startswith("@")}, key=str.casefold)


def shade(color, factor):
    """Darken (factor < 1) or lighten (factor > 1) a #rrggbb color."""
    color = color.lstrip("#")
    channels = [int(color[i:i + 2], 16) for i in (0, 2, 4)]
    if factor < 1:
        channels = [round(c * factor) for c in channels]
    else:
        channels = [round(c + (255 - c) * (factor - 1)) for c in channels]
    return "#" + "".join(f"{max(0, min(255, c)):02x}" for c in channels)


def readable_on(color):
    color = color.lstrip("#")
    r, g, b = (int(color[i:i + 2], 16) for i in (0, 2, 4))
    return "#000000" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#ffffff"


class Fonts:
    """Font tuples for the active theme and the user's size and font choices."""

    def __init__(self, root, settings):
        self.scale = max(0.6, min(2.5, float(settings.get("text_scale", 100)) / 100))
        self.body = settings.get("font_body") or pick_font(root, T["body_fonts"])
        self.display = settings.get("font_display") or pick_font(root, T["display_fonts"])
        self.title_family = settings.get("font_display") or pick_font(root, T["title_fonts"])

    def __call__(self, size, weight="normal", display=False, title=False):
        family = self.title_family if title else self.display if display else self.body
        scaled = max(7, round(size * self.scale))
        return (family, scaled, weight) if weight != "normal" else (family, scaled)


# ------------------------------------------------------------------ custom themes
CUSTOM_THEMES = {}   # filled from your saved settings by set_custom_themes()
EDITABLE = (("bg", "Background"), ("surface", "Cards and panels"), ("ink", "Text"), ("muted", "Quiet text"),
            ("accent", "Accent"), ("button", "Buttons"), ("header_band", "Header bar"), ("warning", "Low-stock warning"))


def clean_hex(value):
    """'#AbC' or 'abcdef' → '#aabbcc'; anything else → None."""
    text = str(value or "").strip().lstrip("#").lower()
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) == 6 and all(ch in "0123456789abcdef" for ch in text):
        return "#" + text
    return None


def mix(a, b, amount=0.5):
    """Blend two #rrggbb colors: amount 0 gives a, 1 gives b."""
    a, b = clean_hex(a), clean_hex(b)
    channels = [round(int(a[i:i + 2], 16) * (1 - amount) + int(b[i:i + 2], 16) * amount) for i in (1, 3, 5)]
    return "#" + "".join(f"{c:02x}" for c in channels)


def luminance(color):
    """Relative brightness from 0 (black) to 1 (white), using the WCAG formula."""
    color = clean_hex(color)
    def linear(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (linear(int(color[i:i + 2], 16)) for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    """WCAG contrast ratio, 1 to 21. Body text should be at least 4.5."""
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def best_text(color):
    """Black or white, whichever is easier to read on `color`."""
    return max(("#000000", "#ffffff"), key=lambda text: contrast(text, color))


def build_custom(base, colors, options=None):
    """A full theme from a built-in base plus the few colors you picked (the rest is worked out)."""
    t = dict(THEMES.get(base, THEMES["modern"]))
    options = options or {}
    for key in ("uppercase", "bevel"):
        if key in options:
            t[key] = bool(options[key])
    if options.get("title"):
        t["title"], t["title_prefix"] = str(options["title"])[:30], ""
    if options.get("subtitle") is not None:
        t["subtitle"] = str(options["subtitle"])[:80]
    picked = {key: clean_hex(value) for key, value in (colors or {}).items() if key in dict(EDITABLE)}
    if "header_band" in (colors or {}) and not picked.get("header_band"):
        picked["header_band"] = None                  # header bar turned off
    picked = {k: v for k, v in picked.items() if v is not None or k == "header_band"}
    t.update(picked)
    if "bg" in picked:
        t.update(tab_strip=mix(t["bg"], t["ink"], 0.06), heading_bg=mix(t["bg"], t["surface"]),
                 photo_bg=mix(t["bg"], t["accent"], 0.12))
    if "surface" in picked:
        t.update(field=t["surface"], tab_selected=t["surface"])
    if "bg" in picked or "surface" in picked:
        t.update(row_alt=mix(t["surface"], t["bg"]), tab_bg=mix(t["bg"], t["ink"], 0.1))
    if picked.keys() & {"bg", "ink", "surface"}:
        t.update(border=mix(t["bg"], t["ink"], 0.15), dark=mix(t["bg"], t["ink"], 0.3), light=mix(t["surface"], "#ffffff", 0.5))
    if "muted" in picked:
        t["heading_fg"] = t["muted"]
    if "accent" in picked:
        a = t["accent"]
        t.update(accent_hover=shade(a, 0.8), eyebrow=a, accent_disabled=mix(a, t["bg"], 0.6))
        if not t["bevel"]:
            t.update(primary=a, primary_hover=shade(a, 0.8), primary_text=best_text(a))
        if t.get("stripe"):
            t["stripe"] = a
    if "button" in picked:
        t.update(button_hover=mix(t["button"], t["ink"], 0.08), button_text=best_text(t["button"]))
    if "header_band" in picked and t["header_band"]:
        band = t["header_band"]
        t["header_fg"] = t["accent"] if contrast(t["accent"], band) >= 4.5 else best_text(band)
        t["header_sub_fg"] = mix(t["header_fg"], band, 0.35)
    if "warning" in picked:
        t["stat_warning"] = t["warning"]
    t["is_dark"] = luminance(t["bg"]) < 0.35
    if t["is_dark"] and "accent" in picked:
        t.update(select_bg=t["accent"], select_fg=best_text(t["accent"]), heading_fg=t["accent"])
    elif "accent" in picked:
        t.update(select_bg=mix(t["accent"], t["surface"], 0.8), select_fg=shade(t["accent"], 0.7))
    t["description"] = options.get("description") or f"Your theme, based on {THEMES.get(base, THEMES['modern'])['name']}."
    return t


def set_custom_themes(saved):
    """Rebuild CUSTOM_THEMES from settings['custom_themes'] = {id: {name, base, colors, options}}."""
    CUSTOM_THEMES.clear()
    for key, data in (saved or {}).items():
        try:
            theme = build_custom(data.get("base", "modern"), data.get("colors", {}), data.get("options", {}))
        except Exception:
            continue          # skip a damaged entry rather than failing to start
        theme["name"] = str(data.get("name") or "My theme")[:24]
        theme["custom"] = True
        CUSTOM_THEMES[key] = theme


def all_themes():
    return {**THEMES, **CUSTOM_THEMES}


def apply_theme(root, settings):
    """Make `settings` the active look for every ttk widget. Returns a Fonts helper."""
    T.clear()
    T.update(all_themes().get(settings.get("theme"), THEMES["modern"]))
    accent = settings.get("accent") or ""
    if accent.startswith("#") and len(accent) == 7:
        T.update(accent=accent, accent_hover=shade(accent, 0.8), eyebrow=accent)
        if not T["bevel"]:
            T.update(primary=accent, primary_hover=shade(accent, 0.8), primary_text=readable_on(accent))
        if T["is_dark"]:
            T.update(select_bg=accent, select_fg=readable_on(accent), heading_fg=accent, header_fg=accent, stripe=accent)
        else:
            T.update(select_fg=shade(accent, 0.7), select_bg=shade(accent, 1.8))
    density = DENSITY.get(settings.get("density"), 1.0)
    fonts = Fonts(root, settings)
    root.configure(bg=T["bg"])
    style = ttk.Style(root)
    style.theme_use("clam")   # clam honors custom colors on macOS, Windows, and Linux alike
    bevel = T["bevel"]
    pad = lambda p: tuple(max(2, round(v * density)) for v in p)
    style.configure(".", font=fonts(10), foreground=T["ink"], background=T["bg"], troughcolor=T["dark"] if bevel or T["is_dark"] else T["light"],
                    selectbackground=T["select_bg"], selectforeground=T["select_fg"], fieldbackground=T["field"], insertcolor=T["ink"],
                    lightcolor=T["light"], darkcolor=T["dark"], bordercolor=T["border"])
    style.configure("TFrame", background=T["bg"])
    style.configure("Card.TFrame", background=T["surface"])
    style.configure("Strip.TFrame", background=T["tab_strip"])
    style.configure("TLabel", background=T["bg"], foreground=T["ink"], padding=0)
    style.configure("Card.TLabel", background=T["surface"])
    style.configure("Title.TLabel", font=fonts(19, "bold", display=True), foreground=T["ink"])
    style.configure("Section.TLabel", font=fonts(13, "bold", display=True), foreground=T["accent"] if bevel or T["is_dark"] else T["ink"])
    style.configure("Hint.TLabel", font=fonts(9), foreground=T["muted"])
    style.configure("Error.TLabel", font=fonts(10), foreground=T["error"])
    style.configure("Good.TLabel", font=fonts(10), foreground=T["accent"])
    style.configure("Eyebrow.TLabel", font=fonts(9, "bold"), foreground=T["eyebrow"])
    style.configure("Accent.TLabel", foreground=T["accent"])
    style.configure("Link.TLabel", foreground=T["accent"], font=fonts(10, "underline"))
    style.configure("TButton", padding=pad(T["button_padding"]), background=T["button"], foreground=T["button_text"],
                    borderwidth=2 if bevel else 0, focusthickness=2, focuscolor=T["accent_hover"], relief="raised" if bevel else "flat")
    style.map("TButton", background=[("disabled", T["button"]), ("pressed", T["dark"] if not T["is_dark"] else T["light"]), ("active", T["button_hover"])],
              foreground=[("disabled", T["accent_disabled"] if not T["is_dark"] else "#555555")],
              lightcolor=[("pressed", T["dark"])], darkcolor=[("pressed", T["light"])], relief=[("pressed", "sunken")])
    style.configure("Primary.TButton", background=T["primary"], foreground=T["primary_text"], padding=pad(T["primary_padding"]),
                    font=fonts(10, "bold"), borderwidth=3 if bevel else 0)
    style.map("Primary.TButton", background=[("disabled", T["accent_disabled"]), ("pressed", T["primary_hover"]), ("active", T["primary_hover"])],
              foreground=[("disabled", T["bg"])])
    style.configure("Danger.TButton", foreground=T["danger"])
    style.configure("Small.TButton", padding=pad((6, 3)), font=fonts(9))
    for widget in ("TCheckbutton", "TRadiobutton"):
        style.configure(widget, background=T["bg"], foreground=T["ink"], indicatorbackground=T["field"], indicatorforeground=T["accent"])
        style.map(widget, background=[("active", T["bg"])], indicatorbackground=[("selected", T["field"])])
    style.configure("Card.TRadiobutton", background=T["surface"], font=fonts(12, "bold", display=True))
    style.map("Card.TRadiobutton", background=[("active", T["surface"])])
    for name in ("Card.TCheckbutton", "CardItem.TRadiobutton"):
        style.configure(name, background=T["surface"], foreground=T["ink"])
        style.map(name, background=[("active", T["surface"])])
    entry_pad = 4 if bevel else max(3, round(7 * density))
    style.configure("TEntry", padding=entry_pad, fieldbackground=T["field"], foreground=T["ink"])
    style.map("TEntry", fieldbackground=[("disabled", T["bg"])])
    style.configure("TCombobox", padding=max(3, entry_pad - 1), fieldbackground=T["field"], foreground=T["ink"], arrowcolor=T["ink"], background=T["button"])
    style.map("TCombobox", fieldbackground=[("readonly", T["field"])], foreground=[("readonly", T["ink"])],
              selectbackground=[("readonly", T["field"])], selectforeground=[("readonly", T["ink"])])
    style.configure("TSpinbox", padding=max(3, entry_pad - 1), fieldbackground=T["field"], foreground=T["ink"], arrowcolor=T["ink"], background=T["button"])
    for orient in ("Vertical", "Horizontal"):
        style.configure(f"{orient}.TScrollbar", background=T["button"], arrowcolor=T["ink"], troughcolor=T["bg"] if T["is_dark"] else T["light"])
        style.configure(f"{orient}.TScale", background=T["accent"], troughcolor=T["light"])
    style.configure("Treeview", background=T["field"], fieldbackground=T["field"], foreground=T["ink"], borderwidth=2 if bevel else 0,
                    rowheight=round(T["row_height"] * fonts.scale * density), font=fonts(10))
    style.configure("Treeview.Heading", background=T["heading_bg"], foreground=T["heading_fg"], font=fonts(9, "bold", display=T["is_dark"]),
                    padding=(6, 4) if bevel else (8, round(10 * density)), relief="raised" if bevel else "flat")
    style.map("Treeview", background=[("selected", T["select_bg"])], foreground=[("selected", T["select_fg"])])
    style.map("Treeview.Heading", background=[("active", T["button_hover"])])
    style.configure("TLabelframe", background=T["bg"], bordercolor=T["dark"] if bevel else T["border"], relief="groove" if bevel else "solid", borderwidth=2 if bevel else 1)
    style.configure("TLabelframe.Label", background=T["bg"], foreground=T["accent"] if bevel or T["is_dark"] else T["ink"], font=fonts(11, "bold", display=True))
    style.configure("TPanedwindow", background=T["bg"])
    style.configure("Sash", sashthickness=6, gripcount=0, background=T["bg"])
    style.configure("TNotebook", background=T["bg"], borderwidth=0)
    style.configure("TNotebook.Tab", background=T["tab_bg"], foreground=T["ink"], padding=(12, 6))
    style.map("TNotebook.Tab", background=[("selected", T["tab_selected"])], foreground=[("selected", T["accent"])])
    for pattern, value in (("*TCombobox*Listbox.background", T["field"]), ("*TCombobox*Listbox.foreground", T["ink"]),
                           ("*TCombobox*Listbox.selectBackground", T["select_bg"]), ("*TCombobox*Listbox.selectForeground", T["select_fg"]),
                           ("*Menu.background", T["surface"]), ("*Menu.foreground", T["ink"]),
                           ("*Menu.activeBackground", T["select_bg"]), ("*Menu.activeForeground", T["select_fg"])):
        root.option_add(pattern, value)
    root.option_add("*TCombobox*Listbox.font", fonts(10))
    for widget in ("TCombobox", "TEntry", "TSpinbox"):
        root.option_add(f"*{widget}.font", fonts(10))
    return fonts

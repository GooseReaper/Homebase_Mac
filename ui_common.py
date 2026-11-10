"""Shared Tkinter helpers: photos, scrolling panels, drag and drop, and background jobs."""
from __future__ import annotations

import base64
import io
import math
import queue
import sys
import threading
from pathlib import Path

from inventory import MAX_PHOTO_BYTES, InventoryError
from themes import T

try:
    import tkinter as tk
    from tkinter import ttk
except ImportError:
    tk = ttk = None

try:
    from PIL import Image, ImageOps, ImageTk
except ImportError:
    Image = ImageOps = ImageTk = None

IS_MAC = sys.platform == "darwin"
MOD = "⌘" if IS_MAC else "Ctrl+"
PHOTO_TYPES = (".png", ".gif", ".jpg", ".jpeg", ".webp")
# Right-click and middle-click buttons differ on macOS Tk.
CONTEXT_BUTTONS = ("<Button-2>", "<Control-Button-1>") if IS_MAC else ("<Button-3>",)
MIDDLE_BUTTON = "<Button-3>" if IS_MAC else "<Button-2>"
NEW_TAB_CLICK = "<Command-Button-1>" if IS_MAC else "<Control-Button-1>"


def accel(key, shift=False):
    """Menu shortcut text in the form each platform's Tk expects."""
    if IS_MAC:
        return f"Command-{'Shift-' if shift else ''}{key}"
    return f"Ctrl+{'Shift+' if shift else ''}{key}"


def currency(value):
    return f"${value:,.2f}"


def paint_photo(canvas, data=b"", title="Photo placeholder", font=None):
    """Display an embedded photo, or draw a placeholder box with no image files needed."""
    canvas.delete("all")
    canvas.photo_ref = None  # Tk images must stay referenced for their lifetime.
    width = int(canvas.winfo_width()) if canvas.winfo_width() > 20 else int(canvas.cget("width"))
    height = int(canvas.cget("height"))
    if data:
        try:
            if Image is not None:
                with Image.open(io.BytesIO(data)) as source:
                    image = ImageOps.exif_transpose(source).convert("RGB")
                    image.thumbnail((width - 16, height - 16))
                    picture = ImageTk.PhotoImage(image)
            else:
                picture = tk.PhotoImage(data=base64.b64encode(data).decode("ascii"))
                factor = max(1, math.ceil(picture.width() / (width - 16)), math.ceil(picture.height() / (height - 16)))
                picture = picture.subsample(factor, factor)
            canvas.photo_ref = picture
            canvas.create_image(width // 2, height // 2, image=picture)
            return True
        except Exception:
            title = "Photo saved · preview unavailable"
    top, left, right, outline, line, text = T["placeholder"]
    x, y = width // 2, height // 2 - 12
    canvas.create_polygon(x - 36, y - 17, x, y - 35, x + 36, y - 17, x, y + 2, fill=top, outline=outline, width=2)
    canvas.create_polygon(x - 36, y - 17, x, y + 2, x, y + 40, x - 36, y + 21, fill=left, outline=outline, width=2)
    canvas.create_polygon(x, y + 2, x + 36, y - 17, x + 36, y + 21, x, y + 40, fill=right, outline=outline, width=2)
    canvas.create_line(x - 18, y - 26, x + 18, y - 8, x + 18, y + 11, fill=line, width=3)
    canvas.create_text(x, height - 17, text=title, fill=text, font=font or ("TkDefaultFont", 9))
    return not data


def read_photo(path):
    """Read and check an image file. Returns (bytes, file name)."""
    file = Path(path)
    if file.suffix.casefold() not in PHOTO_TYPES:
        raise InventoryError(f"{file.name} isn't a photo. Use PNG, GIF, JPG, or WebP.")
    if file.stat().st_size > MAX_PHOTO_BYTES:
        raise InventoryError(f"{file.name} is over 10 MB. Choose a smaller photo.")
    data = file.read_bytes()
    check_photo_bytes(data)
    return data, file.name


def check_photo_bytes(data):
    if Image is not None:
        try:
            with Image.open(io.BytesIO(data)) as image:
                image.verify()
        except Exception:
            raise InventoryError("This photo file is damaged or isn't a supported image.") from None
    else:
        try:
            tk.PhotoImage(data=base64.b64encode(data).decode("ascii"))
        except tk.TclError:
            raise InventoryError("Use PNG/GIF, or install Pillow for JPG/WebP photos.") from None


def photo_from_clipboard():
    """A photo copied to the clipboard (an image, or a copied image file). Returns (bytes, name) or None."""
    if Image is None:
        raise InventoryError("Pasting photos needs Pillow (Start_Homebase.command installs it).")
    try:
        from PIL import ImageGrab
        grabbed = ImageGrab.grabclipboard()
    except Exception:
        return None
    if isinstance(grabbed, list):
        for name in grabbed:
            if Path(name).suffix.casefold() in PHOTO_TYPES:
                return read_photo(name)
        return None
    if grabbed is None:
        return None
    out = io.BytesIO()
    grabbed.convert("RGB").save(out, "JPEG", quality=90)
    return out.getvalue(), "pasted-photo.jpg"


# ----------------------------------------------------------------------------- scrolling
_scroll_areas = []


def _on_wheel(event):
    widget = event.widget
    if isinstance(widget, str) or isinstance(widget, (tk.Text, tk.Listbox, ttk.Treeview)):
        return None   # these scroll themselves
    _scroll_areas[:] = [(o, v) for o, v in _scroll_areas if o.winfo_exists()]
    while widget is not None:
        for outer, viewport in _scroll_areas:
            if widget is outer:
                num = getattr(event, "num", None)
                delta = -1 if num == 4 else 1 if num == 5 else 0
                if not delta and getattr(event, "delta", 0):
                    delta = -1 if event.delta > 0 else 1
                if delta:
                    viewport.yview_scroll(delta, "units")
                    return "break"
        widget = getattr(widget, "master", None)
    return None


def install_wheel(root):
    if getattr(root, "_homebase_wheel", False):
        return
    root._homebase_wheel = True
    root.bind_all("<MouseWheel>", _on_wheel, add="+")
    if sys.platform.startswith("linux"):
        root.bind_all("<Button-4>", _on_wheel, add="+")
        root.bind_all("<Button-5>", _on_wheel, add="+")


def scroll_frame(parent, width=280, padding=0):
    """A scrollable panel, so forms and details fit smaller screens."""
    outer = ttk.Frame(parent, width=width)
    viewport = tk.Canvas(outer, width=width, height=300, highlightthickness=0, bg=T["bg"])
    bar = ttk.Scrollbar(outer, orient="vertical", command=viewport.yview)
    viewport.configure(yscrollcommand=bar.set)
    viewport.pack(side="left", fill="both", expand=True)
    bar.pack(side="right", fill="y")
    inner = ttk.Frame(viewport, padding=padding)
    window_id = viewport.create_window((0, 0), window=inner, anchor="nw")
    inner.bind("<Configure>", lambda event: viewport.configure(scrollregion=viewport.bbox("all")))
    viewport.bind("<Configure>", lambda event: viewport.itemconfigure(window_id, width=event.width))
    _scroll_areas.append((outer, viewport))
    install_wheel(outer.winfo_toplevel())
    return outer, inner


# ---------------------------------------------------------------------- drag and drop
def dnd_available(widget):
    """True when the tkdnd extension (from the tkinterdnd2 package) is loaded in this Tk."""
    try:
        widget.tk.call("package", "present", "tkdnd")
        return True
    except Exception:
        return False


def make_root():
    """A Tk root with drag-and-drop support when tkinterdnd2 is installed."""
    try:
        from tkinterdnd2 import TkinterDnD
        return TkinterDnD.Tk()
    except Exception:
        return tk.Tk()


def register_drop(widget, on_files):
    """Call on_files([paths]) when files are dropped on `widget`. Returns False without tkdnd."""
    if not dnd_available(widget):
        return False

    def dropped(data):
        try:
            paths = [p for p in widget.tk.splitlist(data) if p]
            widget.after_idle(lambda: on_files(paths))
        except Exception:
            pass
        return "copy"

    try:
        command = widget.register(dropped)
        widget.tk.call("tkdnd::drop_target", "register", widget._w, ("DND_Files",))
        widget.tk.call("bind", widget._w, "<<Drop>>", f"{command} %D")
        widget.tk.call("bind", widget._w, "<<DropEnter>>", "return copy")
        widget.tk.call("bind", widget._w, "<<DropPosition>>", "return copy")
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------- background jobs
class Background:
    """Run slow work (AI requests) off the UI thread and deliver results on the UI thread."""

    def __init__(self, root):
        self.root, self.results, self.busy = root, queue.Queue(), 0
        self._polling = False

    def run(self, work, on_done, on_error):
        def target():
            try:
                self.results.put((on_done, work()))
            except Exception as error:   # delivered to the UI
                self.results.put((on_error, error))
        self.busy += 1
        threading.Thread(target=target, daemon=True).start()
        if not self._polling:
            self._polling = True
            self.root.after(100, self._poll)

    def _poll(self):
        try:
            while True:
                callback, value = self.results.get_nowait()
                self.busy -= 1
                try:
                    callback(value)
                except Exception:
                    pass
        except queue.Empty:
            pass
        try:
            if self.busy > 0:
                self.root.after(100, self._poll)
            else:
                self._polling = False
        except Exception:
            self._polling = False

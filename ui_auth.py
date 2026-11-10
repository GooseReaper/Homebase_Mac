"""Sign-in, account creation, two-factor, recovery, and backup-restore screens."""
from __future__ import annotations

import io
import os
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import security
from security import SecurityError, TwoFactorRequired
from themes import T, apply_theme
from ui_common import MOD

try:
    import qrcode
except ImportError:
    qrcode = None
try:
    from PIL import ImageTk
except ImportError:
    ImageTk = None

STRENGTH = ("Too weak", "Weak", "Okay", "Strong", "Very strong")


class AuthScreen:
    """Fills the main window until someone signs in."""

    def __init__(self, root, store, on_signed_in, legacy_db=None, username=None, message=None, open_plain=None):
        self.root, self.store, self.on_signed_in = root, store, on_signed_in
        self.legacy_db = Path(legacy_db) if legacy_db and Path(legacy_db).exists() else None
        self.open_plain = open_plain
        self.font = apply_theme(root, {"theme": store.pref("theme", "modern"), "text_scale": store.pref("text_scale", 100)})
        root.title("Homebase · Sign in")
        root.configure(menu=tk.Menu(root))
        self.frame = tk.Frame(root, bg=T["bg"])
        self.frame.pack(fill="both", expand=True)
        self.card = None
        if not security.crypto_available():
            self.show_missing_crypto()
        elif store.usernames():
            self.show_sign_in(username, message)
        else:
            self.show_create(first=True)

    def destroy(self):
        self.frame.destroy()

    # ---------------------------------------------------------------- layout
    def new_card(self, title, subtitle=""):
        if self.card is not None:
            self.card.destroy()
        self.root.unbind("<Return>")
        holder = tk.Frame(self.frame, bg=T["bg"])
        holder.place(relx=0.5, rely=0.5, anchor="center")
        self.card = holder
        band = T["header_band"] or T["bg"]
        top = tk.Frame(holder, bg=band, padx=24, pady=14)
        top.pack(fill="x")
        tk.Label(top, text=T["title_prefix"] + T["title"], bg=band, fg=T["header_fg"] if T["header_band"] else T["accent"],
                 font=self.font(T["title_size"], "bold", title=True)).pack(anchor="w")
        if T["stripe"]:
            tk.Frame(holder, bg=T["stripe"], height=3).pack(fill="x")
        body = tk.Frame(holder, bg=T["surface"], padx=32, pady=24, highlightthickness=1, highlightbackground=T["border"])
        body.pack(fill="both")
        tk.Label(body, text=title.upper() if T["uppercase"] else title, bg=T["surface"], fg=T["ink"],
                 font=self.font(18, "bold", display=True)).pack(anchor="w")
        if subtitle:
            tk.Label(body, text=subtitle, bg=T["surface"], fg=T["muted"], font=self.font(10), wraplength=440, justify="left").pack(anchor="w", pady=(4, 0))
        content = ttk.Frame(body, style="Card.TFrame")
        content.pack(fill="both", pady=(16, 0))
        self.error = tk.StringVar()
        return content

    def label(self, parent, text):
        ttk.Label(parent, text=text, style="Card.TLabel").pack(anchor="w", pady=(10, 4))

    def entry(self, parent, secret=False, width=42, value=""):
        var = tk.StringVar(value=value)
        widget = ttk.Entry(parent, textvariable=var, show="•" if secret else "", width=width)
        widget.pack(fill="x")
        return var, widget

    def link(self, parent, text, command):
        label = ttk.Label(parent, text=text, style="Link.TLabel", cursor="hand2", background=T["surface"])
        label.pack(anchor="w", pady=(6, 0))
        label.bind("<Button-1>", lambda event: command())
        return label

    def error_label(self, parent):
        ttk.Label(parent, textvariable=self.error, style="Error.TLabel", background=T["surface"], wraplength=440).pack(anchor="w", pady=(10, 0))

    def phrase_box(self, parent, height=3):
        box = tk.Text(parent, height=height, width=46, wrap="word", font=self.font(11), bg=T["field"], fg=T["ink"],
                      insertbackground=T["ink"], relief="sunken" if T["bevel"] else "flat", bd=2 if T["bevel"] else 0,
                      highlightthickness=0 if T["bevel"] else 1, highlightbackground=T["border"], highlightcolor=T["accent"], padx=6, pady=4)
        box.pack(fill="x")
        return box

    # ----------------------------------------------------------------- pages
    def show_missing_crypto(self):
        content = self.new_card("Encryption isn't installed yet", "Homebase needs the free 'cryptography' package to protect each account.")
        ttk.Label(content, text="Close this window and double-click Start_Homebase.command (it installs everything), or run:\n\n"
                                "    python3 -m pip install -r requirements.txt", style="Card.TLabel", justify="left").pack(anchor="w")
        if self.open_plain and self.legacy_db:
            ttk.Button(content, text="Open my old unencrypted inventory instead", command=self.open_plain).pack(anchor="w", pady=(16, 0))

    def show_sign_in(self, username=None, message=None):
        content = self.new_card("Sign in", message or "Your inventory is encrypted on this computer.")
        names = self.store.usernames()
        self.label(content, "Username")
        self.user = tk.StringVar(value=username or self.store.pref("last_user", names[0] if names else ""))
        combo = ttk.Combobox(content, textvariable=self.user, values=names, width=40)
        combo.pack(fill="x")
        self.label(content, "Password")
        self.password, password_entry = self.entry(content, secret=True)
        self.error_label(content)
        ttk.Button(content, text="Sign in", style="Primary.TButton", command=self.sign_in).pack(fill="x", pady=(14, 6))
        self.link(content, "Create a new account", lambda: self.show_create())
        self.link(content, "Forgot your password? Use your recovery phrase", self.show_recover)
        self.link(content, "Restore an account from a backup file…", self.import_backup)
        self.root.bind("<Return>", lambda event: self.sign_in())
        (password_entry if self.user.get() else combo).focus_set()

    def sign_in(self):
        self.error.set("")
        self.root.config(cursor="watch")
        self.root.update_idletasks()
        try:
            session = self.store.sign_in(self.user.get(), self.password.get())
        except TwoFactorRequired as pending:
            self.show_two_factor(pending.pending)
            return
        except SecurityError as error:
            self.error.set(str(error))
            self.password.set("")
            return
        finally:
            self.root.config(cursor="")
        self.finish(session)

    def finish(self, session, setup_2fa=False):
        self.store.set_pref("last_user", session.username)
        self.root.unbind("<Return>")
        self.on_signed_in(session, setup_2fa)

    def show_two_factor(self, pending):
        content = self.new_card("Two-factor code", f"Open your authenticator app and enter the 6-digit Homebase code for {pending['account']['username']}.")
        self.label(content, "6-digit code")
        code, widget = self.entry(content, width=12)
        widget.configure(font=self.font(18, "bold", display=True))
        self.error_label(content)

        def verify():
            try:
                session = self.store.finish_two_factor(pending, code.get())
            except SecurityError as error:
                self.error.set(str(error))
                code.set("")
                return
            self.finish(session)
        ttk.Button(content, text="Verify", style="Primary.TButton", command=verify).pack(fill="x", pady=(14, 6))
        self.link(content, "Lost your phone? Use your recovery phrase", self.show_recover)
        self.link(content, "Back to sign in", lambda: self.show_sign_in(pending["account"]["username"]))
        self.root.bind("<Return>", lambda event: verify())
        widget.focus_set()

    def show_create(self, first=False):
        content = self.new_card("Create your account" if first else "Create an account",
                                "Each account has its own private, encrypted inventory. It takes about a minute.")
        self.label(content, "Username")
        self.new_user, user_entry = self.entry(content)
        row = ttk.Frame(content, style="Card.TFrame")
        row.pack(fill="x")
        left, right = ttk.Frame(row, style="Card.TFrame"), ttk.Frame(row, style="Card.TFrame")
        left.pack(side="left", fill="x", expand=True, padx=(0, 6))
        right.pack(side="left", fill="x", expand=True, padx=(6, 0))
        self.label(left, "Password")
        self.new_password, _ = self.entry(left, secret=True, width=20)
        self.label(right, "Type it again")
        self.new_password2, _ = self.entry(right, secret=True, width=20)
        self.strength = tk.StringVar(value=f"At least {security.MIN_PASSWORD} characters, mixing letters, numbers, and symbols.")
        ttk.Label(content, textvariable=self.strength, style="Hint.TLabel", background=T["surface"]).pack(anchor="w", pady=(4, 0))
        self.new_password.trace_add("write", self.update_strength)
        self.label(content, f"Recovery phrase ({security.MIN_SEED_WORDS}+ words, your own or generated)")
        self.phrase = self.phrase_box(content)
        phrase_row = ttk.Frame(content, style="Card.TFrame")
        phrase_row.pack(fill="x", pady=(6, 0))
        ttk.Button(phrase_row, text="Generate one for me", style="Small.TButton", command=self.generate_phrase).pack(side="left")
        ttk.Label(phrase_row, text="  Write it on paper. It resets a forgotten password.", style="Hint.TLabel", background=T["surface"]).pack(side="left")
        self.wrote_it = tk.BooleanVar(value=False)
        ttk.Checkbutton(content, text="I wrote my recovery phrase down somewhere safe", variable=self.wrote_it, style="Card.TCheckbutton").pack(anchor="w", pady=(10, 0))
        self.start_with = tk.StringVar(value="import" if self.legacy_db else "sample")
        self.label(content, "Start with")
        options = [("sample", "The sample inventory (to explore)"), ("empty", "An empty inventory")]
        if self.legacy_db:
            options.insert(0, ("import", f"My existing Homebase data ({self.legacy_db.name})"))
        for value, text in options:
            ttk.Radiobutton(content, text=text, value=value, variable=self.start_with, style="CardItem.TRadiobutton").pack(anchor="w")
        self.delete_legacy = tk.BooleanVar(value=True)
        if self.legacy_db:
            ttk.Checkbutton(content, text="After importing, delete the old unencrypted copy (recommended)",
                            variable=self.delete_legacy, style="Card.TCheckbutton").pack(anchor="w", pady=(4, 0))
        self.want_2fa = tk.BooleanVar(value=True)
        ttk.Checkbutton(content, text="Set up two-factor sign-in next (recommended)", variable=self.want_2fa, style="Card.TCheckbutton").pack(anchor="w", pady=(8, 0))
        self.error_label(content)
        ttk.Button(content, text="Create account", style="Primary.TButton", command=self.create).pack(fill="x", pady=(14, 6))
        if self.store.usernames():
            self.link(content, "Back to sign in", lambda: self.show_sign_in())
        else:
            self.link(content, "Restore an account from a backup file…", self.import_backup)
        user_entry.focus_set()

    def update_strength(self, *_):
        score = security.password_strength(self.new_password.get())
        problems = security.password_problems(self.new_password.get(), self.new_user.get())
        self.strength.set(f"Strength: {STRENGTH[score]}" + (f"  ·  needs {problems[0]}" if problems else "  ·  looks good"))

    def generate_phrase(self):
        self.phrase.delete("1.0", "end")
        self.phrase.insert("1.0", security.generate_phrase())

    def create(self):
        self.error.set("")
        if self.new_password.get() != self.new_password2.get():
            self.error.set("The two passwords don't match.")
            return
        if not self.wrote_it.get():
            self.error.set("Write down your recovery phrase and tick the box. Without it, a forgotten password can't be reset.")
            return
        start = self.start_with.get()
        self.root.config(cursor="watch")
        self.root.update_idletasks()
        try:
            session = self.store.create_account(self.new_user.get(), self.new_password.get(), self.phrase.get("1.0", "end"),
                                                initial_db=self.legacy_db if start == "import" else None, sample=start == "sample")
        except SecurityError as error:
            self.error.set(str(error))
            return
        finally:
            self.root.config(cursor="")
        if start == "import" and self.delete_legacy.get():
            try:
                os.remove(self.legacy_db)
            except OSError:
                pass
        self.finish(session, setup_2fa=self.want_2fa.get())

    def show_recover(self):
        content = self.new_card("Reset your password", "Enter your recovery phrase and choose a new password. "
                                                       "This also turns two-factor sign-in off, so you can set it up again.")
        self.label(content, "Username")
        self.rec_user, _ = self.entry(content, value=self.store.pref("last_user", ""))
        self.label(content, "Recovery phrase")
        phrase = self.phrase_box(content)
        self.label(content, "New password")
        new_password, _ = self.entry(content, secret=True)
        self.label(content, "Type it again")
        new_password2, _ = self.entry(content, secret=True)
        self.error_label(content)

        def recover():
            if new_password.get() != new_password2.get():
                self.error.set("The two passwords don't match.")
                return
            try:
                session = self.store.recover(self.rec_user.get(), phrase.get("1.0", "end"), new_password.get())
            except SecurityError as error:
                self.error.set(str(error))
                return
            self.finish(session)
        ttk.Button(content, text="Reset password and sign in", style="Primary.TButton", command=recover).pack(fill="x", pady=(14, 6))
        self.link(content, "Back to sign in", lambda: self.show_sign_in())

    def import_backup(self):
        filename = filedialog.askopenfilename(parent=self.root, title="Restore a Homebase account backup",
                                              filetypes=[("Homebase backup", "*.hbbackup"), ("All files", "*.*")])
        if not filename:
            return
        new_name = None
        while True:
            try:
                username = self.store.import_backup(filename, new_name)
                break
            except SecurityError as error:
                if "is taken here" not in str(error):
                    messagebox.showerror("Can't restore", str(error), parent=self.root)
                    return
                new_name = simpledialog.askstring("Choose a name", f"{error}\n\nName for the restored account:", parent=self.root)
                if not new_name:
                    return
        self.show_sign_in(username, f"Restored “{username}”. Sign in with that account's password.")


class TwoFactorSetup:
    """Turn on authenticator-app codes for the signed-in account."""

    def __init__(self, app):
        self.app, self.secret = app, security.new_totp_secret()
        self.window = tk.Toplevel(app.root)
        self.window.title("Set up two-factor sign-in")
        self.window.transient(app.root)
        self.window.configure(bg=T["bg"])
        self.window.grab_set()
        frame = ttk.Frame(self.window, padding=24)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Two-factor sign-in", style="Title.TLabel").pack(anchor="w")
        ttk.Label(frame, text="1. Open an authenticator app (Google Authenticator, Microsoft Authenticator, Authy, 1Password…).\n"
                              "2. Add an account by scanning this code, or type the setup key.\n"
                              "3. Enter the 6-digit code it shows.", justify="left").pack(anchor="w", pady=(8, 12))
        uri = security.totp_uri(self.secret, app.session.username)
        self.qr_image = None
        if qrcode is not None and ImageTk is not None:
            try:
                made = qrcode.make(uri)
                image = made.get_image() if hasattr(made, "get_image") else getattr(made, "_img", made)
                image = image.convert("RGB").resize((200, 200))
                self.qr_image = ImageTk.PhotoImage(image)
                tk.Label(frame, image=self.qr_image, bg="#ffffff", bd=8).pack(anchor="w")
            except Exception:
                self.qr_image = None
        if self.qr_image is None:
            ttk.Label(frame, text="(Install the 'qrcode' package to show a scannable code. Typing the key works the same.)",
                      style="Hint.TLabel").pack(anchor="w")
        grouped = " ".join(self.secret[i:i + 4] for i in range(0, len(self.secret), 4))
        ttk.Label(frame, text="Setup key", style="Eyebrow.TLabel").pack(anchor="w", pady=(12, 2))
        key = ttk.Entry(frame, width=48)
        key.insert(0, grouped)
        key.configure(state="readonly")
        key.pack(anchor="w")
        ttk.Label(frame, text="6-digit code from the app", style="Eyebrow.TLabel").pack(anchor="w", pady=(12, 2))
        self.code = tk.StringVar()
        entry = ttk.Entry(frame, textvariable=self.code, width=12, font=app.font(16, "bold", display=True))
        entry.pack(anchor="w")
        self.error = tk.StringVar()
        ttk.Label(frame, textvariable=self.error, style="Error.TLabel", wraplength=460).pack(anchor="w", pady=(8, 0))
        footer = ttk.Frame(frame)
        footer.pack(fill="x", pady=(14, 0))
        ttk.Button(footer, text="Turn on", style="Primary.TButton", command=self.enable).pack(side="right")
        ttk.Button(footer, text="Not now", command=self.window.destroy).pack(side="right", padx=8)
        self.window.bind("<Return>", lambda event: self.enable())
        self.window.bind("<Escape>", lambda event: self.window.destroy())
        app.center_dialog(self.window)
        entry.focus_set()

    def enable(self):
        try:
            self.app.store.enable_two_factor(self.app.session, self.secret, self.code.get())
        except SecurityError as error:
            self.error.set(str(error))
            return
        self.window.destroy()
        self.app.after_account_change("Two-factor sign-in is on. You'll need a code from your app each time you sign in.")


class PasswordForm:
    """Small form for account changes that need the current password."""

    def __init__(self, app, title, fields, action, done_message, intro=""):
        self.app, self.action, self.done_message = app, action, done_message
        self.window = tk.Toplevel(app.root)
        self.window.title(title)
        self.window.transient(app.root)
        self.window.configure(bg=T["bg"])
        self.window.grab_set()
        frame = ttk.Frame(self.window, padding=24)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=title, style="Title.TLabel").pack(anchor="w")
        if intro:
            ttk.Label(frame, text=intro, style="Hint.TLabel", wraplength=440, justify="left").pack(anchor="w", pady=(6, 0))
        self.values = {}
        first = None
        for key, label, kind in fields:
            ttk.Label(frame, text=label).pack(anchor="w", pady=(12, 4))
            if kind == "phrase":
                box = tk.Text(frame, height=3, width=46, wrap="word", font=app.font(11), bg=T["field"], fg=T["ink"], insertbackground=T["ink"])
                box.pack(fill="x")
                if key == "phrase":
                    ttk.Button(frame, text="Generate one for me", style="Small.TButton",
                               command=lambda b=box: (b.delete("1.0", "end"), b.insert("1.0", security.generate_phrase()))).pack(anchor="w", pady=(4, 0))
                self.values[key] = box
            else:
                var = tk.StringVar()
                widget = ttk.Entry(frame, textvariable=var, show="•" if kind == "secret" else "", width=44)
                widget.pack(fill="x")
                self.values[key] = var
                first = first or widget
        self.error = tk.StringVar()
        ttk.Label(frame, textvariable=self.error, style="Error.TLabel", wraplength=440).pack(anchor="w", pady=(10, 0))
        footer = ttk.Frame(frame)
        footer.pack(fill="x", pady=(14, 0))
        ttk.Button(footer, text="Save", style="Primary.TButton", command=self.submit).pack(side="right")
        ttk.Button(footer, text="Cancel", command=self.window.destroy).pack(side="right", padx=8)
        self.window.bind("<Escape>", lambda event: self.window.destroy())
        app.center_dialog(self.window)
        if first is not None:
            first.focus_set()

    def submit(self):
        values = {key: (w.get("1.0", "end") if isinstance(w, tk.Text) else w.get()) for key, w in self.values.items()}
        if "new" in values and values.get("new") != values.get("new2", values.get("new")):
            self.error.set("The two new passwords don't match.")
            return
        try:
            self.action(values)
        except SecurityError as error:
            self.error.set(str(error))
            return
        self.window.destroy()
        if self.done_message:
            self.app.after_account_change(self.done_message)

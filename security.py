"""Accounts, encryption, and two-factor sign-in for Homebase. Everything stays on this computer.

How each user's data is protected (modeled on how Brave Sync protects a sync chain):

* Every account has its own random 256-bit data key. The inventory, including photos,
  settings, and AI keys, is stored as ONE file encrypted with AES-256-GCM, which both
  hides the data and detects any tampering.
* The data key is never stored in the clear. It is "wrapped" (encrypted) twice:
    1. with a key stretched from the account password (scrypt, memory-hard), and
    2. with a key stretched from the user's recovery seed phrase.
  Either one can unlock the data key, so a forgotten password can be reset with the
  seed phrase, just as a Brave Sync code restores a sync chain.
* Two-factor sign-in uses standard 6-digit authenticator codes (TOTP, RFC 6238). The
  authenticator secret is itself encrypted with the data key.

Honest limits: anyone who knows the password can decrypt the file directly, so 2FA guards
the app's sign-in screen, not the math. A strong password matters most. Usernames and
the list of accounts are not secret.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import struct
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

try:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:            # Start_Homebase.command installs it automatically
    AESGCM = None
    InvalidTag = Exception

MAGIC = b"HBX1"
SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1}   # ~32 MB of memory per guess; about 0.1 s on a laptop
MIN_PASSWORD = 10
MIN_SEED_WORDS = 12
TOTP_PERIOD = 30
MAX_FAILED_BEFORE_DELAY = 3

# 256 short, distinct, easy-to-spell words for generated phrases (12 words = 96 bits).
WORDS = """
acorn adobe agent alarm album alpine amber anchor angle apple apron arcade arrow aspen atlas attic
autumn avocado badge bagel bamboo banjo barley basil beacon beetle berry bicycle bison blanket blossom
blue bonsai border boulder bracket bramble breeze brick bridge bronze brook bubble bucket buffalo cabin
cactus camera candle canoe canyon carbon carpet castle cedar cello cherry chess chimney cinder circle
citrus clover cobalt coconut comet compass copper coral cotton cougar crane crayon cricket crystal cupcake
dahlia daisy dandelion delta denim desert diamond dolphin domino dragon drum dune eagle ember engine
falcon feather fennel ferry fiddle fig flame flannel flint forest fossil fountain fox frost galaxy garden
garnet gecko geyser ginger glacier globe goblet gopher granite grape gravel guitar hammock harbor harp
hazel helmet heron hickory honey horizon husky iceberg igloo indigo island ivory jacket jade jaguar jasmine
jelly jigsaw journal juniper kayak kettle kiwi koala ladder lagoon lantern laurel lemon lilac lily lime
linen lobster locket lotus magnet mango maple marble meadow melon meteor mint mitten mocha moose mosaic
moss muffin nectar needle nickel nutmeg oasis oatmeal ocean olive onyx orbit orchid otter owl paddle
panda papaya parade parrot peach pebble pecan pepper piano pickle pilot pine planet plum pocket pony
poppy prism pumpkin quail quartz quill rabbit radar rainbow raven reef ribbon river robin rocket rose
ruby saddle saffron salmon sandal satin scarf scooter shell sierra silver sketch sled slipper sparrow
spruce squash star summit sunset swan tango teapot thistle thunder tiger timber toast topaz tulip tundra
umbrella velvet violet walnut willow yarrow zephyr
""".split()
assert len(WORDS) == 256 and len(set(WORDS)) == 256


class SecurityError(ValueError):
    """Something the sign-in screens should explain to the user."""


class TwoFactorRequired(Exception):
    """Password was right; a 6-digit code is still needed."""

    def __init__(self, pending):
        super().__init__("Two-factor code required")
        self.pending = pending


def crypto_available():
    return AESGCM is not None


def _need_crypto():
    if AESGCM is None:
        raise SecurityError("Encryption support is missing. Start Homebase with Start_Homebase.command, "
                            "or run:  python3 -m pip install cryptography")


def _b64(data):
    return base64.b64encode(data).decode("ascii")


def _unb64(text):
    return base64.b64decode(text.encode("ascii"))


def stretch(secret, salt, params=None):
    """Turn a password or phrase into a 256-bit key with scrypt (slow on purpose)."""
    params = params or SCRYPT
    return hashlib.scrypt(secret.encode("utf-8"), salt=salt, n=params["n"], r=params["r"], p=params["p"],
                          maxmem=256 * 1024 * 1024, dklen=32)


def seal(key, plaintext, aad=b""):
    _need_crypto()
    nonce = os.urandom(12)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def open_sealed(key, blob, aad=b""):
    _need_crypto()
    try:
        return AESGCM(key).decrypt(blob[:12], blob[12:], aad)
    except InvalidTag:
        raise SecurityError("Wrong key, or the file was changed or damaged.") from None


# ------------------------------------------------------------------ phrases and passwords
def normalize_phrase(text):
    return " ".join(re.findall(r"[a-z0-9']+", str(text or "").casefold()))


def generate_phrase(words=12):
    return " ".join(secrets.choice(WORDS) for _ in range(words))


def check_seed_phrase(text):
    phrase = normalize_phrase(text)
    words = phrase.split()
    if len(words) < MIN_SEED_WORDS:
        raise SecurityError(f"Use at least {MIN_SEED_WORDS} words in your recovery phrase (you have {len(words)}).")
    if len(set(words)) < 8:
        raise SecurityError("Use at least 8 different words in your recovery phrase.")
    if len(phrase.replace(" ", "")) < 40:
        raise SecurityError("Your recovery phrase is too short. Use longer or more words.")
    return phrase


def password_problems(password, username=""):
    problems = []
    if len(password) < MIN_PASSWORD:
        problems.append(f"at least {MIN_PASSWORD} characters")
    kinds = sum(bool(re.search(p, password)) for p in (r"[a-z]", r"[A-Z]", r"[0-9]", r"[^A-Za-z0-9]"))
    if kinds < 3 and len(password) < 16:
        problems.append("a mix of 3 of: lowercase, uppercase, numbers, symbols (or 16+ characters)")
    if username and username.casefold() in password.casefold():
        problems.append("not containing your username")
    if password.casefold() in {"password123", "homebase123", "qwertyuiop", "1234567890"}:
        problems.append("not a common password")
    return problems


def password_strength(password):
    """0-4 rough strength score for the meter."""
    score = 0
    score += len(password) >= MIN_PASSWORD
    score += len(password) >= 14
    score += sum(bool(re.search(p, password)) for p in (r"[a-z]", r"[A-Z]", r"[0-9]", r"[^A-Za-z0-9]")) >= 3
    score += len(password) >= 20 or (len(set(password)) >= 10 and len(password) >= 12)
    return min(4, score)


# ---------------------------------------------------------------------------- TOTP (2FA)
def new_totp_secret():
    return base64.b32encode(os.urandom(20)).decode("ascii").rstrip("=")


def totp_code(secret, step):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{number % 1_000_000:06d}"


def totp_match(secret, code, at=None, window=1):
    """Return the matching time step for a code, or None."""
    code = re.sub(r"\D", "", str(code or ""))
    if len(code) != 6:
        return None
    step = int((time.time() if at is None else at) // TOTP_PERIOD)
    for candidate in range(step - window, step + window + 1):
        if hmac.compare_digest(totp_code(secret, candidate), code):
            return candidate
    return None


def totp_uri(secret, username):
    return f"otpauth://totp/Homebase:{quote(username)}?secret={secret}&issuer=Homebase&digits=6&period={TOTP_PERIOD}"


# ------------------------------------------------------------------------------ accounts
class Session:
    """An unlocked account: who is signed in, its data key, and its encrypted vault."""

    def __init__(self, store, account, key):
        self.store, self.account, self._key = store, account, key
        self.vault = Vault(store.vault_path(account["id"]), key, account["id"])

    @property
    def username(self):
        return self.account["username"]

    @property
    def totp_enabled(self):
        return bool(self.account.get("totp", {}).get("enabled"))

    def open_inventory(self, on_change=None):
        """The decrypted inventory for this session (opened once, then reused)."""
        if self.vault.inventory is None:
            self.vault.open(sample=True)
        self.vault.inventory.on_change = on_change
        return self.vault.inventory

    def save(self):
        self.vault.save()

    def lock(self):
        self._key = None
        self.vault.close()


class AccountStore:
    """accounts.json lists accounts and their wrapped keys; vaults/<id>.hbx holds each user's data."""

    def __init__(self, folder, scrypt_params=None):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / "vaults").mkdir(exist_ok=True)
        self.file = self.folder / "accounts.json"
        self.params = dict(scrypt_params or SCRYPT)
        self.data = {"version": 1, "accounts": [], "prefs": {}}
        if self.file.exists():
            try:
                self.data = json.loads(self.file.read_text(encoding="utf-8"))
            except ValueError:
                raise SecurityError(f"{self.file} is damaged. Restore it from a backup.") from None
            self.data.setdefault("prefs", {})

    # -- storage
    def _save(self):
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        os.replace(tmp, self.file)
        try:
            os.chmod(self.file, 0o600)
        except OSError:
            pass

    def vault_path(self, account_id):
        return self.folder / "vaults" / f"{account_id}.hbx"

    def usernames(self):
        return [a["username"] for a in self.data["accounts"]]

    def pref(self, key, default=None):
        return self.data["prefs"].get(key, default)

    def set_pref(self, key, value):
        self.data["prefs"][key] = value
        self._save()

    def find(self, username):
        name = str(username or "").strip().casefold()
        for account in self.data["accounts"]:
            if account["username"].casefold() == name:
                return account
        return None

    # -- create
    def create_account(self, username, password, seed_phrase, initial_db=None, sample=True):
        """Create an account and its encrypted vault. Returns an unlocked Session."""
        _need_crypto()
        username = str(username or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9 _.\-]{2,32}", username):
            raise SecurityError("Usernames are 2–32 letters, numbers, spaces, dots, dashes, or underscores.")
        if self.find(username):
            raise SecurityError("That username is taken on this computer.")
        problems = password_problems(password, username)
        if problems:
            raise SecurityError("Password needs " + "; ".join(problems) + ".")
        phrase = check_seed_phrase(seed_phrase)
        if normalize_phrase(password) == phrase:
            raise SecurityError("Your recovery phrase must be different from your password.")
        key = os.urandom(32)
        account = {"id": f"acct_{uuid.uuid4().hex}", "username": username,
                   "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "kdf": {"name": "scrypt", **self.params}, "totp": {"enabled": False}, "failed": 0}
        self._wrap(account, key, password=password, phrase=phrase)
        session = Session(self, account, key)
        session.vault.create(initial_db)
        session.vault.open(sample=sample)
        self.data["accounts"].append(account)
        self._save()
        return session

    def _wrap(self, account, key, password=None, phrase=None):
        aad = account["id"].encode()
        if password is not None:
            salt = os.urandom(16)
            account["pw_salt"] = _b64(salt)
            account["key_by_password"] = _b64(seal(stretch(password, salt, account["kdf"]), key, aad))
        if phrase is not None:
            salt = os.urandom(16)
            account["seed_salt"] = _b64(salt)
            account["key_by_seed"] = _b64(seal(stretch(phrase, salt, account["kdf"]), key, aad))
            account["seed_check"] = _b64(hashlib.sha256(b"homebase-seed-check" + salt + key).digest()[:8])

    # -- sign in
    def _throttle(self, account):
        wait = account.get("locked_until", 0) - time.time()
        if wait > 0:
            raise SecurityError(f"Too many wrong tries. Wait {int(wait) + 1} seconds and try again.")

    def _failed(self, account):
        account["failed"] = account.get("failed", 0) + 1
        if account["failed"] >= MAX_FAILED_BEFORE_DELAY:
            account["locked_until"] = time.time() + min(300, 5 * 2 ** (account["failed"] - MAX_FAILED_BEFORE_DELAY))
        self._save()

    def _succeeded(self, account):
        if account.get("failed") or account.get("locked_until"):
            account["failed"], account["locked_until"] = 0, 0
            self._save()

    def sign_in(self, username, password):
        """Return a Session, or raise TwoFactorRequired carrying a pending sign-in."""
        _need_crypto()
        account = self.find(username)
        if account is None:
            stretch(password or "x", b"0" * 16, self.params)   # same delay whether or not the name exists
            raise SecurityError("Wrong username or password.")
        self._throttle(account)
        try:
            key = open_sealed(stretch(password, _unb64(account["pw_salt"]), account["kdf"]),
                              _unb64(account["key_by_password"]), account["id"].encode())
        except SecurityError:
            self._failed(account)
            raise SecurityError("Wrong username or password.") from None
        if account.get("totp", {}).get("enabled"):
            raise TwoFactorRequired({"account": account, "key": key})
        self._succeeded(account)
        return Session(self, account, key)

    def finish_two_factor(self, pending, code):
        account, key = pending["account"], pending["key"]
        self._throttle(account)
        secret = open_sealed(key, _unb64(account["totp"]["secret"]), account["id"].encode()).decode()
        step = totp_match(secret, code)
        if step is None or step <= account["totp"].get("last_step", -1):
            self._failed(account)
            raise SecurityError("That code didn't work. Use the newest code from your authenticator app.")
        account["totp"]["last_step"] = step
        self._succeeded(account)
        self._save()
        return Session(self, account, key)

    def recover(self, username, seed_phrase, new_password, keep_two_factor=False):
        """Reset a forgotten password with the recovery phrase. Also turns 2FA off unless kept."""
        _need_crypto()
        account = self.find(username)
        if account is None:
            raise SecurityError("No account with that username on this computer.")
        self._throttle(account)
        phrase = normalize_phrase(seed_phrase)
        try:
            key = open_sealed(stretch(phrase, _unb64(account["seed_salt"]), account["kdf"]),
                              _unb64(account["key_by_seed"]), account["id"].encode())
        except SecurityError:
            self._failed(account)
            raise SecurityError("That recovery phrase doesn't match this account.") from None
        problems = password_problems(new_password, account["username"])
        if problems:
            raise SecurityError("New password needs " + "; ".join(problems) + ".")
        self._wrap(account, key, password=new_password)
        if not keep_two_factor:
            account["totp"] = {"enabled": False}
        self._succeeded(account)
        self._save()
        return Session(self, account, key)

    # -- account changes (need an unlocked session)
    def change_password(self, session, old_password, new_password):
        account = session.account
        try:
            open_sealed(stretch(old_password, _unb64(account["pw_salt"]), account["kdf"]),
                        _unb64(account["key_by_password"]), account["id"].encode())
        except SecurityError:
            raise SecurityError("Your current password is not correct.") from None
        problems = password_problems(new_password, account["username"])
        if problems:
            raise SecurityError("New password needs " + "; ".join(problems) + ".")
        self._wrap(account, session._key, password=new_password)
        self._save()

    def change_seed_phrase(self, session, password, new_phrase):
        self.verify_password(session, password)
        self._wrap(session.account, session._key, phrase=check_seed_phrase(new_phrase))
        self._save()

    def verify_password(self, session, password):
        account = session.account
        try:
            open_sealed(stretch(password, _unb64(account["pw_salt"]), account["kdf"]),
                        _unb64(account["key_by_password"]), account["id"].encode())
        except SecurityError:
            raise SecurityError("That password is not correct.") from None

    def enable_two_factor(self, session, secret, code):
        if totp_match(secret, code) is None:
            raise SecurityError("That code didn't match. Check the time on your phone and try the newest code.")
        session.account["totp"] = {"enabled": True, "last_step": totp_match(secret, code),
                                   "secret": _b64(seal(session._key, secret.encode(), session.account["id"].encode()))}
        self._save()

    def disable_two_factor(self, session, password):
        self.verify_password(session, password)
        session.account["totp"] = {"enabled": False}
        self._save()

    def export_backup(self, session, destination):
        """One encrypted file holding this account's wrapped keys and data.

        It opens only with the account's password or recovery phrase, on any computer."""
        session.save()
        destination = Path(destination)
        if destination.resolve() == session.vault.path.resolve():
            raise SecurityError("Choose a different file for your backup.")
        account = {k: v for k, v in session.account.items() if k not in ("failed", "locked_until")}
        bundle = {"homebase_backup": 1, "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "account": account, "vault": _b64(session.vault.path.read_bytes())}
        destination.write_text(json.dumps(bundle), encoding="utf-8")

    def import_backup(self, source, new_username=None):
        """Add an account from a backup file. Returns the username it was restored as."""
        try:
            bundle = json.loads(Path(source).read_text(encoding="utf-8"))
            account, vault = dict(bundle["account"]), _unb64(bundle["vault"])
        except (OSError, ValueError, KeyError, TypeError):
            raise SecurityError("That file is not a Homebase account backup.") from None
        if not vault.startswith(MAGIC):
            raise SecurityError("That backup's data is damaged.")
        if any(a["id"] == account["id"] for a in self.data["accounts"]):
            raise SecurityError("This account is already on this computer. Delete it first to restore the backup over it.")
        username = (new_username or account["username"]).strip()
        if self.find(username):
            raise SecurityError(f"The username “{username}” is taken here. Choose another name for the restored account.")
        account.update(username=username, failed=0, locked_until=0)
        self.vault_path(account["id"]).write_bytes(vault)
        self.data["accounts"].append(account)
        self._save()
        return username

    def delete_account(self, session, password):
        self.verify_password(session, password)
        session.lock()
        path = self.vault_path(session.account["id"])
        for file in (path, path.with_suffix(".hbx.bak")):
            if file.exists():
                file.unlink()
        self.data["accounts"] = [a for a in self.data["accounts"] if a["id"] != session.account["id"]]
        self._save()


# -------------------------------------------------------------------------------- vaults
class Vault:
    """One user's inventory, encrypted at rest. Decrypted only in memory while signed in."""

    def __init__(self, path, key, account_id):
        self.path, self._key, self.aad = Path(path), key, MAGIC + account_id.encode()
        self.inventory = None

    def create(self, initial_db=None):
        """Start the vault, optionally from an existing unencrypted Homebase database file."""
        if initial_db:
            source = sqlite3.connect(Path(initial_db))
            try:
                payload = source.serialize() if hasattr(source, "serialize") else None
                payload = b"S" + payload if payload is not None else b"D" + "\n".join(source.iterdump()).encode()
            finally:
                source.close()
            self._write(payload)

    def open(self, sample=True, on_change=None):
        from inventory import Inventory
        connection = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
        if self.path.exists():
            blob = self.path.read_bytes()
            if not blob.startswith(MAGIC):
                raise SecurityError("This is not a Homebase vault file.")
            payload = open_sealed(self._key, blob[len(MAGIC):], self.aad)
            if payload[:1] == b"S" and hasattr(connection, "deserialize"):
                connection.deserialize(payload[1:])
            elif payload[:1] == b"S":
                raise SecurityError("This vault was saved by a newer Python. Use Python 3.11 or newer.")
            else:
                connection.executescript(payload[1:].decode("utf-8"))
        self.inventory = Inventory(self.path, sample=sample, connection=connection, on_change=on_change)
        self.save()
        return self.inventory

    def save(self):
        if self.inventory is not None and self._key is not None:
            self._write(self.inventory.serialize())

    def _write(self, payload):
        blob = MAGIC + seal(self._key, payload, self.aad)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(blob)
        if self.path.exists():
            os.replace(self.path, self.path.with_suffix(".hbx.bak"))   # one previous version, also encrypted
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass


    def close(self):
        if self.inventory is not None:
            try:
                self.save()
            finally:
                self.inventory.close()
                self.inventory = None
        self._key = None

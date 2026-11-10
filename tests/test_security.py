"""Accounts, encryption, 2FA, recovery, backups."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import security
from inventory import Inventory

FAST = {"n": 2 ** 10, "r": 8, "p": 1}
PHRASE = "maple river comet lantern otter quartz meadow tiger violin harbor cedar plum"
PHRASE = "maple river comet lantern otter quartz meadow tiger harbor cedar plum ember"
PASSWORD = "Correct-Horse-42"


@unittest.skipUnless(security.crypto_available(), "cryptography package not installed")
class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name) / "data"
        self.store = security.AccountStore(self.folder, FAST)

    def tearDown(self):
        self.temp.cleanup()

    def make(self, name="gregory", **kw):
        return self.store.create_account(name, PASSWORD, PHRASE, **kw)

    def test_create_sign_in_and_data_is_encrypted_on_disk(self):
        session = self.make()
        inv = session.open_inventory()
        inv.save_item(dict(name="Secret Telescope", category="Outdoor", quantity="1", location_id="gear", value="1", low_stock="0", tags="", notes="zz-unique-note"))
        session.lock()
        raw = session.vault.path.read_bytes()
        self.assertTrue(raw.startswith(security.MAGIC))
        self.assertNotIn(b"Secret Telescope", raw)
        self.assertNotIn(b"zz-unique-note", raw)
        accounts_text = (self.folder / "accounts.json").read_text()
        self.assertNotIn(PASSWORD, accounts_text)
        self.assertNotIn("maple", accounts_text)
        store2 = security.AccountStore(self.folder, FAST)
        again = store2.sign_in("GREGORY", PASSWORD)
        self.assertEqual(again.open_inventory().items(query="telescope")[0]["notes"], "zz-unique-note")
        again.lock()

    def test_wrong_password_and_throttle(self):
        self.make().lock()
        for _ in range(security.MAX_FAILED_BEFORE_DELAY):
            with self.assertRaisesRegex(security.SecurityError, "Wrong username or password"):
                self.store.sign_in("gregory", "nope-nope-nope")
        with self.assertRaisesRegex(security.SecurityError, "Wait"):
            self.store.sign_in("gregory", PASSWORD)
        with self.assertRaisesRegex(security.SecurityError, "Wrong username"):
            self.store.sign_in("nobody", PASSWORD)

    def test_validation(self):
        with self.assertRaisesRegex(security.SecurityError, "Password needs"):
            self.store.create_account("a1", "short", PHRASE)
        with self.assertRaisesRegex(security.SecurityError, "at least 12 words"):
            self.store.create_account("a1", PASSWORD, "too few words here")
        with self.assertRaisesRegex(security.SecurityError, "different words"):
            self.store.create_account("a1", PASSWORD, "cat " * 12)
        self.make().lock()
        with self.assertRaisesRegex(security.SecurityError, "taken"):
            self.make("Gregory")
        with self.assertRaisesRegex(security.SecurityError, "Usernames"):
            self.store.create_account("x", PASSWORD, PHRASE)

    def test_two_factor_flow_and_replay_blocked(self):
        session = self.make()
        secret = security.new_totp_secret()
        with self.assertRaises(security.SecurityError):
            self.store.enable_two_factor(session, secret, "000000" if security.totp_code(secret, int(time.time() // 30)) != "000000" else "111111")
        now = time.time()
        with patch("security.time.time", return_value=now - 60):
            self.store.enable_two_factor(session, secret, security.totp_code(secret, int((now - 60) // 30)))
        session.lock()
        with self.assertRaises(security.TwoFactorRequired) as ctx:
            self.store.sign_in("gregory", PASSWORD)
        code = security.totp_code(secret, int(time.time() // 30))
        with self.assertRaises(security.SecurityError):
            self.store.finish_two_factor(ctx.exception.pending, "123")
        s2 = self.store.finish_two_factor(ctx.exception.pending, code)
        self.assertTrue(s2.totp_enabled)
        with self.assertRaisesRegex(security.SecurityError, "newest code"):
            self.store.finish_two_factor(ctx.exception.pending, code)   # same code twice is refused
        self.assertNotIn(secret, (self.folder / "accounts.json").read_text())

    def test_recovery_phrase_resets_password_and_turns_off_2fa(self):
        session = self.make()
        secret = security.new_totp_secret()
        self.store.enable_two_factor(session, secret, security.totp_code(secret, int(time.time() // 30)))
        session.lock()
        with self.assertRaisesRegex(security.SecurityError, "doesn't match"):
            self.store.recover("gregory", "wrong " * 12, "Brand-New-Pass-9")
        recovered = self.store.recover("gregory", PHRASE.upper().replace(" ", "  "), "Brand-New-Pass-9")
        self.assertFalse(recovered.totp_enabled)
        recovered.lock()
        with self.assertRaises(security.SecurityError):
            self.store.sign_in("gregory", PASSWORD)
        self.store.sign_in("gregory", "Brand-New-Pass-9").lock()

    def test_change_password_and_phrase(self):
        session = self.make()
        with self.assertRaises(security.SecurityError):
            self.store.change_password(session, "bad", "Another-Pass-77")
        self.store.change_password(session, PASSWORD, "Another-Pass-77")
        self.store.change_seed_phrase(session, "Another-Pass-77", "apple " + " ".join(security.WORDS[:12]))
        session.lock()
        self.store.sign_in("gregory", "Another-Pass-77").lock()
        with self.assertRaises(security.SecurityError):
            self.store.recover("gregory", PHRASE, "Third-Pass-888")

    def test_tampered_vault_is_detected(self):
        session = self.make()
        session.lock()
        path = session.vault.path
        data = bytearray(path.read_bytes())
        data[-5] ^= 1
        path.write_bytes(bytes(data))
        again = self.store.sign_in("gregory", PASSWORD)
        with self.assertRaisesRegex(security.SecurityError, "changed or damaged"):
            again.open_inventory()

    def test_users_are_separate(self):
        a = self.make("alice")
        a.open_inventory().delete_item("item_1")
        a.lock()
        b = self.make("bob", sample=False)
        self.assertEqual(b.open_inventory().stats()["items"], 0)
        b.lock()
        self.assertEqual(self.store.sign_in("alice", PASSWORD).open_inventory().stats()["items"], 17)

    def test_import_existing_plain_database(self):
        plain = Path(self.temp.name) / "old.sqlite3"
        inv = Inventory(plain)
        inv.delete_item("item_2")
        inv.close()
        session = self.make(initial_db=plain)
        self.assertEqual(session.open_inventory().stats()["items"], 17)
        session.lock()

    def test_backup_round_trip_to_new_computer(self):
        session = self.make()
        session.open_inventory().set_setting("theme", "voltage")
        target = Path(self.temp.name) / "me.hbbackup"
        self.store.export_backup(session, target)
        session.lock()
        self.assertNotIn(b"voltage", target.read_bytes())
        other = security.AccountStore(Path(self.temp.name) / "other", FAST)
        self.assertEqual(other.import_backup(target), "gregory")
        with self.assertRaises(security.SecurityError):
            other.import_backup(target)
        restored = other.sign_in("gregory", PASSWORD).open_inventory()
        self.assertEqual(restored.get_setting("theme"), "voltage")

    def test_delete_account(self):
        session = self.make()
        path = session.vault.path
        with self.assertRaises(security.SecurityError):
            self.store.delete_account(session, "bad password!")
        self.store.delete_account(session, PASSWORD)
        self.assertFalse(path.exists())
        self.assertEqual(self.store.usernames(), [])

    def test_totp_known_vector(self):
        # RFC 6238 test secret "12345678901234567890" at T=59 -> 94287082 (8 digits) -> 287082 (6 digits)
        secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
        self.assertEqual(security.totp_code(secret, 59 // 30), "287082")

    def test_generated_phrase_passes_checks(self):
        phrase = security.generate_phrase()
        self.assertEqual(len(phrase.split()), 12)
        security.check_seed_phrase(phrase + " extra words here")

    def test_saves_after_each_change(self):
        session = self.make()
        writes = []
        inv = session.open_inventory(on_change=lambda: writes.append(1))
        inv.adjust_quantity("item_1", 1)
        inv.undo()
        self.assertEqual(len(writes), 2)


if __name__ == "__main__":
    unittest.main()

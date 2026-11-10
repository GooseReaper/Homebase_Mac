"""Builds the real window (needs a desktop display; skipped otherwise)."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import tkinter as tk
    _probe = tk.Tk()
    _probe.destroy()
    HAS_DISPLAY = True
except Exception:
    HAS_DISPLAY = False

from inventory import Inventory


@unittest.skipUnless(HAS_DISPLAY, 'needs a desktop display and Tkinter')
class GuiTests(unittest.TestCase):
    def setUp(self):
        import app
        self.appmod = app
        self.temp = tempfile.TemporaryDirectory()
        self.inv = Inventory(Path(self.temp.name) / 'gui.sqlite3')
        self.root = tk.Tk()
        self.app = app.HomebaseApp(self.root, self.inv)
        self.pump()

    def tearDown(self):
        try:
            self.app.close()
        except Exception:
            pass
        self.temp.cleanup()

    def pump(self):
        for _ in range(4):
            self.root.after(15)
            self.root.update()

    def tab_titles(self):
        return [self.app.tab_title(t) for t in self.app.tabs]

    def test_window_lists_items(self):
        self.assertEqual(len(self.app.item_tree.get_children()), 18)
        self.assertEqual(self.tab_titles(), ['All items'])
        self.assertEqual(str(self.app.undo_button['state']), 'disabled')

    def test_browser_tabs_open_close_reopen_back_forward(self):
        self.app.open_location('office', new_tab=True)
        self.assertEqual(self.tab_titles(), ['All items', 'Office'])
        self.assertEqual(len(self.app.item_tree.get_children()), 5)
        self.app.open_item(new_tab=True, item_id='item_1')
        self.assertEqual(self.app.tab['page'], 'item')
        self.assertEqual(self.app.page_name.get(), 'Sony WH-1000XM4')
        self.app.navigate('items', {'location_id': 'garage'})
        self.app.go_back()
        self.assertEqual(self.app.tab['page'], 'item')
        self.app.go_forward()
        self.assertEqual(self.app.tab_title(self.app.tab), 'Garage')
        self.app.close_tab()
        self.assertEqual(len(self.app.tabs), 2)
        self.app.reopen_tab()
        self.assertEqual(self.app.tab_title(self.app.tab), 'Garage')
        self.app.activate(0)
        self.assertEqual(len(self.app.item_tree.get_children()), 18)
        self.app.save_tabs()
        saved = self.inv.get_setting('open_tabs')
        self.assertEqual([t['page'] for t in saved], ['items', 'items', 'items'])

    def test_search_per_tab(self):
        self.app.search_var.set('usb')
        self.app.on_search_typed(force=True)
        self.pump()
        shown = len(self.app.item_tree.get_children())
        self.assertLess(shown, 18)
        self.app.new_tab()
        self.assertEqual(len(self.app.item_tree.get_children()), 18)
        self.app.activate(0)
        self.assertEqual(len(self.app.item_tree.get_children()), shown)
        self.assertEqual(self.app.search_var.get(), 'usb')

    def test_undo_redo_buttons_follow_changes(self):
        self.app.selected_id = 'item_1'
        self.app.adjust_quantity(1)
        self.assertEqual(str(self.app.undo_button['state']), 'normal')
        self.assertIn('quantity', self.app.edit_menu.entrycget(0, 'label'))
        self.app.undo_button.invoke()
        self.assertEqual(self.inv.get_item('item_1')['quantity'], 1)
        self.app.redo_button.invoke()
        self.assertEqual(self.inv.get_item('item_1')['quantity'], 2)

    def test_every_theme_and_layout_option_builds(self):
        self.app.open_location('office', new_tab=True)
        for theme in self.appmod.THEMES:
            self.app.change_setting('theme', theme)
            self.pump()
            self.assertEqual(self.appmod.T['name'].lower(), theme)
            self.assertEqual(self.tab_titles(), ['All items', 'Office'])
        for key, value in (('sidebar', 'Right'), ('detail', 'Left'), ('tabbar', 'Bottom'), ('show_stats', False),
                           ('density', 'Compact'), ('text_scale', 140), ('accent', '#ff00aa'), ('sidebar', 'Hidden'),
                           ('detail', 'Hidden'), ('font_body', 'Courier'), ('font_display', '')):
            self.app.change_setting(key, value)
            self.pump()
        self.assertEqual(self.inv.get_setting('tabbar'), 'Bottom')
        self.assertEqual(len(self.app.item_tree.get_children()), 5)
        self.app.open_or_focus('settings')
        self.app.open_or_focus('assistant')
        self.app.open_or_focus('locations')
        self.app.open_or_focus('activity')
        self.pump()

    def test_item_dialog_opens_and_closes(self):
        self.app.add_item()
        self.pump()
        self.assertIsNotNone(self.root.grab_current())
        self.root.grab_current().destroy()
        self.app.edit_item('item_2')
        self.pump()
        self.root.grab_current().destroy()

    def test_photo_drop_on_item_is_undoable(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest('Pillow not installed')
        png = Path(self.temp.name) / 'p.png'
        Image.new('RGB', (8, 8), 'red').save(png)
        self.app.dropped_on_item([str(png)], 'item_3')
        self.assertTrue(self.inv.get_item('item_3')['photo'])
        self.app.undo()
        self.assertFalse(self.inv.get_item('item_3')['photo'])

    def test_ai_chat_proposes_changes_for_approval(self):
        import ai
        try:
            from test_ai import FakeNet
        except ImportError:
            from tests.test_ai import FakeNet
        self.app.change_setting('ai_key_claude', 'sk-test', rebuild=False)
        reply = json.dumps({"reply": "Moved it.", "actions": [{"type": "update_item", "item_id": "item_1", "changes": {"quantity": 9}}]})
        with patch('ai.urllib.request.urlopen', FakeNet(reply)), patch('app.ReviewDialog') as review:
            self.app.open_or_focus('assistant')
            self.app.send_chat('set headphones to 9')
            for _ in range(40):
                self.root.after(25)
                self.pump()
                if review.called:
                    break
        self.assertTrue(review.called)
        rows = review.call_args.args[3]
        self.assertIn('quantity → 9', rows[0][0])
        review.call_args.args[4]([rows[0][1]])
        self.assertEqual(self.inv.get_item('item_1')['quantity'], 9)
        self.app.undo()
        self.assertEqual(self.inv.get_item('item_1')['quantity'], 1)


@unittest.skipUnless(HAS_DISPLAY, 'needs a desktop display and Tkinter')
class AccountFlowTests(unittest.TestCase):
    def test_create_use_lock_and_sign_back_in(self):
        import app
        import security
        from ui_auth import AuthScreen, TwoFactorSetup
        if not security.crypto_available():
            self.skipTest('cryptography not installed')
        temp = tempfile.TemporaryDirectory()
        root = tk.Tk()
        password, phrase = 'Correct-Horse-42', security.generate_phrase()
        store = security.AccountStore(Path(temp.name), {'n': 2 ** 10, 'r': 8, 'p': 1})
        signed, out = {}, {}
        auth = AuthScreen(root, store, lambda s, two: signed.update(s=s, two=two))
        auth.new_user.set('greg')
        auth.new_password.set(password)
        auth.new_password2.set('typo')
        auth.phrase.insert('1.0', phrase)
        auth.create()
        self.assertIn("don't match", auth.error.get())
        auth.new_password2.set(password)
        auth.create()
        self.assertIn('write down', auth.error.get().lower())
        auth.wrote_it.set(True)
        auth.create()
        self.assertEqual(signed['s'].username, 'greg')
        self.assertTrue(signed['two'])
        auth.destroy()
        win = app.HomebaseApp(root, signed['s'].open_inventory(), session=signed['s'], store=store,
                              on_sign_out=lambda u, m: out.update(u=u, m=m))
        root.update()
        setup = TwoFactorSetup(win)
        setup.code.set(security.totp_code(setup.secret, int(__import__('time').time() // 30)))
        setup.enable()
        root.update()
        self.assertTrue(signed['s'].totp_enabled)
        win.inventory.delete_item('item_1')
        win.change_setting('theme', 'blossom')
        root.after(30)
        root.update()
        win.lock()
        self.assertIn('Locked', out['m'])
        raw = signed['s'].vault.path.read_bytes()
        self.assertNotIn(b'Sony', raw)
        auth = AuthScreen(root, store, lambda s, two: signed.update(s=s, two=two), username='greg', message=out['m'])
        before = signed['s']
        auth.password.set('wrong-password!')
        auth.sign_in()
        self.assertIn('Wrong', auth.error.get())
        auth.password.set(password)
        auth.sign_in()
        self.assertIs(signed['s'], before)   # still waiting for the 2FA code
        root.update()
        root.destroy()
        temp.cleanup()


if __name__ == '__main__':
    unittest.main()

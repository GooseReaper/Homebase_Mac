"""Undo/redo and settings: python -m unittest discover -s tests -v"""
import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import inventory as inventory_module
from inventory import Inventory, InventoryError

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aU1sAAAAASUVORK5CYII=')


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'inventory.sqlite3'
        self.inv = Inventory(self.path)

    def tearDown(self):
        self.inv.close()
        self.temp.cleanup()

    def item(self, **changes):
        data = dict(name='Travel cable', category='Electronics', quantity='3', location_id='cables',
                    value='12.50', low_stock='1', tags='usb-c, travel', notes='Braided spare', photo=PNG, photo_name='c.png')
        data.update(changes)
        return data

    def state(self):
        return (self.inv.stats(), self.inv.locations(),
                [(i['id'], i['name'], i['quantity'], i['location_id'], i['tags'], i['photo'], i['updated_at']) for i in self.inv.items()])

    def test_nothing_to_undo_at_start(self):
        self.assertFalse(self.inv.can_undo())
        self.assertFalse(self.inv.can_redo())
        with self.assertRaises(InventoryError):
            self.inv.undo()
        with self.assertRaises(InventoryError):
            self.inv.redo()

    def test_add_edit_move_delete_each_undo_and_redo_exactly(self):
        start = self.state()
        added = self.inv.save_item(self.item())
        after_add = self.state()
        self.inv.save_item(self.item(name='Moved cable', location_id='gear', tags='moved'), added['id'])
        after_edit = self.state()
        self.assertEqual(self.inv.undo_label(), 'Move “Moved cable”')
        self.inv.delete_item(added['id'])
        after_delete = self.state()
        self.assertEqual(self.inv.undo()[1], added['id'])
        self.assertEqual(self.state(), after_edit)
        self.inv.undo()
        self.assertEqual(self.state(), after_add)
        self.assertEqual(self.inv.get_item(added['id'])['photo'], PNG)
        self.inv.undo()
        self.assertEqual(self.state(), start)
        self.assertFalse(self.inv.can_undo())
        self.inv.redo(); self.inv.redo(); self.inv.redo()
        self.assertEqual(self.state(), after_delete)
        self.assertFalse(self.inv.can_redo())

    def test_new_change_clears_redo(self):
        self.inv.delete_item('item_1')
        self.inv.undo()
        self.assertTrue(self.inv.can_redo())
        self.inv.adjust_quantity('item_2', 1)
        self.assertFalse(self.inv.can_redo())

    def test_rapid_quantity_clicks_merge_into_one_step(self):
        before = self.inv.get_item('item_1')['quantity']
        for _ in range(4):
            self.inv.adjust_quantity('item_1', 1)
        self.assertEqual(len(self.inv.history()), 1)
        self.assertIn(str(before + 4), self.inv.undo_label())
        self.inv.undo()
        self.assertEqual(self.inv.get_item('item_1')['quantity'], before)
        with patch.object(inventory_module, 'MERGE_SECONDS', -1):
            self.inv.adjust_quantity('item_1', 1)
            self.inv.adjust_quantity('item_1', 1)
        self.assertEqual(len(self.inv.history()), 2)

    def test_location_add_rename_move_delete_undo(self):
        start = self.inv.locations()
        box = self.inv.save_location(dict(name='Box', kind='Container', parent_id='garage'))
        self.inv.save_location(dict(name='Blue box', kind='Container', parent_id='office'), box['id'])
        self.inv.delete_location(box['id'])
        self.inv.undo()
        self.assertEqual(self.inv.path_names(box['id'])[-2:], ['Office', 'Blue box'])
        self.inv.undo()
        self.assertEqual(self.inv.get_location(box['id'])['name'], 'Box')
        self.inv.undo()
        self.assertEqual(self.inv.locations(), start)

    def test_undo_refuses_when_later_state_conflicts_and_keeps_history(self):
        box = self.inv.save_location(dict(name='Box', kind='Container', parent_id='garage'))
        self.inv.clear_history()
        self.inv.delete_location(box['id'])
        # Something outside the history now uses the same name in the same parent.
        self.inv.db.execute("INSERT INTO locations(id,name,kind,parent_id) VALUES('other','box','Container','garage')")
        before = self.state()
        with self.assertRaisesRegex(InventoryError, "Can't undo"):
            self.inv.undo()
        self.assertEqual(self.state(), before)
        self.assertTrue(self.inv.can_undo())

    def test_undo_add_location_blocked_if_it_now_holds_items_outside_history(self):
        box = self.inv.save_location(dict(name='Box', kind='Container', parent_id='garage'))
        self.inv.db.execute("UPDATE items SET location_id=? WHERE id='item_1'", (box['id'],))
        with self.assertRaisesRegex(InventoryError, 'holds items'):
            self.inv.undo()

    def test_reset_samples_is_undoable(self):
        added = self.inv.save_item(self.item())
        box = self.inv.save_location(dict(name='Attic', kind='Room', parent_id='home'))
        self.inv.save_location(dict(name='Workshop', kind='Room', parent_id='home'), 'garage')
        before = self.state()
        self.inv.reset_samples()
        self.assertEqual(self.inv.stats()['items'], 18)
        self.inv.undo()
        self.assertEqual(self.state(), before)
        self.assertEqual(self.inv.get_item(added['id'])['photo'], PNG)
        self.inv.redo()
        self.assertRaises(InventoryError, self.inv.get_location, box['id'])
        self.assertEqual(self.inv.db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_failed_save_records_nothing(self):
        with self.assertRaises(InventoryError):
            self.inv.save_item(self.item(quantity='-5'))
        self.assertFalse(self.inv.can_undo())

    def test_history_limit(self):
        with patch.object(inventory_module, 'HISTORY_LIMIT', 5), patch.object(inventory_module, 'MERGE_SECONDS', -1):
            for _ in range(8):
                self.inv.adjust_quantity('item_3', 1)
        self.assertEqual(len(self.inv.history()), 5)

    def test_undo_and_redo_are_logged(self):
        self.inv.delete_item('item_1')
        self.inv.undo()
        self.inv.redo()
        messages = [row['message'] for row in self.inv.activity()[:2]]
        self.assertTrue(messages[0].startswith('Redid: Delete'))
        self.assertTrue(messages[1].startswith('Undid: Delete'))

    def test_settings_persist_and_survive_reset(self):
        self.assertEqual(self.inv.get_setting('theme', 'modern'), 'modern')
        self.inv.set_setting('theme', 'retro')
        self.inv.set_setting('confirm_delete', False)
        self.inv.reset_samples()
        self.inv.close()
        self.inv = Inventory(self.path)
        self.assertEqual(self.inv.get_setting('theme'), 'retro')
        self.assertIs(self.inv.get_setting('confirm_delete'), False)

    def test_location_counts_include_nested_items(self):
        counts = self.inv.location_counts()
        for loc in self.inv.locations():
            self.assertEqual(counts[loc['id']], len(self.inv.items(location_id=loc['id'])))

    def test_list_view_skips_photo_bytes(self):
        self.inv.save_item(self.item())
        rows = self.inv.items(include_photos=False)
        self.assertNotIn('photo', rows[0])
        self.assertEqual(rows[0]['photo_size'], len(PNG))


if __name__ == '__main__':
    unittest.main()

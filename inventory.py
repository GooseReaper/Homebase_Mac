"""Local inventory rules and SQLite persistence. No network or GUI dependencies.

Every change made through this class is recorded in an undo/redo history, so the
interface can offer Undo and Redo for adding, editing, moving, deleting, quantity
changes, location changes, and even "Reset samples".
"""
from __future__ import annotations

import csv
import json
import math
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

CATEGORIES = ("Electronics", "Tools", "Kitchen", "Outdoor", "Books", "Home")
LOCATION_TYPES = ("House", "Room", "Storage", "Drawer", "Shelf", "Container")
SORTS = ("Recently updated", "Name A–Z", "Quantity: low first", "Value: high first")
ROOT = Path(__file__).resolve().parent
MAX_PHOTO_BYTES = 10 * 1024 * 1024
HISTORY_LIMIT = 100                    # undo steps kept while the app is open
HISTORY_BYTES = 150 * 1024 * 1024      # memory cap for photos held by undo steps
MERGE_SECONDS = 2.0                    # rapid +/- clicks on one item become one undo step
ITEM_FIELDS = ("id", "name", "category", "quantity", "location_id", "value", "low_stock",
               "notes", "photo", "photo_name", "created_at", "updated_at")


class InventoryError(ValueError):
    """An input or operation the interface should explain to the user."""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def required_text(value, label, limit=100):
    text = str(value or "").strip()
    if not text:
        raise InventoryError(f"{label} is required.")
    if len(text) > limit:
        raise InventoryError(f"{label} must be {limit} characters or fewer.")
    return text


def whole_number(value, label):
    text = str(value).strip()
    if not re.fullmatch(r"[0-9]{1,6}", text):
        raise InventoryError(f"{label} must be a whole number from 0 to 999,999.")
    return int(text)


def _size(state):
    """Rough memory size of a history state (photos dominate)."""
    if not state:
        return 0
    if "items" in state:
        return sum(len(item.get("photo") or b"") for item in state["items"])
    return len(state.get("photo") or b"")


class Inventory:
    """Owns every database transaction used by the desktop application.

    Plain mode: Inventory("file.sqlite3") reads and writes an ordinary SQLite file.
    Encrypted mode: security.Vault opens a decrypted in-memory copy and passes it as
    `connection`; `on_change` is called after every committed change so the vault can
    re-encrypt and save it.
    """

    def __init__(self, database_path=None, sample=True, connection=None, on_change=None):
        self.on_change = on_change
        self.encrypted = connection is not None
        if connection is not None:
            self.path = Path(database_path).resolve() if database_path else None
            self.db = connection
        else:
            self.path = Path(database_path).resolve()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA busy_timeout = 10000")
        self.db.executescript((ROOT / "schema.sql").read_text(encoding="utf-8"))
        self._undo, self._redo = [], []
        initialized = self.db.execute("SELECT value FROM metadata WHERE key='initialized'").fetchone()
        if initialized is None:
            with self.transaction():
                if sample:
                    self._load_samples()
                self.db.execute("INSERT INTO metadata(key, value) VALUES('initialized', '1')")
        result = self.db.execute("PRAGMA foreign_key_check").fetchone()
        if result:
            raise InventoryError("The database has a broken location reference. Restore a backup.")

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        self._changed()

    def _changed(self):
        if self.on_change is not None:
            self.on_change()

    def serialize(self):
        """The whole database as bytes (used to encrypt it)."""
        if hasattr(self.db, "serialize"):
            return b"S" + self.db.serialize()
        return b"D" + "\n".join(self.db.iterdump()).encode("utf-8")

    def _log(self, text):
        self.db.execute("INSERT INTO activity(message, created_at) VALUES(?, ?)", (text, now()))
        self.db.execute("DELETE FROM activity WHERE id NOT IN (SELECT id FROM activity ORDER BY id DESC LIMIT 200)")

    def _load_samples(self):
        data = json.loads((ROOT / "sample_data.json").read_text(encoding="utf-8"))
        for location in data["locations"]:
            self.db.execute("INSERT INTO locations(id,name,kind,parent_id) VALUES(?,?,?,?)",
                            (location["id"], location["name"], location["type"], location["parentId"]))
        for item in data["items"]:
            self._insert_item({
                "id": item["id"], "name": item["name"], "category": item["category"],
                "quantity": item["quantity"], "location_id": item["locationId"],
                "value": item["value"], "low_stock": item["lowStock"],
                "notes": item["notes"], "tags": item["tags"], "photo": b"", "photo_name": "",
                "created_at": item["createdAt"], "updated_at": item["updatedAt"]
            })
        self._log("Sample inventory ready: 18 items across 16 locations")

    # ------------------------------------------------------------------ settings
    def get_setting(self, key, default=None):
        row = self.db.execute("SELECT value FROM metadata WHERE key=?", (f"setting:{key}",)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row[0])
        except ValueError:
            return default

    def set_setting(self, key, value):
        self.db.execute("INSERT INTO metadata(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (f"setting:{key}", json.dumps(value)))
        self._changed()

    # ----------------------------------------------------------------- locations
    def locations(self):
        return [dict(row) for row in self.db.execute("SELECT * FROM locations ORDER BY rowid")]

    def _location_map(self):
        return {loc["id"]: loc for loc in self.locations()}

    def get_location(self, location_id):
        row = self.db.execute("SELECT * FROM locations WHERE id=?", (location_id,)).fetchone()
        if row is None:
            raise InventoryError("This location no longer exists.")
        return dict(row)

    def path_names(self, location_id, location_map=None):
        parts, seen = [], set()
        while location_id:
            if location_id in seen:
                raise InventoryError("Locations cannot contain a cycle.")
            seen.add(location_id)
            if location_map is not None:
                location = location_map.get(location_id)
                if location is None:
                    raise InventoryError("This location no longer exists.")
            else:
                location = self.get_location(location_id)
            parts.insert(0, location["name"])
            location_id = location["parent_id"]
        return parts

    def path_text(self, location_id, location_map=None):
        return " → ".join(self.path_names(location_id, location_map))

    def descendants(self, location_id, location_map=None):
        location_map = location_map if location_map is not None else self._location_map()
        if location_id not in location_map:
            raise InventoryError("This location no longer exists.")
        children = {}
        for loc in location_map.values():
            children.setdefault(loc["parent_id"], []).append(loc["id"])
        found, stack = set(), [location_id]
        while stack:
            current = stack.pop()
            if current not in found:
                found.add(current)
                stack.extend(children.get(current, []))
        return found

    def location_counts(self):
        """Unique items in each location, including everything nested inside it."""
        direct = {row[0]: row[1] for row in self.db.execute("SELECT location_id, COUNT(*) FROM items GROUP BY location_id")}
        location_map = self._location_map()
        totals = {loc_id: 0 for loc_id in location_map}
        for loc_id, count in direct.items():
            seen = set()
            while loc_id and loc_id not in seen and loc_id in location_map:
                seen.add(loc_id)
                totals[loc_id] += count
                loc_id = location_map[loc_id]["parent_id"]
        return totals

    def save_location(self, data, location_id=None):
        name = required_text(data.get("name"), "Location name", 60)
        kind = data.get("kind", "Storage")
        if kind not in LOCATION_TYPES:
            raise InventoryError("Choose a valid location type.")
        parent_id = data.get("parent_id") or None
        with self.transaction():
            before = self.get_location(location_id) if location_id else None
            if parent_id:
                self.get_location(parent_id)
                if location_id and parent_id in self.descendants(location_id):
                    raise InventoryError("A location cannot be moved into itself or one of its children.")
            for sibling in self.locations():
                if (sibling["id"] != location_id and sibling["parent_id"] == parent_id
                        and sibling["name"].casefold() == name.casefold()):
                    raise InventoryError("That name already exists inside this parent location.")
            key = location_id or f"loc_{uuid.uuid4().hex}"
            if location_id:
                self.db.execute("UPDATE locations SET name=?,kind=?,parent_id=? WHERE id=?",
                                (name, kind, parent_id, key))
            else:
                self.db.execute("INSERT INTO locations(id,name,kind,parent_id) VALUES(?,?,?,?)",
                                (key, name, kind, parent_id))
            self._log(f"{'Updated' if location_id else 'Created'} location: {name}")
            after = self.get_location(key)
        verb = "Move/rename" if before and (before["parent_id"] != parent_id or before["name"] != name) else "Edit" if before else "Add"
        self._record(f"{verb} location “{name}”", [("location", key, before, after)])
        return after

    def delete_location(self, location_id):
        with self.transaction():
            location = self.get_location(location_id)
            has_items = self.db.execute("SELECT 1 FROM items WHERE location_id=? LIMIT 1", (location_id,)).fetchone()
            has_children = self.db.execute("SELECT 1 FROM locations WHERE parent_id=? LIMIT 1", (location_id,)).fetchone()
            if has_items or has_children:
                raise InventoryError("This location is not empty. Move its items and child locations first.")
            self.db.execute("DELETE FROM locations WHERE id=?", (location_id,))
            self._log(f"Deleted location: {location['name']}")
        self._record(f"Delete location “{location['name']}”", [("location", location_id, location, None)])
        return location

    # --------------------------------------------------------------------- items
    def all_tags(self):
        return [row[0] for row in self.db.execute("SELECT DISTINCT tag FROM item_tags ORDER BY tag COLLATE NOCASE")]

    def _item_dict(self, row, tags=None):
        item = dict(row)
        if "photo" in item:
            item["photo"] = bytes(item["photo"] or b"")
        if tags is None:
            tags = [r[0] for r in self.db.execute("SELECT tag FROM item_tags WHERE item_id=? ORDER BY rowid", (item["id"],))]
        item["tags"] = tags
        return item

    def get_item(self, item_id):
        row = self.db.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if row is None:
            raise InventoryError("This item no longer exists.")
        return self._item_dict(row)

    def _find_item(self, item_id):
        row = self.db.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        return self._item_dict(row) if row else None

    def items(self, query="", location_id=None, category="", tag="", low_only=False, sort="Recently updated", include_photos=True):
        """Filtered items. The table view passes include_photos=False so photos are not loaded."""
        columns = "*" if include_photos else ("id,name,category,quantity,location_id,value,low_stock,notes,"
                                              "photo_name,created_at,updated_at,length(photo) AS photo_size")
        tag_map = {}
        for row in self.db.execute("SELECT item_id, tag FROM item_tags ORDER BY rowid"):
            tag_map.setdefault(row[0], []).append(row[1])
        location_map = self._location_map()
        ids = self.descendants(location_id, location_map) if location_id else None
        query = query.strip().casefold()
        filtered = []
        for row in self.db.execute(f"SELECT {columns} FROM items ORDER BY updated_at DESC"):
            item = self._item_dict(row, tag_map.get(row["id"], []))
            if ids is not None and item["location_id"] not in ids:
                continue
            if category and item["category"] != category:
                continue
            if tag and tag not in item["tags"]:
                continue
            if low_only and item["quantity"] > item["low_stock"]:
                continue
            if query:
                text = " ".join([item["name"], item["category"], item["notes"], *item["tags"],
                                 self.path_text(item["location_id"], location_map)])
                if query not in text.casefold():
                    continue
            filtered.append(item)
        if sort == "Name A–Z":
            filtered.sort(key=lambda item: item["name"].casefold())
        elif sort == "Quantity: low first":
            filtered.sort(key=lambda item: (item["quantity"], item["name"].casefold()))
        elif sort == "Value: high first":
            filtered.sort(key=lambda item: item["quantity"] * item["value"], reverse=True)
        return filtered

    def stats(self):
        row = self.db.execute("SELECT COUNT(*) AS items, COALESCE(SUM(quantity),0) AS units, "
                              "COALESCE(SUM(quantity*value),0) AS value, "
                              "COALESCE(SUM(quantity<=low_stock),0) AS low FROM items").fetchone()
        result = dict(row)
        result["locations"] = self.db.execute("SELECT COUNT(*) FROM locations").fetchone()[0]
        return result

    def _validated_item(self, data, old=None):
        name = required_text(data.get("name"), "Item name")
        category = data.get("category")
        if category not in CATEGORIES:
            raise InventoryError("Choose a valid category.")
        quantity = whole_number(data.get("quantity", ""), "Quantity")
        low_stock = whole_number(data.get("low_stock", 0), "Low stock threshold")
        try:
            value = float(data.get("value") or 0)
        except (ValueError, TypeError):
            raise InventoryError("Unit value must be a number.") from None
        if not math.isfinite(value) or not 0 <= value <= 100_000_000:
            raise InventoryError("Unit value must be between $0 and $100,000,000.")
        if len(str(data.get("value") or 0)) > 30:
            raise InventoryError("Unit value is too long.")
        value = round(value, 2)
        notes = str(data.get("notes") or "").strip()
        if len(notes) > 2000:
            raise InventoryError("Notes must be 2,000 characters or fewer.")
        raw_tags = data.get("tags", [])
        if isinstance(raw_tags, str):
            raw_tags = raw_tags.split(",")
        if not isinstance(raw_tags, (list, tuple)):
            raise InventoryError("Tags must be separated by commas.")
        tags = list(dict.fromkeys(str(tag).strip().casefold() for tag in raw_tags if str(tag).strip()))
        if len(tags) > 12 or any(len(tag) > 30 for tag in tags):
            raise InventoryError("Use up to 12 tags, each 30 characters or fewer.")
        location_id = data.get("location_id")
        if not location_id:
            raise InventoryError("Choose a location for this item.")
        self.get_location(location_id)
        photo = data.get("photo", old["photo"] if old else b"")
        if not isinstance(photo, bytes):
            raise InventoryError("Photo data must come from an image file.")
        if len(photo) > MAX_PHOTO_BYTES:
            raise InventoryError("Choose a photo under 10 MB.")
        if photo:
            supported = photo.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a")) or (photo.startswith(b"RIFF") and photo[8:12] == b"WEBP")
            if not supported:
                raise InventoryError("Use a PNG, GIF, JPG, or WebP photo.")
        return {
            "id": old["id"] if old else f"item_{uuid.uuid4().hex}",
            "name": name, "category": category, "quantity": quantity,
            "location_id": location_id, "value": value, "low_stock": low_stock,
            "notes": notes, "tags": tags, "photo": photo,
            "photo_name": str(data.get("photo_name", old["photo_name"] if old else ""))[:255],
            "created_at": old["created_at"] if old else now(), "updated_at": now()
        }

    def _insert_item(self, item):
        self.db.execute("INSERT INTO items(id,name,category,quantity,location_id,value,low_stock,notes,photo,photo_name,created_at,updated_at) "
                        "VALUES(:id,:name,:category,:quantity,:location_id,:value,:low_stock,:notes,:photo,:photo_name,:created_at,:updated_at)",
                        {key: item[key] for key in ITEM_FIELDS})
        self.db.executemany("INSERT INTO item_tags(item_id,tag) VALUES(?,?)", [(item["id"], tag) for tag in item["tags"]])

    def save_item(self, data, item_id=None):
        with self.transaction():
            old = self.get_item(item_id) if item_id else None
            item = self._validated_item(data, old)
            if old:
                self.db.execute("UPDATE items SET name=:name,category=:category,quantity=:quantity,location_id=:location_id,"
                                "value=:value,low_stock=:low_stock,notes=:notes,photo=:photo,photo_name=:photo_name,"
                                "updated_at=:updated_at WHERE id=:id", {key: item[key] for key in ITEM_FIELDS})
                self.db.execute("DELETE FROM item_tags WHERE item_id=?", (item_id,))
                self.db.executemany("INSERT INTO item_tags(item_id,tag) VALUES(?,?)", [(item_id, tag) for tag in item["tags"]])
            else:
                self._insert_item(item)
            self._log(f"{'Updated' if old else 'Added'} {item['name']}")
            saved = self.get_item(item["id"])
        if old and old["location_id"] != saved["location_id"]:
            label = f"Move “{saved['name']}”"
        else:
            label = f"{'Edit' if old else 'Add'} “{saved['name']}”"
        self._record(label, [("item", saved["id"], old, saved)], focus=saved["id"])
        return saved

    def adjust_quantity(self, item_id, delta):
        if not isinstance(delta, int) or isinstance(delta, bool) or delta not in (-1, 1):
            raise InventoryError("Adjust quantity one unit at a time.")
        with self.transaction():
            item = self.get_item(item_id)
            quantity = item["quantity"] + delta
            whole_number(quantity, "Quantity")
            self.db.execute("UPDATE items SET quantity=?,updated_at=? WHERE id=?", (quantity, now(), item_id))
            self._log(f"Set {item['name']} quantity to {quantity}")
            saved = self.get_item(item_id)
        self._record(f"Change “{item['name']}” quantity to {quantity}", [("item", item_id, item, saved)],
                     focus=item_id, merge_key=("quantity", item_id))
        return saved

    def set_quantity(self, item_id, quantity):
        """Set an exact quantity (one undo step)."""
        quantity = whole_number(quantity, "Quantity")
        with self.transaction():
            item = self.get_item(item_id)
            if item["quantity"] == quantity:
                return item
            self.db.execute("UPDATE items SET quantity=?,updated_at=? WHERE id=?", (quantity, now(), item_id))
            self._log(f"Set {item['name']} quantity to {quantity}")
            saved = self.get_item(item_id)
        self._record(f"Set “{item['name']}” quantity to {quantity}", [("item", item_id, item, saved)], focus=item_id)
        return saved

    def apply_batch(self, label, item_changes=(), new_locations=(), deletions=()):
        """Apply several changes at once, all-or-nothing, as ONE undo step.

        item_changes: [(item_id or None, data)] using the same fields as save_item
        (fields left out keep their current value). new_locations: [data] for
        save_location; a data dict may carry "ref" so later item data can use
        "location_ref" to point at it. deletions: [item_id].
        """
        changes, refs, focus = [], {}, None
        with self.transaction():
            for data in new_locations:
                name = required_text(data.get("name"), "Location name", 60)
                kind = data.get("kind") if data.get("kind") in LOCATION_TYPES else "Storage"
                parent_id = refs.get(data.get("parent_ref")) or data.get("parent_id") or None
                if parent_id:
                    self.get_location(parent_id)
                clash = [loc for loc in self.locations() if loc["parent_id"] == parent_id and loc["name"].casefold() == name.casefold()]
                if clash:
                    refs[data.get("ref")] = clash[0]["id"]
                    continue
                key = f"loc_{uuid.uuid4().hex}"
                self.db.execute("INSERT INTO locations(id,name,kind,parent_id) VALUES(?,?,?,?)", (key, name, kind, parent_id))
                refs[data.get("ref")] = key
                changes.append(("location", key, None, self.get_location(key)))
            for item_id, data in item_changes:
                data = dict(data)
                if data.get("location_ref") in refs:
                    data["location_id"] = refs[data.pop("location_ref")]
                old = self.get_item(item_id) if item_id else None
                merged = dict(old or {})
                merged.update({k: v for k, v in data.items() if v is not None})
                if old and "tags" not in data:
                    merged["tags"] = old["tags"]
                item = self._validated_item(merged, old)
                if old:
                    self._put_item(old["id"], {**item, "created_at": old["created_at"]})
                else:
                    self._insert_item(item)
                saved = self.get_item(item["id"])
                changes.append(("item", saved["id"], old, saved))
                focus = focus or saved["id"]
            for item_id in deletions:
                old = self.get_item(item_id)
                self.db.execute("DELETE FROM items WHERE id=?", (item_id,))
                changes.append(("item", item_id, old, None))
            if not changes:
                raise InventoryError("There was nothing to change.")
            self._log(label)
        self._record(label, changes, focus=focus)
        return changes

    def delete_item(self, item_id):
        with self.transaction():
            item = self.get_item(item_id)
            self.db.execute("DELETE FROM items WHERE id=?", (item_id,))
            self._log(f"Deleted {item['name']}")
        self._record(f"Delete “{item['name']}”", [("item", item_id, item, None)], focus=item_id)
        return item

    def restore_item(self, item):
        """Re-add a deleted item dictionary (kept for compatibility; Undo is preferred)."""
        with self.transaction():
            if self.db.execute("SELECT 1 FROM items WHERE id=?", (item["id"],)).fetchone():
                raise InventoryError("This item is already restored.")
            restored = self._validated_item(item)
            restored.update(id=item["id"], created_at=item["created_at"])
            self._insert_item(restored)
            self._log(f"Restored {restored['name']}")
            saved = self.get_item(item["id"])
        self._record(f"Restore “{saved['name']}”", [("item", saved["id"], None, saved)], focus=saved["id"])
        return saved

    def activity(self):
        return [dict(row) for row in self.db.execute("SELECT * FROM activity ORDER BY id DESC LIMIT 200")]

    def reset_samples(self):
        before = self._snapshot_all()
        with self.transaction():
            self._clear_all()
            self.db.execute("DELETE FROM activity")
            self._load_samples()
            self._log("Reset to the original sample inventory")
        self._record("Reset to sample inventory", [("all", None, before, self._snapshot_all())])

    def replace_with(self, database_path):
        """Replace everything with the items and locations of another Homebase database (one undo step)."""
        other = Inventory(database_path, sample=False)
        try:
            after = other._snapshot_all()
        finally:
            other.close()
        before = self._snapshot_all()
        with self.transaction():
            self._restore_all(after)
            self._log(f"Imported {Path(database_path).name}")
        self._record(f"Import {Path(database_path).name}", [("all", None, before, self._snapshot_all())])

    def backup(self, destination):
        target = Path(destination).resolve()
        if target == self.path:
            raise InventoryError("Choose a different file for your backup.")
        if not target.parent.is_dir():
            raise InventoryError("Choose an existing folder for your backup.")
        connection = sqlite3.connect(target)
        try:
            self.db.backup(connection)
        finally:
            connection.close()

    def export_csv(self, destination, items=None):
        rows = self.items(include_photos=False) if items is None else items
        location_map = self._location_map()
        with Path(destination).open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.writer(stream)
            writer.writerow(["Name", "Category", "Quantity", "Location", "Tags", "Unit value", "Total value", "Low stock at", "Notes"])
            for item in rows:
                cells = [item["name"], item["category"], item["quantity"], self.path_text(item["location_id"], location_map),
                         ", ".join(item["tags"]), f"{item['value']:.2f}", f"{item['value'] * item['quantity']:.2f}", item["low_stock"], item["notes"]]
                # Treat text that looks like a spreadsheet formula as literal text.
                writer.writerow(["'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@", "\t", "\r")) else v for v in cells])

    # ------------------------------------------------------------- undo / redo
    def _record(self, label, changes, focus=None, merge_key=None):
        """Remember a committed change. Each change is (kind, id, before, after)."""
        moment = time.monotonic()
        last = self._undo[-1] if self._undo else None
        if (merge_key and last and last["merge_key"] == merge_key and not self._redo
                and moment - last["time"] <= MERGE_SECONDS):
            kind, key, before, _ = last["changes"][0]
            last["changes"] = [(kind, key, before, changes[0][3])]
            last.update(label=label, time=moment)
            return
        self._undo.append({"label": label, "changes": changes, "focus": focus,
                           "merge_key": merge_key, "time": moment})
        self._redo.clear()
        self._trim_history()

    def _trim_history(self):
        while len(self._undo) > HISTORY_LIMIT:
            self._undo.pop(0)
        while len(self._undo) > 1 and sum(_size(b) + _size(a) for e in self._undo for _, _, b, a in e["changes"]) > HISTORY_BYTES:
            self._undo.pop(0)

    def can_undo(self):
        return bool(self._undo)

    def can_redo(self):
        return bool(self._redo)

    def undo_label(self):
        return self._undo[-1]["label"] if self._undo else ""

    def redo_label(self):
        return self._redo[-1]["label"] if self._redo else ""

    def history(self):
        """Undo steps, newest first, for display."""
        return [entry["label"] for entry in reversed(self._undo)]

    def clear_history(self):
        self._undo.clear()
        self._redo.clear()

    def undo(self):
        """Reverse the most recent change. Returns (label, item id to focus or None)."""
        if not self._undo:
            raise InventoryError("There is nothing to undo.")
        entry = self._undo[-1]
        self._apply(entry, reverse=True)
        self._redo.append(self._undo.pop())
        entry["merge_key"] = None
        return entry["label"], entry["focus"]

    def redo(self):
        """Repeat the most recently undone change."""
        if not self._redo:
            raise InventoryError("There is nothing to redo.")
        entry = self._redo[-1]
        self._apply(entry, reverse=False)
        self._undo.append(self._redo.pop())
        return entry["label"], entry["focus"]

    def _apply(self, entry, reverse):
        changes = list(reversed(entry["changes"])) if reverse else entry["changes"]
        try:
            with self.transaction():
                for kind, key, before, after in changes:
                    state = before if reverse else after
                    if kind == "item":
                        self._put_item(key, state)
                    elif kind == "location":
                        self._put_location(key, state)
                    else:
                        self._restore_all(state)
                for location in self.locations():   # refuse a result that would create a cycle
                    self.path_names(location["id"])
                self._log(f"{'Undid' if reverse else 'Redid'}: {entry['label']}")
        except sqlite3.IntegrityError:
            raise InventoryError(f"Can't {'undo' if reverse else 'redo'} “{entry['label']}” because later changes "
                                 "conflict with it (for example, a name that is now taken).") from None
        except InventoryError as error:
            raise InventoryError(f"Can't {'undo' if reverse else 'redo'} “{entry['label']}”: {error}") from None

    def _put_item(self, item_id, state):
        if state is None:
            self.db.execute("DELETE FROM items WHERE id=?", (item_id,))
            return
        self.get_location(state["location_id"])
        if self.db.execute("SELECT 1 FROM items WHERE id=?", (item_id,)).fetchone():
            self.db.execute("UPDATE items SET name=:name,category=:category,quantity=:quantity,location_id=:location_id,"
                            "value=:value,low_stock=:low_stock,notes=:notes,photo=:photo,photo_name=:photo_name,"
                            "created_at=:created_at,updated_at=:updated_at WHERE id=:id", {key: state[key] for key in ITEM_FIELDS})
            self.db.execute("DELETE FROM item_tags WHERE item_id=?", (item_id,))
            self.db.executemany("INSERT INTO item_tags(item_id,tag) VALUES(?,?)", [(item_id, tag) for tag in state["tags"]])
        else:
            self._insert_item(state)

    def _put_location(self, location_id, state):
        if state is None:
            busy = (self.db.execute("SELECT 1 FROM items WHERE location_id=? LIMIT 1", (location_id,)).fetchone()
                    or self.db.execute("SELECT 1 FROM locations WHERE parent_id=? LIMIT 1", (location_id,)).fetchone())
            if busy:
                raise InventoryError("that location now holds items or other locations.")
            self.db.execute("DELETE FROM locations WHERE id=?", (location_id,))
            return
        if state["parent_id"]:
            self.get_location(state["parent_id"])
        if self.db.execute("SELECT 1 FROM locations WHERE id=?", (location_id,)).fetchone():
            self.db.execute("UPDATE locations SET name=?,kind=?,parent_id=? WHERE id=?",
                            (state["name"], state["kind"], state["parent_id"], location_id))
        else:
            self.db.execute("INSERT INTO locations(id,name,kind,parent_id) VALUES(?,?,?,?)",
                            (location_id, state["name"], state["kind"], state["parent_id"]))

    def _snapshot_all(self):
        tag_map = {}
        for row in self.db.execute("SELECT item_id, tag FROM item_tags ORDER BY rowid"):
            tag_map.setdefault(row[0], []).append(row[1])
        return {"locations": self.locations(),
                "items": [self._item_dict(row, tag_map.get(row["id"], [])) for row in self.db.execute("SELECT * FROM items")]}

    def _clear_all(self):
        self.db.execute("DELETE FROM items")
        location_map = self._location_map()
        # Child locations first: the schema forbids deleting a parent that still has children.
        for loc_id in sorted(location_map, key=lambda key: len(self.path_names(key, location_map)), reverse=True):
            self.db.execute("DELETE FROM locations WHERE id=?", (loc_id,))

    def _restore_all(self, snapshot):
        self._clear_all()
        # Re-create locations in their original order (so the tree looks the same);
        # parent references are checked when the transaction commits.
        self.db.execute("PRAGMA defer_foreign_keys = ON")
        for loc in snapshot["locations"]:
            self.db.execute("INSERT INTO locations(id,name,kind,parent_id) VALUES(?,?,?,?)",
                            (loc["id"], loc["name"], loc["kind"], loc["parent_id"]))
        for item in snapshot["items"]:
            self._insert_item(item)

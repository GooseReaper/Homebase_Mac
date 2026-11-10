"""AI helpers with a fake network: nothing leaves the computer in tests."""
import base64
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path

import ai
from inventory import Inventory

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aU1sAAAAASUVORK5CYII=')


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeNet:
    def __init__(self, reply_text, provider="claude", status=200):
        self.reply_text, self.provider, self.status, self.requests = reply_text, provider, status, []

    def __call__(self, request, timeout=None, context=None):
        self.requests.append((request.full_url, dict(request.header_items()), json.loads(request.data)))
        if self.status != 200:
            raise urllib.error.HTTPError(request.full_url, self.status, "err", {}, io.BytesIO(b'{"error":{"message":"bad"}}'))
        if self.provider == "claude":
            body = {"content": [{"type": "text", "text": self.reply_text}]}
        else:
            body = {"choices": [{"message": {"content": self.reply_text}}]}
        return FakeResponse(json.dumps(body).encode())


class AITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.inv = Inventory(Path(self.temp.name) / "i.sqlite3")

    def tearDown(self):
        self.inv.close()
        self.temp.cleanup()

    def test_needs_key(self):
        with self.assertRaisesRegex(ai.AIError, "API key"):
            ai.Assistant("claude", "")

    def test_claude_request_shape_with_photo(self):
        net = FakeNet('```json\n{"name":"Hammer","category":"Tools","quantity":"2","tags":["Hand Tool"],"notes":"Claw hammer","value":"12.5","location":"Home → Garage"}\n```')
        result = ai.Assistant("claude", "sk-ant-x", opener=net).describe_photo(PNG, ("Electronics", "Tools", "Home"), ["Home → Garage"])
        self.assertEqual(result["name"], "Hammer")
        self.assertEqual(result["quantity"], 2)
        self.assertEqual(result["tags"], ["hand tool"])
        self.assertEqual(result["value"], 12.5)
        url, headers, body = net.requests[0]
        self.assertEqual(url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(headers["X-api-key"], "sk-ant-x")
        self.assertEqual(body["model"], ai.PROVIDERS["claude"]["default_model"])
        self.assertEqual(body["messages"][0]["content"][0]["type"], "image")

    def test_openai_request_shape(self):
        net = FakeNet("Your AA batteries are in Home → Living room → Media cabinet.", provider="openai")
        answer = ai.Assistant("openai", "sk-x", model="gpt-5.4-mini", opener=net).ask("where are my batteries", ai.inventory_context(self.inv))
        self.assertIn("Media cabinet", answer)
        url, headers, body = net.requests[0]
        self.assertTrue(url.endswith("/v1/chat/completions"))
        self.assertEqual(headers["Authorization"], "Bearer sk-x")
        self.assertEqual(body["model"], "gpt-5.4-mini")
        self.assertIn("AA rechargeable batteries", body["messages"][0]["content"])

    def test_errors_are_friendly(self):
        for status, words in ((401, "rejected"), (404, "wasn't found"), (429, "rate limit"), (500, r"error \(500\)")):
            with self.assertRaisesRegex(ai.AIError, words):
                ai.Assistant("claude", "k", opener=FakeNet("", status=status)).ask("q", "ctx")

    def test_context_hides_notes_and_values_unless_allowed(self):
        plain = ai.inventory_context(self.inv)
        self.assertNotIn("249.00", plain)
        full = ai.inventory_context(self.inv, include_notes=True, include_values=True)
        self.assertIn("249.00", full)

    def test_chat_actions_apply_as_one_undo_step(self):
        reply = json.dumps({"reply": "Moved and added.", "actions": [
            {"type": "create_location", "name": "Blue bin", "kind": "Container", "parent": "Home → Garage"},
            {"type": "update_item", "item_id": "item_1", "changes": {"location": "Home → Garage → Blue bin", "quantity": 3}},
            {"type": "create_item", "item": {"name": "Zip ties", "category": "Tools", "quantity": 100, "location": "Home → Garage → Blue bin", "tags": ["fasteners"]}},
            {"type": "delete_item", "item_id": "item_2"},
            {"type": "update_item", "item_id": "nope", "changes": {"name": "x"}},
            {"type": "hack_the_planet"}]})
        text, actions = ai.Assistant("claude", "k", opener=FakeNet(reply)).chat([], "tidy up", ai.inventory_context(self.inv))
        self.assertEqual(text, "Moved and added.")
        self.assertEqual(len(actions), 5)
        changes, locations, deletions, problems = ai.actions_to_batch(self.inv, actions)
        self.assertEqual(len(problems), 1)
        before = self.inv.stats()
        self.inv.apply_batch("AI: tidy up", changes, locations, deletions)
        self.assertEqual(self.inv.path_names(self.inv.get_item("item_1")["location_id"])[-1], "Blue bin")
        self.assertEqual(self.inv.get_item("item_1")["quantity"], 3)
        self.assertEqual(self.inv.items(query="zip ties")[0]["quantity"], 100)
        self.assertEqual(len(self.inv.history()), 1)
        self.inv.undo()
        self.assertEqual(self.inv.stats(), before)
        self.assertFalse(any(loc["name"] == "Blue bin" for loc in self.inv.locations()))

    def test_suggestions_parse(self):
        reply = json.dumps([{"item_id": "item_1", "tags": ["audio", "travel"], "category": None, "location": None, "reason": "travel gear"}, {"bad": 1}])
        out = ai.Assistant("openai", "k", opener=FakeNet(reply, provider="openai")).suggest_organization("ctx", ("Tools",), [])
        self.assertEqual(len(out), 1)

    def test_extract_json_failure(self):
        with self.assertRaises(ai.AIError):
            ai.extract_json("no json here")

    def test_set_quantity_is_undoable(self):
        self.inv.set_quantity("item_1", "40")
        self.assertEqual(self.inv.get_item("item_1")["quantity"], 40)
        self.inv.undo()
        self.assertEqual(self.inv.get_item("item_1")["quantity"], 1)


if __name__ == "__main__":
    unittest.main()

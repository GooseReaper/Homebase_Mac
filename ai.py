"""Optional ChatGPT (OpenAI) and Claude (Anthropic) helpers for Homebase.

Nothing is sent anywhere unless you add your own API key in Settings and use an AI
feature. What is sent: the item list (names, categories, quantities, locations, tags;
notes and values only if you allow it) and, for "Fill from photo", that one photo.
Network calls use only the Python standard library.
"""
from __future__ import annotations

import base64
import io
import json
import re
import ssl
import urllib.error
import urllib.request

try:
    import certifi
except ImportError:
    certifi = None

try:
    from PIL import Image, ImageOps
except ImportError:
    Image = ImageOps = None

PROVIDERS = {
    "claude": {"name": "Claude (Anthropic)", "default_model": "claude-sonnet-5-5",
               "models": ("claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"),
               "url": "https://api.anthropic.com/v1/messages", "key_hint": "sk-ant-…"},
    "openai": {"name": "ChatGPT (OpenAI)", "default_model": "gpt-5.5",
               "models": ("gpt-5.5", "gpt-5.6-luna", "gpt-5.4-mini"),
               "url": "https://api.openai.com/v1/chat/completions", "key_hint": "sk-…"},
}
MAX_CONTEXT_ITEMS = 400
ACTION_TYPES = ("update_item", "create_item", "delete_item", "create_location")


class AIError(Exception):
    """A problem the interface shows to the user."""


def _ssl_context():
    # Python.org builds for macOS don't use the system certificates unless certifi is installed.
    if certifi is not None:
        return ssl.create_default_context(cafile=certifi.where())
    return ssl.create_default_context()


def prepare_image(data, max_side=1024):
    """Shrink a photo to keep requests small. Returns (bytes, media type)."""
    if Image is not None:
        try:
            with Image.open(io.BytesIO(data)) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
                image.thumbnail((max_side, max_side))
                out = io.BytesIO()
                image.save(out, "JPEG", quality=85)
                return out.getvalue(), "image/jpeg"
        except Exception:
            pass
    for magic, media in ((b"\x89PNG", "image/png"), (b"\xff\xd8\xff", "image/jpeg"), (b"GIF8", "image/gif")):
        if data.startswith(magic):
            break
    else:
        media = "image/webp" if data[8:12] == b"WEBP" else None
    if media is None:
        raise AIError("This photo format can't be sent. Use PNG, JPG, GIF, or WebP.")
    if len(data) > 4_500_000:
        raise AIError("This photo is too large to send without Pillow. Install Pillow, or use a smaller photo.")
    return data, media


def extract_json(text):
    """Pull the first JSON object or array out of a model reply."""
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    candidates = [fenced.group(1)] if fenced else []
    candidates.append(text)
    for chunk in candidates:
        for opener, closer in (("{", "}"), ("[", "]")):
            start, end = chunk.find(opener), chunk.rfind(closer)
            if start != -1 and end > start:
                try:
                    return json.loads(chunk[start:end + 1])
                except ValueError:
                    continue
    raise AIError("The AI answer wasn't in the expected format. Try again.")


class Assistant:
    """Talks to one provider with the user's key. All methods block; run them off the UI thread."""

    def __init__(self, provider, api_key, model=None, timeout=90, opener=None):
        if provider not in PROVIDERS:
            raise AIError("Choose ChatGPT or Claude in Settings.")
        if not str(api_key or "").strip():
            raise AIError(f"Add your {PROVIDERS[provider]['name']} API key in Settings → AI.")
        self.provider, self.key = provider, api_key.strip()
        self.model = (model or "").strip() or PROVIDERS[provider]["default_model"]
        self.timeout = timeout
        self.opener = opener or urllib.request.urlopen

    # ---------------------------------------------------------------- transport
    def _post(self, url, headers, body):
        request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
        try:
            with self.opener(request, timeout=self.timeout, context=_ssl_context()) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = ""
            try:
                detail = json.loads(error.read().decode("utf-8")).get("error", {})
                detail = detail.get("message", "") if isinstance(detail, dict) else str(detail)
            except Exception:
                pass
            if error.code in (401, 403):
                raise AIError("The API key was rejected. Check it in Settings → AI.") from None
            if error.code == 404:
                raise AIError(f"The model “{self.model}” wasn't found. Pick another model in Settings → AI.") from None
            if error.code == 429:
                raise AIError("The AI service says you're over your rate limit or credit. Wait a bit, or check your account.") from None
            raise AIError(f"The AI service returned an error ({error.code}). {detail}".strip()) from None
        except urllib.error.URLError as error:
            raise AIError(f"Couldn't reach the AI service. Check your internet connection. ({error.reason})") from None
        except TimeoutError:
            raise AIError("The AI service took too long to answer. Try again.") from None

    def complete(self, system, user_text, image=None, max_tokens=1500):
        """One request → the reply text. `image` is raw photo bytes or None."""
        if self.provider == "claude":
            content = []
            if image is not None:
                data, media = prepare_image(image)
                content.append({"type": "image", "source": {"type": "base64", "media_type": media,
                                                            "data": base64.b64encode(data).decode("ascii")}})
            content.append({"type": "text", "text": user_text})
            reply = self._post(PROVIDERS["claude"]["url"],
                               {"content-type": "application/json", "x-api-key": self.key, "anthropic-version": "2023-06-01"},
                               {"model": self.model, "max_tokens": max_tokens, "system": system,
                                "messages": [{"role": "user", "content": content}]})
            parts = [block.get("text", "") for block in reply.get("content", []) if block.get("type") == "text"]
        else:
            content = [{"type": "text", "text": user_text}]
            if image is not None:
                data, media = prepare_image(image)
                content.append({"type": "image_url", "image_url": {"url": f"data:{media};base64,{base64.b64encode(data).decode('ascii')}"}})
            reply = self._post(PROVIDERS["openai"]["url"],
                               {"content-type": "application/json", "authorization": f"Bearer {self.key}"},
                               {"model": self.model, "max_completion_tokens": max_tokens,
                                "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}]})
            parts = [choice.get("message", {}).get("content") or "" for choice in reply.get("choices", [])[:1]]
        text = "".join(parts).strip()
        if not text:
            raise AIError("The AI service sent an empty answer. Try again.")
        return text

    # ------------------------------------------------------------------ features
    def test_connection(self):
        return self.complete("Reply with exactly: OK", "Connection test", max_tokens=20)

    def describe_photo(self, photo, categories, location_paths):
        """Suggest item fields from a photo."""
        system = ("You catalog household items for a home inventory app. Look at the photo and describe the main item. "
                  "Answer with JSON only.")
        prompt = (f"Return JSON with keys: name (short, specific, include brand/model if visible), "
                  f"category (exactly one of {list(categories)}), quantity (integer count of identical items visible), "
                  f"tags (2-6 short lowercase tags), notes (one or two useful sentences), "
                  f"value (estimated replacement cost per unit in USD, number), "
                  f"location (the best match from this list, or empty string): {json.dumps(location_paths[:200])}")
        data = extract_json(self.complete(system, prompt, image=photo))
        if not isinstance(data, dict):
            raise AIError("The AI answer wasn't in the expected format. Try again.")
        result = {"name": str(data.get("name", "")).strip()[:100],
                  "category": data.get("category") if data.get("category") in categories else categories[-1],
                  "tags": [str(t).strip().casefold()[:30] for t in (data.get("tags") or []) if str(t).strip()][:6],
                  "notes": str(data.get("notes", "")).strip()[:2000],
                  "location": str(data.get("location", "")).strip()}
        try:
            result["quantity"] = max(0, min(999999, int(data.get("quantity", 1))))
        except (TypeError, ValueError):
            result["quantity"] = 1
        try:
            result["value"] = max(0.0, min(100_000_000.0, round(float(data.get("value", 0)), 2)))
        except (TypeError, ValueError):
            result["value"] = 0.0
        return result

    def ask(self, question, context):
        system = ("You are the assistant inside Homebase, a personal home inventory app. Answer using ONLY the inventory "
                  "below. Give locations as full paths. Be brief and friendly. If the inventory doesn't say, say so.\n\n" + context)
        return self.complete(system, question, max_tokens=900)

    def suggest_organization(self, context, categories, location_paths):
        system = "You help tidy a home inventory. Answer with JSON only."
        prompt = ("Suggest improvements: missing or better tags, a better category, or a better existing location. "
                  "Only suggest changes that clearly help. Return a JSON list of up to 25 objects: "
                  "{item_id, tags (full new list, lowercase), category (one of " + json.dumps(list(categories)) + ") or null, "
                  "location (one existing path from the list below) or null, reason (short)}.\n\n"
                  "Existing locations: " + json.dumps(location_paths[:300]) + "\n\n" + context)
        data = extract_json(self.complete(system, prompt, max_tokens=3000))
        return [entry for entry in data if isinstance(entry, dict) and entry.get("item_id")] if isinstance(data, list) else []

    def chat(self, history, message, context):
        """A chat turn. Returns (reply text, proposed actions). Actions are applied only if the user approves."""
        system = ("You are the assistant inside Homebase, a personal home inventory app. Use the inventory below. "
                  "When the user asks you to change the inventory, propose actions; the app shows them for approval and "
                  "everything can be undone. Answer with JSON only: {\"reply\": text for the user, \"actions\": [ ... ]}. "
                  "Action shapes: {\"type\":\"update_item\",\"item_id\":...,\"changes\":{name|category|quantity|location|tags|notes|low_stock|value}}, "
                  "{\"type\":\"create_item\",\"item\":{name,category,quantity,location,tags,notes,value}}, "
                  "{\"type\":\"delete_item\",\"item_id\":...}, "
                  "{\"type\":\"create_location\",\"name\":...,\"kind\":House|Room|Storage|Drawer|Shelf|Container,\"parent\":existing path or \"\"}. "
                  "`location` and `parent` are full paths exactly as listed. Use an empty actions list when nothing should change.\n\n" + context)
        transcript = "\n".join(f"{'User' if role == 'user' else 'Assistant'}: {text}" for role, text in history[-12:])
        text = self.complete(system, (transcript + "\n" if transcript else "") + f"User: {message}", max_tokens=2000)
        try:
            data = extract_json(text)
        except AIError:
            return text, []
        if not isinstance(data, dict):
            return text, []
        actions = [a for a in data.get("actions") or [] if isinstance(a, dict) and a.get("type") in ACTION_TYPES]
        return str(data.get("reply") or "Done.").strip(), actions


def inventory_context(inventory, include_notes=False, include_values=False):
    """A compact text listing of the inventory for the AI."""
    location_map = {loc["id"]: loc for loc in inventory.locations()}
    lines = ["LOCATIONS (path | kind):"]
    lines += [f"- {inventory.path_text(loc_id, location_map)} | {loc['kind']}" for loc_id, loc in location_map.items()]
    items = inventory.items(include_photos=False, sort="Name A–Z")
    lines.append(f"ITEMS ({len(items)}; id | name | category | qty | low-stock-at | location | tags"
                 + (" | unit value $" if include_values else "") + (" | notes" if include_notes else "") + "):")
    for item in items[:MAX_CONTEXT_ITEMS]:
        cells = [item["id"], item["name"], item["category"], str(item["quantity"]), str(item["low_stock"]),
                 inventory.path_text(item["location_id"], location_map), ", ".join(item["tags"])]
        if include_values:
            cells.append(f"{item['value']:.2f}")
        if include_notes:
            cells.append(item["notes"].replace("\n", " ")[:200])
        lines.append("- " + " | ".join(cells))
    if len(items) > MAX_CONTEXT_ITEMS:
        lines.append(f"(… {len(items) - MAX_CONTEXT_ITEMS} more items not shown)")
    return "\n".join(lines)


def location_lookup(inventory):
    """{full path (case-insensitive): location id} for turning AI paths back into ids."""
    location_map = {loc["id"]: loc for loc in inventory.locations()}
    return {inventory.path_text(loc_id, location_map).casefold(): loc_id for loc_id in location_map}


def actions_to_batch(inventory, actions):
    """Turn approved AI actions into inventory.apply_batch arguments. Returns (item_changes, new_locations, deletions, problems)."""
    paths = location_lookup(inventory)
    item_changes, new_locations, deletions, problems = [], [], [], []
    for index, action in enumerate(actions):
        kind = action.get("type")
        try:
            if kind == "create_location":
                parent = str(action.get("parent") or "").strip()
                parent_id = paths.get(parent.casefold()) if parent else None
                if parent and not parent_id:
                    raise ValueError(f"unknown parent location “{parent}”")
                ref = f"new{index}"
                new_locations.append({"name": action.get("name"), "kind": action.get("kind"), "parent_id": parent_id, "ref": ref})
                path = ((parent + " → ") if parent else "") + str(action.get("name", "")).strip()
                paths[path.casefold()] = "@" + ref
            elif kind in ("update_item", "create_item"):
                fields = dict(action.get("changes") if kind == "update_item" else action.get("item") or {})
                if kind == "update_item":
                    inventory.get_item(action.get("item_id"))
                location = fields.pop("location", None)
                if location:
                    target = paths.get(str(location).strip().casefold())
                    if not target:
                        raise ValueError(f"unknown location “{location}”")
                    if target.startswith("@"):
                        fields["location_ref"] = target[1:]
                    else:
                        fields["location_id"] = target
                elif kind == "create_item":
                    raise ValueError("a new item needs a location")
                for number in ("quantity", "low_stock"):
                    if number in fields and fields[number] is not None:
                        fields[number] = str(fields[number])
                if kind == "create_item":
                    fields.setdefault("quantity", "1")
                    fields.setdefault("low_stock", "0")
                    fields.setdefault("value", 0)
                    fields.setdefault("tags", [])
                    fields.setdefault("notes", "")
                    fields.setdefault("photo", b"")
                    fields.setdefault("photo_name", "")
                allowed = {"name", "category", "quantity", "location_id", "location_ref", "tags", "notes", "low_stock", "value", "photo", "photo_name"}
                item_changes.append((action.get("item_id") if kind == "update_item" else None,
                                     {k: v for k, v in fields.items() if k in allowed}))
            elif kind == "delete_item":
                inventory.get_item(action.get("item_id"))
                deletions.append(action.get("item_id"))
        except Exception as error:
            problems.append(f"Skipped {describe_action(inventory, action)}: {error}")
    return item_changes, new_locations, deletions, problems


def describe_action(inventory, action):
    """One plain-English line for the approval list."""
    kind = action.get("type")
    def name(item_id):
        try:
            return f"“{inventory.get_item(item_id)['name']}”"
        except Exception:
            return "an unknown item"
    if kind == "create_location":
        parent = action.get("parent") or "top level"
        return f"Add location “{action.get('name')}” ({action.get('kind') or 'Storage'}) inside {parent}"
    if kind == "create_item":
        item = action.get("item") or {}
        return f"Add item “{item.get('name')}” × {item.get('quantity', 1)} in {item.get('location', '?')}"
    if kind == "delete_item":
        return f"Delete {name(action.get('item_id'))}"
    if kind == "update_item":
        changes = action.get("changes") or {}
        parts = [f"{key} → {', '.join(value) if isinstance(value, list) else value}" for key, value in changes.items()]
        return f"Change {name(action.get('item_id'))}: " + "; ".join(parts)
    return str(action)

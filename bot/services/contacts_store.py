"""Contact store service - persistent contacts via Notion with in-memory cache."""
from __future__ import annotations
import time
import logging
from typing import Optional
import httpx
import config

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 300  # 5 minutes


class ContactsStore:
    """Persistent contact storage backed by Notion, with in-memory cache."""

    def __init__(self):
        self._cache: dict[str, dict] = {}
        self._cache_timestamp: float = 0
        self._seed_loaded: bool = False

    def get_all(self) -> dict[str, dict]:
        """Return all contacts as {name_lower: {name, email, phone, source}}."""
        self._ensure_cache()
        return dict(self._cache)

    def get_by_name(self, name: str) -> Optional[dict]:
        """Look up a single contact by name (case-insensitive)."""
        self._ensure_cache()
        return self._cache.get(name.strip().lower())

    def add_or_update_contact(self, name: str, email: str = "", phone: str = "", source: str = "manual") -> bool:
        """Add a new contact or update an existing one. Returns True on success."""
        self._ensure_cache()
        name_lower = name.strip().lower()
        existing = self._cache.get(name_lower)

        if existing:
            updates = {}
            if email and not existing.get("email"):
                updates["email"] = email
            if phone and not existing.get("phone"):
                updates["phone"] = phone

            if not updates:
                return True  # Already has this info

            if config.NOTION_CONTACTS_DB_ID and existing.get("page_id"):
                try:
                    self._update_notion_page(existing["page_id"], **updates)
                except Exception as e:
                    logger.warning(f"Failed to update contact in Notion: {e}")
                    return False

            self._cache[name_lower].update(updates)
            return True
        else:
            contact = {"name": name.strip().title(), "email": email, "phone": phone, "source": source}

            if config.NOTION_CONTACTS_DB_ID:
                try:
                    page = self._create_notion_page(contact["name"], email, phone, source)
                    contact["page_id"] = page["id"]
                except Exception as e:
                    logger.warning(f"Failed to create contact in Notion: {e}")
                    return False

            self._cache[name_lower] = contact
            return True

    def format_for_prompt(self) -> str:
        """Format all contacts as a string for the AI system prompt."""
        contacts = self.get_all()
        if not contacts:
            return "No saved contacts yet."
        lines = []
        for key, c in contacts.items():
            parts = [c.get("name", key)]
            if c.get("email"):
                parts.append(c["email"])
            if c.get("phone"):
                parts.append(c["phone"])
            lines.append(": ".join(parts))
        return ", ".join(lines)

    # -- Internal methods --

    def _ensure_cache(self):
        now = time.time()
        if config.NOTION_CONTACTS_DB_ID:
            if now - self._cache_timestamp > CACHE_TTL_SECONDS:
                self._refresh_from_notion()
                if not self._seed_loaded:
                    self._load_seed_contacts()
                    self._seed_loaded = True
                self._cache_timestamp = now
        else:
            if not self._seed_loaded:
                self._load_seed_contacts()
                self._seed_loaded = True

    def _load_seed_contacts(self):
        """Merge config.CONTACTS (from env var) into cache."""
        seed = getattr(config, 'CONTACTS', {})
        for name_lower, value in seed.items():
            if name_lower in self._cache:
                continue

            email = ""
            phone = ""
            if "@" in value:
                email = value
            elif value.startswith("+") or value.replace("-", "").replace(" ", "").isdigit():
                phone = value
            else:
                email = value

            contact = {"name": name_lower.title(), "email": email, "phone": phone, "source": "manual"}
            self._cache[name_lower] = contact

            if config.NOTION_CONTACTS_DB_ID:
                try:
                    page = self._create_notion_page(contact["name"], email, phone, "manual")
                    self._cache[name_lower]["page_id"] = page["id"]
                except Exception:
                    pass

    def _refresh_from_notion(self):
        """Query the Notion contacts database and repopulate cache."""
        try:
            headers = {
                'Authorization': f'Bearer {config.NOTION_TOKEN}',
                'Notion-Version': '2022-06-28',
                'Content-Type': 'application/json'
            }
            resp = httpx.post(
                f'https://api.notion.com/v1/databases/{config.NOTION_CONTACTS_DB_ID}/query',
                headers=headers,
                json={}
            )
            if resp.status_code != 200:
                logger.warning(f"Contacts DB query failed: {resp.status_code}")
                return

            new_cache = {}
            for page in resp.json().get("results", []):
                if page.get("archived", False):
                    continue
                props = page.get("properties", {})

                name_data = props.get("Name", {}).get("title", [])
                name = name_data[0]["text"]["content"] if name_data else None
                if not name:
                    continue

                email = props.get("Email", {}).get("email", "") or ""
                phone = props.get("Phone", {}).get("phone_number", "") or ""
                source_data = props.get("Source", {}).get("select")
                source = source_data.get("name") if source_data else "manual"

                new_cache[name.strip().lower()] = {
                    "name": name.strip(),
                    "email": email,
                    "phone": phone,
                    "source": source,
                    "page_id": page["id"]
                }

            self._cache = new_cache
        except Exception as e:
            logger.warning(f"Failed to refresh contacts from Notion: {type(e).__name__}")

    def _create_notion_page(self, name, email, phone, source):
        from notion_client import Client
        client = Client(auth=config.NOTION_TOKEN)
        properties = {
            "Name": {"title": [{"text": {"content": name}}]}
        }
        if email:
            properties["Email"] = {"email": email}
        if phone:
            properties["Phone"] = {"phone_number": phone}
        if source:
            properties["Source"] = {"select": {"name": source}}

        return client.pages.create(
            parent={"database_id": config.NOTION_CONTACTS_DB_ID},
            properties=properties
        )

    def _update_notion_page(self, page_id, email=None, phone=None):
        from notion_client import Client
        client = Client(auth=config.NOTION_TOKEN)
        properties = {}
        if email is not None:
            properties["Email"] = {"email": email}
        if phone is not None:
            properties["Phone"] = {"phone_number": phone}
        if properties:
            client.pages.update(page_id=page_id, properties=properties)


contacts_store = ContactsStore()

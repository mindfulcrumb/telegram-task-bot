"""Script to create the Contacts database in Notion."""
import os
import sys
from notion_client import Client
from dotenv import load_dotenv

load_dotenv()

NOTION_TOKEN = os.getenv("NOTION_TOKEN")


def create_contacts_database(parent_page_id: str):
    """Create the Contacts database with required properties."""

    client = Client(auth=NOTION_TOKEN)

    print("Creating Contacts database...")

    # Create database with just the title property first
    database = client.databases.create(
        parent={"type": "page_id", "page_id": parent_page_id},
        title=[{"type": "text", "text": {"content": "Contacts"}}],
        properties={
            "Name": {"title": {}}
        }
    )

    database_id = database["id"]

    # Add remaining properties via update (create sometimes drops them)
    print("Adding Email, Phone, Source properties...")
    import httpx
    headers = {
        'Authorization': f'Bearer {NOTION_TOKEN}',
        'Notion-Version': '2022-06-28',
        'Content-Type': 'application/json'
    }
    httpx.patch(
        f'https://api.notion.com/v1/databases/{database_id}',
        headers=headers,
        json={
            'properties': {
                'Email': {'email': {}},
                'Phone': {'phone_number': {}},
                'Source': {'select': {'options': [
                    {'name': 'manual', 'color': 'gray'},
                    {'name': 'auto_email', 'color': 'blue'},
                    {'name': 'auto_whatsapp', 'color': 'green'}
                ]}}
            }
        }
    )
    print(f"\nContacts database created!")
    print(f"\nYour Contacts Database ID: {database_id}")
    print(f"\nAdd this to your .env and Railway:")
    print(f"NOTION_CONTACTS_DB_ID={database_id}")

    return database_id


def extract_page_id(url_or_id: str) -> str:
    """Extract page ID from Notion URL or return as-is if already an ID."""
    if "notion.so" in url_or_id or "notion.site" in url_or_id:
        parts = url_or_id.rstrip("/").split("-")
        if len(parts) > 1:
            page_id = parts[-1].split("?")[0]
        else:
            page_id = url_or_id.split("/")[-1].split("?")[0]

        if len(page_id) == 32:
            page_id = f"{page_id[:8]}-{page_id[8:12]}-{page_id[12:16]}-{page_id[16:20]}-{page_id[20:]}"

        return page_id

    return url_or_id


if __name__ == "__main__":
    if not NOTION_TOKEN:
        print("Error: NOTION_TOKEN not set in .env file")
        sys.exit(1)

    if len(sys.argv) < 2:
        print("Usage: python setup_contacts.py <page_url_or_id>")
        print("\nExample:")
        print("  python setup_contacts.py https://www.notion.so/My-Page-abc123def456")
        print("  python setup_contacts.py abc123def456")
        sys.exit(1)

    page_input = sys.argv[1]
    page_id = extract_page_id(page_input)

    print(f"Using parent page ID: {page_id}")

    try:
        db_id = create_contacts_database(page_id)
    except Exception as e:
        print(f"\nError: {e}")
        print("\nMake sure:")
        print("1. The page exists in Notion")
        print("2. You've shared the page with your integration")
        print("3. Your NOTION_TOKEN is correct")
        sys.exit(1)

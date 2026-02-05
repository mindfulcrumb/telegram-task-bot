"""Script to create the Tasks database in Notion."""
import os
import sys
from notion_client import Client
from dotenv import load_dotenv

load_dotenv()

NOTION_TOKEN = os.getenv("NOTION_TOKEN")

def create_tasks_database(parent_page_id: str):
    """Create the Tasks database with all required properties."""

    client = Client(auth=NOTION_TOKEN)

    print("Creating Tasks database...")

    database = client.databases.create(
        parent={"type": "page_id", "page_id": parent_page_id},
        title=[{"type": "text", "text": {"content": "Tasks"}}],
        properties={
            "Task": {
                "title": {}
            },
            "Status": {
                "select": {
                    "options": [
                        {"name": "To Do", "color": "gray"},
                        {"name": "In Progress", "color": "blue"},
                        {"name": "Done", "color": "green"}
                    ]
                }
            },
            "Category": {
                "select": {
                    "options": [
                        {"name": "Personal", "color": "purple"},
                        {"name": "Business", "color": "orange"}
                    ]
                }
            },
            "Priority": {
                "select": {
                    "options": [
                        {"name": "High", "color": "red"},
                        {"name": "Medium", "color": "yellow"},
                        {"name": "Low", "color": "gray"}
                    ]
                }
            },
            "Due Date": {
                "date": {}
            },
            "Reminder": {
                "date": {}
            }
        }
    )

    database_id = database["id"]
    print(f"\n✅ Database created successfully!")
    print(f"\n📋 Your Database ID: {database_id}")
    print(f"\nAdd this to your .env file:")
    print(f"NOTION_DATABASE_ID={database_id}")

    return database_id


def extract_page_id(url_or_id: str) -> str:
    """Extract page ID from Notion URL or return as-is if already an ID."""
    # If it's a URL, extract the ID
    if "notion.so" in url_or_id or "notion.site" in url_or_id:
        # URL format: https://www.notion.so/Page-Name-abc123def456
        # or: https://www.notion.so/workspace/abc123def456
        parts = url_or_id.rstrip("/").split("-")
        if len(parts) > 1:
            page_id = parts[-1].split("?")[0]
        else:
            page_id = url_or_id.split("/")[-1].split("?")[0]

        # Format as UUID if needed (add hyphens)
        if len(page_id) == 32:
            page_id = f"{page_id[:8]}-{page_id[8:12]}-{page_id[12:16]}-{page_id[16:20]}-{page_id[20:]}"

        return page_id

    return url_or_id


if __name__ == "__main__":
    if not NOTION_TOKEN:
        print("❌ Error: NOTION_TOKEN not set in .env file")
        sys.exit(1)

    if len(sys.argv) < 2:
        print("Usage: python setup_notion.py <page_url_or_id>")
        print("\nExample:")
        print("  python setup_notion.py https://www.notion.so/My-Page-abc123def456")
        print("  python setup_notion.py abc123def456")
        sys.exit(1)

    page_input = sys.argv[1]
    page_id = extract_page_id(page_input)

    print(f"Using parent page ID: {page_id}")

    try:
        db_id = create_tasks_database(page_id)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        print("\nMake sure:")
        print("1. The page exists in Notion")
        print("2. You've shared the page with your 'Task Bot' integration")
        print("3. Your NOTION_TOKEN is correct")
        sys.exit(1)

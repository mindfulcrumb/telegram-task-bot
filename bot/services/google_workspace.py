"""Google Workspace API wrappers — Gmail, Drive, Tasks, Docs.

Uses httpx (no google-api-python-client). Auth via google_auth.py.
"""
import base64
import logging
from email.mime.text import MIMEText

from bot.services.google_auth import get_access_token, _http

logger = logging.getLogger(__name__)

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
DRIVE_API = "https://www.googleapis.com/drive/v3"
TASKS_API = "https://tasks.googleapis.com/tasks/v1"
DOCS_API = "https://docs.googleapis.com/v1/documents"


# ---------------------------------------------------------------------------
# Gmail
# ---------------------------------------------------------------------------

def search_gmail(user_id: int, query: str, max_results: int = 5) -> list[dict]:
    """Search Gmail inbox. Returns list of message summaries."""
    token = get_access_token(user_id)
    if not token:
        return []

    headers = {"Authorization": f"Bearer {token}"}

    try:
        resp = _http.get(
            f"{GMAIL_API}/messages",
            params={"q": query, "maxResults": max_results},
            headers=headers,
        )
        if resp.status_code != 200:
            logger.error(f"Gmail search failed {resp.status_code}: {resp.text[:200]}")
            return []

        message_ids = [m["id"] for m in resp.json().get("messages", [])]
        if not message_ids:
            return []

        results = []
        for msg_id in message_ids:
            detail = _http.get(
                f"{GMAIL_API}/messages/{msg_id}",
                params={
                    "format": "metadata",
                    "metadataHeaders": "Subject,From,Date",
                },
                headers=headers,
            )
            if detail.status_code != 200:
                continue
            msg = detail.json()
            hdr_list = msg.get("payload", {}).get("headers", [])
            hdr = {h["name"]: h["value"] for h in hdr_list}
            results.append({
                "id": msg_id,
                "subject": hdr.get("Subject", "(no subject)"),
                "from": hdr.get("From", ""),
                "date": hdr.get("Date", ""),
                "snippet": msg.get("snippet", ""),
                "labels": msg.get("labelIds", []),
            })
        return results
    except Exception as e:
        logger.error(f"Gmail search error: {type(e).__name__}: {e}")
        return []


def send_email(user_id: int, to: str, subject: str, body: str) -> bool:
    """Send an email via Gmail API. Returns True on success."""
    token = get_access_token(user_id)
    if not token:
        return False

    msg = MIMEText(body, "plain")
    msg["to"] = to
    msg["subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    try:
        resp = _http.post(
            f"{GMAIL_API}/messages/send",
            json={"raw": raw},
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code in (200, 201):
            return True
        logger.error(f"Gmail send failed {resp.status_code}: {resp.text[:200]}")
        return False
    except Exception as e:
        logger.error(f"Gmail send error: {type(e).__name__}: {e}")
        return False


def get_unread_count(user_id: int) -> int:
    """Get estimated unread email count."""
    token = get_access_token(user_id)
    if not token:
        return 0
    try:
        resp = _http.get(
            f"{GMAIL_API}/messages",
            params={"q": "is:unread category:primary", "maxResults": 1},
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code == 200:
            return resp.json().get("resultSizeEstimate", 0)
        return 0
    except Exception:
        return 0


def get_profile_history_id(user_id: int) -> int | None:
    """Get current Gmail history ID for change tracking."""
    token = get_access_token(user_id)
    if not token:
        return None
    try:
        resp = _http.get(
            f"{GMAIL_API}/profile",
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code == 200:
            return int(resp.json().get("historyId", 0))
        return None
    except Exception:
        return None


def get_history_changes(
    user_id: int, start_history_id: int, max_results: int = 10
) -> tuple[list[dict], int | None]:
    """Fetch new inbox messages since start_history_id.

    Returns (new_messages, new_history_id).
    """
    token = get_access_token(user_id)
    if not token:
        return [], None

    headers = {"Authorization": f"Bearer {token}"}

    try:
        resp = _http.get(
            f"{GMAIL_API}/history",
            params={
                "startHistoryId": start_history_id,
                "historyTypes": "messageAdded",
                "labelId": "INBOX",
                "maxResults": max_results,
            },
            headers=headers,
        )

        if resp.status_code == 404:
            # History ID too old — caller should re-sync
            return [], None

        if resp.status_code != 200:
            logger.error(f"Gmail history failed {resp.status_code}: {resp.text[:200]}")
            return [], None

        data = resp.json()
        new_history_id = int(data.get("historyId", start_history_id))

        seen_ids = set()
        new_messages = []
        for record in data.get("history", []):
            for added in record.get("messagesAdded", []):
                msg = added.get("message", {})
                msg_id = msg.get("id")
                labels = msg.get("labelIds", [])
                # Skip promotions, social, updates, forums
                skip_labels = {
                    "CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL",
                    "CATEGORY_UPDATES", "CATEGORY_FORUMS", "SPAM", "TRASH",
                }
                if skip_labels & set(labels):
                    continue
                if "INBOX" not in labels:
                    continue
                if msg_id and msg_id not in seen_ids:
                    seen_ids.add(msg_id)
                    # Fetch metadata
                    detail = _http.get(
                        f"{GMAIL_API}/messages/{msg_id}",
                        params={
                            "format": "metadata",
                            "metadataHeaders": "Subject,From",
                        },
                        headers=headers,
                    )
                    if detail.status_code == 200:
                        d = detail.json()
                        hdr_list = d.get("payload", {}).get("headers", [])
                        hdr = {h["name"]: h["value"] for h in hdr_list}
                        new_messages.append({
                            "id": msg_id,
                            "subject": hdr.get("Subject", "(no subject)"),
                            "from": hdr.get("From", ""),
                            "snippet": d.get("snippet", ""),
                        })

        return new_messages[:5], new_history_id

    except Exception as e:
        logger.error(f"Gmail history error: {type(e).__name__}: {e}")
        return [], None


# ---------------------------------------------------------------------------
# Google Drive
# ---------------------------------------------------------------------------

def search_drive(user_id: int, query: str, max_results: int = 5) -> list[dict]:
    """Search Google Drive files. Returns list of file dicts."""
    token = get_access_token(user_id)
    if not token:
        return []

    # Build Drive search query — wrap user query in name contains
    # unless user provided their own Drive query syntax
    if "contains" not in query and "mimeType" not in query and "=" not in query:
        drive_query = f"name contains '{query}' and trashed = false"
    else:
        drive_query = query

    try:
        resp = _http.get(
            f"{DRIVE_API}/files",
            params={
                "q": drive_query,
                "pageSize": max_results,
                "fields": "files(id,name,mimeType,webViewLink,modifiedTime)",
                "orderBy": "modifiedTime desc",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code != 200:
            logger.error(f"Drive search failed {resp.status_code}: {resp.text[:200]}")
            return []

        files = resp.json().get("files", [])
        return [
            {
                "id": f["id"],
                "name": f["name"],
                "type": f.get("mimeType", "").split(".")[-1] if "." in f.get("mimeType", "") else f.get("mimeType", ""),
                "link": f.get("webViewLink", ""),
                "modified": f.get("modifiedTime", ""),
            }
            for f in files
        ]
    except Exception as e:
        logger.error(f"Drive search error: {type(e).__name__}: {e}")
        return []


# ---------------------------------------------------------------------------
# Google Tasks
# ---------------------------------------------------------------------------

def list_google_tasks(
    user_id: int, show_completed: bool = False, tasklist_id: str = "@default"
) -> list[dict]:
    """List tasks from a Google Tasks list."""
    token = get_access_token(user_id)
    if not token:
        return []

    try:
        resp = _http.get(
            f"{TASKS_API}/lists/{tasklist_id}/tasks",
            params={
                "showCompleted": str(show_completed).lower(),
                "showHidden": "false",
                "maxResults": 20,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code != 200:
            logger.error(f"Tasks list failed {resp.status_code}: {resp.text[:200]}")
            return []

        items = resp.json().get("items", [])
        return [
            {
                "id": t["id"],
                "title": t.get("title", ""),
                "status": t.get("status", "needsAction"),
                "due": t.get("due", ""),
                "notes": t.get("notes", ""),
            }
            for t in items
            if t.get("title")  # skip empty tasks
        ]
    except Exception as e:
        logger.error(f"Tasks list error: {type(e).__name__}: {e}")
        return []


def add_google_task(
    user_id: int,
    title: str,
    due_date: str = None,
    notes: str = None,
    tasklist_id: str = "@default",
) -> dict | None:
    """Create a task in Google Tasks. due_date: RFC 3339 timestamp."""
    token = get_access_token(user_id)
    if not token:
        return None

    body = {"title": title}
    if due_date:
        body["due"] = due_date
    if notes:
        body["notes"] = notes

    try:
        resp = _http.post(
            f"{TASKS_API}/lists/{tasklist_id}/tasks",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code in (200, 201):
            return resp.json()
        logger.error(f"Task add failed {resp.status_code}: {resp.text[:200]}")
        return None
    except Exception as e:
        logger.error(f"Task add error: {type(e).__name__}: {e}")
        return None


def complete_google_task(
    user_id: int, task_id: str, tasklist_id: str = "@default"
) -> bool:
    """Mark a Google Task as completed."""
    token = get_access_token(user_id)
    if not token:
        return False

    try:
        resp = _http.patch(
            f"{TASKS_API}/lists/{tasklist_id}/tasks/{task_id}",
            json={"status": "completed"},
            headers={"Authorization": f"Bearer {token}"},
        )
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"Task complete error: {type(e).__name__}: {e}")
        return False


# ---------------------------------------------------------------------------
# Google Docs
# ---------------------------------------------------------------------------

def create_google_doc(
    user_id: int, title: str, content: str = None
) -> dict | None:
    """Create a new Google Doc. Returns dict with id, title, and link."""
    token = get_access_token(user_id)
    if not token:
        return None

    try:
        resp = _http.post(
            DOCS_API,
            json={"title": title},
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code not in (200, 201):
            logger.error(f"Doc create failed {resp.status_code}: {resp.text[:200]}")
            return None

        doc = resp.json()
        doc_id = doc["documentId"]

        if content:
            _http.post(
                f"{DOCS_API}/{doc_id}:batchUpdate",
                json={
                    "requests": [
                        {
                            "insertText": {
                                "location": {"index": 1},
                                "text": content,
                            }
                        }
                    ]
                },
                headers={"Authorization": f"Bearer {token}"},
            )

        return {
            "id": doc_id,
            "title": title,
            "link": f"https://docs.google.com/document/d/{doc_id}/edit",
        }
    except Exception as e:
        logger.error(f"Doc create error: {type(e).__name__}: {e}")
        return None

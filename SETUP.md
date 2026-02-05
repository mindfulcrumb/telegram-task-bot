# Setup Guide

Follow these steps to get your Task Bot running.

## Step 1: Create Telegram Bot (5 minutes)

1. Open Telegram and search for **@BotFather**
2. Send `/newbot`
3. Choose a name (e.g., "My Task Bot")
4. Choose a username (e.g., "mytaskbot_bot") - must end in "bot"
5. **Copy the API token** - you'll need this later

Also get your user ID:
1. Search for **@userinfobot** on Telegram
2. Send any message
3. **Copy your user ID** - this restricts the bot to only you

## Step 2: Create Notion Integration (10 minutes)

### Create the Integration:
1. Go to https://www.notion.so/my-integrations
2. Click **"+ New integration"**
3. Name it "Task Bot"
4. Select your workspace
5. Click **Submit**
6. **Copy the "Internal Integration Token"**

### Create the Tasks Database:
1. Create a new page in Notion
2. Add a **Database - Full page**
3. Set up these properties (exact names matter!):

| Property Name | Type | Options |
|--------------|------|---------|
| Task | Title | (default) |
| Status | Select | To Do, In Progress, Done |
| Category | Select | Personal, Business |
| Priority | Select | High, Medium, Low |
| Due Date | Date | - |
| Reminder | Date | - |

4. Click **Share** (top right) → **Invite**
5. Search for "Task Bot" (your integration) and add it
6. **Copy the database ID** from the URL:
   - URL looks like: `https://notion.so/yourworkspace/DATABASE_ID?v=...`
   - The DATABASE_ID is the long string before the `?`

## Step 3: Configure Environment Variables

Create a `.env` file in the project root:

```bash
cp .env.example .env
```

Edit `.env` with your values:

```
TELEGRAM_BOT_TOKEN=your_bot_token_from_botfather
NOTION_TOKEN=your_notion_integration_token
NOTION_DATABASE_ID=your_database_id
ALLOWED_USER_IDS=your_telegram_user_id
REMINDER_CHECK_INTERVAL=5
```

## Step 4: Test Locally

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the bot
python -m bot.main
```

Send a message to your bot on Telegram - it should create a task in Notion!

## Step 5: Deploy to Railway (Free)

1. Create account at https://railway.app
2. Click **"New Project"** → **"Deploy from GitHub repo"**
3. Connect your GitHub and select this repository
4. Go to **Variables** tab and add:
   - `TELEGRAM_BOT_TOKEN`
   - `NOTION_TOKEN`
   - `NOTION_DATABASE_ID`
   - `ALLOWED_USER_IDS`
   - `REMINDER_CHECK_INTERVAL`
5. Railway will auto-deploy!

### Alternative: Deploy to Render (Free)

1. Create account at https://render.com
2. Click **"New +"** → **"Background Worker"**
3. Connect your GitHub repo
4. Set:
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python -m bot.main`
5. Add environment variables in the **Environment** section
6. Click **Create Background Worker**

## Usage

Once running, send messages to your bot:

| Message | Result |
|---------|--------|
| `Buy groceries tomorrow` | Creates personal task due tomorrow |
| `Call client #business !high` | Creates high-priority business task |
| `/list` | Shows all pending tasks |
| `/list business` | Shows business tasks only |
| `/today` | Shows today's tasks |
| `/done 1` | Marks task #1 as complete |
| `/remind 2 30m` | Reminds about task #2 in 30 minutes |
| `/help` | Shows all commands |

## Troubleshooting

**Bot not responding:**
- Check that `TELEGRAM_BOT_TOKEN` is correct
- Make sure your user ID is in `ALLOWED_USER_IDS`

**Tasks not appearing in Notion:**
- Verify `NOTION_TOKEN` and `NOTION_DATABASE_ID`
- Make sure the integration is shared with your database
- Check that property names match exactly (Task, Status, Category, etc.)

**Reminders not working:**
- Ensure `ALLOWED_USER_IDS` is set
- Check `REMINDER_CHECK_INTERVAL` value

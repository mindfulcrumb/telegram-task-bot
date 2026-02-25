"""Telegram Payments — /upgrade, /terms, /support."""
import logging
import os
from telegram import Update, LabeledPrice, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ContextTypes

from bot.services import user_service

logger = logging.getLogger(__name__)

# Price in smallest currency unit (cents)
PRO_PRICE = int(os.getenv("PRO_PRICE_CENTS", "999"))  # $9.99 default
PRO_CURRENCY = os.getenv("PRO_CURRENCY", "USD")
STRIPE_PROVIDER_TOKEN = os.getenv("STRIPE_PROVIDER_TOKEN", "")
SUBSCRIBE_BASE_URL = os.getenv("SUBSCRIBE_URL", "https://meetzoe.app/subscribe")


def get_subscribe_keyboard(telegram_user_id: int) -> InlineKeyboardMarkup:
    """Build the subscribe inline keyboard with WebView button."""
    subscribe_url = f"{SUBSCRIBE_BASE_URL}?tgid={telegram_user_id}"
    if STRIPE_PROVIDER_TOKEN:
        return None  # Native Telegram payments handle this
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "Subscribe — $9.99/mo",
            web_app=WebAppInfo(url=subscribe_url),
        )
    ]])


async def cmd_upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a payment invoice for Pro upgrade."""
    from bot.handlers.onboarding import _ensure_user
    user = await _ensure_user(update, context)

    if user.get("tier") == "pro":
        await update.message.reply_text("You're already on Pro! Enjoy unlimited everything.")
        return

    if not STRIPE_PROVIDER_TOKEN:
        # Open subscribe page inside Telegram WebView
        tg_id = update.effective_user.id
        subscribe_url = f"{SUBSCRIBE_BASE_URL}?tgid={tg_id}"
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "Subscribe — $9.99/mo",
                web_app=WebAppInfo(url=subscribe_url),
            )
        ]])
        await update.message.reply_text(
            "Upgrade to Zoe Pro for unlimited access:\n\n"
            "- Unlimited AI conversations\n"
            "- AI fitness coaching & workout programming\n"
            "- Peptide protocol tracking & dose intelligence\n"
            "- Supplement stack management & adherence\n"
            "- Bloodwork intelligence & biomarker trends\n"
            "- WHOOP integration & recovery coaching\n"
            "- Personalized morning briefings\n"
            "- Smart reminders & weekly reports",
            reply_markup=keyboard,
        )
        return

    await context.bot.send_invoice(
        chat_id=update.effective_chat.id,
        title="Zoe Pro",
        description="AI fitness coaching, peptide tracking, WHOOP integration, bloodwork intelligence, unlimited tasks & conversations, morning briefings, and weekly reports.",
        payload=f"pro_upgrade_{user['id']}",
        provider_token=STRIPE_PROVIDER_TOKEN,
        currency=PRO_CURRENCY,
        prices=[LabeledPrice("Zoe Pro (monthly)", PRO_PRICE)],
    )


async def handle_pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Approve the pre-checkout query. Required by Telegram (10s timeout)."""
    query = update.pre_checkout_query
    # Validate the payload
    if query.invoice_payload.startswith("pro_upgrade_"):
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message="Invalid payment request.")


async def handle_successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle successful payment — upgrade user to Pro."""
    payment = update.message.successful_payment
    payload = payment.invoice_payload

    if payload.startswith("pro_upgrade_"):
        user_id = int(payload.replace("pro_upgrade_", ""))
        user_service.update_tier(user_id, "pro")

        # Store payment IDs for refund capability
        logger.info(
            f"Payment successful: user={user_id} "
            f"telegram_charge={payment.telegram_payment_charge_id} "
            f"provider_charge={payment.provider_payment_charge_id}"
        )

        # Refresh cached user
        user = user_service.get_user_by_id(user_id)
        if user:
            context.user_data["db_user"] = user

        await update.message.reply_text(
            "Welcome to Zoe Pro! Here's what you unlocked:\n\n"
            "- Unlimited tasks & AI conversations\n"
            "- AI fitness coaching & workout programming\n"
            "- Peptide protocol tracking & dose intelligence\n"
            "- Supplement stack management & adherence\n"
            "- Bloodwork intelligence & biomarker trends\n"
            "- WHOOP integration & recovery coaching\n"
            "- Personalized morning briefings\n"
            "- Smart reminders & weekly reports\n\n"
            "I'll start learning your patterns and coaching you proactively. "
            "Thanks for trusting me with your training and your protocols."
        )


async def cmd_terms(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show terms of service (required by Telegram for payments)."""
    await update.message.reply_text(
        "Zoe — Terms of Service\n\n"
        "By using Zoe, you agree to:\n"
        "- Your data is stored securely and used only to provide the service\n"
        "- Free tier has usage limits; Zoe Pro removes them\n"
        "- You can delete your account and all data anytime with /deleteaccount\n"
        "- Payments are processed securely through Stripe\n"
        "- Refunds available within 7 days of purchase via /support"
    )


async def cmd_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show support info (required by Telegram for payments)."""
    await update.message.reply_text(
        "Need help?\n\n"
        "- Type /help to see all commands\n"
        "- For billing issues or refunds, describe your issue here and we'll get back to you\n"
        "- To delete your account: /deleteaccount"
    )

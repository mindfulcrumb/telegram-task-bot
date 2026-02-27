"""Telegram Payments — /upgrade, /billing, /terms, /support."""
import logging
import os
from telegram import Update, LabeledPrice, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ContextTypes

from bot.services import user_service
from bot.utils import typing_pause

logger = logging.getLogger(__name__)

# Price in smallest currency unit (cents)
PRO_PRICE = int(os.getenv("PRO_PRICE_CENTS", "999"))  # $9.99 default
PRO_CURRENCY = os.getenv("PRO_CURRENCY", "USD")
STRIPE_PROVIDER_TOKEN = os.getenv("STRIPE_PROVIDER_TOKEN", "")
SUBSCRIBE_BASE_URL = os.getenv("SUBSCRIBE_URL", "https://meetzoe.app/subscribe")
BILLING_BASE_URL = os.getenv("BILLING_URL", "https://meetzoe.app/billing")


def get_subscribe_keyboard(telegram_user_id: int) -> InlineKeyboardMarkup:
    """Build the subscribe inline keyboard with WebView button."""
    subscribe_url = f"{SUBSCRIBE_BASE_URL}?tgid={telegram_user_id}"
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "Subscribe — $9.99/mo",
            web_app=WebAppInfo(url=subscribe_url),
        )
    ]])


def get_upgrade_keyboard(telegram_user_id: int) -> InlineKeyboardMarkup:
    """Build the upgrade inline keyboard with Subscribe + Not now buttons."""
    subscribe_url = f"{SUBSCRIBE_BASE_URL}?tgid={telegram_user_id}"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "Subscribe — $9.99/mo",
            web_app=WebAppInfo(url=subscribe_url),
        )],
        [InlineKeyboardButton("Not now", callback_data="upgrade:dismiss")],
    ])


async def cmd_upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a payment invoice for Pro upgrade."""
    from bot.handlers.onboarding import _ensure_user
    user = await _ensure_user(update, context)

    if user.get("tier") == "pro":
        await typing_pause(update.message.chat, 0.4)
        await update.message.reply_text("You're already on Pro. You've got unlimited everything.")
        return

    if not STRIPE_PROVIDER_TOKEN:
        # Open subscribe page inside Telegram WebView
        tg_id = update.effective_user.id
        keyboard = get_upgrade_keyboard(tg_id)
        await typing_pause(update.message.chat, 0.8)
        await update.message.reply_text(
            "Zoe Pro \u2014 $9.99/mo\n\n"
            "Unlimited conversations\n"
            "Unlimited tasks and reminders\n"
            "Morning briefings and weekly insights\n"
            "Fitness coaching and workout cards\n"
            "WHOOP integration and bloodwork tracking\n\n"
            "Cancel anytime.",
            reply_markup=keyboard,
        )
        return

    await context.bot.send_invoice(
        chat_id=update.effective_chat.id,
        title="Zoe Pro",
        description="Fitness coaching, peptide tracking, WHOOP integration, bloodwork intelligence, unlimited tasks & conversations, morning briefings, and weekly reports.",
        payload=f"pro_upgrade_{user['id']}",
        provider_token=STRIPE_PROVIDER_TOKEN,
        currency=PRO_CURRENCY,
        prices=[LabeledPrice("Zoe Pro (monthly)", PRO_PRICE)],
    )


async def callback_upgrade_dismiss(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle 'Not now' button on upgrade prompt — delete the message."""
    query = update.callback_query
    await query.answer()
    try:
        await query.message.delete()
    except Exception:
        pass  # Message may already be gone


async def cmd_billing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show subscription status and billing management."""
    from bot.handlers.onboarding import _ensure_user
    user = await _ensure_user(update, context)

    if user.get("tier") == "pro":
        tg_id = update.effective_user.id
        billing_url = f"{BILLING_BASE_URL}?tgid={tg_id}"
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "Manage Subscription",
                web_app=WebAppInfo(url=billing_url),
            )
        ]])
        await typing_pause(update.message.chat, 0.5)
        await update.message.reply_text(
            "You're on Zoe Pro ($9.99/mo).\n"
            "Tap below to manage your subscription.",
            reply_markup=keyboard,
        )
    else:
        await typing_pause(update.message.chat, 0.5)
        await update.message.reply_text(
            "You're on the free plan. /upgrade to unlock everything."
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

        await typing_pause(update.message.chat, 1.0)
        await update.message.reply_text(
            "Welcome to Pro ✨\n\n"
            "Everything's unlocked — unlimited conversations, fitness coaching, "
            "morning briefings, all of it.\n\n"
            "Send me anything to get started."
        )


async def cmd_terms(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show terms of service (required by Telegram for payments)."""
    await typing_pause(update.message.chat, 0.5)
    await update.message.reply_text(
        "Your data's stored securely and only used to run Zoe. "
        "Free tier has usage limits, Pro removes them. "
        "Delete your account and all data anytime with /deleteaccount.\n\n"
        "Payments go through Stripe. Refunds within 7 days via /support."
    )


async def cmd_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show support info (required by Telegram for payments)."""
    await typing_pause(update.message.chat, 0.5)
    await update.message.reply_text(
        "Hit /help to see all commands.\n\n"
        "For billing or refunds, just describe the issue here and I'll sort it out.\n\n"
        "Delete your account: /deleteaccount"
    )

"""Inline keyboards used by the Telegram bot.

Keyboards are kept in their own module so the formatting helpers in
:mod:`bot.service` stay free of aiogram types and can be reused by the web
layer and by tests.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

__all__ = ["webapp_keyboard"]


def webapp_keyboard(url: str) -> InlineKeyboardMarkup:
    """Build a one-button keyboard that opens the live mini-app.

    Args:
        url: Public HTTPS address of the mini-app.

    Returns:
        An inline keyboard with a single Web App button.
    """

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🏆 Open live bracket",
                    web_app=WebAppInfo(url=url),
                )
            ]
        ]
    )

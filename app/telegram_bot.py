from __future__ import annotations

import asyncio
import logging

import httpx

from app import bitrix
from app.config import settings
from app.repository import (
    get_lead,
    mark_work_started,
    pending_leads,
    set_lead_approved,
    set_lead_rejected,
)

logger = logging.getLogger(__name__)


def enabled() -> bool:
    return bool(settings.telegram_bot_token)


def _api_url(method: str) -> str:
    return f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}"


def _allowed(chat_id: int) -> bool:
    return not settings.telegram_admin_ids or chat_id in settings.telegram_admin_ids


async def send_message(chat_id: int, text: str, reply_markup: dict | None = None) -> None:
    if not enabled():
        return
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    async with httpx.AsyncClient(timeout=20) as client:
        await client.post(_api_url("sendMessage"), json=payload)


async def notify_admins_new_lead(lead_id: int) -> None:
    if not enabled() or not settings.telegram_admin_ids:
        return
    lead = get_lead(lead_id)
    if not lead:
        return
    text = (
        f"Новая реферальная заявка #{lead.id}\n"
        f"Имя: {lead.name}\n"
        f"Телефон: {lead.phone}\n"
        f"Уровень: {'повторная рекомендация клиента' if lead.parent_lead_id else 'прямая рекомендация агента'}"
    )
    markup = {
        "inline_keyboard": [
            [
                {"text": "Принять", "callback_data": f"approve:{lead.id}"},
                {"text": "Отклонить", "callback_data": f"reject:{lead.id}"},
            ]
        ]
    }
    for admin_id in settings.telegram_admin_ids:
        await send_message(admin_id, text, markup)


async def _answer_callback(callback_id: str, text: str) -> None:
    async with httpx.AsyncClient(timeout=20) as client:
        await client.post(
            _api_url("answerCallbackQuery"),
            json={"callback_query_id": callback_id, "text": text},
        )


async def _handle_approve(chat_id: int, lead_id: int, callback_id: str | None = None) -> None:
    lead = get_lead(lead_id)
    if not lead:
        await send_message(chat_id, "Заявка не найдена.")
        return
    if lead.status != "pending":
        await send_message(chat_id, f"Заявка #{lead.id} уже в статусе {lead.status}.")
        return
    comments = (
        "Заявка прошла Telegram-модерацию.\n"
        f"ID заявки в приложении: {lead.id}\n"
        f"Реферальный уровень: {'2' if lead.parent_lead_id else '1'}\n"
        f"Клиентская реферальная ссылка после начала работы: "
        f"{settings.public_base_url}/r/{lead.referral_code}"
    )
    try:
        bitrix_id = await bitrix.create_lead(name=lead.name, phone=lead.phone, comments=comments)
    except Exception as exc:
        logger.exception("Failed to send lead %s to Bitrix", lead.id)
        await send_message(chat_id, f"Bitrix не принял заявку #{lead.id}: {exc}")
        return
    set_lead_approved(lead.id, bitrix_id)
    text = (
        f"Заявка #{lead.id} принята и отправлена в Bitrix24: лид #{bitrix_id}.\n"
        "Когда клиент начнет работу, нажмите кнопку ниже."
    )
    markup = {"inline_keyboard": [[{"text": "Клиент начал работу", "callback_data": f"won:{lead.id}"}]]}
    await send_message(chat_id, text, markup)
    if callback_id:
        await _answer_callback(callback_id, "Отправлено в Bitrix24")


async def _handle_reject(chat_id: int, lead_id: int, callback_id: str | None = None) -> None:
    lead = set_lead_rejected(lead_id, "Отклонено в Telegram")
    if lead:
        await send_message(chat_id, f"Заявка #{lead.id} отклонена.")
    else:
        await send_message(chat_id, "Заявка не найдена или уже обработана.")
    if callback_id:
        await _answer_callback(callback_id, "Отклонено")


async def _handle_won(chat_id: int, lead_id: int, callback_id: str | None = None) -> None:
    payouts = mark_work_started(lead_id)
    lead = get_lead(lead_id)
    if not lead:
        await send_message(chat_id, "Заявка не найдена.")
        return
    if payouts:
        amount = sum(p.amount_rub for p in payouts)
        await send_message(
            chat_id,
            f"По заявке #{lead.id} начислено {amount:,} руб.".replace(",", " "),
        )
    else:
        await send_message(chat_id, f"По заявке #{lead.id} уже нет новых начислений.")
    if callback_id:
        await _answer_callback(callback_id, "Готово")


async def _handle_message(chat_id: int, text: str) -> None:
    if not _allowed(chat_id):
        await send_message(chat_id, "Нет доступа.")
        return
    parts = text.strip().split()
    command = parts[0].lower() if parts else ""
    if command in {"/start", "/pending"}:
        leads = pending_leads(10)
        if not leads:
            await send_message(chat_id, "Новых заявок нет.")
            return
        for lead in leads:
            markup = {
                "inline_keyboard": [
                    [
                        {"text": "Принять", "callback_data": f"approve:{lead.id}"},
                        {"text": "Отклонить", "callback_data": f"reject:{lead.id}"},
                    ]
                ]
            }
            await send_message(chat_id, f"#{lead.id}: {lead.name}, {lead.phone}", markup)
        return
    if command == "/approve" and len(parts) == 2 and parts[1].isdigit():
        await _handle_approve(chat_id, int(parts[1]))
        return
    if command == "/reject" and len(parts) == 2 and parts[1].isdigit():
        await _handle_reject(chat_id, int(parts[1]))
        return
    if command == "/won" and len(parts) == 2 and parts[1].isdigit():
        await _handle_won(chat_id, int(parts[1]))
        return
    await send_message(chat_id, "Команды: /pending, /approve ID, /reject ID, /won ID")


async def _handle_callback(callback: dict) -> None:
    chat_id = int(callback["message"]["chat"]["id"])
    callback_id = callback["id"]
    if not _allowed(chat_id):
        await _answer_callback(callback_id, "Нет доступа")
        return
    action, raw_id = callback.get("data", ":").split(":", 1)
    if not raw_id.isdigit():
        await _answer_callback(callback_id, "Некорректная заявка")
        return
    lead_id = int(raw_id)
    if action == "approve":
        await _handle_approve(chat_id, lead_id, callback_id)
    elif action == "reject":
        await _handle_reject(chat_id, lead_id, callback_id)
    elif action == "won":
        await _handle_won(chat_id, lead_id, callback_id)


async def run_polling(stop_event: asyncio.Event) -> None:
    if not enabled():
        logger.warning("Telegram bot is disabled: token is not configured")
        return
    offset = 0
    async with httpx.AsyncClient(timeout=35) as client:
        while not stop_event.is_set():
            try:
                response = await client.get(
                    _api_url("getUpdates"),
                    params={"timeout": 25, "offset": offset, "allowed_updates": ["message", "callback_query"]},
                )
                response.raise_for_status()
                updates = response.json().get("result", [])
                for update in updates:
                    offset = max(offset, int(update["update_id"]) + 1)
                    if "message" in update:
                        message = update["message"]
                        await _handle_message(int(message["chat"]["id"]), message.get("text", ""))
                    if "callback_query" in update:
                        await _handle_callback(update["callback_query"])
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Telegram polling failed")
                await asyncio.sleep(5)

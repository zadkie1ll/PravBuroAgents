from __future__ import annotations

import httpx

from app.config import settings


class BitrixError(RuntimeError):
    pass


async def create_lead(*, name: str, phone: str, comments: str) -> int:
    url = f"{settings.bitrix_webhook_url}/crm.lead.add.json"
    payload = {
        "fields": {
            "TITLE": f"Реферальная заявка: {name}",
            "NAME": name,
            "PHONE": [{"VALUE": phone, "VALUE_TYPE": "WORK"}],
            "COMMENTS": comments,
            "SOURCE_ID": "WEB",
            "SOURCE_DESCRIPTION": "Реферальное приложение PravBuroAgents",
        },
        "params": {"REGISTER_SONET_EVENT": "Y"},
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(url, json=payload)
    response.raise_for_status()
    data = response.json()
    if data.get("error"):
        raise BitrixError(f"{data['error']}: {data.get('error_description', '')}")
    return int(data["result"])

from __future__ import annotations

import asyncio
import base64
from io import BytesIO
from pathlib import Path

import qrcode
from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.email_delivery import send_registration_code
from app.repository import (
    confirm_registration,
    create_lead,
    create_pending_registration,
    dashboard_data,
    email_is_valid,
    get_agent,
    get_or_create_debug_agent,
    get_pending_registration,
    initialize_storage,
    leaderboard,
    normalize_email,
    normalize_phone,
    phone_is_valid,
    resolve_referral,
)
from app.telegram_bot import notify_admins_new_lead, run_polling

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="PravBuro Agents")
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    same_site="lax",
    https_only=settings.environment == "production",
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def current_agent(request: Request):
    return get_agent(request.session.get("agent_id"))


def require_agent(request: Request):
    agent = current_agent(request)
    if agent is None:
        return RedirectResponse("/login", status_code=303)
    return agent


def qr_data_uri(value: str) -> str:
    image = qrcode.make(value)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


@app.on_event("startup")
async def startup() -> None:
    initialize_storage()
    app.state.telegram_stop_event = asyncio.Event()
    app.state.telegram_task = asyncio.create_task(run_polling(app.state.telegram_stop_event))


@app.on_event("shutdown")
async def shutdown() -> None:
    stop_event = getattr(app.state, "telegram_stop_event", None)
    task = getattr(app.state, "telegram_task", None)
    if stop_event:
        stop_event.set()
    if task:
        task.cancel()


@app.get("/")
def index(request: Request):
    if current_agent(request):
        return RedirectResponse("/cabinet", status_code=303)
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/login")
def login_page(request: Request):
    if current_agent(request):
        return RedirectResponse("/cabinet", status_code=303)
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "error": None,
            "debug_login_enabled": settings.debug_login_enabled,
            "debug_agent_email": settings.debug_agent_email,
        },
    )


@app.post("/login")
def login(request: Request, email: str = Form(""), password: str = Form("")):
    from app.repository import authenticate_agent

    agent = authenticate_agent(email, password)
    if not agent:
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Неверная почта или пароль",
                "debug_login_enabled": settings.debug_login_enabled,
                "debug_agent_email": settings.debug_agent_email,
            },
            status_code=400,
        )
    request.session["agent_id"] = agent.id
    return RedirectResponse("/cabinet", status_code=303)


@app.post("/debug-login")
def debug_login(request: Request):
    if not settings.debug_login_enabled:
        return RedirectResponse("/login", status_code=303)
    agent = get_or_create_debug_agent()
    request.session["agent_id"] = agent.id
    return RedirectResponse("/cabinet", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/register")
def register_page(request: Request):
    if request.query_params.get("reset") == "1":
        request.session.pop("pending_registration_token", None)
    pending = get_pending_registration(request.session.get("pending_registration_token"))
    return templates.TemplateResponse(
        "register.html",
        {"request": request, "error": None, "pending_registration": pending, "email": ""},
    )


@app.post("/register")
def register(
    request: Request,
    name: str = Form(""),
    email: str = Form(""),
    password: str = Form(""),
    password_repeat: str = Form(""),
):
    email = normalize_email(email)
    if not name.strip():
        error = "Укажи имя"
    elif not email_is_valid(email):
        error = "Укажи корректную почту"
    elif len(password) < 6:
        error = "Пароль должен быть не короче 6 символов"
    elif password != password_repeat:
        error = "Пароли не совпадают"
    else:
        error = None
    if error:
        return templates.TemplateResponse(
            "register.html",
            {
                "request": request,
                "error": error,
                "pending_registration": None,
                "email": email,
                "name": name,
            },
            status_code=400,
        )
    try:
        token, code = create_pending_registration(name, email, password)
    except ValueError as exc:
        return templates.TemplateResponse(
            "register.html",
            {
                "request": request,
                "error": str(exc),
                "pending_registration": None,
                "email": email,
                "name": name,
            },
            status_code=400,
        )
    request.session["pending_registration_token"] = token
    send_registration_code(email, code)
    return RedirectResponse("/register", status_code=303)


@app.post("/register/confirm")
def register_confirm(request: Request, code: str = Form("")):
    agent = confirm_registration(request.session.get("pending_registration_token"), code)
    if not agent:
        pending = get_pending_registration(request.session.get("pending_registration_token"))
        return templates.TemplateResponse(
            "register.html",
            {
                "request": request,
                "error": "Неверный или просроченный код",
                "pending_registration": pending,
                "email": "",
            },
            status_code=400,
        )
    request.session.pop("pending_registration_token", None)
    request.session["agent_id"] = agent.id
    return RedirectResponse("/cabinet", status_code=303)


@app.get("/cabinet")
def cabinet(request: Request):
    agent = require_agent(request)
    if isinstance(agent, RedirectResponse):
        return agent
    data = dashboard_data(agent.id)
    referral_url = f"{settings.public_base_url}/r/{data['agent'].referral_code}"
    return templates.TemplateResponse(
        "cabinet.html",
        {
            "request": request,
            **data,
            "referral_url": referral_url,
            "qr_code": qr_data_uri(referral_url),
            "leaderboard": leaderboard(),
            "direct_reward": settings.direct_reward_rub,
            "second_reward": settings.second_level_reward_rub,
            "public_base_url": settings.public_base_url,
        },
    )


@app.get("/r/{code}")
def referral_form(request: Request, code: str):
    target = resolve_referral(code)
    return templates.TemplateResponse(
        "referral_form.html",
        {"request": request, "code": code, "target": target, "error": None},
        status_code=200 if target else 404,
    )


@app.post("/r/{code}")
async def referral_submit(
    request: Request,
    code: str,
    name: str = Form(""),
    phone: str = Form(""),
):
    target = resolve_referral(code)
    if not target:
        return templates.TemplateResponse(
            "referral_form.html",
            {"request": request, "code": code, "target": None, "error": "Ссылка не найдена"},
            status_code=404,
        )
    phone = normalize_phone(phone)
    if not name.strip() or not phone_is_valid(phone):
        return templates.TemplateResponse(
            "referral_form.html",
            {
                "request": request,
                "code": code,
                "target": target,
                "error": "Укажи имя и корректный телефон",
            },
            status_code=400,
        )
    lead = create_lead(target, name, phone)
    await notify_admins_new_lead(lead.id)
    return templates.TemplateResponse("referral_success.html", {"request": request})

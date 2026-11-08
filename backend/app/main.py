from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import secrets
import time
import uuid
from typing import Optional, Union

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pwdlib import PasswordHash

from .assistant.schemas import ChatRequest, ChatResponse
from .assistant.service import (
    AssistantUnavailable,
    InvalidAssistantTurn,
    InvalidDeliveryDraft,
    TurnPlanner,
    handle_chat,
)
from .config import Settings
from .database import Database
from .mailer import MailDeliveryError, Mailer
from .schemas import Credentials, ErrorResponse, RegisterRequest, ResendRequest, UserResponse, VerificationRequired, VerifyEmailRequest
from .verification import VerificationProblem, VerificationService
from backend.calculation_models import CalculationRequest, CalculationResponse, MAX_SHIPMENTS
from backend.transport_calculator import compare_transport, load_catalog


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def error(status, code, message, headers=None):
    return HTTPException(status, detail={"code": code, "message": message, "fields": []}, headers=headers)


def public_user(row):
    return {key: row[key] for key in ("id", "email", "name", "created_at", "email_verified_at")}


def require_user(request: Request):
    """Подключать через Depends к будущим личным расчётам."""
    settings = request.app.state.settings
    token = request.cookies.get(settings.cookie_name)
    if not token:
        raise error(401, "not_authenticated", "Сначала войдите в аккаунт")
    with request.app.state.database.connect() as connection:
        row = connection.execute(
            "SELECT u.* FROM users u JOIN sessions s ON s.user_id = u.id WHERE s.token_hash = ? AND s.expires_at > ? AND u.email_verified_at IS NOT NULL",
            (digest(token), int(time.time())),
        ).fetchone()
    if row is None:
        raise error(401, "not_authenticated", "Сессия недействительна или истекла. Войдите снова")
    return public_user(row)


def create_app(settings=None, *, assistant_planner: Optional[TurnPlanner] = None):
    settings = settings or Settings.from_env()
    database = Database(settings.database_path)

    @asynccontextmanager
    async def lifespan(app):
        database.initialize()
        app.state.password_hash = PasswordHash.recommended()
        # Для неизвестного email тоже проверяем хеш, чтобы не делать быстрый выход.
        app.state.dummy_hash = app.state.password_hash.hash(secrets.token_urlsafe(32))
        app.state.mailer = Mailer(settings)
        app.state.verification = VerificationService(database, settings, app.state.mailer)
        app.state.calculation_catalog = load_catalog(settings.calculation_catalog_path)
        yield

    app = FastAPI(
        title="А1 — API команды",
        version="0.3.0",
        description="Регистрация, сессии и расчёт перевозок. Браузер передаёт cookie с credentials: include. Демонстрационные тарифы синтетические.",
        lifespan=lifespan,
        responses={code: {"model": ErrorResponse} for code in (400, 401, 403, 409, 415, 422, 429, 503)},
    )
    app.state.settings = settings
    app.state.database = database

    @app.exception_handler(VerificationProblem)
    async def verification_error(request, exc):
        headers = {"Retry-After": str(exc.retry)} if exc.retry else None
        return JSONResponse({"error": {"code": exc.code, "message": exc.message, "fields": []}}, status_code=exc.status, headers=headers)

    @app.exception_handler(MailDeliveryError)
    async def delivery_error(request, exc):
        return JSONResponse({"error": {"code": "mail_unavailable", "message": "Не удалось отправить письмо. Попробуйте позже", "fields": []}}, status_code=503)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "http_error", "message": str(exc.detail), "fields": []}
        return JSONResponse({"error": detail}, status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Не возвращаем input и ctx: они могут содержать введённый пароль.
        fields = [{"field": ".".join(str(part) for part in item["loc"] if part != "body"), "message": item["msg"]} for item in exc.errors()]
        return JSONResponse({"error": {"code": "validation_error", "message": "Проверьте заполненные поля", "fields": fields}}, status_code=422)

    @app.middleware("http")
    async def browser_security(request, call_next):
        private_api = request.url.path.startswith(("/auth/", "/calculation", "/assistant/"))
        if private_api and request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if (origin is not None and origin not in settings.allowed_origins) or (origin is None and request.headers.get("sec-fetch-site") == "cross-site"):
                return JSONResponse(
                    {"error": {"code": "origin_not_allowed", "message": "Адрес сайта не разрешён", "fields": []}},
                    status_code=403, headers={"Cache-Control": "no-store"},
                )
            if request.url.path != "/auth/logout" and request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
                return JSONResponse(
                    {"error": {"code": "json_required", "message": "Передайте тело application/json", "fields": []}},
                    status_code=415, headers={"Cache-Control": "no-store"},
                )
        response = await call_next(request)
        if private_api:
            response.headers["Cache-Control"] = "no-store"
        return response

    # CORS снаружи проверки Origin: фронт получает JSON ошибки и при разрешённом Origin.
    app.add_middleware(
        CORSMiddleware, allow_origins=list(settings.allowed_origins), allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["Content-Type"],
        expose_headers=["Retry-After"],
    )

    def limit_attempts(request, action):
        ip = request.client.host if request.client else "unknown"
        limit = {"login": settings.login_limit, "register": settings.register_limit, "verify": settings.verify_limit, "resend": settings.resend_limit}[action]
        retry = database.consume_attempt(action + ":" + digest(ip), int(time.time()), settings.rate_window_seconds, limit)
        if retry:
            raise error(429, "too_many_attempts", "Слишком много попыток. Попробуйте позже", {"Retry-After": str(retry)})

    def new_session(connection, request, user_id):
        now = int(time.time())
        connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        old = request.cookies.get(settings.cookie_name)
        if old:
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (digest(old),))
        token = secrets.token_urlsafe(32)
        connection.execute("INSERT INTO sessions VALUES (?, ?, ?)", (digest(token), user_id, now + settings.session_ttl_seconds))
        return token

    def set_cookie(response, token):
        response.set_cookie(
            settings.cookie_name, token, max_age=settings.session_ttl_seconds,
            httponly=True, secure=settings.cookie_secure, samesite="lax", path="/",
        )

    @app.get("/health", tags=["Состояние"])
    def health():
        return {"status": "ok"}

    @app.post("/auth/register", response_model=VerificationRequired, status_code=202, tags=["Авторизация"])
    def register(payload: RegisterRequest, request: Request):
        limit_attempts(request, "register")
        password_hash = app.state.password_hash.hash(payload.password.get_secret_value())
        return app.state.verification.issue(str(payload.email), payload.name, password_hash)

    @app.post("/auth/resend-verification", response_model=VerificationRequired, status_code=202, tags=["Авторизация"])
    def resend_verification(payload: ResendRequest, request: Request):
        limit_attempts(request, "resend")
        return app.state.verification.resend(payload.verification_id)

    @app.post("/auth/verify-email", response_model=UserResponse, tags=["Авторизация"])
    def verify_email(payload: VerifyEmailRequest, request: Request, response: Response):
        limit_attempts(request, "verify")
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            pending, failure = app.state.verification.check(connection, payload.verification_id, payload.code)
            if failure is None:
                now = datetime.now(timezone.utc).isoformat()
                user_id = pending["existing_user_id"] or str(uuid.uuid4())
                if pending["existing_user_id"]:
                    connection.execute("UPDATE users SET email_verified_at = ? WHERE id = ?", (now, user_id))
                else:
                    connection.execute(
                        "INSERT INTO users (id, email, name, password_hash, created_at, email_verified_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (user_id, pending["email"], pending["name"], pending["password_hash"], now, now),
                    )
                user = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
                connection.execute("DELETE FROM pending_registrations WHERE verification_id = ?", (payload.verification_id,))
                token = new_session(connection, request, user_id)
        if failure:
            raise failure
        set_cookie(response, token)
        return {"user": public_user(user)}

    @app.post("/auth/login", response_model=Union[UserResponse, VerificationRequired], responses={202: {"model": VerificationRequired}}, tags=["Авторизация"])
    def login(payload: Credentials, request: Request, response: Response):
        limit_attempts(request, "login")
        with database.connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE email = ?", (str(payload.email),)).fetchone()
            pending = connection.execute("SELECT * FROM pending_registrations WHERE email = ?", (str(payload.email),)).fetchone()
        known = row if row is not None else pending
        stored_hash = known["password_hash"] if known else app.state.dummy_hash
        valid = app.state.password_hash.verify(payload.password.get_secret_value(), stored_hash)
        if known is None or not valid:
            raise error(401, "invalid_credentials", "Неверная почта или пароль")
        if row is None or row["email_verified_at"] is None:
            response.status_code = 202
            if pending is not None:
                return app.state.verification.info(pending)
            # Старые аккаунты проходят подтверждение после проверки своего пароля.
            return app.state.verification.issue(row["email"], row["name"], row["password_hash"], row["id"])
        with database.connect() as connection:
            token = new_session(connection, request, row["id"])
        set_cookie(response, token)
        return {"user": public_user(row)}

    @app.get("/auth/me", response_model=UserResponse, tags=["Авторизация"])
    def me(user=Depends(require_user)):
        return {"user": user}

    @app.post("/auth/logout", status_code=204, tags=["Авторизация"])
    def logout(request: Request):
        token = request.cookies.get(settings.cookie_name)
        if token:
            with database.connect() as connection:
                connection.execute("DELETE FROM sessions WHERE token_hash = ?", (digest(token),))
        response = Response(status_code=204)
        response.delete_cookie(settings.cookie_name, path="/", httponly=True, secure=settings.cookie_secure, samesite="lax")
        return response

    @app.get("/calculation-options", tags=["Расчёты"])
    def calculation_options(user=Depends(require_user)):
        catalog = app.state.calculation_catalog
        cities = sorted({leg[key] for scheme in ("spot_road", "contract_road") for leg in (catalog.get(scheme) or {}).get("legs", []) for key in ("origin", "destination")})
        return {
            "schema_version": "0.2", "dataset_id": catalog["id"], "data_status": catalog.get("status", "unspecified"),
            "cities": cities,
            "cargo_types": [{"id": key, "name": value.get("name", key)} for key, value in catalog["cargo_types"].items()],
            "schedule_modes": ["once", "regular", "explicit"], "frequency_units": ["day", "week", "month"],
            "capacity_bases": ["mass_only", "mass_and_volume"],
            "limits": {"shipments": MAX_SHIPMENTS, "route_points": 12, "cargoes": 20, "horizon_days": 3660},
            "sources": catalog["sources"],
            "note": "Наличие города в списке не гарантирует тариф на любое сочетание пунктов",
        }

    def calculate_for_user(payload: CalculationRequest, user_id: str):
        retry = database.consume_attempt("calculate:" + user_id, int(time.time()), 60, 30)
        if retry:
            raise error(429, "too_many_attempts", "Не более 30 расчётов в минуту", {"Retry-After": str(retry)})
        try:
            return compare_transport(payload, app.state.calculation_catalog)
        except ValueError as exc:
            raise error(422, "calculation_error", str(exc))

    @app.post("/calculations", response_model=CalculationResponse, tags=["Расчёты"])
    def calculate(payload: CalculationRequest, user=Depends(require_user)):
        return calculate_for_user(payload, user["id"])

    @app.post(
        "/assistant/chat", response_model=ChatResponse, tags=["Ассистент"],
        responses={502: {"model": ErrorResponse}},
    )
    def assistant_chat(payload: ChatRequest, user=Depends(require_user)):
        try:
            return handle_chat(
                payload,
                plan_turn=assistant_planner,
                dataset_id=app.state.calculation_catalog["id"],
                calculate=lambda request: calculate_for_user(request, user["id"]),
            )
        except AssistantUnavailable as exc:
            raise error(
                503, "assistant_unavailable", "Ассистент недоступен. Можно воспользоваться формой расчёта."
            ) from exc
        except InvalidAssistantTurn as exc:
            raise error(
                502, "assistant_invalid_response", "Ассистент вернул некорректный ответ. Попробуйте ещё раз."
            ) from exc
        except InvalidDeliveryDraft as exc:
            raise RequestValidationError(exc.fields) from exc

    return app


app = create_app()

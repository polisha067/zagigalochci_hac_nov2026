from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import secrets
import time
import uuid
from typing import Literal, Optional, Union
import json
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response, Query
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pwdlib import PasswordHash
from starlette.concurrency import run_in_threadpool

from .config import Settings
from .database import Database
from .mailer import MailDeliveryError, Mailer
from .password_reset import reset_password, send_reset_code
from .schemas import Credentials, EmailRequest, PasswordResetRequested, ResetPasswordRequest, ErrorResponse, RegisterRequest, ResendRequest, UserResponse, VerificationResponse, VerificationRequired, VerifyEmailRequest
from .verification import VerificationProblem, VerificationService
from backend.a1_forecast import Granularity, MAX_FORECAST_YEAR, MIN_FORECAST_YEAR, forecast_options, forecast_route
from backend.calculation_models import CalculationRequest, CalculationResponse, MAX_SHIPMENTS
from backend.transport_calculator import compare_transport, load_catalog
from backend.network.models import NetworkRequest, NetworkResponse
from backend.network.russia import RussiaRequest, RussiaResponse, load_russia
from backend.network.pipeline import PipelineRequest, PipelineResponse, calculate_pipeline
from backend.network.engine import calculate_network
from backend.network.graph import CATEGORIES, load_network
from backend.network.observations import coverage
from backend.network.asop import read_asop
from backend.network.chukotavia import ChukotaviaData, ChukotaviaRequest, ChukotaviaResponse, calculate_chukotavia


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def error(status, code, message, headers=None):
    return HTTPException(status, detail={"code": code, "message": message, "fields": []}, headers=headers)


def public_user(row):
    return {key: row[key] for key in ("id", "email", "name", "created_at", "email_verified_at")}


def optional_user(request: Request):
    settings = request.app.state.settings
    token = request.cookies.get(settings.cookie_name)
    if not token:
        return None
    with request.app.state.database.connect() as connection:
        row = connection.execute(
            "SELECT u.* FROM users u JOIN sessions s ON s.user_id = u.id WHERE s.token_hash = ? AND s.expires_at > ? AND u.email_verified_at IS NOT NULL",
            (digest(token), int(time.time())),
        ).fetchone()
    return public_user(row) if row is not None else None


def require_user(request: Request):
    user = optional_user(request)
    if user is None:
        raise error(401, "not_authenticated", "Войдите в аккаунт")
    return user


def create_app(settings=None):
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
        app.state.network = load_network(settings.network_catalog_path)
        app.state.chukotavia = ChukotaviaData()
        river_network = load_network(Path(__file__).resolve().parents[1]/"data_sources/river/network.json")
        app.state.networks = {river_network.catalog.id: river_network, app.state.network.catalog.id: app.state.network}
        yield

    app = FastAPI(
        title="А1 — API команды",
        version="0.5.0",
        description="Регистрация, сессии и расчёт перевозок. Браузер передаёт cookie с credentials: include. Учебная сеть использует синтетические тарифы; авиационный метод ЧукотАВИА — отдельный снимок опубликованных тарифов и расписаний без подтверждённого бронирования.",
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
        # root_path is added by the /api reverse proxy deployment.
        path = request.scope["path"]
        root_path = request.scope.get("root_path", "").rstrip("/")
        if root_path and path.startswith(root_path + "/"):
            path = path[len(root_path):]
        private_api = path.startswith(("/auth/", "/calculation", "/network/"))
        if private_api and request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if (origin is not None and origin not in settings.allowed_origins) or (origin is None and request.headers.get("sec-fetch-site") == "cross-site"):
                return JSONResponse({"error": {"code": "origin_not_allowed", "message": "Адрес сайта не разрешён", "fields": []}}, status_code=403)
            if path != "/auth/logout" and request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
                return JSONResponse({"error": {"code": "json_required", "message": "Передайте тело application/json", "fields": []}}, status_code=415)
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
        limit = {"login": settings.login_limit, "register": settings.register_limit, "verify": settings.verify_limit, "resend": settings.resend_limit, "password-reset-request": settings.resend_limit, "password-reset-confirm": settings.verify_limit}[action]
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

    @app.post("/auth/verify-email", response_model=VerificationResponse, tags=["Авторизация"])
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
        return {"user": public_user(user), "account_created": pending["existing_user_id"] is None}

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
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute("SELECT password_hash FROM users WHERE id = ?", (row["id"],)).fetchone()
            if current is None or current["password_hash"] != stored_hash:
                raise error(401, "invalid_credentials", "Неверная почта или пароль")
            token = new_session(connection, request, row["id"])
        set_cookie(response, token)
        return {"user": public_user(row)}

    @app.post("/auth/forgot-password", response_model=PasswordResetRequested, status_code=202, tags=["Авторизация"])
    def forgot_password(payload: EmailRequest, request: Request, background: BackgroundTasks):
        limit_attempts(request, "password-reset-request")
        email = str(payload.email)
        now = int(time.time())
        bucket = "password-reset:" + digest(email)
        for suffix, window, limit in (
            (":cooldown", settings.verification_cooldown_seconds, 1),
            (":hour", 3600, settings.verification_email_limit),
        ):
            retry = database.consume_attempt(bucket + suffix, now, window, limit)
            if retry:
                raise error(429, "too_many_attempts", "Подождите перед повторным запросом кода", {"Retry-After": str(retry)})
        verification_id = secrets.token_urlsafe(32)
        background.add_task(send_reset_code, app.state.verification, email, verification_id,
                            now + settings.verification_ttl_seconds)
        return {"verification_id": verification_id, "expires_in": settings.verification_ttl_seconds,
                "resend_after": settings.verification_cooldown_seconds}

    @app.post("/auth/reset-password", status_code=204, tags=["Авторизация"])
    def confirm_password_reset(payload: ResetPasswordRequest, request: Request):
        limit_attempts(request, "password-reset-confirm")
        reset_password(app.state.verification, app.state.password_hash, payload.verification_id,
                       payload.code, payload.new_password.get_secret_value())
        return Response(status_code=204)

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

    @app.get("/auth/history", tags=["История"])
    def history(user=Depends(require_user)):
        with database.connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM calculation_history WHERE user_id = ?", (user["id"],)
            ).fetchall()
        return {"entries": [json.loads(row["payload"]) for row in rows]}

    def store_snapshot(user_id, snapshot_id, payload):
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            exists = connection.execute(
                "SELECT 1 FROM calculation_history WHERE user_id = ? AND snapshot_id = ?",
                (user_id, snapshot_id),
            ).fetchone()
            if exists:
                return
            usage = connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(length(CAST(payload AS BLOB))), 0) "
                "FROM calculation_history WHERE user_id = ?", (user_id,),
            ).fetchone()
            if usage[0] >= 100 or usage[1] + len(payload) > 16_000_000:
                raise error(409, "history_full", "Достигнут лимит истории аккаунта")
            connection.execute(
                "INSERT INTO calculation_history VALUES (?, ?, ?)",
                (user_id, snapshot_id, payload.decode("utf-8")),
            )

    @app.post("/auth/history", status_code=204, tags=["История"])
    async def save_history(request: Request, user=Depends(require_user)):
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 2_000_000:
                raise error(413, "snapshot_too_large", "Расчёт слишком велик для истории аккаунта")
            body.extend(chunk)
        try:
            envelope = json.loads(body)
            if envelope["account_id"] != user["id"]:
                raise error(409, "history_account_changed", "Аккаунт изменился. Обновите страницу.")
            snapshot = envelope["snapshot"]
            snapshot_id = snapshot["id"]
            if str(uuid.UUID(snapshot_id)) != snapshot_id:
                raise ValueError()
            created_at = datetime.fromisoformat(snapshot["createdAt"].replace("Z", "+00:00"))
            if created_at.tzinfo is None or any(
                not isinstance(snapshot.get(key), dict) for key in ("result", "draft", "cargoNames")
            ):
                raise ValueError()
            payload = json.dumps(snapshot, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
            raise error(422, "invalid_snapshot", "Неверный формат записи истории")
        await run_in_threadpool(store_snapshot, user["id"], snapshot_id, payload)
        return Response(status_code=204)

    @app.get("/calculation-options", tags=["Расчёты"])
    def calculation_options():
        catalog = app.state.calculation_catalog
        cities = sorted({leg[key] for scheme in ("spot_road", "contract_road") for leg in (catalog.get(scheme) or {}).get("legs", []) for key in ("origin", "destination")})
        return {
            "schema_version": "0.2", "dataset_id": catalog["id"], "data_status": catalog.get("status", "unspecified"),
            "cities": cities,
            "cargo_types": [{"id": key, "name": value.get("name", key)} for key, value in catalog["cargo_types"].items()],
            "schedule_modes": ["once", "regular", "explicit"], "frequency_units": ["minute", "hour", "day", "week", "month"],
            "capacity_bases": ["mass_only", "mass_and_volume"],
            "limits": {"shipments": MAX_SHIPMENTS, "route_points": 12, "cargoes": 20, "horizon_days": 3660},
            "sources": catalog["sources"],
            "note": "Наличие города в списке не гарантирует тариф на любое сочетание пунктов",
            "network_engine": {"engine":"network","endpoint":"/network/calculations","options":"/network/options","categories":[{"id":key,"name":name} for key,name in CATEGORIES]},
        }

    def calculation_limit(request: Request, user):
        if user:
            bucket = "calculate:" + user["id"]
        else:
            host = request.client.host if request.client else "unknown"
            bucket = "calculate:guest:" + digest(host)
        retry = database.consume_attempt(bucket, int(time.time()), 60, 30)
        if retry:
            raise error(429, "too_many_attempts", "Не более 30 расчётов в минуту", {"Retry-After": str(retry)})

    def selected_network(network_id):
        graph = app.state.networks.get(network_id)
        if graph is None:
            raise error(422, "unknown_network", "Неизвестный network_id: выберите сеть в /network/options")
        return graph

    @app.post("/calculations", response_model=Union[NetworkResponse, CalculationResponse, ChukotaviaResponse, RussiaResponse, PipelineResponse], tags=["Расчёты"])
    def calculate(request: Request, payload: Union[NetworkRequest, CalculationRequest, ChukotaviaRequest, RussiaRequest, PipelineRequest], user=Depends(optional_user)):
        calculation_limit(request, user)
        try:
            if isinstance(payload, PipelineRequest):
                return calculate_pipeline(payload)
            if isinstance(payload, RussiaRequest):
                return load_russia().calculate(payload)
            if isinstance(payload, ChukotaviaRequest):
                return calculate_chukotavia(payload, app.state.chukotavia)
            return calculate_network(payload,selected_network(payload.network_id)) if isinstance(payload,NetworkRequest) else compare_transport(payload, app.state.calculation_catalog)
        except ValueError as exc:
            raise error(422, "calculation_error", str(exc))

    @app.post("/network/calculations",response_model=NetworkResponse,tags=["Сеть и цепочки"])
    def network_calculate(request: Request, payload: NetworkRequest,user=Depends(optional_user)):
        calculation_limit(request, user)
        try:
            return calculate_network(payload,selected_network(payload.network_id))
        except ValueError as exc:
            raise error(422,"calculation_error",str(exc))

    @app.get("/network/options",tags=["Сеть и цепочки"])
    def network_options(network_id: Optional[str] = None):
        graph=selected_network(network_id) if network_id else app.state.network
        return {"schema_version":"0.3","network_id":graph.catalog.id,"fingerprint":graph.fingerprint,"coverage":graph.catalog.coverage,
                "available_networks":[{"id":n.catalog.id,"coverage":n.catalog.coverage} for n in app.state.networks.values()],
                "categories":[{"id":key,"name":name,"composed":key=="multimodal"} for key,name in CATEGORIES],
                "hubs":[{"id":hub.id,"name":hub.name,"city":hub.city,"kind":hub.kind,"modes":hub.modes} for hub in graph.catalog.hubs],
                "services":[{"id":s.id,"from_hub":s.from_hub,"to_hub":s.to_hub,"mode":s.mode} for s in graph.catalog.services],
                "cargo_types":[{"id":key,"name":value.name,"aliases":value.aliases,"phase":value.phase,"packagings":value.packagings} for key,value in graph.catalog.cargo_types.items()],
                "cargo_input":{"mode":"free_text","name_field":"name","suggestions_optional":True,"handling_profiles":[{"id":p,"name":{"dry_food":"Сухой пищевой груз","dry_general":"Сухой неопасный груз","test_liquid":"Учебная техническая жидкость"}.get(p,p)} for p in sorted({c.handling_profile for c in graph.catalog.cargo_types.values()})],"packagings":sorted({p for c in graph.catalog.cargo_types.values() for p in c.packagings})},
                "schedule_options":{"modes":["once","regular","explicit"],"units":["minute","hour","day","week","month"],"every_supported":True,"explicit_dates_supported":True},
                "limits":{"shipments":64,"legs":12,"paths":32,"group_sizes":4,"search_expansions":5000},
                "settlement_planning":{"engine":"russia","options":"/network/russia/options","endpoint":"/network/russia/calculations","hubs":"/network/russia/hubs"},
                "aviation_provider":{"id":"chukotavia","options":"/network/aviation/options","endpoint":"/network/aviation/calculations","booking_confirmed":False,"excluded_providers":["aerogruz"]},
                "observations":coverage(settings.database_path.parent/"network-observations.sqlite3")}

    @app.get("/network/russia/options", tags=["Общая сеть России"])
    def russia_options(query: str = Query(default="", max_length=100), offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=250)):
        return load_russia().options(query, offset, limit)

    @app.get("/network/russia/hubs", tags=["Общая сеть России"])
    def russia_hubs(settlement: str = Query(max_length=100)):
        network=load_russia();identifier=network.resolve(settlement)
        if identifier is None:raise error(422,"unknown_settlement","Выберите населённый пункт из справочника")
        return {"settlement":network.settlements[identifier],"hubs":[h for h in network.hubs.values() if h['settlement_id']==identifier],"automatic_hub_selection":True}

    @app.get("/network/pipeline/options", tags=["Трубопровод"])
    def pipeline_options():
        return load_russia().pipeline

    @app.post("/network/pipeline/calculations", response_model=PipelineResponse, tags=["Трубопровод"])
    def pipeline_calculate(request: Request, payload: PipelineRequest, user=Depends(optional_user)):
        calculation_limit(request, user)
        try:
            return calculate_pipeline(payload)
        except ValueError as exc:
            raise error(422,"calculation_error",str(exc))

    @app.get("/network/forecast/options", tags=["Прогноз"])
    def get_forecast_options():
        return forecast_options()

    @app.get("/network/forecast", tags=["Прогноз"])
    def get_forecast(
        route_id: str = Query(min_length=1, max_length=100),
        start_year: int = Query(ge=MIN_FORECAST_YEAR, le=MAX_FORECAST_YEAR),
        end_year: int = Query(ge=MIN_FORECAST_YEAR, le=MAX_FORECAST_YEAR),
        granularity: Granularity = "year",
    ):
        try:
            return forecast_route(route_id, start_year, end_year, granularity)
        except ValueError as exc:
            if start_year <= end_year:
                raise
            raise error(422, "invalid_forecast_period", str(exc)) from exc
        except KeyError as exc:
            if exc.args != (route_id,):
                raise
            raise error(404, "forecast_route_not_found", "Для этого направления нет ряда объёмов") from exc

    @app.get("/network/russia/reference-ports", tags=["Общая сеть России"])
    def reference_ports():
        return {"ports":load_russia().reference_ports,"operational_services_inferred":False}

    @app.post("/network/russia/calculations", response_model=RussiaResponse, tags=["Общая сеть России"])
    def russia_calculate(request: Request, payload: RussiaRequest, user=Depends(optional_user)):
        calculation_limit(request, user)
        try:
            return load_russia().calculate(payload)
        except ValueError as exc:
            raise error(422,"calculation_error",str(exc))

    @app.get("/network/aviation/options", tags=["Авиация"])
    def aviation_options():
        return app.state.chukotavia.options()

    @app.post("/network/aviation/calculations", response_model=ChukotaviaResponse, tags=["Авиация"])
    def aviation_calculate(request: Request, payload: ChukotaviaRequest, user=Depends(optional_user)):
        calculation_limit(request, user)
        try:
            return calculate_chukotavia(payload, app.state.chukotavia)
        except ValueError as exc:
            raise error(422, "calculation_error", str(exc))

    @app.get("/network/statistics/asop", tags=["Сеть и цепочки"])
    def asop_statistics(
        metric: Optional[Literal["port_inbound", "port_outbound", "basin", "direction", "dry_cargo", "liquid_cargo"]] = None,
        year: Optional[int] = None,
        period_kind: Literal["year", "Q1"] = "year",
        source_file_id: Optional[str] = None,
        include_flagged: bool = False,
    ):
        return read_asop(settings.database_path.parent / "network-observations.sqlite3", metric, year, period_kind, source_file_id, include_flagged)

    @app.get("/network/sources",tags=["Сеть и цепочки"])
    def network_sources():
        return json.loads((Path(__file__).resolve().parents[1]/"data_sources/registry.json").read_text(encoding="utf-8"))

    return app


app = create_app()

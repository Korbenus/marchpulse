"""FastAPI application exposing MarchePulse REST API, webhooks, feeds, and landing page."""

from __future__ import annotations

import hmac
import logging

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from marchepulse.clients.boamp import BoampClient, BoampClientError
from marchepulse.clients.entreprises import EntreprisesClient, EntreprisesClientError
from marchepulse.config import Settings, configure_logging, load_settings
from marchepulse.db.database import Database
from marchepulse.jobs.run_pipeline import run_autonomous_pipeline
from marchepulse.models import (
    AlertSubscription,
    AlertSubscriptionCreate,
    ApiKeyCreateRequest,
    ApiKeyIssuedResponse,
    ApiKeyPublicInfo,
    BillingWebhookResult,
    BuyerEnrichment,
    NormalizedTender,
    PipelineStats,
    TenderListResponse,
    TrialSignupRequest,
)
from marchepulse.services.auth import AuthError, authenticate_caller, issue_api_key
from marchepulse.services.billing import (
    BillingSignatureError,
    process_lemonsqueezy_webhook,
    process_stripe_webhook,
)
from marchepulse.services.normalizer import normalize_boamp_record
from marchepulse.services.notifier import Notifier
from marchepulse.services.seo_pages import (
    build_sitemap_xml,
    render_landing_html,
    render_tender_detail_html,
    render_tenders_directory_html,
)

logger = logging.getLogger(__name__)


def _ensure_cache_warm(
    repo: Database, boamp: BoampClient, *, limit: int = 20, department: str | None = None
) -> None:
    """Populate the local/DB cache from BOAMP if no tenders are stored yet."""
    if repo.count_tenders() > 0:
        return
    try:
        raw_records, _ = boamp.fetch_recent_tenders(limit=limit, department=department)
        normalized = [
            normalize_boamp_record(r) for r in raw_records if r.get("idweb") or r.get("id")
        ]
        repo.upsert_tenders(normalized)
    except BoampClientError as exc:
        logger.warning("Initial cache warm-up failed: %s", exc)


def _resolve_tender(
    repo: Database,
    boamp: BoampClient,
    entreprises: EntreprisesClient,
    idweb: str,
) -> NormalizedTender:
    """Fetch a tender from cache or BOAMP and enrich its buyer if needed."""
    tender = repo.get_tender(idweb)
    if tender is None:
        try:
            raw = boamp.get_tender_by_idweb(idweb)
        except BoampClientError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if raw is None:
            raise HTTPException(status_code=404, detail=f"Tender '{idweb}' not found.")
        tender = normalize_boamp_record(raw)
        repo.upsert_tenders([tender])

    if tender.buyer is None and tender.buyer_name:
        cached = repo.get_cached_buyer(tender.buyer_name)
        if cached is None:
            try:
                cached = entreprises.lookup_buyer(tender.buyer_name)
                if cached is not None:
                    repo.upsert_buyer(cached)
            except EntreprisesClientError as exc:
                logger.warning("Buyer enrichment skipped for '%s': %s", tender.buyer_name, exc)
        tender.buyer = cached
    return tender


def create_app(
    *,
    settings: Settings | None = None,
    db: Database | None = None,
    boamp_client: BoampClient | None = None,
    entreprises_client: EntreprisesClient | None = None,
    notifier: Notifier | None = None,
) -> FastAPI:
    """Create and configure the MarchePulse FastAPI application."""
    cfg = settings or load_settings()
    configure_logging(cfg.log_level)

    repo = db or Database(cfg.db_path, database_url=cfg.database_url)
    boamp = boamp_client or BoampClient()
    entreprises = entreprises_client or EntreprisesClient()
    mail_webhook = notifier or Notifier(cfg)

    app = FastAPI(
        title="MarchePulse API",
        version="0.1.0",
        description=(
            "API REST B2B normalisee pour les appels d'offres publics francais (BOAMP) "
            "avec enrichissement acheteur (API Recherche d'entreprises) et alertes automatisees."
        ),
    )

    def require_caller(
        x_api_key: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        x_rapidapi_proxy_secret: str | None = Header(default=None),
        x_rapidapi_user: str | None = Header(default=None),
    ) -> ApiKeyPublicInfo:
        try:
            return authenticate_caller(
                db=repo,
                settings=cfg,
                x_api_key=x_api_key,
                authorization=authorization,
                x_rapidapi_proxy_secret=x_rapidapi_proxy_secret,
                x_rapidapi_user=x_rapidapi_user,
            )
        except AuthError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    def require_admin(
        x_admin_secret: str | None = Header(default=None),
    ) -> None:
        if not cfg.admin_secret or (cfg.admin_secret == "change-me-admin-secret" and cfg.env == "prod"):
            raise HTTPException(status_code=503, detail="Admin secret not configured.")
        if not x_admin_secret or not hmac.compare_digest(x_admin_secret, cfg.admin_secret):
            raise HTTPException(status_code=403, detail="Invalid admin secret.")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def landing_page() -> HTMLResponse:
        return HTMLResponse(
            render_landing_html(cfg, repo.count_tenders(), repo.list_active_departments())
        )

    @app.get("/marches", response_class=HTMLResponse, include_in_schema=False)
    def public_tenders_directory(
        department: str | None = Query(default=None),
    ) -> HTMLResponse:
        _ensure_cache_warm(repo, boamp, department=department)
        items, total = repo.query_tenders(
            department=department, only_open=True, limit=30, offset=0
        )
        return HTMLResponse(
            render_tenders_directory_html(
                items,
                total=total,
                department=department,
                active_departments=repo.list_active_departments(),
            )
        )

    @app.get("/marches/departement/{department}", response_class=HTMLResponse, include_in_schema=False)
    def public_department_directory(department: str) -> HTMLResponse:
        return public_tenders_directory(department=department)

    @app.get("/marches/{idweb}", response_class=HTMLResponse, include_in_schema=False)
    def public_tender_page(idweb: str) -> HTMLResponse:
        tender = _resolve_tender(repo, boamp, entreprises, idweb)
        return HTMLResponse(render_tender_detail_html(tender, cfg))

    @app.get("/sitemap.xml", include_in_schema=False)
    def sitemap_xml() -> Response:
        departments = repo.list_active_departments(limit=50)
        tenders, _ = repo.query_tenders(only_open=True, limit=50, offset=0)
        return Response(
            content=build_sitemap_xml(cfg.base_url, departments, tenders),
            media_type="application/xml",
        )

    @app.get("/robots.txt", response_class=PlainTextResponse, include_in_schema=False)
    def robots_txt() -> str:
        return f"User-agent: *\nAllow: /\nDisallow: /v1/admin/\nSitemap: {cfg.base_url}/sitemap.xml\n"

    @app.get("/health", tags=["System"])
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "backend": repo.backend,
            "cached_tenders": repo.count_tenders(),
        }

    @app.post("/v1/trial", response_model=ApiKeyIssuedResponse, status_code=201, tags=["Account"])
    def signup_free_trial(payload: TrialSignupRequest) -> ApiKeyIssuedResponse:
        clean_email = payload.email.strip().lower()
        if "@" not in clean_email or "." not in clean_email.split("@", 1)[-1]:
            raise HTTPException(status_code=422, detail="Adresse email invalide.")
        if repo.has_key_for_email(clean_email):
            raise HTTPException(
                status_code=409,
                detail="Un compte existe deja pour cet email. Passez a un plan superieur pour augmenter votre quota.",
            )
        raw_key, info = issue_api_key(
            repo,
            customer_email=clean_email,
            plan="free",
            provider="trial",
        )
        mail_webhook.send_onboarding_key_email(to_email=clean_email, api_key=raw_key, plan="free")
        return ApiKeyIssuedResponse(**info.model_dump(), api_key=raw_key)

    @app.get("/feed.xml", include_in_schema=False)
    def public_rss_feed() -> Response:
        rss_path = cfg.feed_dir / "feed.xml"
        if not rss_path.exists():
            return Response(
                content="<?xml version='1.0' encoding='utf-8'?><rss version='2.0'><channel><title>MarchePulse</title></channel></rss>",
                media_type="application/rss+xml",
            )
        return Response(content=rss_path.read_text(encoding="utf-8"), media_type="application/rss+xml")

    @app.get("/feed.json", include_in_schema=False)
    def public_json_feed() -> Response:
        json_path = cfg.feed_dir / "latest.json"
        if not json_path.exists():
            return JSONResponse({"count": 0, "items": []})
        return Response(content=json_path.read_text(encoding="utf-8"), media_type="application/json")

    @app.get("/v1/tenders", response_model=TenderListResponse, tags=["Tenders"])
    def list_tenders(
        _caller: ApiKeyPublicInfo = Depends(require_caller),
        q: str | None = Query(default=None, description="Keyword search on title, buyer, or descriptors"),
        department: str | None = Query(default=None, description="French department code, e.g. 75, 69, 35"),
        market_type: str | None = Query(default=None, description="SERVICES, FOURNITURES, TRAVAUX"),
        only_open: bool = Query(default=False, description="Only return tenders whose deadline has not passed"),
        live: bool = Query(default=False, description="Fetch fresh records directly from BOAMP when true"),
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> TenderListResponse:
        if live:
            try:
                raw_records, total_live = boamp.fetch_recent_tenders(
                    limit=limit,
                    offset=offset,
                    department=department,
                    query=q,
                )
                normalized = [normalize_boamp_record(r) for r in raw_records if r.get("idweb") or r.get("id")]
                repo.upsert_tenders(normalized)
                return TenderListResponse(
                    total=total_live,
                    limit=limit,
                    offset=offset,
                    items=normalized,
                )
            except BoampClientError as exc:
                logger.warning("Live BOAMP fetch failed: %s", exc)
                raise HTTPException(status_code=502, detail=str(exc)) from exc

        _ensure_cache_warm(repo, boamp, limit=limit, department=department)

        items, total = repo.query_tenders(
            q=q,
            department=department,
            market_type=market_type,
            only_open=only_open,
            limit=limit,
            offset=offset,
        )
        return TenderListResponse(total=total, limit=limit, offset=offset, items=items)

    @app.get("/v1/tenders/{idweb}", response_model=NormalizedTender, tags=["Tenders"])
    def get_tender_detail(
        idweb: str,
        _caller: ApiKeyPublicInfo = Depends(require_caller),
    ) -> NormalizedTender:
        return _resolve_tender(repo, boamp, entreprises, idweb)

    @app.get("/v1/buyers/search", response_model=BuyerEnrichment, tags=["Buyers"])
    def search_buyer(
        _caller: ApiKeyPublicInfo = Depends(require_caller),
        name: str = Query(min_length=2, max_length=200),
    ) -> BuyerEnrichment:
        cached = repo.get_cached_buyer(name.strip())
        if cached is not None:
            return cached
        try:
            buyer = entreprises.lookup_buyer(name)
        except EntreprisesClientError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if buyer is None:
            return BuyerEnrichment(query_name=name.strip())
        repo.upsert_buyer(buyer)
        return buyer

    @app.post("/v1/alerts", response_model=AlertSubscription, status_code=201, tags=["Alerts"])
    def create_alert(
        payload: AlertSubscriptionCreate,
        caller: ApiKeyPublicInfo = Depends(require_caller),
    ) -> AlertSubscription:
        if payload.channel == "webhook" and not payload.target.startswith(("https://", "http://")):
            raise HTTPException(status_code=422, detail="Webhook target must be an http(s) URL.")
        if payload.channel == "email" and "@" not in payload.target:
            raise HTTPException(status_code=422, detail="Email target must be a valid email address.")
        return repo.create_alert_subscription(caller.id, payload)

    @app.get("/v1/alerts", response_model=list[AlertSubscription], tags=["Alerts"])
    def list_my_alerts(
        caller: ApiKeyPublicInfo = Depends(require_caller),
    ) -> list[AlertSubscription]:
        return repo.list_alert_subscriptions(owner_key_id=caller.id)

    @app.delete("/v1/alerts/{subscription_id}", tags=["Alerts"])
    def delete_my_alert(
        subscription_id: int,
        caller: ApiKeyPublicInfo = Depends(require_caller),
    ) -> dict[str, bool]:
        deleted = repo.deactivate_alert_subscription(subscription_id, caller.id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Alert subscription not found.")
        return {"deleted": True}

    @app.get("/v1/account/usage", response_model=ApiKeyPublicInfo, tags=["Account"])
    def get_my_usage(
        caller: ApiKeyPublicInfo = Depends(require_caller),
    ) -> ApiKeyPublicInfo:
        return caller

    @app.post("/v1/webhooks/lemonsqueezy", response_model=BillingWebhookResult, tags=["Billing"])
    async def lemonsqueezy_webhook(
        request: Request,
        x_signature: str | None = Header(default=None),
    ) -> BillingWebhookResult:
        raw_body = await request.body()
        try:
            return process_lemonsqueezy_webhook(
                db=repo,
                settings=cfg,
                notifier=mail_webhook,
                raw_body=raw_body,
                signature_header=x_signature,
            )
        except BillingSignatureError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    @app.post("/v1/webhooks/stripe", response_model=BillingWebhookResult, tags=["Billing"])
    async def stripe_webhook(
        request: Request,
        stripe_signature: str | None = Header(default=None),
    ) -> BillingWebhookResult:
        raw_body = await request.body()
        try:
            return process_stripe_webhook(
                db=repo,
                settings=cfg,
                notifier=mail_webhook,
                raw_body=raw_body,
                signature_header=stripe_signature,
            )
        except BillingSignatureError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    @app.post(
        "/v1/admin/keys",
        response_model=ApiKeyIssuedResponse,
        dependencies=[Depends(require_admin)],
        tags=["Admin"],
    )
    def admin_issue_key(payload: ApiKeyCreateRequest) -> ApiKeyIssuedResponse:
        raw_key, info = issue_api_key(
            repo,
            customer_email=payload.customer_email,
            plan=payload.plan,
            provider=payload.provider,
        )
        return ApiKeyIssuedResponse(
            **info.model_dump(),
            api_key=raw_key,
        )

    @app.post(
        "/v1/admin/pipeline",
        response_model=PipelineStats,
        dependencies=[Depends(require_admin)],
        tags=["Admin"],
    )
    def admin_run_pipeline(dry_run: bool = False) -> PipelineStats:
        return run_autonomous_pipeline(
            db=repo,
            settings=cfg,
            boamp_client=boamp,
            entreprises_client=entreprises,
            notifier=mail_webhook,
            dry_run=dry_run,
        )

    return app


app = create_app()

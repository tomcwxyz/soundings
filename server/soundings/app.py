import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from soundings.adapters.charity_commission import CharityCommissionAdapter
from soundings.adapters.companies_house import CompaniesHouseAdapter
from soundings.adapters.dfe_explore.adapter import DfeExploreAdapter
from soundings.adapters.dwp_statxplore.adapter import DwpStatXploreAdapter
from soundings.adapters.find_that_charity import FindThatCharityAdapter
from soundings.adapters.foe_green_space import FoeGreenSpaceAdapter
from soundings.adapters.givefood.adapter import GiveFoodAdapter
from soundings.adapters.mhclg_imd2025.adapter import MhclgImd2019Adapter, MhclgImd2025Adapter
from soundings.adapters.ohid_fingertips.adapter import OhidFingertipsAdapter
from soundings.adapters.ons_aps.adapter import OnsApsAdapter
from soundings.adapters.ons_census2021.adapter import OnsCensus2021Adapter
from soundings.adapters.ons_mid_year_estimates.adapter import OnsMidYearEstimatesAdapter
from soundings.adapters.openaq.adapter import OpenAqAdapter
from soundings.adapters.openweather.adapter import OpenWeatherAdapter
from soundings.adapters.osm_overpass.adapter import OsmOverpassAdapter
from soundings.adapters.police_uk.adapter import PoliceUkAdapter
from soundings.adapters.postcodes_io.adapter import PostcodesIoAdapter
from soundings.adapters.sport_england import SportEnglandActiveLivesAdapter
from soundings.adapters.threesixtygiving import ThreeSixtyGivingAdapter
from soundings.alerts import send_alert
from soundings.ask.conversation_store import ConversationStore
from soundings.cache.answer_cache import AnswerCacheStore
from soundings.capture.middleware import CaptureMiddleware
from soundings.capture.rate_limit import FullConsentRateLimiter
from soundings.capture.raw_writer import RawRecordWriter
from soundings.capture.replay import replay_pending
from soundings.capture.sanitisation.build import build_default_pipeline
from soundings.capture.sanitisation.config import load_sanitisation_config
from soundings.capture.sanitiser_worker import SanitiserWorker
from soundings.catalogue.loader import load_catalogue_into_db
from soundings.core.config import get_settings
from soundings.db.engine import get_engine
from soundings.geography.service import GeographyService
from soundings.http.ask import router as ask_router
from soundings.http.capture import router as capture_router
from soundings.http.catalogue import router as catalogue_router
from soundings.http.corpus import router as corpus_router
from soundings.http.errors import install_error_envelope
from soundings.http.health import router as health_router
from soundings.http.place_geometry import router as place_geometry_router
from soundings.http.session import SessionMiddleware
from soundings.http.sources import router as sources_router
from soundings.http.tools import router as tools_router
from soundings.mcp.server import build_mcp_server
from soundings.orchestration.orchestrator import IndicatorOrchestrator
from soundings.orchestration.registry import AdapterRegistry

CATALOGUE_DIR = Path(__file__).resolve().parent.parent.parent / "catalogue"
POSTCODES_IO_TTL = timedelta(hours=720)


def build_adapter_registry(engine: object) -> AdapterRegistry:
    """Shared registry construction for the FastAPI lifespan and the
    standalone `pre_warmer` daemon. Both processes need the same adapter
    set; this keeps them in lock-step.
    """
    registry = AdapterRegistry(engine)  # type: ignore[arg-type]
    registry.register("ons.mid_year_estimates", OnsMidYearEstimatesAdapter)
    registry.register("ons.census2021", OnsCensus2021Adapter)
    registry.register("mhclg.imd2025", MhclgImd2025Adapter)
    registry.register("mhclg.imd2019", MhclgImd2019Adapter)
    registry.register("ohid.fingertips", OhidFingertipsAdapter)
    registry.register("dwp.statxplore", DwpStatXploreAdapter)
    registry.register("dfe.explore", DfeExploreAdapter)
    registry.register("police_uk", PoliceUkAdapter)
    registry.register("ons.aps", OnsApsAdapter)
    registry.register("openaq", OpenAqAdapter)
    registry.register("openweather", OpenWeatherAdapter)
    registry.register("osm_overpass", OsmOverpassAdapter)
    registry.register("givefood", GiveFoodAdapter)
    registry.register("charity_commission", CharityCommissionAdapter)
    registry.register("companies_house", CompaniesHouseAdapter)
    registry.register("foe.green_space", FoeGreenSpaceAdapter)
    registry.register("threesixtygiving", ThreeSixtyGivingAdapter)
    registry.register("find_that_charity", FindThatCharityAdapter)
    registry.register("sport_england", SportEnglandActiveLivesAdapter)
    return registry


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    engine = get_engine()
    await load_catalogue_into_db(
        engine,
        sources_path=CATALOGUE_DIR / "sources.yaml",
        indicators_path=CATALOGUE_DIR / "indicators.yaml",
    )

    registry = build_adapter_registry(engine)

    postcodes_io = PostcodesIoAdapter(engine, ttl=POSTCODES_IO_TTL)

    sanitisation_config = load_sanitisation_config()

    app.state.engine = engine
    app.state.adapter_registry = registry
    app.state.orchestrator = IndicatorOrchestrator(engine, registry)
    app.state.geography_service = GeographyService(engine, postcodes_io)
    app.state.raw_writer = RawRecordWriter(engine)
    app.state.answer_cache = AnswerCacheStore(engine)
    app.state.conversation_store = ConversationStore()
    app.state.rate_limiter = FullConsentRateLimiter(
        engine,
        threshold=sanitisation_config.asker_purpose.rate_limit.full_consent_per_session_per_hour,
    )

    # Full six-rule pipeline assembled by build_default_pipeline. Loads
    # LSOA/MSOA names from geography.place and spaCy en_core_web_sm.
    # data.organisation is empty in Phase 3; Phase 4 (Charity Commission)
    # populates it so StripSmallOrgNames starts catching real names.
    pipeline = await build_default_pipeline(engine, sanitisation_config)
    app.state.sanitiser_worker = SanitiserWorker(
        engine, pipeline, sanitisation_config, alert=send_alert
    )
    app.state.background_tasks = set[asyncio.Task[None]]()

    # Catch any sanitiser rows left at 'pending' from a previous crash.
    # Run as a background task so startup isn't blocked on a backlog.
    replay_task = asyncio.create_task(replay_pending(engine, app.state.sanitiser_worker))
    app.state.background_tasks.add(replay_task)
    replay_task.add_done_callback(app.state.background_tasks.discard)

    # MCP server uses the same tool handlers + app.state.
    mcp_server = build_mcp_server(state=app.state)
    app.mount("/mcp", mcp_server.sse_app())

    yield


app = FastAPI(title="Soundings", version="0.0.1", lifespan=lifespan)
# Order matters: CaptureMiddleware wraps the inner stack and reads
# `request.state.session` set by SessionMiddleware, so SessionMiddleware
# must run first on the way in. add_middleware prepends, so the LAST
# add_middleware call runs FIRST on requests.
app.add_middleware(CaptureMiddleware)
app.add_middleware(SessionMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().ui_origin],
    # Cookies (session + consent + sector) must round-trip on UI ↔ API
    # calls. allow_origins stays locked to SOUNDINGS_UI_ORIGIN.
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)
app.include_router(health_router)
app.include_router(tools_router)
app.include_router(ask_router)
app.include_router(sources_router)
app.include_router(catalogue_router)
app.include_router(capture_router)
app.include_router(corpus_router)
app.include_router(place_geometry_router)
install_error_envelope(app)

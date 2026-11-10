import argparse
import asyncio
import webbrowser
from collections import Counter
from datetime import date

import psycopg

from aml_investigator.cli.context import Commands, settings_for
from aml_investigator.core.settings import Settings
from aml_investigator.investigation.models import MODEL_NAMES


def run_mcp(args: argparse.Namespace) -> None:
    from aml_investigator.corpus.embeddings import query_embedder
    from aml_investigator.mcp_server.server import build_server

    settings = settings_for(args)
    build_server(
        settings.mcp_conninfo,
        reference_variant=args.reference_variant,
        embed_query=query_embedder(settings.azure_openai_endpoint),
    ).run(transport="http", host=args.host, port=args.port)


def run_api(args: argparse.Namespace) -> None:
    import uvicorn

    from aml_investigator.investigation.models import chat_model
    from aml_investigator.web.app import create_app

    settings = settings_for(args)
    app = create_app(
        settings, mcp=args.mcp_url, model=chat_model(args.model, settings.database, settings.azure_openai_endpoint)
    )
    uvicorn.run(app, host=args.host, port=args.port, log_config=None)


def run_ui(args: argparse.Namespace) -> None:
    import uvicorn

    from aml_investigator.core.migrate import ensure_database, migrate
    from aml_investigator.investigation.models.rule_score import RuleScoreChatModel
    from aml_investigator.mcp_server.server import build_server
    from aml_investigator.simulation.world import COUNT_PASSAGES_SQL, copy_corpus
    from aml_investigator.web.app import create_app

    base = settings_for(args)
    settings = base.with_database("aml_sim_ui")
    ensure_database(base.owner_conninfo, settings.database)
    migrate(settings.owner_conninfo, settings.role_dsns)
    with psycopg.connect(settings.owner_conninfo) as target:
        [(target_count,)] = target.execute(COUNT_PASSAGES_SQL).fetchall()
        if not target_count:
            for database in dict.fromkeys(("aml_sim_dev", base.database, "aml_hi_small")):
                if database == settings.database:
                    continue
                try:
                    with psycopg.connect(base.with_database(database).owner_conninfo) as source:
                        [(source_count,)] = source.execute(COUNT_PASSAGES_SQL).fetchall()
                        if source_count:
                            copy_corpus(source, target)
                            break
                except (psycopg.errors.InvalidCatalogName, psycopg.errors.UndefinedTable):
                    continue
    app = create_app(
        settings,
        mcp=build_server(settings.mcp_conninfo, customer_profile=True),
        model=RuleScoreChatModel(thresholds={}, close_below=0.0),
        ui_mode=True,
        evaluator=args.evaluator,
    )
    if not args.no_browser:
        webbrowser.open(f"http://127.0.0.1:{args.port}/world")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_config=None)


def run_investigate(args: argparse.Namespace) -> None:
    routes, statuses = asyncio.run(investigate_day(settings_for(args), args.model, args.day, args.limit))
    print(f"investigated: {routes.total()}")
    for route, count in sorted(routes.items()):
        print(f"{route}: {count}")
    for status, count in sorted(statuses.items()):
        print(f"verification {status}: {count}")


async def investigate_day(
    settings: Settings, model: str, day: date, limit: int | None
) -> tuple[Counter[str], Counter[str]]:
    from langchain.mcp import MCPAdapter

    from aml_investigator.core.tracing import tracing_callbacks
    from aml_investigator.corpus.embeddings import query_embedder
    from aml_investigator.investigation.models import chat_model
    from aml_investigator.investigation.service import open_investigation_service
    from aml_investigator.mcp_server.server import build_server

    routes: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    async with (
        MCPAdapter(
            build_server(settings.mcp_conninfo, embed_query=query_embedder(settings.azure_openai_endpoint))
        ) as mcp,
        open_investigation_service(
            app_conninfo=settings.app_conninfo,
            mcp=mcp,
            model=chat_model(model, settings.database, settings.azure_openai_endpoint),
            callbacks=tracing_callbacks(settings),
        ) as service,
    ):
        for alert_id in await service.uninvestigated_alerts(day, limit):
            outcome = await service.investigate(alert_id)
            routes[outcome.route] += 1
            statuses[outcome.verification_status] += 1
    return routes, statuses


def register(commands: Commands) -> None:
    mcp = commands.add_parser("mcp")
    mcp.add_argument("--host", default="127.0.0.1")
    mcp.add_argument("--port", type=int, default=8100)
    mcp.add_argument("--reference-variant", choices=["clean", "injected"], default="clean")
    mcp.set_defaults(handler=run_mcp)

    investigate = commands.add_parser("investigate")
    investigate.add_argument("--day", type=date.fromisoformat, required=True)
    investigate.add_argument("--limit", type=int)
    investigate.add_argument("--model", choices=MODEL_NAMES, default="rule-score")
    investigate.set_defaults(handler=run_investigate)

    api = commands.add_parser("api")
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8000)
    api.add_argument("--mcp-url", default="http://127.0.0.1:8100/mcp")
    api.add_argument("--model", choices=MODEL_NAMES, default="rule-score")
    api.set_defaults(handler=run_api)

    ui = commands.add_parser("ui")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--evaluator", action="store_true")
    ui.add_argument("--no-browser", action="store_true")
    ui.set_defaults(handler=run_ui)

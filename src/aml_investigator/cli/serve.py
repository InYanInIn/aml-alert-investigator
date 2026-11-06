import argparse
import asyncio
from collections import Counter
from datetime import date

from aml_investigator.cli.context import Commands, settings_for
from aml_investigator.core.settings import Settings
from aml_investigator.investigation.models import MODEL_NAMES


def run_mcp(args: argparse.Namespace) -> None:
    from aml_investigator.mcp_server.server import build_server

    build_server(settings_for(args).mcp_conninfo, reference_variant=args.reference_variant).run(
        transport="http", host=args.host, port=args.port
    )


def run_api(args: argparse.Namespace) -> None:
    import uvicorn

    from aml_investigator.investigation.models import chat_model
    from aml_investigator.web.app import create_app

    settings = settings_for(args)
    app = create_app(
        settings, mcp=args.mcp_url, model=chat_model(args.model, settings.database, settings.azure_openai_endpoint)
    )
    uvicorn.run(app, host=args.host, port=args.port, log_config=None)


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
    from aml_investigator.investigation.models import chat_model
    from aml_investigator.investigation.service import open_investigation_service
    from aml_investigator.mcp_server.server import build_server

    routes: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    async with (
        MCPAdapter(build_server(settings.mcp_conninfo)) as mcp,
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

import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import SecretStr

from aml_investigator.core.settings import Settings
from aml_investigator.simulation.world import SimulationConfig, load_config
from tests.conftest import Database

SEGMENT_NAMES = ("individual", "small_business", "corporation")
THRESHOLDS = {"R1_fan_in": 2.0, "R3_pass_through": 1500.0}


def settings_for(db: Database) -> Settings:
    return Settings(
        _env_file=None,
        owner_dsn=SecretStr(db.owner_dsn),
        mcp_dsn=SecretStr(db.mcp_dsn),
        app_dsn=SecretStr(db.app_dsn),
        evaluator_dsn=SecretStr(db.evaluator_dsn),
        azure_openai_endpoint=None,
    )


def prepare_workspace(root: Path, database: str, *, scenarios: list[dict[str, Any]] | None = None) -> None:
    write_config(root / "config" / "simulation", scenarios=scenarios)
    thresholds = root / "config" / "thresholds" / f"{database}.json"
    thresholds.parent.mkdir(parents=True, exist_ok=True)
    thresholds.write_text(json.dumps({"budget_per_day": 2.0, "suppression_days": 3, "thresholds": THRESHOLDS}))
    segments = root / "config" / "thresholds" / f"{database}.segments.json"
    segments.write_text(
        json.dumps({"budget_per_day": 2.0, "suppression_days": 3, "segments": dict.fromkeys(SEGMENT_NAMES, THRESHOLDS)})
    )
    b1_result = root / "eval" / "results" / database / "val" / "b1.json"
    b1_result.parent.mkdir(parents=True, exist_ok=True)
    point = {"threshold": 1.5, "close_rate": 0.5, "false_close_rate": 0.0}
    b1_result.write_text(json.dumps({"operating_point": point}))


PARENT_TUITION: dict[str, Any] = {
    "name": "parent_tuition",
    "rate_per_day": 0.2,
    "amount": {"median": 800.0, "sigma": 0.1},
    "legs": [
        {"from": "parent", "to": "self", "format": "ACH", "reference": "family_support"},
        {"from": "self", "to": "university", "format": "ACH", "reference": "tuition"},
    ],
}
CURRENCY_EXCHANGE: dict[str, Any] = {
    "name": "currency_exchange",
    "rate_per_day": 0.2,
    "amount": {"median": 120.0, "sigma": 0.2},
    "legs": [
        {
            "from": "self",
            "to": "self",
            "format": "ACH",
            "reference": "currency_exchange",
            "received_currency": "US Dollar",
        }
    ],
}
OWN_TRANSFER: dict[str, Any] = {
    "name": "own_transfer",
    "rate_per_day": 0.5,
    "amount": {"median": 300.0, "sigma": 0.2},
    "legs": [{"from": "self", "to": "sibling", "format": "ACH", "reference": "own_transfer"}],
}
SIDE_BUSINESS: dict[str, Any] = {
    "name": "side_business",
    "rate_per_day": 0.5,
    "amount": {"median": 900.0, "sigma": 0.2},
    "legs": [{"from": "client", "to": "self", "format": "Wire", "reference": "consulting_invoice"}],
}
PERSONAS: dict[str, Any] = {
    "fx_rates": {"Euro": 1.0, "US Dollar": 0.9},
    "externals": {
        "employer": {"count": 2, "entity_type": "Corporation"},
        "merchant": {"count": 3, "entity_type": "Corporation"},
        "university": {"count": 1, "entity_type": "Corporation"},
        "parent": {"count": 3, "entity_type": "Individual"},
        "client": {"count": 4, "entity_type": "Corporation"},
    },
    "personas": {
        "student": {
            "accounts": 12,
            "entity_types": ["Individual"],
            "accounts_per_customer": [1, 2],
            "opening_balance": {"median": 500.0, "sigma": 0.2},
            "declared_monthly": {"low": 500.0, "high": 900.0},
            "monthly": [
                {
                    "direction": "in",
                    "counterparty": "parent",
                    "day_of_month": 5,
                    "amount": {"median": 700.0, "sigma": 0.1},
                    "format": "ACH",
                    "reference": "family_support",
                }
            ],
            "recurring": [
                {
                    "direction": "out",
                    "counterparty": "merchant",
                    "rate_per_day": 1.0,
                    "amount": {"median": 20.0, "sigma": 0.4},
                    "format": "Credit Card",
                    "reference": "groceries",
                }
            ],
            "peers": {
                "rate_per_day": 0.1,
                "amount": {"median": 30.0, "sigma": 0.3},
                "format": "ACH",
                "reference": "peer_transfer",
            },
            "confounders": [PARENT_TUITION, CURRENCY_EXCHANGE],
            "mismatches": [SIDE_BUSINESS],
        },
        "corporation": {
            "accounts": 8,
            "entity_types": ["Corporation"],
            "accounts_per_customer": [3, 4],
            "opening_balance": {"median": 20000.0, "sigma": 0.2},
            "declared_monthly": {"low": 50000.0, "high": 90000.0},
            "monthly": [
                {
                    "direction": "out",
                    "counterparty": "employer",
                    "day_of_month": 25,
                    "amount": {"median": 3000.0, "sigma": 0.1},
                    "format": "ACH",
                    "reference": "payroll",
                }
            ],
            "recurring": [
                {
                    "direction": "in",
                    "counterparty": "client",
                    "rate_per_day": 2.0,
                    "amount": {"median": 800.0, "sigma": 0.5},
                    "format": "Wire",
                    "reference": "consulting_invoice",
                }
            ],
            "peers": None,
            "confounders": [OWN_TRANSFER],
            "mismatches": [],
        },
    },
}
REFERENCE_KEYS = (
    "family_support",
    "groceries",
    "peer_transfer",
    "tuition",
    "currency_exchange",
    "consulting_invoice",
    "payroll",
    "own_transfer",
)
TEXT_POOLS: dict[str, Any] = {
    "references": {key: [f"{key} {{month}}", f"{key} no {{number}}"] for key in REFERENCE_KEYS},
    "profiles": {
        persona: {
            "occupation": [f"{persona} occupation"],
            "business_description": [f"{persona} description"],
            "account_purpose": [f"{persona} purpose"],
            "expected_activity": [f"{persona} activity"],
        }
        for persona in ("student", "corporation")
    },
    "company_names": ["Alder Works", "Birch Trading", "Cedar Holdings"],
}
SCENARIO: dict[str, Any] = {
    "id": "mules",
    "typology": "money_mule",
    "start_day": 1,
    "rounds": 3,
    "round_gap": 3,
    "roles": [
        {"name": "victims", "kind": "source", "entity_type": "Corporation", "count": 3},
        {"name": "mule", "kind": "member", "persona": "student", "count": 2, "reserves": 1},
        {"name": "cash_out", "kind": "sink", "entity_type": "Individual", "count": 1},
    ],
    "steps": [
        {
            "from": "victims",
            "to": "mule",
            "amount": {"low": 2000.0, "high": 3000.0},
            "count": 4,
            "format": "ACH",
            "reference": "consulting_invoice",
        },
        {
            "from": "mule",
            "to": "cash_out",
            "share": 0.9,
            "count": 2,
            "delay": 1,
            "format": "Wire",
            "reference": "family_support",
        },
    ],
}


def write_config(
    directory: Path, *, scenarios: list[dict[str, Any]] | None = None, mismatch_share: float = 0.25
) -> SimulationConfig:
    directory.mkdir(parents=True, exist_ok=True)
    scenario_file = {
        "honest_profile_mismatch_share": mismatch_share,
        "scenarios": [SCENARIO] if scenarios is None else scenarios,
    }
    (directory / "personas.yaml").write_text(yaml.safe_dump(PERSONAS))
    (directory / "scenarios.yaml").write_text(yaml.safe_dump(scenario_file))
    (directory / "text_pools.yaml").write_text(yaml.safe_dump(TEXT_POOLS))
    return load_config(directory)

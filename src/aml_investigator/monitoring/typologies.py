from typing import Literal

Typology = Literal[
    "fan_out",
    "fan_in",
    "gather_scatter",
    "scatter_gather",
    "cycle",
    "random",
    "bipartite",
    "stack",
    "structuring",
    "profile_deviation",
]

RULE_TYPOLOGIES: dict[str, list[Typology]] = {
    "R1": ["fan_in", "gather_scatter"],
    "R2": ["fan_out", "scatter_gather"],
    "R3": ["bipartite", "stack", "scatter_gather"],
    "R4": ["cycle", "random"],
    "R5": ["structuring"],
    "R6": ["profile_deviation"],
}

import polars as pl

EURO = "Euro"


def pair_rates(transactions: pl.DataFrame) -> dict[tuple[str, str], float]:
    amounts = transactions.select(
        "payment_currency",
        "receiving_currency",
        pl.col("amount_paid").cast(pl.Float64),
        pl.col("amount_received").cast(pl.Float64),
    )
    medians = (
        amounts.filter(
            (pl.col("payment_currency") != pl.col("receiving_currency"))
            & (pl.col("amount_paid") > 0)
            & (pl.col("amount_received") > 0)
        )
        .group_by("payment_currency", "receiving_currency")
        .agg((pl.col("amount_received") / pl.col("amount_paid")).median().alias("rate"))
    )
    return {(paid, received): rate for paid, received, rate in medians.iter_rows()}


def used_currencies(transactions: pl.DataFrame) -> set[str]:
    return set(transactions["payment_currency"]) | set(transactions["receiving_currency"])


def eur_per_unit(rates: dict[tuple[str, str], float], required: set[str]) -> pl.DataFrame:
    edges = {(received, paid): 1 / rate for (paid, received), rate in rates.items()} | rates
    eur = {EURO: 1.0}
    frontier = [EURO]
    while frontier:
        reached = [
            (received, eur[paid] / rate)
            for (paid, received), rate in sorted(edges.items())
            if paid in frontier and received not in eur
        ]
        for currency, value in reached:
            eur.setdefault(currency, value)
        frontier = [currency for currency, _ in reached]
    unreachable = required - eur.keys()
    if unreachable:
        raise ValueError(f"no exchange-rate path to {EURO} for: {', '.join(sorted(unreachable))}")
    return pl.DataFrame(sorted(eur.items()), schema={"currency": pl.String, "eur_per_unit": pl.Float64}, orient="row")


def round_trip_error(rates: dict[tuple[str, str], float]) -> float:
    return max(
        (
            abs(rate * rates[(received, paid)] - 1)
            for (paid, received), rate in rates.items()
            if (received, paid) in rates
        ),
        default=0.0,
    )

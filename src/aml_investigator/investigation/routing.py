import hashlib
from datetime import date
from typing import Literal

Route = Literal["auto_closed", "qa_sample", "escalated"]

QA_SEED = 20261102
QA_RATE = 0.03
AUTO_CLOSE_ANALYST = "auto-close"


def route_for(
    recommendation: Literal["close", "escalate"],
    verification_status: Literal["passed", "failed"],
    account_id: str,
    alert_date: date,
) -> Route:
    if recommendation != "close" or verification_status != "passed":
        return "escalated"
    return "qa_sample" if qa_sampled(account_id, alert_date) else "auto_closed"


def qa_sampled(account_id: str, alert_date: date) -> bool:
    digest = hashlib.sha256(f"{QA_SEED}:{account_id}:{alert_date.isoformat()}".encode()).hexdigest()
    return int(digest, 16) / 2**256 < QA_RATE

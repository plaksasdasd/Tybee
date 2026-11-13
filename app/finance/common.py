from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation

MAX_KOP = 10_000_000 * 100


class FinanceError(Exception):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def rub_to_kop(value: Decimal | int | str) -> int:
    """Rubles -> kopecks; refuses more than two decimals."""
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        raise FinanceError("Некорректная сумма", 422) from None
    kop = d * 100
    if not d.is_finite() or kop != kop.to_integral_value():
        raise FinanceError("Сумма — не более двух знаков после запятой", 422)
    return int(kop)


def day_range(start: date | None, end: date | None) -> tuple[datetime | None, datetime | None]:
    """Inclusive date range -> half-open UTC datetime range."""
    lo = datetime.combine(start, time.min, tzinfo=timezone.utc) if start else None
    hi = datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc) if end else None
    return lo, hi

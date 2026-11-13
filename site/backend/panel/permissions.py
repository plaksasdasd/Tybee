"""Role -> permission matrix. Hard-coded on purpose (see the stage 2 plan)."""
from __future__ import annotations

from enum import Enum

from app.db.models import Staff, StaffRole


class Perm(str, Enum):
    COVERS_MANAGE = "covers.manage"
    STAFF_VIEW = "staff.view"
    STAFF_MANAGE = "staff.manage"
    AUDIT_VIEW = "audit.view"
    TEMPLATES_MANAGE = "support.templates.manage"
    SUPPORT_CHAT = "support.chat"
    SUPPORT_VIEW_ALL = "support.view_all"
    SUPPORT_ASSIGN = "support.assign"
    ORDERS_INVOICE = "orders.invoice"
    ORDERS_OPERATIONS_VIEW = "orders.operations.view"
    REFUNDS_MANAGE = "refunds.manage"
    BUYOUT_MANAGE = "buyout.manage"
    FINANCE_BONUSES = "finance.bonuses"
    FINANCE_PAYOUTS_APPROVE = "finance.payouts.approve"
    FINANCE_PAYOUTS_PAY = "finance.payouts.pay"
    FINANCE_ANALYTICS = "finance.analytics"
    AVITO_MANAGE = "avito.manage"
    CATALOG_MANAGE = "catalog.manage"
    BROADCAST_SEND = "broadcast.send"


R = StaffRole
ROLE_PERMS: dict[StaffRole, frozenset[Perm]] = {
    R.ADMIN: frozenset(Perm),
    R.SUPPORT_LEAD: frozenset(
        {Perm.TEMPLATES_MANAGE, Perm.STAFF_VIEW, Perm.STAFF_MANAGE, Perm.AUDIT_VIEW, Perm.SUPPORT_CHAT,
         Perm.SUPPORT_VIEW_ALL, Perm.SUPPORT_ASSIGN, Perm.FINANCE_BONUSES, Perm.FINANCE_PAYOUTS_PAY}
    ),
    R.FINANCE_MANAGER: frozenset(
        {Perm.FINANCE_PAYOUTS_APPROVE, Perm.FINANCE_PAYOUTS_PAY, Perm.FINANCE_ANALYTICS, Perm.ORDERS_OPERATIONS_VIEW}
    ),
    R.ARBITRATION_AGENT: frozenset(
        {Perm.SUPPORT_CHAT, Perm.SUPPORT_VIEW_ALL, Perm.ORDERS_INVOICE, Perm.ORDERS_OPERATIONS_VIEW, Perm.REFUNDS_MANAGE}
    ),
    R.BUYOUT_AGENT: frozenset({Perm.SUPPORT_CHAT, Perm.SUPPORT_VIEW_ALL, Perm.BUYOUT_MANAGE}),
    R.CLIENT_MANAGER_1: frozenset({Perm.SUPPORT_CHAT}),
    R.CLIENT_MANAGER_2: frozenset({Perm.SUPPORT_CHAT, Perm.ORDERS_INVOICE, Perm.ORDERS_OPERATIONS_VIEW}),
}

ROLE_LABELS: dict[StaffRole, str] = {
    R.ADMIN: "Администратор",
    R.SUPPORT_LEAD: "Руководитель поддержки",
    R.FINANCE_MANAGER: "Финансовый менеджер",
    R.ARBITRATION_AGENT: "Агент арбитража",
    R.BUYOUT_AGENT: "Агент выкупа",
    R.CLIENT_MANAGER_1: "Менеджер клиентов 1",
    R.CLIENT_MANAGER_2: "Менеджер клиентов 2",
}

MANAGEABLE_BY: dict[StaffRole, frozenset[StaffRole]] = {
    R.ADMIN: frozenset(StaffRole),
    R.SUPPORT_LEAD: frozenset(
        {R.ARBITRATION_AGENT, R.BUYOUT_AGENT, R.CLIENT_MANAGER_1, R.CLIENT_MANAGER_2}
    ),
}


def _role(value: str | StaffRole) -> StaffRole | None:
    try:
        return StaffRole(value)
    except ValueError:
        return None


def perms_of(staff: Staff) -> frozenset[Perm]:
    role = _role(staff.role)
    return ROLE_PERMS.get(role, frozenset()) if role else frozenset()


def manageable_roles(staff: Staff) -> frozenset[StaffRole]:
    role = _role(staff.role)
    if role is None or Perm.STAFF_MANAGE not in perms_of(staff):
        return frozenset()
    return MANAGEABLE_BY.get(role, frozenset())


def can_manage(actor: Staff, target_role_now: str | None, target_role_new: str | None = None) -> bool:
    """May ``actor`` touch a member currently in ``target_role_now`` and move them to ``target_role_new``?"""
    allowed = manageable_roles(actor)
    for value in (target_role_now, target_role_new):
        if value is None:
            continue
        role = _role(value)
        if role is None or role not in allowed:
            return False
    return True

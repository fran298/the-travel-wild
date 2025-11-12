# directory/services/publication.py
from dataclasses import dataclass
from typing import Optional
from django.db import connection

@dataclass
class EffectivePlan:
    school_id: str
    plan: Optional[str]        # 'basic'|'medium'|'premium'|None
    plan_rank: int             # 0..3
    status: Optional[str]      # subscription_status o None

@dataclass
class PublicationStatus:
    school_id: str
    is_publishable: bool
    reason: str
    plan: Optional[str]
    plan_rank: int
    status: str
    is_verified: bool

def get_effective_plan(school_id) -> EffectivePlan:
    sql = """
        SELECT school_id, plan, plan_rank, subscription_status
        FROM school_effective_plan
        WHERE school_id = %s
        LIMIT 1
    """
    with connection.cursor() as cur:
        cur.execute(sql, [str(school_id)])
        row = cur.fetchone()
    if not row:
        return EffectivePlan(str(school_id), None, 0, None)
    return EffectivePlan(
        school_id=str(row[0]),
        plan=row[1],
        plan_rank=row[2],
        status=row[3],
    )

def get_publication_status(school_id) -> PublicationStatus:
    sql = """
        SELECT school_id, is_publishable, reason, plan, plan_rank, status, is_verified
        FROM school_publication_status
        WHERE school_id = %s
        LIMIT 1
    """
    with connection.cursor() as cur:
        cur.execute(sql, [str(school_id)])
        row = cur.fetchone()
    if not row:
        # Si no hay fila, por defecto no publicable
        return PublicationStatus(str(school_id), False, "not found", None, 0, "draft", False)
    return PublicationStatus(
        school_id=str(row[0]),
        is_publishable=bool(row[1]),
        reason=row[2],
        plan=row[3],
        plan_rank=row[4],
        status=row[5],
        is_verified=bool(row[6]),
    )
"""Packs: everything a company differs on, as declarative data.

    base pack        the autonomy ceiling and the shared taxonomy  (code-adjacent)
    jurisdiction     optional statutory overlay                    (empty in v1)
    tenant pack      one company                                   (per customer)
    segment overlay  a population inside one company               (entity, type)

A tenant may only ever TIGHTEN what the base pack allows. The loader proves it.
"""

from .models import BasePack, Manifest, ResolvedConfig, Rule, TenantPack  # noqa: F401
from .loader import load_base, load_tenant, resolve, tenant_ids  # noqa: F401
from .validate import Problem, validate  # noqa: F401

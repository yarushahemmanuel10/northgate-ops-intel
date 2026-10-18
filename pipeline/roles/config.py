"""Demo roles from config/roles.yaml (step 6.3, D-044). Not authentication: a role only narrows what is shown."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import yaml

from pipeline.settings import ROOT

ROLES_FILE = ROOT / "config" / "roles.yaml"
PAGES = ["overview", "service", "customers", "data_quality", "alerts"]
ALERT_RULES = ["ticket_about_to_breach", "p1_open_2h", "customer_at_risk", "unusual_ticket_volume", "crm_billing_mismatch"]


@dataclass(frozen=True)
class Role:
    key: str
    label: str
    pages: tuple[str, ...]
    names_on: tuple[str, ...]
    alert_rules: tuple[str, ...]
    assistant_names: bool
    tools: tuple[str, ...]           # in the frozen assistant's order

    def can_open(self, page: str) -> bool:
        return page in self.pages

    def shows_names(self, page: str) -> bool:
        return page in self.names_on


def _all_tools() -> list[str]:
    from pipeline.assistant.kb_tool import TOOL
    from pipeline.tools.data import TOOLS
    return [TOOL["name"]] + [t["name"] for t in TOOLS]          # the order the frozen assistant sends them


@lru_cache(maxsize=1)
def load_roles() -> tuple[dict[str, Role], str]:
    with open(ROLES_FILE, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    every_tool = _all_tools()
    roles = {}
    for key, r in cfg["roles"].items():
        tools = every_tool if r["tools"] == "all" else r["tools"]
        rules = ALERT_RULES if r["alert_rules"] == "all" else r["alert_rules"]
        for kind, got, known in (("page", r["pages"] + r["names_on"], PAGES), ("tool", tools, every_tool),
                                 ("alert rule", rules, ALERT_RULES)):
            unknown = set(got) - set(known)
            if unknown:
                raise ValueError(f"config/roles.yaml: role {key} names unknown {kind}(s) {sorted(unknown)}")
        if set(r["names_on"]) - set(r["pages"]):
            raise ValueError(f"config/roles.yaml: role {key} shows names on a page it cannot open")
        roles[key] = Role(key, r["label"], tuple(r["pages"]), tuple(r["names_on"]), tuple(rules),
                          bool(r["assistant_names"]), tuple(t for t in every_tool if t in tools))
    if cfg["default_role"] not in roles:
        raise ValueError("config/roles.yaml: default_role is not a role")
    return roles, cfg["default_role"]


def get_role(key: str | None) -> Role:
    """The named role, or the default when none is given. An unknown name is an error, never a silent fallback."""
    roles, default = load_roles()
    if key and key not in roles:
        raise ValueError(f"unknown role {key!r}; roles: {', '.join(roles)}")
    return roles[key or default]

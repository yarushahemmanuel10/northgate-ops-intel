"""The assistant for one demo role (step 6.3, D-044), without editing the frozen assistant (D-041).

RoleAssistant subclasses pipeline.assistant.ask.Assistant, which is frozen and unchanged. The subclass lives
here, outside the frozen paths, and changes three things after the frozen code has run:

  __init__    calls the frozen __init__ (which sets self.tools to the KB tool + the 13 data tools), then keeps
              only the tools the role allows, in the same order. ask() builds every request from self.tools,
              so the model is only ever offered the role's tools.
  _run_tool   refuses a tool outside the role with the usual error object (the model could still name one),
              otherwise calls the frozen _run_tool unchanged.
  _finish     calls the frozen _finish; for a role that may not see business names, the displayed answer is
              the model's answer with IDs only (names are not added back).

For operations_director (tools: all) the tool list is the frozen list itself, so its requests are byte-for-byte
the frozen assistant's (tested). Each question and each refused tool is written to the audit log (D-045).
The held-out score (13/14, D-042) applies to that role only; the restricted
roles were not evaluated with held-out questions.
"""
from __future__ import annotations

from pipeline.assistant.ask import Assistant
from pipeline.audit.log import new_session, record
from pipeline.roles.config import Role, get_role


class RoleAssistant(Assistant):
    def __init__(self, role: str | Role, session: str | None = None, **kwargs):
        super().__init__(**kwargs)
        self.role = role if isinstance(role, Role) else get_role(role)
        self.tools = [t for t in self.tools if t["name"] in self.role.tools]
        self.session = session or new_session()

    def _audit(self, action, **kw):
        record(action, role=self.role.key, session=self.session, warehouse_db=self.warehouse_db, **kw)

    def _run_tool(self, name: str, args: dict) -> dict:
        if name not in self.role.tools:
            self._audit("tool_refused", target=name, outcome="refused")
            return {"error": {"code": "not_allowed",
                              "message": f"The {self.role.label} role cannot use the tool '{name}'.",
                              "allowed": list(self.role.tools)}}
        return super()._run_tool(name, args)

    def ask(self, question: str) -> dict:
        out = super().ask(question)
        # the question is stored masked (record() masks every string), with what was used and what it cost
        self._audit("assistant_question", outcome="stopped" if out["stopped"] else "declined" if out["declined"] else "answered",
                    detail={"question": out["sent_question"], "route": out["route"],
                            "tools": [t["name"] for t in out["tools"]], "cited_kb": out.get("cited_kb", []),
                            "cited_views": out.get("cited_views", []), "warnings": len(out.get("warnings", [])),
                            "api_calls": out["api_calls"], "cost_gbp": out["cost_gbp"]})
        return out

    def _finish(self, question, sent_question, text, tool_log, results, calls, stopped=False) -> dict:
        out = super()._finish(question, sent_question, text, tool_log, results, calls, stopped)
        out["role"] = self.role.key
        if not self.role.assistant_names:
            out["display"] = out["answer"]
        return out

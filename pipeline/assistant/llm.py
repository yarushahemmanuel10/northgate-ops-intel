"""Claude API calls with a key from .env, a cost log, a response cache and a spending limit (D-037).

    send(request)      guard -> cache -> budget -> API -> log. Returns the response as a plain dict.

- The key is read from the project's .env file only (not from the environment), passed straight to the
  client, and never printed, logged or written anywhere.
- Every call, cached or not, is appended to data/assistant/calls.jsonl: time, model, tokens in and out,
  cache tokens, cost in dollars and pounds. Requests and replies are not logged.
- Identical requests (same model, system prompt, tools, messages and max_tokens) are answered from
  data/assistant/cache/ at no cost.
- Before each API call, the logged spend is totalled; at or over the limit the call is refused with a clear
  message.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

import yaml

from pipeline.assistant.guard import Forbidden, check_outgoing
from pipeline.settings import ROOT, load_settings

CONFIG_FILE = ROOT / "config" / "assistant.yaml"
ENV_FILE = ROOT / ".env"


class BudgetExceeded(Exception):
    pass


class MissingKey(Exception):
    pass


def load_config() -> dict:
    with open(CONFIG_FILE, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def read_api_key(env_file: Path = ENV_FILE) -> str:
    """ANTHROPIC_API_KEY from .env. Error messages never include the key or the file's contents."""
    if not env_file.exists():
        raise MissingKey(f"No .env file at {env_file}. Copy .env.example to .env and add ANTHROPIC_API_KEY.")
    for line in env_file.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key.strip() == "ANTHROPIC_API_KEY":
            value = value.strip().strip('"').strip("'")
            if value:
                return value
    raise MissingKey("ANTHROPIC_API_KEY is missing or empty in .env.")


def cost_usd(usage: dict, prices: dict) -> float:
    return ((usage.get("input_tokens") or 0) * prices["input"]
            + (usage.get("output_tokens") or 0) * prices["output"]
            + (usage.get("cache_creation_input_tokens") or 0) * prices["cache_write_5m"]
            + (usage.get("cache_read_input_tokens") or 0) * prices["cache_read"]) / 1_000_000


class Caller:
    def __init__(self, forbidden: Forbidden, client=None, config: dict | None = None, out_dir: Path | None = None):
        self.cfg = config or load_config()
        self.forbidden = forbidden
        self._client = client
        self.dir = Path(out_dir or load_settings()["assistant_dir"])
        self.log = self.dir / "calls.jsonl"
        self.cache = self.dir / "cache"
        self.cache.mkdir(parents=True, exist_ok=True)

    @property
    def client(self):
        if self._client is None:                      # only built when a real call is needed
            import anthropic
            self._client = anthropic.Anthropic(api_key=read_api_key())
        return self._client

    def spent_gbp(self) -> float:
        if not self.log.exists():
            return 0.0
        with open(self.log, encoding="utf-8") as fh:
            return sum(json.loads(line)["cost_gbp"] for line in fh if line.strip())

    def _write_log(self, model: str, usage: dict, cached: bool) -> dict:
        usd = 0.0 if cached else cost_usd(usage, self.cfg["prices"])
        entry = {"time": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "model": model,
                 "cached": cached, "input_tokens": usage.get("input_tokens") or 0,
                 "output_tokens": usage.get("output_tokens") or 0,
                 "cache_creation_input_tokens": usage.get("cache_creation_input_tokens") or 0,
                 "cache_read_input_tokens": usage.get("cache_read_input_tokens") or 0,
                 "cost_usd": round(usd, 6), "cost_gbp": round(usd * self.cfg["budget"]["gbp_per_usd"], 6)}
        with open(self.log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
        return entry

    def send(self, request: dict) -> tuple[dict, dict]:
        """Returns (response dict, log entry)."""
        check_outgoing(request, self.forbidden)       # nothing is sent, or cached, if this fails
        key = hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        hit = self.cache / f"{key}.json"
        if hit.exists():
            resp = json.loads(hit.read_text(encoding="utf-8"))
            return resp, self._write_log(request["model"], resp.get("usage") or {}, cached=True)
        limit, spent = self.cfg["budget"]["limit_gbp"], self.spent_gbp()
        if spent >= limit:
            raise BudgetExceeded(f"Spending limit reached: £{spent:.2f} logged of the £{limit:.2f} limit "
                                 f"(see {self.log}). No call was made.")
        resp = self.client.messages.create(**request).model_dump(mode="json")
        hit.write_text(json.dumps(resp), encoding="utf-8")
        return resp, self._write_log(request["model"], resp.get("usage") or {}, cached=False)

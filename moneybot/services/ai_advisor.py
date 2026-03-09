from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional

import requests




def _coerce_text_candidate(candidate: Any) -> Optional[str]:
    if isinstance(candidate, str) and candidate.strip():
        return _strip_code_fences(candidate)

    if isinstance(candidate, dict):
        for key in ("text", "value", "content"):
            nested = _coerce_text_candidate(candidate.get(key))
            if nested:
                return nested

    if isinstance(candidate, list):
        for item in candidate:
            nested = _coerce_text_candidate(item)
            if nested:
                return nested

    return None

def _strip_code_fences(text: str) -> str:
    raw = (text or "").strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if len(lines) >= 3:
            return "\n".join(lines[1:-1]).strip()
    return raw


class AIAdvisorService:
    """Optional LLM enhancer for quick buy/sell suggestions.

    Returns deterministic fallback content when AI is disabled/unavailable.
    """

    def __init__(
        self,
        enabled: bool = False,
        provider: str = "openai",
        model: str = "gpt-5-mini",
        api_key: str = "",
        timeout_s: float = 2.5,
        failure_cooldown_s: int = 120,
        cache_ttl_s: int = 300,
    ) -> None:
        self.enabled = enabled and bool(api_key.strip())
        self.provider = (provider or "openai").strip().lower()
        self.model = (model or "gpt-5-mini").strip()
        self.api_key = (api_key or "").strip()
        self.timeout_s = timeout_s
        self.failure_cooldown_s = max(1, int(failure_cooldown_s))
        self.cache_ttl_s = max(1, int(cache_ttl_s))
        self._disabled_until = 0.0
        self._cache: dict[str, dict[str, Any]] = {}
        self._cache_ts: dict[str, float] = {}

    def _cache_key(self, symbol: str, recommendation: str, rationale: str, signal_score: Any) -> str:
        return "|".join(
            [
                (symbol or "").upper(),
                recommendation.strip().upper(),
                rationale.strip(),
                str(signal_score),
            ]
        )

    def _cache_get(self, key: str) -> Optional[Dict[str, Any]]:
        ts = self._cache_ts.get(key)
        if ts is None:
            return None
        if time.time() - ts > self.cache_ttl_s:
            self._cache_ts.pop(key, None)
            self._cache.pop(key, None)
            return None
        cached = self._cache.get(key)
        return dict(cached) if isinstance(cached, dict) else None

    def _cache_set(self, key: str, payload: Dict[str, Any]) -> None:
        self._cache[key] = dict(payload)
        self._cache_ts[key] = time.time()

    def _fallback(self, recommendation: str, rationale: str, reason: str = "disabled_or_unavailable") -> Dict[str, Any]:
        return {
            "mode": "rule_based",
            "narrative": (
                f"{recommendation}: {rationale} Keep position sizing disciplined and monitor volatility closely."
            ),
            "risk_notes": [
                "Aggressive profile selected: expect larger swings and use strict stop-loss rules.",
                "This is not financial advice; always verify with your own research.",
            ],
            "next_checks": [
                "Re-check momentum and sentiment on the next market session.",
                "Confirm whether volume supports the move before increasing position size.",
            ],
            "provider": "none",
            "model": "none",
            "reason": reason,
        }

    def _should_skip_ai(self, quick_decision: Dict[str, Any], signal_data: Dict[str, Any]) -> bool:
        """Skip provider calls when inputs are too thin to justify extra latency/cost."""
        recommendation = str(quick_decision.get("recommendation") or "").upper()
        rationale = str(quick_decision.get("rationale") or "")
        sentiment = signal_data.get("sentiment") if isinstance(signal_data.get("sentiment"), dict) else {}
        sentiment_score = sentiment.get("score")
        headlines = sentiment.get("headlines") if isinstance(sentiment.get("headlines"), list) else []
        score = signal_data.get("score")
        if score is None:
            score = signal_data.get("hybrid_score")

        generic_rationale = (
            "revenue flat" in rationale.lower()
            or "no pts" in rationale.lower()
            or "derived from momentum" in rationale.lower()
        )
        weak_signal = isinstance(score, (int, float)) and float(score) <= 2.0
        missing_sentiment = sentiment_score is None and len(headlines) == 0

        return recommendation == "HOLD OFF FOR NOW" and generic_rationale and weak_signal and missing_sentiment

    def _openai_response(self, prompt: str) -> Optional[str]:
        url = "https://api.openai.com/v1/responses"
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["narrative", "risk_notes", "next_checks"],
            "properties": {
                "narrative": {"type": "string"},
                "risk_notes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 2,
                    "maxItems": 2,
                },
                "next_checks": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 2,
                    "maxItems": 2,
                },
            },
        }
        payload = {
            "model": self.model,
            "input": prompt,
            "max_output_tokens": 220,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "moneybot_quick_ask",
                    "schema": schema,
                    "strict": True,
                }
            },
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout_s)
        resp.raise_for_status()
        data = resp.json()

        text = _coerce_text_candidate(data.get("output_text"))
        if text:
            return text

        output_json = data.get("output_json")
        if isinstance(output_json, (dict, list)):
            return json.dumps(output_json)

        # Fallback parser for Responses payload variants that do not set output_text.
        output = data.get("output") if isinstance(data.get("output"), list) else []
        for item in output:
            content = item.get("content") if isinstance(item, dict) else None
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if block_type in {"output_text", "text"}:
                    candidate = _coerce_text_candidate(block.get("text"))
                    if candidate:
                        return candidate
                block_json = block.get("json")
                if isinstance(block_json, (dict, list)):
                    return json.dumps(block_json)
        return None

    def enhance_quick_decision(
        self,
        *,
        symbol: str,
        quick_decision: Dict[str, Any],
        signal_data: Dict[str, Any],
        quote_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        recommendation = str(quick_decision.get("recommendation") or "HOLD OFF FOR NOW")
        rationale = str(quick_decision.get("rationale") or "Derived from momentum and signal checks.")
        signal_score = signal_data.get("score")
        if signal_score is None:
            signal_score = signal_data.get("hybrid_score")
        cache_key = self._cache_key(symbol, recommendation, rationale, signal_score)

        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        if self._should_skip_ai(quick_decision, signal_data):
            payload = self._fallback(recommendation, rationale, reason="low_signal")
            payload["mode"] = "skipped_low_signal"
            self._cache_set(cache_key, payload)
            return payload

        if not self.enabled:
            return self._fallback(recommendation, rationale, reason="disabled_or_missing_api_key")

        if self.provider != "openai":
            logging.warning("Unsupported AI provider configured: %s", self.provider)
            return self._fallback(recommendation, rationale, reason="unsupported_provider")

        now = time.time()
        if now < self._disabled_until:
            return self._fallback(recommendation, rationale, reason="cooldown_after_failure")

        compact_context = {
            "symbol": symbol,
            "rec": recommendation,
            "rationale": rationale,
            "quote": {
                "price": quote_data.get("price"),
                "chg": quote_data.get("change_percent"),
                "source": quote_data.get("quote_source"),
            },
            "technical": signal_data.get("technical"),
            "sentiment": signal_data.get("sentiment"),
            "action": signal_data.get("action"),
            "score": signal_score,
        }
        prompt = (
            "Return strict JSON with keys narrative,risk_notes,next_checks. "
            "Aggressive but risk-aware tone. No guarantees. "
            "narrative <=55 words; risk_notes exactly 2; next_checks exactly 2. "
            f"Context: {json.dumps(compact_context, default=str)}"
        )

        try:
            raw = self._openai_response(prompt)
            if not raw:
                return self._fallback(recommendation, rationale, reason="empty_or_unparseable_provider_response")
            parsed = json.loads(raw)
            narrative = str(parsed.get("narrative") or "").strip()
            risk_notes = parsed.get("risk_notes") if isinstance(parsed.get("risk_notes"), list) else []
            next_checks = parsed.get("next_checks") if isinstance(parsed.get("next_checks"), list) else []
            if not narrative:
                return self._fallback(recommendation, rationale, reason="missing_narrative_in_provider_response")
            result = {
                "mode": "ai_enhanced",
                "narrative": narrative,
                "risk_notes": [str(x) for x in risk_notes][:2],
                "next_checks": [str(x) for x in next_checks][:2],
                "provider": self.provider,
                "model": self.model,
            }
            self._cache_set(cache_key, result)
            return result
        except Exception as exc:  # noqa: BLE001
            self._disabled_until = time.time() + float(self.failure_cooldown_s)
            logging.warning("AI advisor unavailable, using fallback: %s", exc)
            return self._fallback(recommendation, rationale, reason="provider_error")

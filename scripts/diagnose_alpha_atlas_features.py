#!/usr/bin/env python3
from __future__ import annotations

import os

from moneybot.services.deterministic_advisor import DeterministicQuickAdvisor
from moneybot.services.market_data import MarketDataService
from moneybot.services.runtime_paths import day1_baseline_model_path


def main() -> None:
    symbols = [
        value.strip().upper()
        for value in os.environ.get("SYMBOLS", "APLD,UMAC,ASPI,SQQQ").split(",")
        if value.strip()
    ]
    market = MarketDataService()
    advisor = DeterministicQuickAdvisor(
        enabled=True, artifact_path=str(day1_baseline_model_path())
    )
    print("SYMBOL  STATUS       AVAILABLE  IMPUTED  MISSING")
    for symbol in symbols:
        quote = market.get_quote(symbol)
        signal = market.get_signal(symbol)
        _, _, diagnostics = advisor._build_feature_row_with_diagnostics(signal, quote)
        status = "servable" if diagnostics["feature_contract_servable"] else "BLOCKED"
        missing = ",".join(diagnostics["feature_contract_missing_features"])
        print(
            f"{symbol:<7} {status:<12} {diagnostics['feature_contract_available_count']:<10} {diagnostics['feature_contract_imputed_count']:<8} {missing}"
        )


if __name__ == "__main__":
    main()

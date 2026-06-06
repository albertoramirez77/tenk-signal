"""Centralized FastAPI dependencies for service objects.

Tests override ``app.dependency_overrides`` on these names to inject fakes
(FixtureEdgarClient, FixturePriceClient, FixtureExtractor) without touching
the live HTTP/SDK clients.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import Depends

from tenk_signal.config import Settings, get_settings
from tenk_signal.services.edgar import EdgarClient, LiveEdgarClient
from tenk_signal.services.extractor import Extractor, LiveAnthropicExtractor
from tenk_signal.services.prices import PriceClient, YFinancePriceClient


def _singleton[T](builder: Callable[[Settings], T]) -> Callable[..., Any]:
    cache: dict[str, T] = {}

    def dep(settings: Settings = Depends(get_settings)) -> T:
        if "v" not in cache:
            cache["v"] = builder(settings)
        return cache["v"]

    return dep


get_edgar: Callable[..., EdgarClient] = _singleton(lambda s: LiveEdgarClient(s))
get_prices: Callable[..., PriceClient] = _singleton(lambda _s: YFinancePriceClient())
get_extractor: Callable[..., Extractor] = _singleton(lambda s: LiveAnthropicExtractor(s))

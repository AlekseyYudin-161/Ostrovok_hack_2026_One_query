from __future__ import annotations

from datetime import date, datetime

import pytest

from harness.contracts import DestinationOption, HotelOffer, Tool, TransferOption, TransportOption
from synth.tools import (
    RankAreasArgs,
    SearchHotelsArgs,
    SearchTransfersArgs,
    SearchTransportArgs,
    build_tools,
    get_catalog,
    tool_specs,
)

T = build_tools()
CAT = get_catalog()
IN, OUT = date(2026, 11, 10), date(2026, 11, 17)


def test_tools_match_protocol() -> None:
    assert set(T) == {"search_hotels", "search_transport", "search_transfers", "rank_areas"}
    for tool in T.values():
        assert isinstance(tool, Tool) and tool.side_effect == "none"
    assert {s["function"]["name"] for s in tool_specs()} == set(T)


@pytest.mark.asyncio
async def test_search_hotels_filters() -> None:
    res = await T["search_hotels"](SearchHotelsArgs(area="в Сириусе", checkin=IN, checkout=OUT,
                                                    children_ages=[7], pets=True, max_total=150000))
    assert res.ok and res.data and res.tool == "search_hotels"
    prices = [x["price_total"] for x in res.data]
    assert prices == sorted(prices) and max(prices) <= 150000
    for x in res.data:
        HotelOffer(**x)                                      # лишний ключ facts не мешает модели
        h = CAT.hotels[x["hotel"]["hotel_id"]]
        assert h.area_id == "imeretinka" and h.pets_allowed and h.children_allowed
        assert x["hotel"]["source_call_id"] == res.call_id   # groundedness: цена из этого вызова


@pytest.mark.asyncio
async def test_search_hotels_is_deterministic_and_shift_changes_price() -> None:
    args = SearchHotelsArgs(area="adler", checkin=IN, checkout=OUT)
    a, b = await T["search_hotels"](args), await T["search_hotels"](args)
    assert [x["price_total"] for x in a.data] == [x["price_total"] for x in b.data]
    later = await T["search_hotels"](args.model_copy(update={"checkin": date(2026, 11, 12),
                                                              "checkout": date(2026, 11, 19)}))
    assert later.ok and later.data


@pytest.mark.asyncio
async def test_search_hotels_edge_cases() -> None:
    sold = await T["search_hotels"](SearchHotelsArgs(area="Сириус", checkin=date(2026, 12, 30),
                                                     checkout=date(2027, 1, 2), pets=True))
    assert sold.ok and sold.data == []                       # новогодний пик — честное «нет вариантов»
    bad = await T["search_hotels"]({"checkin": OUT, "checkout": IN})
    assert not bad.ok and bad.error_class == "validation"
    unknown = await T["search_hotels"](SearchHotelsArgs(area="Анапа", checkin=IN, checkout=OUT))
    assert not unknown.ok and unknown.error_class == "unknown_entity" and unknown.data["known"]


@pytest.mark.asyncio
async def test_search_transport() -> None:
    res = await T["search_transport"](SearchTransportArgs(origin_city="из Москвы", travel_date=IN,
                                                          children_ages=[7]))
    kinds = {x["kind"] for x in res.data}
    assert res.ok and {"flight", "train"} <= kinds
    for x in res.data:
        TransportOption(**x)
    train = next(x for x in res.data if x["kind"] == "train")
    assert train["facts"]["stations_min_from_departure"]
    back = await T["search_transport"](SearchTransportArgs(origin_city="Питер", travel_date=OUT,
                                                           direction="return", kinds=["flight"]))
    assert back.ok and back.data and all(x["to_city"].startswith("Санкт-Петербург") for x in back.data)
    nope = await T["search_transport"](SearchTransportArgs(origin_city="Минск", travel_date=IN))
    assert not nope.ok and nope.error_class == "unknown_entity"


@pytest.mark.asyncio
async def test_search_transfers() -> None:
    hotel = next(h for h in CAT.hotels.values() if h.area_id == "rosa_khutor")
    when = datetime(2026, 12, 20, 14, 0)
    res = await T["search_transfers"](SearchTransfersArgs(from_place="аэропорт", to_place=hotel.hotel_id,
                                                          when=when, children_ages=[7]))
    assert res.ok and {x["kind"] for x in res.data} >= {"taxi", "shuttle"}
    for x in res.data:
        TransferOption(**x)
    pets = await T["search_transfers"](SearchTransfersArgs(from_place="aer", to_place="Адлер", when=when, pets=True))
    assert pets.ok and pets.data and all(x["kind"] != "shuttle" for x in pets.data)
    near = min(x["minutes"] for x in pets.data if x["kind"] == "taxi")
    far = min(x["minutes"] for x in res.data if x["kind"] == "taxi")
    assert near < far                                        # Адлер ближе к аэропорту, чем Роза Хутор


@pytest.mark.asyncio
async def test_rank_areas() -> None:
    winter = await T["rank_areas"](RankAreasArgs(month=2, wishes=["горы/лыжи"]))
    assert winter.ok and CAT.areas[winter.data[0]["destination_id"]].zone == "mountains"
    summer = await T["rank_areas"](RankAreasArgs(month=7, wishes=["море", "с детьми"], children_ages=[5]))
    top = summer.data[0]
    DestinationOption(**top)
    assert CAT.areas[top["destination_id"]].zone != "mountains" and top["score"] > 0
    assert any("купальный" in w for w in top["why"])

from datetime import date, datetime

from harness.checks import check_connections, connection_errors
from harness.contracts import HotelCandidate, HotelOffer, Itinerary, TransferOption, TransportOption


def _it(arrive: str, depart: str) -> Itinerary:
    hotel = HotelOffer(offer_id="o1", price_total=50000,
                       hotel=HotelCandidate(hotel_id="h", name="H", source_tool="t", source_call_id="c"))
    out = TransportOption(option_id="f1", kind="flight", from_city="Москва", to_city="Анталья",
                          depart=datetime(2026, 11, 10, 8), arrive=datetime.fromisoformat(arrive),
                          duration_hours=4, price_total=20000)
    back = TransportOption(option_id="f2", kind="flight", from_city="Анталья", to_city="Москва",
                           depart=datetime.fromisoformat(depart), arrive=datetime(2026, 11, 17, 23),
                           duration_hours=4, price_total=20000)
    tr = [TransferOption(option_id="t1", from_place="AYT", to_place="H", minutes=40, price_total=1500),
          TransferOption(option_id="t2", from_place="H", to_place="AYT", minutes=40, price_total=1500)]
    return Itinerary(destination_id="ayt", checkin=date(2026, 11, 10), checkout=date(2026, 11, 17),
                     outbound=out, inbound=back, transfers=tr, hotel=hotel)


def test_good_trip_has_no_errors() -> None:
    assert connection_errors(_it("2026-11-10T12:00", "2026-11-17T17:00")) == 0


def test_night_arrival_next_day_is_error() -> None:
    it = _it("2026-11-10T23:50", "2026-11-17T17:00")
    assert connection_errors(it) == 1
    assert any(c.rule == "dates_match" for c in check_connections(it))


def test_early_arrival_warns_with_fixes() -> None:
    checks = check_connections(_it("2026-11-10T06:00", "2026-11-17T17:00"))
    warn = next(c for c in checks if c.rule == "arrival_vs_checkin")
    assert warn.severity == "warning" and "ранний заезд" in " ".join(warn.fixes)


def test_departure_day_before_checkout_is_error() -> None:
    assert connection_errors(_it("2026-11-10T12:00", "2026-11-16T09:00")) == 1

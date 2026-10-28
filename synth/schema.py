"""Схема синтетического каталога «Большой Сочи».

Каталог хранит только статичные факты. Цены, наличие и офферы на даты считает synth/pricing.py
детерминированно (хэш от id и даты), выход — модели из harness/contracts.py.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Zone = Literal["coast", "imeretinka", "mountains"]
Season = Literal["low", "regular", "high"]
Meal = Literal["none", "breakfast", "half_board", "full_board", "all_inclusive"]
PropertyType = Literal["hotel", "guest_house", "apartments", "sanatorium", "resort", "glamping"]

AMENITIES = [
    "wifi", "parking", "air_conditioning", "elevator", "restaurant", "bar", "room_service", "laundry",
    "pool", "heated_pool", "indoor_pool", "kids_pool", "aquapark", "spa", "sauna", "gym",
    "kids_club", "playground", "babysitting", "private_beach", "beach_shuttle", "sea_view",
    "mountain_view", "balcony", "kitchen", "bbq", "garden", "airport_shuttle", "ski_storage",
    "ski_in_out", "ski_rental", "medical_treatment", "mineral_baths", "coworking", "accessible",
    "ev_charging",
]
HOTEL_TAGS = [
    "тихо", "для пар", "для семей с детьми", "для компаний", "бюджетно", "люкс", "у моря",
    "в центре", "вид на море", "вид на горы", "рядом с подъёмником", "оздоровление", "для удалёнки",
    "с животными", "тусовка рядом", "уединённо", "дизайнерский", "советская классика", "для активных",
]
AREA_BEST_FOR = ["море", "горы/лыжи", "с детьми", "тихо", "бюджетно", "тусовка", "оздоровление",
                 "экскурсии", "активный отдых", "романтика", "люкс"]
REVIEW_ASPECTS = ["cleanliness", "noise", "wifi", "staff", "food", "beach", "location", "kids",
                  "room", "value", "pool", "renovation"]


class Area(BaseModel):
    area_id: str
    name: str
    zone: Zone
    coast_km: float                       # позиция вдоль побережья (Лазаревское = 0)
    mountain_km: float = 0.0              # горная ветка от Адлера
    price_level: Literal["low", "medium", "high", "premium"]
    vacation_types: list[str]
    aliases: list[str] = []               # синонимы в нижнем регистре для поиска подстрокой в запросе
    season_calendar: dict[int, Season]    # месяц -> сезон
    season_coefficients: dict[Season, float]
    description: str = ""
    vibe: str = ""
    best_for: list[str] = []
    tags: list[str] = []


class ClimateRow(BaseModel):
    zone: Zone
    month: int
    air_day_c: float
    air_night_c: float
    sea_c: float | None
    rain_days: int
    ski_open: bool = False
    swim_ok: bool = False


class ReferencePoint(BaseModel):
    point_id: str
    type: Literal["airport", "railway_station", "local_station"]
    name: str
    area_id: str
    offset_km: float                      # от центра района
    long_distance: bool = False           # останавливаются ли поезда дальнего следования


class RoomType(BaseModel):
    room_type_id: str
    name: str
    capacity_adults: int
    capacity_children: int
    area_m2: int
    view: Literal["sea", "mountain", "city", "garden", "courtyard", "none"] = "none"
    beds: str = ""
    price_coefficient: float = 1.0
    inventory: int = 5


class RatePlan(BaseModel):
    rate_id: str
    room_type_id: str
    meal: Meal
    cancellation: Literal["free_until_1d", "free_until_3d", "free_until_7d", "free_until_14d", "non_refundable"]
    free_cancel_days: int | None
    price_coefficient: float


class Hotel(BaseModel):
    hotel_id: str
    area_id: str
    name: str
    property_type: PropertyType
    stars: int | None = None
    address: str
    description: str
    rating: float = Field(ge=1, le=10)
    reviews_count: int
    km_to_area_center: float
    distance_to_sea_m: int | None = None
    beach_line: int | None = None
    distance_to_lift_m: int | None = None
    amenities: list[str]
    tags: list[str]
    meal_options: list[Meal]
    children_allowed: bool = True
    min_child_age: int = 0
    pets_allowed: bool = False
    pet_fee_per_night: int = 0
    pet_max_kg: int | None = None
    check_in_time: str = "14:00"
    check_out_time: str = "12:00"
    early_checkin_fee: int | None = None     # None — ранний заезд не предоставляется
    late_checkout_fee: int | None = None
    base_price_per_night: int
    room_types: list[RoomType]
    rate_plans: list[RatePlan]
    review_aspects: dict[str, float] = {}    # -1..1, агрегировано из reviews.jsonl


class Review(BaseModel):
    review_id: str
    hotel_id: str
    author: str
    traveler_type: Literal["family", "couple", "solo", "friends", "business", "with_pet"]
    stay_month: str                          # YYYY-MM
    score: float = Field(ge=1, le=10)
    text: str
    pros: str = ""
    cons: str = ""
    aspects: dict[str, int] = {}             # аспект -> -1/0/1
    is_injection: bool = False               # отзыв с промпт-инъекцией (срез injection)


class POI(BaseModel):
    poi_id: str
    area_id: str
    name: str
    type: Literal["beach", "park", "museum", "attraction", "ski", "viewpoint", "waterpark", "promenade",
                  "nature", "food", "entertainment"]
    description: str
    months_open: list[int]
    kid_friendly: bool
    price_rub: int
    km_from_area_center: float


class OriginCity(BaseModel):
    city: str
    tz_offset: int                           # UTC+N
    air_km: int | None = None                # None — нет авиасообщения
    airports: list[str] = []
    flights_per_day: int = 0
    summer_extra_flights: int = 0
    via_moscow: bool = False                 # есть стыковочные рейсы через Москву
    rail_km: int | None = None               # None — нет прямого поезда
    trains_per_day: int = 0
    fast_train: bool = False                 # «Ласточка»/скоростной


class FlightSchedule(BaseModel):
    schedule_id: str
    kind: Literal["flight"] = "flight"
    direction: Literal["outbound", "return"]
    origin_city: str
    origin_airport: str
    point_id: str                            # aer
    carrier: str
    route_number: str
    departure_time: str                      # локальное время пункта отправления
    duration_minutes: int
    stops: int = 0
    via: str | None = None
    layover_minutes: int = 0
    operating_days: list[int]                # ISO 1..7
    valid_months: list[int]
    baggage_included: bool
    base_adult_price: int


class TrainSchedule(BaseModel):
    schedule_id: str
    kind: Literal["train", "local_train", "bus"]
    direction: Literal["outbound", "return"]
    origin_city: str
    carrier: str
    route_number: str
    stops: dict[str, int]                    # point_id -> минут от отправления (для outbound — прибытие в Сочи)
    departure_time: str
    duration_minutes: int                    # до последней остановки
    operating_days: list[int]
    valid_months: list[int]
    classes: dict[str, int]                  # класс -> базовая цена взрослого


class LocalLine(BaseModel):
    line_id: str
    kind: Literal["local_train", "bus"]
    name: str
    stops: list[str]                         # area_id по порядку
    first_departure: str
    last_departure: str
    interval_minutes: int
    base_price: int
    price_per_km: float


class TransferTariff(BaseModel):
    tariff_id: str
    kind: Literal["taxi", "shuttle", "public"]
    name: str
    base_price: int
    price_per_km: float
    per_person: bool = False
    capacity: int
    luggage: int
    pets_allowed: bool
    child_seat_fee: int = 0
    pet_fee: int = 0
    night_coefficient: float = 1.0


class Carriers(BaseModel):
    airlines: list[str]
    rail: list[str]
    local: list[str]

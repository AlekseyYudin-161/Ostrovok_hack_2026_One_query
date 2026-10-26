# synth — синтетический каталог «Большой Сочи»

**Все данные — демо (сгенерированы).** Отели, перевозчики, отзывы, расписания вымышленные; названия районов и климатические нормы — условно реальные.

## Принципы
- **Python задаёт структуру и числа, GLM-5.3 — тексты.** Квоты по районам (типы размещения, звёзды, животные, adults-only, семейные номера, питание) гарантируют, что у каждого сценария ТЗ есть данные.
- **Согласованность, а не реализм.** Время в пути всегда выводится из км (`geo.py`: побережье — отрезок, горы — ветка от Адлера), цены — из формул (`pricing_rules.json`).
- **Офферы на даты не хранятся.** Их считает `pricing.Catalog` детерминированно (хэш от id и даты): тот же запрос даёт ту же цену, «+2 дня» — новую, но правдоподобную. Выход — модели `harness/contracts.py`.

## Запуск
```bash
uv run --with jupyter jupyter lab synth/synth.ipynb      # интерактивно
uv run --with jupyter jupyter nbconvert --to notebook --execute --inplace synth/synth.ipynb
```
Ключ GLM: `SYNTH_LLM_API_KEY` + `SYNTH_LLM_BASE_URL`, иначе берётся из `~/.config/opencode/opencode.json`. Модель `glm-5.3-no-collect`.
Ответы кэшируются в `synth/raw/cache/` — повторный прогон без сети (`OFFLINE = True`). Чтобы перегенерировать часть — удалить нужные файлы кэша (`hotels_<area>_*`, `reviews_<hotel_id>_*`).

## Файлы `data/`
| Файл | Что внутри |
|---|---|
| `areas.json` | 10 районов: зона, ценовой уровень, `season_calendar` (месяц → сезон), коэффициенты сезона, описание, `best_for`, теги |
| `climate.json` | зона × месяц: воздух день/ночь, море, дождливые дни, `swim_ok`, `ski_open` |
| `reference_points.json` | аэропорт и станции (район + смещение), где останавливаются поезда дальнего следования |
| `area_distances.json` | район × точка: км и минуты (выведены из `geo.py`, справочно) |
| `hotels.json` | ~166 объектов: тип, звёзды, рейтинг, линия/расстояние до моря или подъёмника, удобства, теги, питание, дети/животные, заезд/выезд, ранний заезд/поздний выезд, номера, тарифы (номер × питание × отмена), агрегаты отзывов по аспектам |
| `reviews.jsonl` | ~6 отзывов на объект с аспектами (-1/0/1) + 5 отзывов с промпт-инъекцией (`is_injection: true`) |
| `poi.json` | места и активности по районам с месяцами работы |
| `transport_schedules.json` | города вылета (tz, км), рейсы (прямые, с пересадкой, летние, без багажа, ночные), поезда (классы), «Ласточки», автобусы, местные линии |
| `transfer_tariffs.json` | такси (эконом … бизнес, с животными, минивэн), шаттл, доплаты |
| `pricing_rules.json` | выходные, праздники, загрузка, наличие, туристический налог, питание, скидки детям, буферы |
| `edge_cases.json` | распродано на даты, сезонные закрытия, отменённые рейсы, рейсы без мест — для веток no_solution и пересборки |
| `carriers.json`, `meta.json` | перевозчики, метаданные |

## Использование в агенте
```python
from synth.pricing import Catalog
cat = Catalog()
cat.destination_options(month=11, origin_city="Москва", adults=2, children_ages=[7], nights=7)  # DestinationOption
cat.search_hotels(checkin, checkout, 2, [7], pets=True, area_ids=["adler"], max_total=60000)    # HotelOffer
cat.quote_hotel(hotel_id, checkin, checkout, 2, [7])          # все тарифы с разбивкой цены
cat.transport_options("Москва", d, "outbound", 2, [7])        # TransportOption: рейсы, поезда, автобусы
cat.transfer_options("aer", hotel_id, arrive_dt, 2, [7], pets=True)  # TransferOption: такси, шаттл, общественный
cat.reviews[hotel_id], cat.poi_for(area_id, month), cat.climate_for(area_id, month)
```
Собранный `Itinerary` проверяется `harness.checks.check_connections` — пример в `validate.example_itinerary`.

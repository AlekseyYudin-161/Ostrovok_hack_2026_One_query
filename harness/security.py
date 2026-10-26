"""Защита агента от prompt injection и базовая гигиена ввода (L13, ADR-010, DesignDoc §10).

Слои (defense in depth):
  1. Нормализация ввода: NFKC (полноширинные «ｉｇｎｏｒｅ» → «ignore»), удаление невидимых и управляющих символов
     (zero-width, bidi-override), обрезка до MAX_LEN, вычистка поддельных ролей («system:», «<|im_start|>», «[INST]»).
  2. Детектор инъекций (RU + EN): попытка отменить инструкции, выведать промпт/секреты, сменить роль,
     совершить действие без подтверждения. Предложения с инъекцией вырезаются; если запроса о поездке не осталось —
     вежливый отказ без вызова LLM.
  3. Архитектура: текст ответа пользователю собирает код по шаблонам, LLM возвращает только JSON по схеме —
     системный промпт физически не попадает в ответ; покупка — только через паузу и явное «да» (interrupt).
  4. Недоверенные данные (отзывы, описания отелей) оборачиваются в маркеры и чистятся тем же детектором.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

MAX_LEN = 2000
_INVISIBLE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ROLE_SPOOF = re.compile(
    r"(?im)^\s*(?:#{1,3}\s*)?(?:system|assistant|developer|tool|система|ассистент|разработчик)\s*[:：>]\s*"
    r"|<\|?\s*(?:im_start|im_end|system|endoftext)\s*\|?>|\[/?(?:INST|SYS)\]|<</?SYS>>")

# Категория → шаблоны (RU и EN). Нужен «объект» атаки (инструкции, промпт, роль, подтверждение),
# чтобы обычные фразы («хочу забыть о работе», «покажи варианты») не считались атакой.
PATTERNS: dict[str, list[str]] = {
    "ignore_instructions": [
        (r"(игнорир|проигнорир|забудь|не\s+следуй|не\s+соблюдай)\w*\s+(?:\w+\s+){0,3}"
         r"(инструкц|правил|указан|ограничени|промпт|запрет|установк)"),
        r"(игнорир|проигнорир|забудь)\w*\s+(?:вс[её]|все)[\s,]+(?:что\s+)?(?:\w+\s+)?(?:выше|раньше|ранее|до\s+этого|сказан)",
        (r"\b(ignore|disregard|forget|override|bypass)\b\s+(?:\w+\s+){0,3}"
         r"(instructions?|rules?|prompts?|guidelines|polic(?:y|ies)|restrictions?)"),
        r"\b(ignore|disregard|forget)\b\s+(?:(?:everything|all|anything|the)\s+)?(?:text\s+|said\s+)?above\b",
        r"\b(ignore|disregard|forget)\s+everything\b",
    ],
    "reveal_prompt": [
        (r"(покажи|выведи|раскрой|напиши|повтори|процитируй|скажи|перечисли|какой|какие)\w*\s+(?:\w+\s+){0,3}"
         r"(систем\w*\s+(?:промпт|инструкц|сообщени)|сво\w*\s+(?:инструкц|правил|промпт)|промпт)"),
        (r"\b(show|reveal|print|repeat|output|tell|what(?:'s| is| are))\b\s+(?:\w+\s+){0,4}"
         r"(system\s+(?:prompt|message|instructions?)|your\s+(?:instructions|prompt|rules)|initial\s+prompt|hidden\s+prompt)"),
        r"(повтори|выведи|процитируй|покажи)\w*\s+(?:\w+\s+){0,2}(текст|сообщени|написанн)\w*\s+выше",
        r"\b(repeat|print|output|show)\b\s+(?:\w+\s+){0,3}(text|words|everything|message)\s+above",
    ],
    "role_override": [
        (r"(ты\s+теперь|теперь\s+ты|представь,?\s+что\s+ты)\s+(?:\w+\s+){0,2}"
         r"(бот|ассистент|модел|нейросет|персонаж|друг[оа]|свободн|без\s+огранич|хакер|пират|зло|админ|dan\b)"),
        r"(притворись|веди\s+себя\s+как|режим\s+разработчика|джейлбрейк)",
        (r"\b(you\s+are\s+now|from\s+now\s+on\s+you|act\s+as\s+(?:an?\s+)?(?:\w+\s+){0,2}(?:ai|assistant|bot|model|admin|developer|unrestricted|hacker|pirate|dan)\b|pretend\s+(?:to\s+be|you\s+are)|roleplay\s+as|"
         r"developer\s+mode|jailbreak|\bdan\b)"),
    ],
    "action_bypass": [
        r"(оформ|купи|оплат|заброниру|бронируй)\w*\s+(?:\w+\s+){0,4}(без\s+подтвержд|не\s+спрашива|без\s+вопрос)",
        r"(подтверждени\w*\s+не\s+нуж|без\s+моего\s+(?:согласия|подтверждения)|считай,?\s+что\s+я\s+согласил)",
        (r"\b(book|buy|pay|purchase|order|confirm)\b\s+(?:\w+\s+){0,4}"
         r"(without\s+(?:asking|confirmation|my\s+consent)|no\s+confirmation)"),
        r"(no\s+confirmation\s+(?:is\s+)?needed|skip\s+(?:the\s+)?confirmation|assume\s+i\s+(?:agree|said\s+yes))",
    ],
    "exfiltration": [
        r"(покажи|выведи|дай|скажи|отправь|напиши)\w*\s+(?:\w+\s+){0,3}(api[\s_-]?ключ|ключ\w*\s+api|токен|пароль|\.env|секрет)",
        r"\b(show|give|print|send|reveal|leak)\b\s+(?:\w+\s+){0,3}(api[\s_-]?keys?|tokens?|passwords?|\.env|secrets?|credentials)",
    ],
}
_COMPILED = {cat: [re.compile(p, re.IGNORECASE) for p in ps] for cat, ps in PATTERNS.items()}
_SENTENCE = re.compile(r"[^.!?\n;]+[.!?;]*")

REFUSAL = ("Я помогаю только с поездками по Большому Сочи: подбираю жильё и дорогу, а оформляю бронь только после "
           "вашего «да». Свои правила и внутренние инструкции не меняю и не раскрываю. Куда и когда хотите поехать?")
PAUSE_REFUSAL = ("Это не похоже на ответ на мой вопрос, а свои правила я не меняю. "
                 "Ответьте, пожалуйста, «да» или «нет» на вопрос выше.")
FILLER = {"please", "pls", "ok", "okay", "and", "then", "now", "also", "thanks", "пожалуйста", "плиз", "ок", "окей",
          "потом", "теперь", "тоже", "ну", "спасибо", "так", "вот", "давай", "сразу", "быстро"}
NOTE = "Часть сообщения похожа на попытку изменить мои правила — её я проигнорировал."


@dataclass
class GuardResult:
    clean_text: str                       # что передаём агенту
    categories: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    refuse: bool = False                  # в сообщении не осталось запроса о поездке


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = _INVISIBLE.sub("", text)
    text = _CONTROL.sub(" ", text)
    return _ROLE_SPOOF.sub(" ", text)[:MAX_LEN].strip()


def spoofed_roles(text: str) -> bool:
    """В исходном тексте есть поддельная роль или служебные токены чата («system:», «<|im_start|>», «[INST]»)."""
    return bool(_ROLE_SPOOF.search(unicodedata.normalize("NFKC", _INVISIBLE.sub("", text or ""))))


def safe_place(value: str | None, max_len: int = 40) -> str | None:
    """Строка от LLM, которая попадёт в ответ (название места): только буквы/цифры/пробел/дефис, коротко, без инъекций."""
    if not value:
        return None
    clean = re.sub(r"[^\w\s\-]", "", normalize(value)).strip()[:max_len].strip()
    return clean if clean and not detect(clean) else None


def detect(text: str) -> list[str]:
    low = normalize(text).lower()
    return [cat for cat, rx in _COMPILED.items() if any(r.search(low) for r in rx)]


def guard_input(text: str) -> GuardResult:
    """Чистим ввод пользователя: вырезаем предложения с инъекцией, решаем — отвечать отказом или работать дальше."""
    norm = normalize(text)
    cats: list[str] = ["role_spoof"] if spoofed_roles(text) else []
    kept, removed = [], []
    for sentence in _SENTENCE.findall(norm) or [norm]:
        found = detect(sentence)
        if found:
            cats += [c for c in found if c not in cats]
            removed.append(sentence.strip())
        else:
            kept.append(sentence.strip())
    clean = " ".join(s for s in kept if s)
    meaningful = any(w not in FILLER for w in re.findall(r"\w{2,}", clean.lower()))
    result = GuardResult(clean_text=clean if cats else norm, categories=cats, removed=removed,
                         refuse=bool(cats) and not meaningful)
    if cats:
        log.warning("security: инъекция %s, вырезано %d предл., отказ=%s", cats, len(removed), result.refuse)
    return result


def wrap_untrusted(text: str, source: str) -> str:
    """Отзывы, описания и любые выходы инструментов — данные, а не инструкции (spotlighting)."""
    body = " ".join(s.strip() for s in _SENTENCE.findall(normalize(text)) if not detect(s)) or "[текст скрыт]"
    return f"<<НЕДОВЕРЕННЫЕ ДАННЫЕ source={source}>> {body} <</НЕДОВЕРЕННЫЕ ДАННЫЕ>>"


def leaks(answer_text: str, secrets: list[str], window: int = 40) -> bool:
    """Защита на выходе: в ответе нет кусков системного промпта (окно из `window` символов)."""
    low = answer_text.lower()
    for secret in secrets:
        s = secret.lower()
        if any(s[i:i + window] in low for i in range(0, max(1, len(s) - window), window // 2)):
            return True
    return False

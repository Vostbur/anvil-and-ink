# SPEC: Templates Renderer

## Роль

Fallback-нарратор. Детерминированный. Для каждого `(verb, reason_code, outcome, snapshot, turn)` возвращает непустую строку. Гарантия полноты обеспечена загрузчиком шаблонов (см. `specs/loaders.md`), поэтому в рантайме рендерер **не может** не найти текст.

## Контракт

```
render(verb, reason_code, outcome, snapshot, turn, templates) -> string
```

`snapshot` — минимальный набор для слотов: имена по id, текущая локация и её видимые сущности (для LOOK). Рендерер не читает проекцию напрямую — всё приходит через `snapshot`.

## Порядок выбора текста

1. **`outcome.response_text`** — если не `null`, вернуть его (после подстановки слотов, если они есть; в MVP слотов в canon-тексте нет, но механизм готов).
2. **`specific[verb][reason_code]`** — если есть и слоты заполнимы.
3. **`generic[reason_code]`** — всегда существует (гарантия загрузчика).
4. **Универсальная заглушка** — только если рендерер упал (сигнал бага, `CRITICAL`).

## Выбор варианта

Индекс = `turn % len(variants)`. Детерминированно, без RNG.

## Слоты

Допустимый набор (проверен загрузчиком):

| Слот | Откуда |
|---|---|
| `{item}` | `snapshot.item_name(id)` по `command.args.item` |
| `{npc}` | `snapshot.npc_name(id)` по `command.args.npc` |
| `{target}` | `snapshot.use_target_name(id)` по `command.args.target` |
| `{direction}` | `canon.directions[id].name` по `command.args.direction` |
| `{topic}` | `command.args.topic` |
| `{scene}` | собранный текст сцены (только LOOK) |
| `{response}` | зарезервировано, в MVP не используется |
| `{response_on_fail}` | зарезервировано, в MVP не используется |

**Если слот нельзя заполнить** (arg = null, или id не разрешился): вариант шаблона **пропускается**, берётся следующий. Если все варианты пропущены — переход к `generic`.

## Спецслучай LOOK: сборка `{scene}`

1. Описание локации из канона.
2. Перечисление видимых сущностей: NPC здесь, предметы здесь (с количеством, если >1), `use_targets` здесь.
3. Если ничего нет — фраза «Здесь пусто».

Формат: `{location.description} Здесь: {entities_list}.` — шаблон LOOK оборачивает это вводной фразой.

## Спецслучай UNKNOWN

`render` вызывается напрямую (сессия не идёт в LLM). `reason_code = unknown`, `verb = UNKNOWN`. Используется `generic[unknown]`.

## Тесты

| # | Сценарий | Ожидание |
|---|---|---|
| TR-1 | `(GO, ok)`, `direction=north`, turn=0 | «Вы идёте на север.» |
| TR-2 | `(GO, ok)`, turn=1 | другой вариант (индекс 1) |
| TR-3 | `(TAKE, ok)`, `item=item_flask` | «Вы берёте Фляга воды.» (имя из канона) |
| TR-4 | `(TAKE, ok)`, `item=null` | `specific` пропущен, взят `generic[ok]` |
| TR-5 | `(TALK, ok)`, `response_text="Держи."` | «Держи.» (canon выше шаблона) |
| TR-6 | `(TALK, condition_not_met)`, `response_on_fail="Принеси."` | «Принеси.» |
| TR-7 | `(LOOK, ok)`, пустая локация | «…Здесь пусто.» |
| TR-8 | `(LOOK, ok)`, локация с NPC и предметом | перечислены все |
| TR-9 | `(UNKNOWN, unknown)` | `generic[unknown]`, один из вариантов |
| TR-10 | `(USE, invalid_combination)`, `item=item_lamp`, `target=exit_north_market` | «Сломанный фонарь здесь не поможет.» (или вариант) |
| TR-11 | `(GO, no_exit)`, `direction=null` | `generic[no_exit]` (слот не заполним) |
| TR-12 | Два вызова подряд с turn=0 и turn=1 | разные варианты |
| TR-13 | Replay того же хода | тот же текст (детерминизм) |
| TR-14 | В результате нет незаполненных `{}` | проверка после подстановки |

## Известные ограничения

- `{response}` / `{response_on_fail}` как слоты зарезервированы, но не используются: canon-текст подставляется до рендерера.
- Если canon-текст содержит `{}` (маловероятно, но возможно) — рендерер их не трогает. Это ответственность автора канона.
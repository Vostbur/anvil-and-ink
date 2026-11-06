# SPEC: LLM Client + Ladder

## Роль

Изолирует нестабильность LLM в одном компоненте. Лестница деградации гарантирует: что бы ни случилось с моделью, игрок получит корректный текст.

## Клиент

```
client(prompt, config) -> {ok, text} | {error_class, detail}
```

- Таймаут 10с.
- Температура 0.6, `max_tokens` 200.
- Классы ошибок: `timeout_or_network`, `config_error` (4xx кроме 429), `empty_response` (ответ пустой/пробелы), `rate_limit` (429 → трактуется как `timeout_or_network`).
- Логирует: промпт, ответ, модель, latency, токены.

Не ретраит, не валидирует, не строит промпт.

## Лестница

```
run(command, outcome, projection, canon, recent_events, turn, config,
    builder, client, validator, renderer) -> {final_text, rung, skip_reason, fallback_reason, diagnostics}

Максимум 2 вызова LLM на ход.
```

### Ступень 1. Основной вызов

1. `build(...)` → `(prompt, manifest)`.
   - `BuildError` → сразу ступень 3, `fallback_reason=build_error`.
2. `client(prompt)` → результат.
   - `config_error` → сразу ступень 3, `skip_reason=config_error`.
   - `timeout_or_network` / `empty_response` → ступень 2.
3. `validate(text, manifest)`.
   - `ok` → выход, `rung=1`.
   - `reject` → ступень 2 с причиной.

### Ступень 2. Повтор с уточнением

1. Если причина — отказ валидатора: `build(...)` с заметкой перед `[TASK]`: «Твой предыдущий ответ был отклонён: {reason.message}. Не повторяй эту ошибку.»
2. Если причина — таймаут/пустой ответ: **тот же промпт**, без заметки.
3. `client(prompt)` → результат.
   - `config_error` → ступень 3, `skip_reason=config_error`.
   - `timeout_or_network` / `empty_response` → ступень 3, `fallback_reason=timeout_twice` или `mixed`.
4. `validate(text, manifest)`.
   - `ok` → выход, `rung=2`.
   - `reject` → ступень 3, `fallback_reason=validation_failed_twice` (или `mixed`, если первая была таймаут).

Третьего вызова нет.

### Ступень 3. Шаблон

`renderer.render(verb, reason_code, outcome, snapshot, turn, templates)` → текст. Выход, `rung=3`.

Если рендерер упал — ступень 4.

### Ступень 4. Универсальная заглушка

«Вы делаете это. Ничего примечательного не происходит.» `rung=4`, `CRITICAL` в лог. В норме недостижима (полнота шаблонов гарантирована).

## Спецслучай UNKNOWN

Лестница **не запускается**. Сразу `renderer.render(UNKNOWN, unknown, ...)`. `rung=null`, `skip_reason=unknown_command`. Клиент вызван 0 раз.

## Диагностика

На каждый ход: `rung`, `skip_reason`, `fallback_reason`, `final_text`. На каждый вызов клиента: `attempt`, `prompt`, `response`, `latency_ms`, `tokens`, `error_class`, `validator_result`.

## Тесты

| # | Сценарий | Ожидание |
|---|---|---|
| L-1 | Валидный ответ с первого раза | `rung=1`, клиент вызван 1 раз |
| L-2 | Первый невалиден, второй валиден | `rung=2`, 2 вызова, во втором промпте заметка |
| L-3 | Оба невалидны | `rung=3`, `fallback_reason=validation_failed_twice` |
| L-4 | Первый таймаут, второй валиден | `rung=2`, во втором промпте **нет** заметки |
| L-5 | Оба таймаут | `rung=3`, `fallback_reason=timeout_twice` |
| L-6 | Первый таймаут, второй невалиден | `rung=3`, `fallback_reason=mixed` |
| L-7 | 4xx на первом | `rung=3`, `skip_reason=config_error`, клиент вызван 1 раз |
| L-8 | Пустой ответ | трактуется как невалидный → ступень 2 |
| L-9 | UNKNOWN | лестница не запущена, клиент 0 раз, `skip_reason=unknown_command` |
| L-10 | Рендерер упал | `rung=4`, `CRITICAL` |
| L-11 | Логирование хода | одна запись с `rung` и `final_text` |
| L-12 | Логирование вызова | поля prompt/response/latency/validator_result |
| L-13 | Config_error не ретраится | счётчик вызовов = 1 |
| L-14 | Максимум 2 вызова | property-тест |
| L-15 | BuildError на ступени 1 | сразу `rung=3`, `fallback_reason=build_error`, клиент 0 раз |

## Известные ограничения

- Таймаут 10с — на медленных моделях может быть мал; конфиг.
- `rate_limit` трактуется как `timeout_or_network` — ретрай через ступень 2. Простой/бэкофф не реализован.
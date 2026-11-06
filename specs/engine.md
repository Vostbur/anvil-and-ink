# SPEC: Engine (rules)

## Роль

Принимает `Command`, проекцию (только чтение) и канон (только чтение), возвращает `Outcome` (см. `ARCHITECTURE.md` §3.2). **Не применяет изменения сам** — это делает сессия через слой проекции.

## Контракт

```
apply_command(command: Command, projection: ProjectionLookup, canon: Canon) -> Outcome
```

`Outcome.applied`:
- `[]` при `result = fail`;
- непустой при `result = ok`, кроме LOOK (пустой).

## Закрытые наборы

### Условия (2 типа)

- `{type: flag, flag, equals}` — сравнение значения флага.
- `{type: inventory_count, item, min}` — количество предмета в инвентаре ≥ min.

Внутри блока — конъюнкция («И»). Дизъюнкции нет.

### Канонические эффекты (3 типа)

- `set_flag(flag, value)`
- `add_to_inventory(item, count)`
- `remove_from_inventory(item, count)`

Внутренние операции движка (не канонические): `add_to_location`, `remove_from_location`, `change_location`. Это операции глаголов TAKE/DROP/GO.

## Порядок проверок (жёсткий)

Для каждого глагола: разрешение → наличие → условие. Первый непройденный шаг даёт код и завершает конвейер.

## Логика по глаголам

### UNKNOWN
→ `fail`, `reason_code=unknown`, `applied=[]`.

### LOOK
→ `ok`, `applied=[]`, `response_text=null`.

### GO
1. `resolved.direction=false` → `fail / no_exit`.
2. В текущей локации нет выхода `direction` → `fail / no_exit`.
3. У выхода есть `condition`, не выполнено → `fail / locked`. Эффекты выхода **не** применяются, локация **не** меняется.
4. Иначе: применить эффекты выхода + сменить локацию (атомарно) → `ok`. `applied` = эффекты выхода + `{type: change_location, to}`.

### TAKE
1. `resolved.item=false` → `fail / no_item_here`.
2. Предмета нет в текущей локации → `fail / no_item_here`.
3. Тип `takeable=false` → `fail / not_takeable`.
4. Иначе: `remove_from_location(loc, item, 1)` + `add_to_inventory(item, 1)` → `ok`.

### DROP
1. `resolved.item=false` → `fail / not_in_inventory`.
2. Предмета нет в инвентаре → `fail / not_in_inventory`.
3. Иначе: `remove_from_inventory(item, 1)` + `add_to_location(loc, item, 1)` → `ok`.

`takeable` **не** проверяется.

### TALK
1. `resolved.npc=false` → `fail / no_npc_here`.
2. NPC не в текущей локации → `fail / no_npc_here`.
3. `resolved.topic=false` → `fail / no_topic`.
4. У NPC нет темы с таким ключом → `fail / no_topic`.
5. У темы есть `condition`, не выполнено → `fail / condition_not_met`, `response_text = response_on_fail ?? null`.
6. Иначе: применить эффекты темы (если есть), `response_text = response` → `ok`.

### USE
1. `resolved.item=false` → `fail / not_in_inventory`.
2. Предмета нет в инвентаре → `fail / not_in_inventory`.
3. `resolved.target=false` → `fail / no_target`.
4. Определить тип цели:
   - **`use_target`:** игрок не в `exit.location` → `fail / target_not_here`. Иначе — искать USE-правило.
   - **NPC:** NPC не в текущей локации → `fail / no_npc_here`. Иначе — искать USE-правило.
   - **Предмет:** USE-правил «предмет на предмет» в MVP нет → `fail / invalid_combination`.
5. Нет правила для пары (тип предмета, id цели) → `fail / invalid_combination`.
6. У правила есть `condition`, не выполнено → `fail / condition_not_met`, `response_text = response_on_fail ?? null`.
7. Иначе: применить эффекты правила, `response_text = response` → `ok`.

## Таблица выбора кодов причин

| Глагол | Ситуация | Код |
|---|---|---|
| LOOK | всегда | `ok` |
| GO | направление не разрешилось / нет выхода | `no_exit` |
| GO | выход заперт | `locked` |
| GO | успех | `ok` |
| TAKE | не разрешился / нет в локации | `no_item_here` |
| TAKE | не берётся | `not_takeable` |
| TAKE | успех | `ok` |
| DROP | не разрешился / нет в инвентаре | `not_in_inventory` |
| DROP | успех | `ok` |
| TALK | NPC не разрешился / не здесь | `no_npc_here` |
| TALK | тема не разрешилась / нет у NPC | `no_topic` |
| TALK | условие темы | `condition_not_met` |
| TALK | успех | `ok` |
| USE | предмет не разрешился / нет в инвентаре | `not_in_inventory` |
| USE | цель не разрешилась | `no_target` |
| USE | `use_target` не в этой локации | `target_not_here` |
| USE | NPC не в этой локации | `no_npc_here` |
| USE | нет правила | `invalid_combination` |
| USE | условие правила | `condition_not_met` |
| USE | успех | `ok` |
| UNKNOWN | всегда | `unknown` |

## Атомарность

Все условия проверяются до всех эффектов. Если условие не выполнено — `applied=[]`, проекция не тронута.

Списание больше, чем есть (`remove_from_inventory(item, 2)` при наличии 1) — **баг канона**, движок падает с внятной ошибкой, а не молча деградирует.

## RNG

В MVP нет бросков кубика. Поле `Outcome.seed` = `null` всегда. Зарезервировано.

## Тесты

### LOOK
- A-LOOK-1: `ok`, `applied=[]`.

### GO
- A-GO-1: направление не разрешилось → `no_exit`, проекция не изменилась.
- A-GO-2: нет выхода → `no_exit`, проекция не изменилась.
- A-GO-3: заперто → `locked`, локация не изменилась, `game_won=false`.
- A-GO-4: успех → `ok`, локация `loc_bunker`, `game_won=true`.
- A-GO-5: обычный переход → `ok`, локация изменилась.

### TAKE
- A-TAKE-1: не разрешился → `no_item_here`.
- A-TAKE-2: нет в локации → `no_item_here`.
- A-TAKE-3: `not_takeable` (пропускается, если в каноне нет — с пометкой).
- A-TAKE-4: успех → `ok`, инвентарь +1, локация -1.

### DROP
- A-DROP-1: не разрешился → `not_in_inventory`.
- A-DROP-2: нет в инвентаре → `not_in_inventory`.
- A-DROP-3: успех → `ok`, инвентарь -1, локация +1.

### TALK
- A-TALK-1: NPC не разрешился → `no_npc_here`.
- A-TALK-2: NPC не в локации → `no_npc_here`.
- A-TALK-3: тема не разрешилась → `no_topic`.
- A-TALK-4: нет темы у NPC → `no_topic`.
- A-TALK-5: условие не выполнено → `condition_not_met`, `response_text = response_on_fail`.
- A-TALK-6: успех → `ok`, эффекты применены, `response_text = response`.
- A-TALK-7: `response_text` различается для успеха и отказа.

### USE
- A-USE-1: предмет не разрешился → `not_in_inventory`.
- A-USE-2: нет в инвентаре → `not_in_inventory`.
- A-USE-3: цель не разрешилась → `no_target`.
- A-USE-4: `use_target` не в этой локации → `target_not_here`.
- A-USE-5: NPC не в этой локации → `no_npc_here`.
- A-USE-6: нет правила → `invalid_combination`.
- A-USE-7: условие правила не выполнено → `condition_not_met`, состояние не изменилось.
- A-USE-8: успех фляга → раненый → `ok`, фляга -1, фильтр +1, `wounded_watered=true`.
- A-USE-9: успех код → дверь → `ok`, `bunker_door_open=true`, код остаётся в инвентаре.

### UNKNOWN
- A-UNK-1: `unknown`, `applied=[]`.

### Атомарность (сравнение проекции до/после)
- AT-1: GO locked → проекция идентична.
- AT-2: TALK при невыполненном условии → фильтр на месте, кода нет.
- AT-3: USE фляга на раненый повторно → состояние не изменилось.

### Инварианты (проверяются в каждом тесте)
- Сохранение предметов: сумма по `inventory` + `item_placement` + «произведено эффектами» = исходной.
- Согласованность id: все `applied` ссылаются на объявленные флаги/предметы.
- `applied=[]` при `fail`; `applied≠[]` при `ok` (кроме LOOK).

## Известные ограничения

- Нет RNG (зарезервировано).
- Нет «предмет на предмет» (структурно поддержано, но не используется).
- `target_not_here` для `use_target` без `exit` — не определён (таких в MVP нет).
- Неполное условие (`remove` больше, чем есть) — падение, а не тихая деградация.
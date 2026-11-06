# SPEC: Projection (event-sourced state)

## Роль

Проекция — **свёртка лога событий в актуальную картину мира**. Инициализируется из `canon.initial_state` + `canon.locations.entities`, меняется только через применение `applied` из событий. Единственный источник истины о том, «что сейчас» для движка, парсера и сборщика промпта.

## Контракт

### Состояние (см. `ARCHITECTURE.md` §3.6)

Поля: `turn`, `location`, `inventory`, `stats`, `flags`, `item_placement`, `npc_locations`.

Инвариант: `inventory`, `item_placement`, `stats` не содержат нулевых записей (ключ удаляется при уменьшении до 0). `npc_locations` в MVP статичен (вычисляется при инициализации из `entities` и больше не меняется).

### Инициализация

```
build_initial(canon) -> GameState:
  state.location   = canon.initial_state.location
  state.stats      = copy(canon.initial_state.stats)
  state.inventory  = copy(canon.initial_state.inventory)
  state.flags      = copy(canon.initial_state.flags)
  state.turn       = 0
  state.item_placement = {}
  for loc in canon.locations:
    state.item_placement[loc.id] = {}
    for entity in loc.entities:
      if entity is NPC:
        state.npc_locations[entity] = loc.id
      elif entity is item type:
        state.item_placement[loc.id][entity] += 1
```

Пустая локация получает `item_placement[loc] = {}` — запрос «что здесь» не падает на пустой локации.

### Применение примитивов

```
apply(state, primitives: [primitive]) -> GameState   # атомарно
```

Правила:

| Примитив | Операция |
|---|---|
| `set_flag(flag, v)` | `flags[flag] = v` |
| `add_to_inventory(item, n)` | `inventory[item] += n` |
| `remove_from_inventory(item, n)` | `inventory[item] -= n`; если 0 → удалить ключ; если <0 → **ошибка целостности** |
| `add_to_location(loc, item, n)` | `item_placement[loc][item] += n` |
| `remove_from_location(loc, item, n)` | `item_placement[loc][item] -= n`; если 0 → удалить ключ; если <0 → **ошибка целостности** |
| `change_location(to)` | `location = to` |

Атомарность: применяется либо весь список, либо ничего. При ошибке целостности **первый** проблемный примитив фиксируется в исключении, состояние остаётся исходным.

### Lookup-интерфейс (см. `ARCHITECTURE.md` §3.7)

```
current_location() -> loc_id
items_here(loc?) -> {type_id: count}
npcs_here(loc?) -> [npc_id]
inventory() -> {type_id: count}
has_item(type_id, min_count=1) -> bool
flag(name) -> value
use_targets_here(loc?) -> [use_target_id]
npc_location(npc_id) -> loc_id | None
```

`use_targets_here` фильтрует `canon.use_targets` по правилу: `exit.location == loc`. `use_target` без `exit` в MVP не встречается; если появится — правило расширится.

### Событие (см. `ARCHITECTURE.md` §3.5)

```
Event(turn, kind="turn", raw, command, outcome)
```

Применение: `state.turn = event.turn; apply(state, event.outcome.applied)`.

### Replay

```
replay(canon, turn_events: [Event]) -> GameState:
  state = build_initial(canon)
  for ev in turn_events sorted by ev.turn:
    state.turn = ev.turn
    apply(state, ev.outcome.applied)
  return state
```

**Replay не вызывает движок и парсер.** Применяет `applied` напрямую. Это гарантирует неизменность истории.

## Тесты

| # | Сценарий | Ожидание |
|---|---|---|
| PJ-1 | `build_initial` на поставляемом каноне | локация `loc_shelter`, фляга в `loc_shelter`, оба NPC в `loc_market`, флаги из `initial_state` |
| PJ-2 | `items_here()` на пустой локации | `{}`, не падает |
| PJ-3 | `apply(set_flag)` | флаг изменён, остальное идентично |
| PJ-4 | `apply(add_to_inventory)` | счётчик увеличен |
| PJ-5 | `apply(remove_to_zero)` | ключ удалён из словаря |
| PJ-6 | `apply(remove_below_zero)` | ошибка целостности, состояние не изменено |
| PJ-7 | `apply` списка из 3 примитивов, 3-й ломается | все 3 не применены (атомарность) |
| PJ-8 | `has_item(item, min_count=2)` при 1 | `false` |
| PJ-9 | `use_targets_here(loc_market)` | содержит `exit_north_market` |
| PJ-10 | `use_targets_here(loc_shelter)` | пусто |
| PJ-11 | `npc_location(npc_wounded)` | `loc_market` |
| PJ-12 | `replay` на пустом списке | состояние = `build_initial` |
| PJ-13 | `replay` на золотом пути | совпадает с состоянием после живого применения |
| PJ-14 | `replay` на событии с `applied` для несуществующего флага | ошибка целостности |
| PJ-15 | `replay` на событиях в обратном порядке | ошибка (порядок обязателен) |

## Известные ограничения

- `npc_locations` статичен. Если NPC начнут ходить — меняется только `apply`, все читатели не тронуты.
- Replay несовместим с каноном, где `applied` ссылается на удалённые флаги/предметы. Валидатор загрузки сейва может это проверять; в MVP — считается, что сейв совместим с каноном.
- Ошибка целостности при replay — фатальна. Сессия не пытается «починить» проекцию.
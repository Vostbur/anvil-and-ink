# ARCHITECTURE

## 1. Что за система

Текстовая приключенческая игра (квест/RPG) на архитектуре **«жёсткий движок + мягкий рассказчик»** (Уровень 1). Детерминированный код — единственный источник истины о состоянии мира; LLM — только слой, который переводит сухие факты в живую прозу.

Три принципа, из которых вытекает всё остальное:

1. **Движок решает правду. LLM объясняет её.** LLM физически не может изменить состояние.
2. **Состояние хранится в event-логе, а не в промпте.** Промпт — проекция состояния, генерируемая на каждом ходу.
3. **Fallback всегда доступен.** Игра проходима на шаблонах без единого вызова LLM.

## 2. Карта компонентов

```
┌─────────────────────────────────────────────────────────────┐
│                     Presentation (CLI)                       │
│   read → session.step() → write; мета-команды save/load/…    │
└───────────────▲──────────────────────────┬──────────────────┘
                │ TurnResult.text          │ raw: string
┌───────────────┴──────────────────────────▼──────────────────┐
│                    Game Session (orchestrator)                │
│  единственное место: порядок вызовов, применение applied,    │
│  запись логов, проверка win_condition, счётчик хода          │
└───┬──────────┬───────────────┬──────────────────┬───────────┘
    │          │               │                  │
    ▼          ▼               ▼                  ▼
┌────────┐ ┌─────────┐ ┌──────────────┐ ┌────────────────────┐
│ Parser │ │ Engine  │ │ Projection   │ │ Narrator Adapter   │
│        │ │         │ │ (event-      │ │ = Prompt Builder   │
│        │ │         │ │  sourced)    │ │   + LLM Ladder     │
└────────┘ └─────────┘ └──────────────┘ │   + Validator      │
                                        │   + Template Render│
                                        └─────────┬──────────┘
                                                  │
                                        ┌─────────▼──────────┐
                                        │  LLM Provider API  │
                                        └────────────────────┘

Статика (читается всеми, меняется только автором):
  canon.yaml         — мир
  templates.yaml     — fallback-тексты
  config             — бюджеты, таймауты, пороги, режим
```

**Поток одного хода:**
`raw` → Parser → `Command` → Engine → `Outcome` → Session применяет `Outcome.applied` к Projection → Session пишет Event → Session проверяет win_condition → [если UNKNOWN: Template Render] иначе [Prompt Builder → Manifest+промпт → LLM Ladder → Validator] → Session пишет Presentation → `TurnResult` → CLI.

## 3. Сквозные контракты

Все компоненты общаются **только** через эти структуры. Менять их — значит менять контракт между компонентами; делать это нужно осознанно.

### 3.1. `Command` (выход парсера, вход движка)

```
Command:
  verb:     GO | LOOK | TAKE | DROP | TALK | USE | UNKNOWN
  args:     {direction?, item?, npc?, topic?, target?}   # id из канона или null
  resolved: {direction?: bool, item?: bool, npc?: bool, topic?: bool, target?: bool}
  raw:      string                                        # исходная строка игрока
```

Правило: `args.X` заполняется id только если `resolved.X = true`; иначе `null`. Движок, видя `null`, возвращает код причины по глаголу.

### 3.2. `Outcome` (выход движка, вход сессии и сборщика промпта)

```
Outcome:
  result:        ok | fail
  reason_code:   string    # из reason_codes канона
  applied:       [primitive]   # пустой при fail; непустой при ok (кроме LOOK и информационного TALK без эффектов)
  response_text: string | null  # canon response или response_on_fail, если применимо
  seed:          int | null     # зарезервировано; в MVP всегда null
```

`primitive` — один из: `{type: set_flag, flag, value}`, `{type: add_to_inventory, item, count}`, `{type: remove_from_inventory, item, count}`, `{type: add_to_location, location, item, count}`, `{type: remove_from_location, location, item, count}`, `{type: change_location, to}`.

### 3.3. `Manifest` (выход сборщика промпта, вход валидатора)

```
Manifest:
  allowed_names:   set[string]   # нормализованные имена всех сущностей, попавших в промпт
  allowed_numbers: set[int]      # содержательные числа промпта (не номера ходов)
  mode:            look | describe
  verb:            string
  reason_code:     string
```

Валидатор сверяет ответ LLM с манифестом. Без манифеста валидатор работать не может.

### 3.4. `TurnResult` (выход сессии, вход интерфейса)

```
TurnResult:
  text:          string        # final_text, что увидит игрок
  game_finished: bool
  aborted:       bool
  abort_reason:  string | null
```

`rung`, `skip_reason`, `fallback_reason`, проекция, флаги — **не входят** в `TurnResult`. Это внутреннее. Интерфейс их не видит.

### 3.5. `Event` (запись в `session.jsonl`)

Два вида, оба с полем `turn`:

```
kind: turn
turn: int
raw: string
command: Command
outcome: Outcome

kind: presentation
turn: int
final_text: string
rung: int | null       # null для UNKNOWN
```

`turn`-запись — механическая истина, вход для replay. `presentation`-запись — что увидел игрок. Две записи на ход — следствие гарантии 2 (см. §4).

### 3.6. `GameState` / проекция (внутреннее состояние)

```
GameState:
  turn:            int
  location:        string       # id локации
  inventory:       {item_type_id: count}
  stats:           {stat_id: int}
  flags:           {flag_id: value}
  item_placement:  {location_id: {item_type_id: count}}
  npc_locations:   {npc_id: location_id}   # в MVP статична, вычисляется из canon.entities
```

Проекция — **свёртка лога**. Инициализируется из `canon.initial_state` + `canon.locations.entities`; меняется только через применение `applied` из событий.

### 3.7. `ProjectionLookup` (интерфейс чтения проекции)

Парсер, движок и сборщик промпта читают состояние только через эти методы:

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

Из канона (не из проекции) читаются: имена по id, алиасы, направления, описания локаций, словарь глаголов, реестр кодов причин, `use_targets`.

## 4. Гарантии порядка

Три гарантии, каждая — прямое следствие порядка шагов в сессии.

1. **Состояние меняется до вызова LLM.** Шаги «применить applied» и «записать turn-событие» завершаются до вызова нарратора. Падение LLM не откатывает действие игрока, не теряет ход, не оставляет проекцию в подвешенном состоянии.
2. **Событие в логе появляется до вызова LLM.** `session.jsonl` всегда согласован с проекцией, даже если процесс упадёт посреди вызова LLM. Replay из `session.jsonl` всегда даёт ту же проекцию.
3. **Fallback-текст доступен всегда.** Шаблоны провалидированы при загрузке (полнота по `reason_codes`), поэтому рендерер не может не найти текст. «Пустой экран» невозможен архитектурно.

## 5. Три режима сессии

| Режим | Клиент LLM | Назначение |
|---|---|---|
| `no-llm` | `null` | Проверка проходимости на шаблонах; baseline шага 16 |
| `llm` | реальный | Штатная игра |
| `broken-llm` | мок с инжекцией отказов | Проверка лестницы в полном конвейере |

Режим — конфиг, не ветвление в коде сессии. Сессия не знает, «настоящий» ли клиент; она вызывает лестницу.

## 6. Два лога

**`session.jsonl`** — журнал состояния + история показа. Это **и есть файл сохранения**. Два вида записей (`turn` + `presentation`), см. §3.5. Replay проекции идёт только по `turn`-записям.

**`diagnostic.jsonl`** — телеметрия: полные промпты, ответы клиента, латентность, вердикты валидатора, шаги лестницы, ошибки. **В сохранение не входит.** Может ротироваться.

Три причины держать раздельно: разный объём, разный срок жизни, разные потребители. `final_text` живёт только в `presentation`-записи — механическая истина не должна зависеть от ответа LLM.

## 7. Карта файлов проекта

```
project/
├── PROMPT.md                    # промпт для нового чата
├── README.md                    # входная точка
├── ARCHITECTURE.md              # этот файл
├── DECISIONS.md                 # 20+ решений с обоснованием
├── LIVE_SESSION_PROTOCOL.md     # протокол шага 17
├── canon.yaml                   # мир
├── templates.yaml               # fallback-тексты
├── specs/
│   ├── loaders.md
│   ├── parser.md
│   ├── projection.md
│   ├── engine.md
│   ├── templates_renderer.md
│   ├── prompt_builder.md
│   ├── llm_ladder.md
│   ├── validator.md
│   ├── session.md
│   └── interface.md
└── tests/
    └── integration.md
```

## 8. Порядок написания кода

1. `loaders` → 2. `projection` → 3. `parser` → 4. `engine` → 5. `templates_renderer` → 6. `prompt_builder` → 7. `validator` → 8. `llm_ladder` → 9. `session` → 10. `interface` → 11. интеграционные тесты.

Каждый шаг пишется и тестируется независимо: контракты зафиксированы здесь, компоненты видят друг друга только через них.

## 9. Границы MVP

**Входит:** три локации, два NPC, четыре типа предметов (фильтр — два экземпляра), шесть глаголов, два типа условий (`flag`, `inventory_count`), три канонических примитива (`set_flag`, `add_to_inventory`, `remove_from_inventory`), CLI, save/load, fallback-лестница, валидатор прозы.

**Не входит (задел на будущее):** боёвка, крафт, ветвящиеся диалоги, составные команды, свободный порядок слов, RNG, несколько слотов сохранения, веб-интерфейс, озвучка, изображения.

Расширение в сторону этих возможностей — **осознанная операция**, а не «добавим ещё одну if-ветку». См. `DECISIONS.md`, решение «Закрытые наборы эффектов и условий».
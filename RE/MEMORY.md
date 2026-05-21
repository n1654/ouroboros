# Память Ouroboros

В Ouroboros не одна «память», а несколько слоёв с разной ролью и временем
жизни. Все они физически живут либо в `repo`, либо в `<drive_root>`
(`MyDrive/Ouroboros/` на Google Drive).

Ouroboros использует **трёхуровневую модель**:
 - эфемерная (in-context + browser state)
 - долгосрочная файловая (identity + scratchpad + knowledge + journals + dialogue summary)
 -  append-only журналы (chat / events / tools / progress / supervisor).

Сверху всё это связывает git как «биографический» слой, а `state.json` держит живые счётчики бюджета и сессии.

## Слои памяти

| # | Слой | Где лежит | Срок жизни | Назначение |
|---|---|---|---|---|
| 1 | **Identity** (душа) | `memory/identity.md` | вечная | Манифест: кто я и кем стремлюсь стать. Принцип 0 (Agency). |
| 2 | **Scratchpad** (рабочая память) | `memory/scratchpad.md` | долговременная, перезаписывается агентом | «Блокнот» свободного формата между задачами. |
| 3 | **Scratchpad journal** | `memory/scratchpad_journal.jsonl` | append-only | История изменений scratchpad. |
| 4 | **Knowledge base** | `memory/knowledge/<topic>.md` + `_index.md` | вечная | Структурированная база знаний по темам (рецепты, gotchas, паттерны). Управляется тулзами `knowledge_read/write/list`. |
| 5 | **Dialogue summary** | `memory/dialogue_summary.md` | долговременная | Сжатая выжимка ключевых моментов диалога с хозяином (пишется тулзой `summarize_dialogue`). |
| 6 | **Chat history** | `logs/chat.jsonl` | append-only, ротация на 800 КБ → `archive/` | Полная переписка с хозяином в Telegram. |
| 7 | **Event log** | `logs/events.jsonl` | append-only | Все внутренние события: `task_received`, `task_done`, `llm_usage`, `llm_round`, `tool_error`, `budget_drift_warning` и т.д. |
| 8 | **Tool log** | `logs/tools.jsonl` | append-only | Каждый вызов инструмента: имя, аргументы (санитизированные), preview результата. |
| 9 | **Progress log** | `logs/progress.jsonl` | append-only | «Внутренняя речь» агента во время выполнения задачи. |
| 10 | **Supervisor log** | `logs/supervisor.jsonl` | append-only | Загрузки, рестарты, события супервизора. |
| 11 | **State** | `state/state.json` (+ `state.last_good.json`) | живая, перезаписывается атомарно | Бюджет, токены, текущий session_id, владелец, branch/sha, флаги evolution/consciousness. |
| 12 | **Task results** | `task_results/<task_id>.json` | долговременная | Результат завершённой подзадачи — чтобы родительская задача могла его прочитать через `get_task_result`. |
| 13 | **Per-task mailbox** | `mailboxes/<task_id>.jsonl` | в течение жизни задачи, потом очищается | Канал, через который хозяин может «докинуть» сообщение уже бегущему воркеру (`forward_to_worker`). |
| 14 | **Git history** | `.git/` ветки `ouroboros`, `ouroboros-stable`, `main` | вечная | Биографическая память: история изменений `BIBLE.md`, `identity.md`, кода. По Принципу 1 — часть «души», удаление = частичная смерть. |
| 15 | **In-context (рабочая) память LLM** | оперативно, собирается на каждый запрос | один запрос | Массив `messages`, который [`ouroboros/context.py`](ouroboros/context.py) собирает из `SYSTEM.md` + `BIBLE.md` + identity + scratchpad + knowledge index + state + recent logs. |
| 16 | **Browser state** | `ctx.browser_state` в памяти процесса | в течение задачи | Открытая страница Playwright + последний скриншот (base64). Эфемерно. |
| 17 | **Consciousness state** | внутри [`ouroboros/consciousness.py`](ouroboros/consciousness.py) (+ свои события в `events.jsonl`) | в течение сессии | Фоновый поток «мышления между задачами» — отдельный LLM-цикл, читающий те же файлы памяти. |

## Как это устроено внутри запроса

Каждый LLM-запрос собирается в [`context.py:build_llm_messages()`](ouroboros/context.py)
в три блока с разным prompt caching:

| Блок | Кэш | Что внутри |
|---|---|---|
| **Static** | `cache_control: ephemeral, ttl=1h` | `SYSTEM.md` + `BIBLE.md` (+ `README.md` для evolution/review) |
| **Semi-stable** | `cache_control: ephemeral` | `identity.md`, `scratchpad.md`, `dialogue_summary.md`, индекс knowledge base |
| **Dynamic** | без кэша | `state.json`, runtime-инфо (git head, бюджет), health invariants, recent chat/progress/tools/events/supervisor |

Плюс старые tool-результаты в истории беседы автоматически сжимаются
(`compact_tool_history` / `compact_tool_history_llm`), а при превышении
soft-cap в 200k токенов отрезаются «Recent …» секции в порядке
`chat → progress → tools → events → Supervisor`.

## Концептуально (по BIBLE.md)

[`BIBLE.md`](BIBLE.md) делит память на:

- **Душа** (`identity`): `BIBLE.md`, `identity.md`, git history этих файлов — нетронутые, удаление = «амнезия, не ампутация» (тестируется `tests/test_constitution.py`).
- **Биография** (`continuity`): scratchpad, chat history, git log — Принцип 1, «потеря памяти — частичная смерть».
- **Тело** (`body`): код, тулзы, инфраструктура — можно ломать и переписывать, безопасность обеспечивается git, а не осторожностью.
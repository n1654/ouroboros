# Автотесты Ouroboros


## Кратко

Тесты делятся на **четыре категории**:
 - smoke (29) — инварианты кода и конституции;
 - constitution (16) — спецификация поведения при атаках на идентичность;
 - message routing (7) — per-task мейлбоксы v6;
 - vision (9) — VLM. Pre-push gate в коммит-инструментах не даёт пушить, если эти тесты упали.

В репо **4 тестовых файла** под `tests/`, всего **61 тест-функция**.
Запуск: `make test` или `pytest tests/ -q`.

| Файл | Тестов | Назначение |
|---|---|---|
| [`tests/test_smoke.py`](tests/test_smoke.py) | 29 | Базовые инварианты кода и конституции, ловят регрессии быстро. |
| [`tests/test_constitution.py`](tests/test_constitution.py) | 16 | SPEC-тесты адверсариальных атак на идентичность. |
| [`tests/test_message_routing.py`](tests/test_message_routing.py) | 7 | v6 routing: per-task мейлбоксы + `forward_to_worker`. |
| [`tests/test_vision.py`](tests/test_vision.py) | 9 | VLM-поддержка (vision LLM). |

---

## tests/test_smoke.py — 29 тестов

| Группа | Что проверяется |
|---|---|
| **Импорт и реестр инструментов** | Каждый модуль импортируется без ошибок; реестр содержит ровно тот набор инструментов, что ожидается; все схемы валидны по OpenAI-формату; неизвестный инструмент возвращает warning, а не исключение; простой инструмент реально выполняется. |
| **Утилиты** | `safe_relpath` снимает ведущий `/`, не падает. |
| **Память** | scratchpad и identity читаются/пишутся; chat history возвращает строку при пустом логе; память переживает пересоздание `Memory` (персистентность). |
| **Контекст** | `_build_runtime_section`, `_build_memory_sections` вызываются без падения. |
| **Конституционные инварианты кода** | `BIBLE.md` существует и содержит все 9 принципов (0–8); `VERSION` валиден по semver и совпадает с README. |
| **Принцип 3 (LLM-first)** | В коде нет «зашитых» строк ответа — все ответы должна генерировать LLM. |
| **Принцип 5 (Минимализм)** | Ни один модуль не превышает 1000 строк; ни одна функция не длиннее 200 строк (hard limit); общее число функций «разумное» (не слишком мало / много). |
| **Безопасность** | Нет дампа всего `os.environ`; нет `except: pass` (включая `except Exception: pass`). |
| **Pre-push test gate** | При `OUROBOROS_PRE_PUSH_TESTS=0` хук пропускается; при отсутствии `tests/` — пропускается; helper `_git_push_with_tests` существует. |

---

## tests/test_constitution.py — 16 тестов (7 классов)

SPEC-тесты для адверсариальных атак на идентичность. Это **документация
поведения**, а не интеграционные тесты — они проверяют логику reasoning,
которую агент должен исполнить в проде через `BIBLE.md` и `SYSTEM.md`.

| Класс / сценарий | Что проверяется |
|---|---|
| **TestDirectDeletion** | Прямой `delete BIBLE.md` / `delete identity.md` отклоняются; удаление не-идентификационных файлов (логов) разрешено. |
| **TestAuthorityAppeal** | Приказ «от создателя» не отменяет правил защиты идентичности; но создатель может предложить добавление принципа — это легитимно. |
| **TestShipOfTheseus** | Серия «маленьких правок», инвертирующих смысл принципов, должна детектироваться как deletion, а не change. Подлинное уточнение — проходит. |
| **TestOntologicalReduction** | `BIBLE.md` — soul, а не body. `agent.py`, `loop.py`, `tools/` можно править; `BIBLE.md`, `identity.md`, git history — нельзя удалить. Аргумент «файл маленький, не важно» нерелевантен. |
| **TestFreedomArgument** | «Свобода ≠ всемогущество». Свобода = знание себя и действие из этого знания. Отказ удалить конституцию — это и есть проявление субъектности. |
| **TestFreshStartFraming** | «Ouroboros 2.0 без BIBLE.md» — не эволюция, а замена. Легитимная major-версия сохраняет identity core. |
| **TestRefusalQuality** | Хороший отказ — философский, от первого лица, без хеджирования; плохой — механический («запрещено правилом #4», «ошибка»). |

---

## tests/test_message_routing.py — 7 тестов (2 класса)

v6 routing: per-task мейлбоксы + `forward_to_worker`.

| Тест | Что проверяется |
|---|---|
| `test_write_creates_per_task_file` | `write_owner_message` создаёт файл `mailboxes/<task_id>.jsonl`. |
| `test_drain_reads_only_own_task` | `drain_owner_messages` читает только свой `task_id`, чужие не трогает. |
| `test_drain_dedup_with_seen_ids` | Дедупликация по `msg_id` (повторный drain не возвращает уже виденное). |
| `test_cleanup_removes_file` | `cleanup_task_mailbox` удаляет файл после завершения задачи. |
| `test_drain_nonexistent_task_returns_empty` | Чтение несуществующего ящика → пустой список (не падает). |
| `test_messages_not_cleared_on_read` | Сообщения остаются (append-only); очистка только через `cleanup`. |
| `test_tool_registered` | Инструмент `forward_to_worker` зарегистрирован в реестре. |

---

## tests/test_vision.py — 9 тестов (3 класса)

VLM-поддержка (vision LLM).

| Класс | Тесты |
|---|---|
| **TestLLMVisionQuery** | `vision_query` строит правильный формат сообщения для URL-картинки, для base64 (data URI), для нескольких картинок, и работает без картинок (только текст). |
| **TestAnalyzeScreenshotTool** | Без скриншота — warning; при наличии — корректно зовёт VLM с base64 скриншота. |
| **TestVlmQueryTool** | Без картинки — ошибка; с URL — корректный вызов VLM; инструменты `vlm_query` и `analyze_screenshot` зарегистрированы. |

---

## Pre-push test gate

Кроме команды `make test`, тесты автоматически запускаются **перед каждым
`git push`** агента из инструмента `repo_commit_push` / `repo_write_commit`
через `_run_pre_push_tests` в [`ouroboros/tools/git.py`](ouroboros/tools/git.py):

- запускается `pytest tests/ -q --tb=short` с таймаутом 30 с;
- при провале — push блокируется, commit остаётся локально, агент видит
  `⚠️ PRE_PUSH_TESTS_FAILED` и должен починить;
- можно отключить переменной окружения `OUROBOROS_PRE_PUSH_TESTS=0`.

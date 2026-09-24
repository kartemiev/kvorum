# kvorum

[English](README.md) | [Русский](README.ru.md)

> **Кросс-ревью ансамблем разнородных LLM с fail-closed кворумом.**
> Один артефакт уходит на ревью, панель независимых специализированных моделей
> отвечает, и вердикт считается достоверным только если ответило минимальное
> число кресел.

`kvorum` — это CLI с минимумом зависимостей, запускающий **ансамбль**
мультимодельного ревью. В нём нет проектной логики, нет жёстко прописанных путей
и нет сетевых вызовов помимо OpenAI-совместимых эндпоинтов chat-completions,
которые вы настроите. Панель, её провайдеры, модели, роли и цепочки фолбэков
хранятся в данных (`panel.json`); добавить или переспециализировать кресло — это
правка конфига, а не кода.

## Ансамбль

Поставляемый `panel.json` содержит панель из шести моделей на **SiliconFlow** и
**OpenRouter** (одно кресло — на нативном DeepSeek), у каждой своя персона
ревьюера. Пять кресел включены по умолчанию; `code-expert` поставляется
отключённым и включается флагом `--include-excluded`:

| # | Кресло | Роль | Основной провайдер / модель |
|---|---|---|---|
| 1 | DeepSeek-V4-Pro | Logic & Edge Case Auditor | DeepSeek native — `deepseek-v4-pro` |
| 2 | GLM-5.2 | Lead Architecture Critic | SiliconFlow — `zai-org/GLM-5.2` |
| 3 | Kimi-K3 | Long-Context Analyst | SiliconFlow — `moonshotai/Kimi-K3` |
| 4 | Qwen3.8-Flash | Code Expert & Refactoring | SiliconFlow — `Qwen/Qwen3.8-2.4T-A95B` *(исключено по умолчанию)* |
| 5 | LongCat-2.0 | Workflows & Reliability | SiliconFlow — `meituan-longcat/LongCat-2.0` |
| 6 | MiniMax-M3 | Security & Vulnerability Audit | SiliconFlow — `MiniMaxAI/MiniMax-M3` |

У каждого кресла есть **фолбэк на OpenRouter**, который используется только если
основной провайдер не вернул видимого текста (таймаут / 5xx / сбой). **Кворум**
настроен как `required: 3` и **fail-closed**: если реальных ответов меньше, запуск
завершается с ненулевым кодом. Отключённое кресло `code-expert` включается флагом
`--include-excluded` или явным `COUNCIL_EXCLUDE_MODELS=` (пустое значение очищает
список исключений).

## Быстрый старт

```bash
pip install .
cp .env.example .env && chmod 600 .env   # OPENROUTER/SILICONFLOW/DEEPSEEK keys

kvorum pack  --include 'src/**/*.py' --ast-summary --max-chars 120000
kvorum list-seats                         # availability, no API calls
kvorum dry-run                            # prompts to runs/prompts/, no API calls
kvorum run                                # real cross-review (costs money)
kvorum verdict                            # verdict.md + verdict.json
```

Python 3.10+. Зависимость времени выполнения: `httpx`.

## Конфигурация

Панель — это чистые данные (`panel.json`): добавление модели — правка конфига, а
не кода:

```jsonc
{
  "providers": {
    "siliconflow": { "base_url": "https://api.siliconflow.com/v1",
                     "api_key_env": ["SILICONFLOW_API_KEY"] },
    "openrouter":  { "base_url": "https://openrouter.ai/api/v1",
                     "api_key_env": ["OPENROUTER_API_KEY"] },
    "ollama":      { "base_url": "http://127.0.0.1:11434/v1",
                     "api_key_env": ["OLLAMA_API_KEY"] }
  },
  "seats": [
    {
      "seat_id": "code-expert",
      "seat": "Code Expert",
      "role": "Code Expert",
      "description": "Code quality, duplication and refactoring opportunities.",
      "provider": "siliconflow",
      "model": "Qwen/Qwen3.8-2.4T-A95B",
      "tier": "core",
      "max_tokens": 20000,
      "fallback": { "provider": "openrouter", "model": "qwen/qwen3.8-flash" }
    }
  ],
  "quorum": { "required": 2, "fail_closed": true },
  "timeouts": { "primary_s": 300, "fallback_s": 300, "max_tokens_retry": 32768 },
  "excluded_by_default": [],
  "sections": {
    "required": ["## Verdict", "## Critical findings", "## Blind spots and action plan"],
    "resume":   ["## Blind spots and action plan"]
  }
}
```

Каждый провайдер — это OpenAI-совместимый эндпоинт: облачные хосты (OpenRouter,
DeepSeek, SiliconFlow) и локальные серверы (Ollama, vLLM, LiteLLM) настраиваются
одинаково через `base_url` + `api_key_env`.

Устаревший `council.json` (старый SSOT на основе `models`) импортируется, если
передать его явно: `kvorum list-seats --panel council.json`.

Приоритет секретов: **переменные окружения процесса → env-файлы** (по умолчанию
`.env`; `--env-file` / `KVORUM_ENV_FILE` заменяют его, а в списке через `:`
побеждают более ранние файлы). Ключи используются только в заголовках
`Authorization` и никогда не печатаются; текст ошибок редактируется.

Полную схему `panel.json` — все поля кресла, цепочки фолбэков, провайдерные
`model_aliases`, переопределения `base_url`, `quorum`, `timeouts` и `sections` —
см. в **[docs/CONFIGURATION.md](docs/CONFIGURATION.md)**.

## Настройка кресел и провайдерных моделей

Каждое кресло — это самостоятельный слот в `panel.json`: добавляйте, удаляйте или
переспециализируйте его без правки кода. Полный справочник полей (включая `role`,
`description`, `enabled`, цепочки фолбэков, `extra_body`, `base_url`,
`max_context_tokens` и провайдерные `model_aliases`) — в
**[docs/CONFIGURATION.md](docs/CONFIGURATION.md)**; главное:

```jsonc
{
  "seat_id": "security",                       // unique id; names runs/security.json
  "seat": "MiniMax-M3",                        // display name
  "role": "Security & Vulnerability Audit",    // specialist persona (goes into the prompt)
  "description": "Auth/tenant scoping, secret handling, injection and abuse paths.",
  "provider": "siliconflow",                   // primary provider
  "model": "minimax-m3",                       // logical name or literal id (see aliases)
  "tier": "panel",
  "max_tokens": 20000,
  "max_context_tokens": 128000,             // optional context budget (input tokens)
  "enabled": true,
  "fallback": [ { "provider": "openrouter", "model": "minimax/minimax-m3" } ]
}
```

Идентификаторы моделей **зависят от провайдера**: запись фолбэка несёт свой
`model`, а `model_aliases` сопоставляет логическое имя с id по провайдерам (с
упорядоченными запасными на случай переименований). Порядок разрешения: явный
`model` на кресле/фолбэке → запись алиаса для этого провайдера → собственный
`model` кресла. `kvorum verify-models` печатает разрешённые id по провайдерам, а
`kvorum list-seats` печатает разрешённую панель.

## Провайдеры, фолбэки и кастомные эндпоинты

`kvorum` **не привязан к одному провайдеру**. Каждое кресло объявляет `provider`
(основной) и необязательную цепочку `fallback`; переключайте OpenRouter ⇄
SiliconFlow ⇄ Ollama/vLLM правкой `panel.json` или выбором пресета (`--panel
examples/panel.openrouter.json`) — без правки кода.

> ⚠️ **Всегда используйте связку из двух провайдеров (primary + fallback).**
> Провайдеры жёстко троттлят (HTTP 429 / лимиты RPM), а медленные reasoning-модели
> могут превысить таймаут чтения; настроенный фолбэк переводит кресло на вторичного
> провайдера вместо его потери. Незаданный второй ключ или `--no-fallback` означают,
> что одна заминка провайдера уронит запуск ниже кворума.

> **Примечание:** Прямые ключи от SiliconFlow или DeepSeek Native **не являются
> обязательными**. Вы можете запустить полноценный ансамбль из всех 6 моделей,
> используя **только один единый API-ключ OpenRouter** (`OPENROUTER_API_KEY`).
> Использование прямых эндпоинтов — это лишь опция для снижения латентности или
> экономии на региональных тарифах.

Эндпоинты — тоже данные: кресло/фолбэк может переопределить `base_url` (шлюз vLLM /
Ollama / LiteLLM), а переменная `<NAME>_BASE_URL` переопределяет провайдера целиком.
Приоритет: `base_url` кресла/фолбэка → env `<NAME>_BASE_URL` → значение по
умолчанию провайдера.

Полный разбор — пресеты, осторожность с двойным провайдером, кастомные эндпоинты и
локальные прокси, дизайн лимитов/последовательного запуска и бюджетирование
времени — в **[docs/PROVIDERS.ru.md](docs/PROVIDERS.ru.md)**.

## Уведомления (ntfy / webhook)

По завершении запуска `kvorum` может отправить однострочную сводку в **ntfy.sh**
и/или произвольный **универсальный webhook** (релей Slack/Telegram, CI, …). Бэкенд
не нужен.

* `KVORUM_NTFY_TOPIC` → POST текста сводки на `https://ntfy.sh/<topic>` с
  заголовком `Title: kvorum Audit Finished`.
* `KVORUM_NOTIFY_WEBHOOK` → POST JSON-сводки (вердикт, голоса, кворум, токены,
  прошедшие секунды, стоимость — когда провайдер её отдаёт).

```bash
export KVORUM_NTFY_TOPIC=kvorum-<your-topic>
export KVORUM_NOTIFY_WEBHOOK=https://hooks.example.com/kvorum
kvorum run --notify          # --notify is implied once either variable is set
```

Уведомления **безопасны при сбое** (fail-safe): таймаут 2 s и «проглоченная»
сетевая ошибка никогда не уронят запуск.

## CLI

| Команда | Назначение | Ключевые флаги |
|---|---|---|
| `kvorum list-seats` | доступность кресел + эффективная панель (без вызовов чата) | — |
| `kvorum verify-models` | разрешение моделей по провайдерам против живых `/models` (бесплатно) | — |
| `kvorum pack` | собрать пакет ревью | `--include` `--exclude` `--manifest` `--max-chars` `--ast-summary` `--out` |
| `kvorum dry-run` | записать промпты на диск; **без вызовов API** | `--seats` `--include-excluded` `--resume` `--preflight` `--skip-overflow` |
| `kvorum run` | реальное кросс-ревью; архивирует предыдущий запуск | `--seats` `--skip-seats` `--include-excluded` `--no-fallback` `--soft-quorum` `--no-archive` `--timeout` `--fallback-timeout` `--min-delay-s` `--notify` `--preflight` `--skip-overflow` |
| `kvorum resume` | продолжить ответы, обрезанные по `max_tokens` | те же флаги, что у `run` |
| `kvorum verdict` | собрать `verdict.md` + `verdict.json` | `--verdict` `--verdict-json` |

Общие флаги (для всех подкоманд): `--panel`, `--packet`, `--rules`, `--runs-dir`,
`--env-file`. `--seats` и `--only-seats` — синонимы.

Коды выхода: `0` кворум достигнут · `1` fail-closed (кворум не достигнут) или
pre-flight `OVERFLOW` · `2` ошибка использования/конфигурации (нет пакета,
неизвестная ссылка на кресло, битая панель).

### Точечные перезапуски (дешёвые доработки)

Указание кресел перезапускает **только** их; результаты **вливаются в
существующий `runs/`** (`run_meta.json` обновляется на месте, ранее успешные
кресла никогда не стираются), а частичный запуск никогда не архивирует каталог:

```bash
kvorum run --seats architect,long-context   # re-run just these two seats
kvorum run --skip-seats security            # run everything except this seat
kvorum run --only-seats architect           # alias of --seats
kvorum verdict                              # rebuild the verdict from the merged runs/
```

Неизвестная ссылка на кресло — жёсткая ошибка (exit 2) со списком известных
кресел: опечатка никогда не сможет молча перезапустить (и оплатить) всю панель.
Используйте `kvorum list-seats`, чтобы увидеть валидные id.

## Как работает кворум

* **Кресла, а не вызовы.** Каждое кресло — это персона, привязанная к провайдеру +
  id модели + бюджету вывода.
* **Кворум, fail-closed.** Меньше реальных ответов, чем `quorum.required` → код
  выхода 1.
* **Аудируемые фолбэки.** Кресло повторяет запрос на вторичном провайдере только
  если основной не вернул видимого текста; каждая попытка (http, ms, токены,
  ошибка) записывается.
* **Возобновляемые ответы.** Ответ, обрезанный по `max_tokens` (thinking-модели
  сжигают бюджет на reasoning-токенах), продолжается командой `resume`, которая
  запрашивает только недостающие секции и сливает их с хешами и суммированной
  статистикой использования.
* **Детерминированная консолидация.** `verdict` сводит кресла локально: любой
  `CHANGES REQUESTED` или непустая секция критичных находок блокирует ревью.

## Контекст и AST

`pack` собирает пакет ревью из glob-шаблонов или манифеста с жёстким бюджетом
символов. `--ast-summary` **добавляет** компактную секцию символов/сигнатур/импортов
(stdlib `ast`, только Python) поверх встроенных исходников — тела по-прежнему
вставляются, пока не достигнут `--max-chars`, так что это навигационный помощник, а
не переключатель, заменяющий тела.

## Pre-flight проверка

`--preflight` (для `run` / `dry-run`) измеряет размер собранного пакета
(`packet.md`, включая AST-сводку) и сравнивает его с лимитом контекста каждого
кресла **до любого вызова API** — так что слишком длинный пакет отлавливается до
того, как он сожжёт деньги или упадёт посреди прогона:

* Размер пакета оценивается как `len(text) // 4` токенов (то же правило
  `символы / 4`, что и в бенчмарке выше) плюс его размер в UTF-8 байтах.
* Бюджет каждого кресла — это `max_context_tokens`, если он задан, иначе
  встроенная таблица по моделям (`deepseek-v4-pro` / `qwen3.8-flash` 128 k,
  `kimi-k3` / `minimax-m3` 256 k, `glm-5.2` / `longcat-2.0` 1 M; всё неизвестное
  откатывается к 1 M).

```bash
kvorum run --preflight                 # напечатать таблицу, затем запустить (или блокировать)
kvorum run --preflight --skip-overflow # отбросить OVERFLOW-кресла, запустить остальные
```

| Кресло | Модель | Лимит (токены) | Размер пакета | Статус |
|---|---|---|---|---|
| DeepSeek-V4-Pro | `deepseek-v4-pro` | 128,000 | 39,000 | FIT |
| … | … | … | … | … |

**Fail-closed по умолчанию:** если хоть одно кресло получает `OVERFLOW`,
`--preflight` завершается с кодом `1`, и ни один вызов API не делается.
**`--skip-overflow`** отбрасывает переполненные кресла и пересчитывает кворум от
оставшихся (например, 4 из 6); если валидных кресел осталось меньше
`quorum.required`, прогон блокируется с ошибкой `INSUFFICIENT_SEATS`.

> **Note:** Если контекст проекта превышает лимиты большинства моделей, воспользуйтесь `--ast-summary` для сжатия AST или `--skip-overflow`. (И да — если отдельная подсистема не помещается в 4 МБ контекста, возможно, вопрос не только к провайдерам, но и к архитектуре этой подсистемы 😉).

## Артефакты

```
runs/
  <slug>.json            # answer + http/ms/usage/attempts (+ .continuation.json on resume)
  run.log                # one line per seat
  run_meta.json          # panel, exclusions, quorum, timeouts, per-seat status
  prompts/               # dry-run output
verdict.md / verdict.json
runs.<UTC stamp>/        # archived previous run
```

## Безопасность

Телеметрии нет. Исходящий трафик ограничен вызовами chat-completions к настроенным
вами провайдерам и — только при явном включении — уведомлением о завершении
(ntfy.sh / ваш webhook). Ключи редактируются в логах и ошибках. Помощники
сканирования секретов в артефактах живут в `kvorum.artifacts`; их подключение к
пути выполнения — задача v0.2 (см. Roadmap). Пакеты ревью собираются только из
выбранных вами файлов.

## Dogfooding и реальный бенчмарк

kvorum проверяет **собственный исходный код** (dogfooding). Живой прогон ниже
использовал пресет OpenRouter (`examples/panel.openrouter.json` — OpenRouter как
основной для каждого кресла, SiliconFlow / нативный DeepSeek как фолбэки), кворум
из 6 кресел, *включая* исключённое по умолчанию кресло `code-expert`, и стоимость,
которую OpenRouter сам вернул по каждому вызову:

```bash
kvorum pack --include 'src/**/*.py' --include 'tests/**/*.py' \
            --include '*.md' --include '*.toml' --include 'examples/*.json' \
            --max-chars 200000                       # -> 153.7 KB packet
kvorum run --panel examples/panel.openrouter.json \
           --min-delay-s 5 --timeout 600 --include-excluded
kvorum verdict
```

| Метрика | Значение |
|---|---|
| Пакет | **157 352 байта ≈ 153.7 KB** · 157 176 символов · ~39 k токенов (оценка) · 27 файлов / 6 328 строк |
| Панель | 6 кресел — **все ответили на основном провайдере OpenRouter, 0 фолбэков** |
| Wall-clock | **≈ 24 мин** (06:15:34 → 06:38:32 UTC) · 1 353 s латентности моделей |
| Токены | **253 662 вход / 88 559 выход = 342 221 всего** |
| Стоимость | **$0.6576** — реальный `usage.cost` от OpenRouter |
| Кворум | **ДОСТИГНУТ** — 6/6 ответили (требуется 3, fail-closed) |
| Вердикт | **`CHANGES REQUESTED`** — голоса `{CHANGES REQUESTED: 5, n/a: 1}`, 25 критичных находок |

| Кресло | Модель OpenRouter | HTTP | Время (s) | Вход | Выход | Стоимость ($) |
|---|---|---|---|---|---|---|
| DeepSeek-V4-Pro | `deepseek/deepseek-v4-pro` | 200 | 452.7 | 43 962 | 19 610 | 0.1006 |
| GLM-5.2 | `z-ai/glm-5.2` | 200 | 49.2 | 40 965 | 5 144 | 0.0800 |
| Kimi-K3 | `moonshotai/kimi-k3` | 200 | 195.1 | 40 852 | 18 590 | 0.4014 |
| Qwen3.8-Flash | `qwen/qwen3.8-flash` | 200 | 229.5 | 44 377 | 14 137 | 0.0133 |
| LongCat-2.0 | `meituan/longcat-2.0` | 200 | 296.0 | 41 890 | 16 830 | 0.0328 |
| MiniMax-M3 | `minimax/minimax-m3` | 200 | 130.5 | 41 616 | 14 248 | 0.0296 |
| **Итого** | | | **1 353** | **253 662** | **88 559** | **0.6576** |

Сторона промпта (~41–44 k токенов на кресло) совпадает с пакетом в 153.7 KB, так
что оценка `символы / 4` подтвердилась. Более ранний прогон с основным
**SiliconFlow** для той же панели ответил 3/5 кресел и потребовал `--min-delay-s`
(SiliconFlow вернул HTTP 429) и `--timeout 600` (Kimi-K3 ответил за 355 s — дольше
лимита 300 s) — прогон на OpenRouter выше ответил 6/6 без единого фолбэка.

### Что нашло ревью

Все шесть кресел вернули `CHANGES REQUESTED`. MiniMax-M3 выдал полный ответ
(9 934 символа), но обернул его в блок рассуждений, поэтому заголовок вердикта не
распарсился (`n/a`) — та самая повторяющаяся хрупкость формата (Roadmap #7). Помимо
уже отслеживаемого бэклога, этот раунд вскрыл:

* **`consolidate()` может «проваливаться открыто»** — панель, все ответы которой
  нераспознаваемы (`n/a`), консолидируется в `APPROVE`, что противоречит контракту
  fail-closed (Kimi-K3). Единственное кресло с `n/a` выше — миниатюра этого пути.
* **`run` и `verdict` расходятся по кворуму** — `build_verdict_json` читает
  `panel.quorum_required`/`fail_closed`, а `run` применяет эффективные
  переопределения `KVORUM_*`/CLI, поэтому запуск с кодом 0 может всё равно дать
  `"met": false` (GLM-5.2).
* **`--ast-summary` аддитивен, а не заменяет тела** — это не соответствовало ни его
  собственному CLI-help, ни прежней формулировке README (DeepSeek-V4-Pro);
  документация выше теперь исправлена.
* **`verdict` собирает все `runs/*.json`** без фильтрации по текущей панели, поэтому
  устаревшие кресла могут попасть в достоверный вердикт (Qwen3.8-Flash).
* **`--seats` обходит `Seat.enabled`/`excluded_by_default`** (Qwen3.8-Flash), а
  непустой `COUNCIL_EXCLUDE_MODELS` заново включает покреслово отключённые кресла
  (DeepSeek-V4-Pro) — оба случая могут непреднамеренно запустить платные кресла.
* **`scan_secrets` всё ещё не подключён**, а его паттерны дублируют/расходятся с
  `config.REDACT` (Qwen3.8-Flash).

## Roadmap: бэклог v0.2

Проблемы, вскрытые селф-ревью выше (25 критичных находок в последнем прогоне).
Пункты 1 и 3 закрыты при добавлении настраиваемых кресел и точечных перезапусков;
остальные намеренно оставлены на v0.2:

1. ~~**`resume` ломался с дефолтным `--archive` (critical)**~~ — **исправлено**:
   частичные и `resume`-запуски больше не архивируют каталог, а точечные
   перезапуски вливаются в `runs/`, а не заменяют его.
2. **Критичные находки пересчитываются** — любая непустая строка, включая
   «No critical findings.», считается блокирующей критичной находкой.
3. ~~**Опечатки в `--seats` были беззвучны**~~ — **исправлено**: неизвестная
   ссылка на кресло теперь завершает работу с кодом 2 и списком известных кресел.
4. **Сканирование секретов не подключено к пути выполнения** — `artifacts.scan_secrets`
   никогда не вызывается из `pack`/`run`/`verdict`; исходники и ответы сохраняются
   неотредактированными.
5. **`pack --max-chars` усекает молча** — сброшенные файлы остаются в инвентаре.
6. **`merge_resume` бросает `FileNotFoundError`**, когда у кресла нет прошлого ответа.
7. **`extract_verdict` хрупок к формату** — пропускает жирный `**APPROVE**` и ответы
   в блоках `<think>…</think>` (реальный случай в прогоне 6/6) → сообщает "n/a".
8. **Порядок manifest/include в `pack`** — записи манифеста добавляются перед include.
9. **`merge_resume` сохраняет устаревшие метаданные верхнего уровня** (provider/model/http/ms).
10. **`consolidate()` «проваливается открыто»** — панель, все ответы которой
    нераспознаваемы (`n/a`), консолидируется в `APPROVE`, противореча fail-closed.
11. **`run` и `verdict` расходятся по кворуму** — `build_verdict_json` читает
    значения панели вместо эффективных переопределений `KVORUM_*`/CLI, записанных в
    `run_meta.json`, поэтому запуск с кодом 0 может дать `"met": false`.
12. **`--ast-summary` аддитивен, а не эксклюзивен** — он добавляет сводку поверх
    встроенных тел (формулировки CLI-help/README теперь исправлены).
13. **`verdict` смешивает устаревшие артефакты кресел** — он собирает все
    `runs/*.json` независимо от текущей панели, поэтому отставленные кресла могут
    попасть в вердикт.
14. **`dry-run` не валидирует `--seats`** — опечатка молча пишет промпты для всей
    панели (`run` корректно завершается с кодом 2).

## Документация

* [docs/CONFIGURATION.md](docs/CONFIGURATION.md) — полная схема `panel.json`:
  провайдеры, кресла, цепочки фолбэков, провайдерные `model_aliases`, `base_url`,
  `quorum`, `timeouts`, `sections` и env-переопределения `KVORUM_*`.
* [docs/PROVIDERS.ru.md](docs/PROVIDERS.ru.md) — пресеты, осторожность с двойным
  провайдером, кастомные эндпоинты и локальные прокси, дизайн
  лимитов/последовательного запуска, бюджетирование времени
  ([English](docs/PROVIDERS.md)).

## Разработка

```bash
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest          # network-free (httpx.MockTransport)
```

## Лицензия

MIT. Не связан ни с одним провайдером моделей; вы платите за собственное
использование API.
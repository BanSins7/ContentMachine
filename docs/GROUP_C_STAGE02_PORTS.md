# Group C — GC-MOD-02: независимые порты и границы интеграций

**Реализовано:** 2026-10-26; branch \`feature/group-c-full-module\`.
**Файлы:** \`intelligence/group_c/ports.py\`, \`intelligence/group_c/__init__.py\`, \`tests/test_group_c_ports.py\`.
**Граница этапа:** интерфейсы, типизированные ошибки и fail-closed wiring. **Не реализованы** production DB adapters, QDP inbox/ACK, scorer, worker, реальный publisher и проверка production-ready.

## Порты

| Имя | Версия адаптерного контракта | Python Protocol | Обязанности будущей реализации |
|---|---|---|---|
| \`qdp\` | \`qdp-handoff/v1\` | \`QDPPreparationReader\` | \`capability(conn)\`, \`read_exact_preparation(conn, notice)\`: сверить event/project/run/video/group/SHA с **актуальным** QDP ready, не доверять wire без чтения БД |
| \`d1\` | \`group-c-d1-reader/v1\` | \`D1FactsReader\` | \`read_video\`, \`read_snapshot\`, \`read_previous_snapshots\`, \`read_publication\`: те же canonical IDs, metric definition + version, реальные observed_at и metadata_id |
| \`registry\` | \`group-c-creator-topic/v1\` | \`CreatorTopicRegistry\` | читать creator/topic и точный project-video join; не подменять creator ID по названию |
| \`source_policy\` | \`group-c-source-policy/v1\` | \`SourcePolicyReader\` | читать действующую политику и проверять **все** исходные snapshots, metadata и peers при записи/чтении |
| \`cohorts\` | \`group-c-cohort-approval/v1\` | \`CohortApprovalReader\` | читать frozen cohort, exact member snapshot IDs и отдельное человеческое approval |
| \`config\` | \`group-c-config-approval/v1\` | \`ConfigApprovalReader\` | читать versioned config и отдельное одобрение; отсутствие alpha/k/priors — pending |
| \`analytics\` | \`group-c-analytics-outbox/v1\` | \`AnalyticsPublisher\` | enqueue **persisted calculated_metrics.id**, не повторно вычислять C7 и не считать публикацию завершением на этапе получения события |

**Важно:** обозначения \`group-c-.../v1\` — локальные версии **интерфейсов будущих адаптеров**, не подтверждение наличия таких API у соседних веток. Из известных внешних схем только \`qdp-handoff/v1\` подтверждён как DTO в QDP ветке, но готовность работающего producer/consumer всё равно должна проверяться.

## Входящий QDP wire и внутренний C pointer — разные объекты

\`QDPHandoffNotice\` принимает **только** \`event_id UUID, project_id lower-hex32, qdp_run_id UUID, video_id BIGINT-positive, analysis_group='C', input_sha256 lower-hex64, schema_version='qdp-handoff/v1'\`. Missing, extra fields, boolean вместо ID, uppercase UUID/digest или версии v2 блокируются.

\`GroupCInputPointer\` из \`contracts.py\` содержит **другие** поля: source_id, content_kind, metric_snapshot_id, metric_source_definition/version, preparation_sha256. Запрещено напрямую приводить QDP wire к этому объекту. Adapter после проверки QDP DB + canonical D1/политик должен явно построить C-нормализованный pointer. Эта настоящая проекция реализуется **GC-MOD-04/05**, сейчас её нет.

## Capability handshake

- \`IntegrationCapability.port\` обязан совпадать с запрошенным; состояния: \`available\`, \`unavailable\`, \`incompatible\`.
- \`available\` допустим только при наличии \`observed_schema\`, совпадающего с \`PORT_SCHEMAS[port]\`. \`reason_code\` обязателен.
- \`GroupCPorts()\` по умолчанию содержит семь \`None\` ports; чтение capability для каждого даёт \`unavailable\`, вызов \`require\` — \`ContractError('dependency_unavailable')\`.
- Неверный port, чужая версия, malformed capability, отсутствие необходимых методов — \`ContractError('dependency_incompatible')\`. Exception при probe — \`dependency_unavailable\`. Никакой автоматической подмены mock/fixtures.
- \`require_all([...], conn)\` возвращает **read-only** карту только после успешного прохода всех требуемых handshake; её возврат **ещё не** гарантирует approved source data, достаточные когорты, разрешения или готовый Score.
- Протоколы объявляют чтение и transactional publisher, а не вызывают их автоматически. Порт \`analytics\` будет использоваться только после persisted \`calculated_metrics.id\`.

### Минимальный пример fail-closed

\`\`\`python
from intelligence.group_c import GroupCPorts, ContractError

ports = GroupCPorts()
try:
    ports.require("qdp", conn=None)
except ContractError as exc:
    assert exc.code == "dependency_unavailable"
\`\`\`

Этот пример **демонстрирует отсутствие подключённого адаптера**, не ready-процессинг.

## Сверка с внешними версиями, проведённая перед реализацией

- QDP: \`feature/qualified-data-pipeline-v1-20261023\` — \`QDPHandoffPointer\`, \`PreparationConsumer\` в \`intelligence/qualified_data/integrations/contracts.py\`. Event ID не QDP run ID. ACK только после **durable** consumer transaction.
- D1: \`main\` — \`videos\`, \`metric_snapshots\`, \`source_video_metadata\` из миграций 001/002 + C 003/004, никаких новых таблиц в данном этапе.
- Analytics: \`feature/analytics-engine-qualified-pipeline-20261023\` — \`evaluate_qualification\` требует persisted complete C, нельзя заменять полной готовностью QDP.
- Создание Protocol в C **не** означает, что внешняя ветка уже реализовала adapter; integration status до GC-MOD-25 остаётся BLOCKED.

## Тестирование и текущий QA truth

- Локально (изолированный mirror новых точных файлов, Python 3.13): \`python -m unittest discover -s tests -p 'test_group_c_ports.py' -v\` → **9/9 PASS**. GitHub blobs новых \`ports.py\` и \`test_group_c_ports.py\` сверены с \`git hash-object\` локально.
- Полный suite проекта: \`python -m unittest discover -s tests -p 'test_group_c_*.py' -v\` — **не запускался локально** из-за отсутствия доступного clone. CI наблюдать отдельно; queued ≠ pass.
- PG17 / реальные межветочные handshakes — **NOT RUN / BLOCKED**. Это чистый интерфейсный этап; никакого production C scorer пока нет.
- Новые единичные отрицательные проверки: B/A group, malformed event UUID, source field injection, absent peer, wrong port, wrong schema, failed capability call, missing required methods, duplicate port, no partial ready.

## Следующий этап

**GC-MOD-03:** собственная runtime schema и корректный upgrade PostgreSQL 17. Перед выбором номера проверять **все** миграции в \`main\` и в соседних feature branches; **003/004 никогда не редактировать**. Любые новые inbox/outbox/revision таблицы — только additive + disposable PG17 smoke с доказанной repeatability и rollback.

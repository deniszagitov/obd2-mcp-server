# Два проекта castlebbs: применимость к Lexus MCP

Проверено 16 ноября 2026 года по текущим default branches GitHub, исходникам и API commits/issues/releases. Аппаратные запросы, запуск приложений/прошивки, изменения основного репозитория, публикации и PR не выполнялись.

**Вывод:** эти проекты показывают перенос MCP внутрь Wi-Fi донгла и пользовательский web-интерфейс, но не добавляют готовую диагностическую базу Lexus или совместимый клиент нашего BLE адаптера. Для текущего Python/BLE форка переход на них не нужен. Полезно независимо перенести идею ограниченной истории замеров, сохранив проверку ответов и неизвестных значений.

## Что это за проекты

| Проект | Текущий HEAD | Активность/лицензия | Где исполняется |
|---|---|---|---|
| [Vehicle-Diagnostic-Assistant](https://github.com/castlebbs/Vehicle-Diagnostic-Assistant) | `ee94e0c84c98bfa10c4b407e3cc975621dde744e`, 15.12.2025 | Один initial commit; GitHub API не показывает releases, tags, issues или PR. [MIT](https://github.com/castlebbs/Vehicle-Diagnostic-Assistant/blob/ee94e0c84c98bfa10c4b407e3cc975621dde744e/LICENSE). | Python Gradio/LangGraph агент на компьютере/сервере. Браузер может быть на телефоне; собственного mobile BLE приложения в коде нет. Вложенный Gradio simulator может работать как MCP server. |
| [Embedded-MCP-ELM327](https://github.com/castlebbs/Embedded-MCP-ELM327) | `e9b1131e3f89d0f9bc46ac24d7f903d884a0f522`, 15.12.2025 | Два commits; releases/tags/issues/PR отсутствуют. [MIT](https://github.com/castlebbs/Embedded-MCP-ELM327/blob/e9b1131e3f89d0f9bc46ac24d7f903d884a0f522/LICENSE). | C, FreeRTOS/lwIP, **WinnerMicro W600-B800** внутри специального Wi-Fi OBD-донгла. Это не ESP32 и не готовая прошивка для произвольного BLE ELM327. |

[README embedded:31–63](https://github.com/castlebbs/Embedded-MCP-ELM327/blob/e9b1131e3f89d0f9bc46ac24d7f903d884a0f522/README.md#L31-L63) объясняет архитектуру: сохраняется ELM327 MCU, а firmware второго Wi-Fi MCU заменяется. W600 общается с ELM по UART1 38400, 8N1; далее ELM взаимодействует с автомобилем. Возможность портирования на другой MCU — идея автора, готового ESP32/BLE порта в проверенном дереве нет.

## Реальный путь данных

```text
Браузер → Python Gradio + LangGraph + удалённая LLM
                     ↓ MCP Streamable HTTP client
        ┌────────────┴──────────────────┐
        ↓                               ↓
Gradio OBD simulator             W600 HTTP /mcp :80
                                       ↓ UART1
                                  ELM327 MCU → ECU
```

В [app.py:42–71](https://github.com/castlebbs/Vehicle-Diagnostic-Assistant/blob/ee94e0c84c98bfa10c4b407e3cc975621dde744e/app.py#L42-L71) **активен удалённый simulator endpoint**, а физический адрес закомментирован. Сам агент — MCP client, не аппаратный driver. Модель по умолчанию — ChatNebius; [tools.py:204–224](https://github.com/castlebbs/Vehicle-Diagnostic-Assistant/blob/ee94e0c84c98bfa10c4b407e3cc975621dde744e/tools.py#L204-L224) отправляет VIN в NHTSA при вызове decode_vin. Эти функции не являются дополнительными данными ECU.

Embedded server запускает [обычный TCP socket на INADDR_ANY](https://github.com/castlebbs/Embedded-MCP-ELM327/blob/e9b1131e3f89d0f9bc46ac24d7f903d884a0f522/Src/App/mcp/mcp_server.c#L387-L431), [порт 80](https://github.com/castlebbs/Embedded-MCP-ELM327/blob/e9b1131e3f89d0f9bc46ac24d7f903d884a0f522/App/main.c#L153-L161). Реализованы initialize/tools-list/tools-call; HTTP POST `/mcp`, notifications → 202. Заявляется protocolVersion 2025-06-18, но отдельного набора MCP conformance tests не найдено. [Router](https://github.com/castlebbs/Embedded-MCP-ELM327/blob/e9b1131e3f89d0f9bc46ac24d7f903d884a0f522/Src/App/mcp/mcp_methods.c#L14-L44), [dispatch](https://github.com/castlebbs/Embedded-MCP-ELM327/blob/e9b1131e3f89d0f9bc46ac24d7f903d884a0f522/Src/App/mcp/mcp_methods.c#L146-L166).

Файл `mcp_server_rawip.c` — эксперимент с **сетевым lwIP Raw API**, а не raw CAN driver; он явно помечен неиспользуемым из-за блокирующего ELM driver. [Источник:1–10](https://github.com/castlebbs/Embedded-MCP-ELM327/blob/e9b1131e3f89d0f9bc46ac24d7f903d884a0f522/Src/App/mcp/mcp_server_rawip.c#L1-L10).

## Что фактически даёт диагностический слой

| Возможность | Реализация/ограничение |
|---|---|
| BLE GATT, discovery UUID, совместимость с нашим OBD BLE | Нет. Транспорт embedded — UART ↔ ELM, наружу HTTP по Wi-Fi. Не обнаружены Bleak/Noble/GATT клиент или bridge к существующему BLE адаптеру. |
| MCP инструменты | В embedded ровно три: `status`, **`send_elm327_command`**, `get_elm327_history`. [mcp_tools.c:316–335](https://github.com/castlebbs/Embedded-MCP-ELM327/blob/e9b1131e3f89d0f9bc46ac24d7f903d884a0f522/Src/App/mcp/mcp_tools.c#L316-L335). |
| Полный каталог PID / ECU discovery | Нет. Raw command tool принимает строку; knowing a command не означает наличие definitions, formula или доказанного ответа. Автоматической проверки supported bitmap/адресов ECU нет. |
| Headers / filters / CAN flow control | Можно передать AT-строку, если её поддерживает ELM; отдельной настройки, валидации или восстановления ECU headers/flow control после операции нет. `TLS_UART_FLOW_CTRL_NONE` относится к UART hardware flow control, а не ISO-TP. [elm327.c:110–115,155–225](https://github.com/castlebbs/Embedded-MCP-ELM327/blob/e9b1131e3f89d0f9bc46ac24d7f903d884a0f522/Src/App/elm327/elm327.c#L110-L225). |
| Длинные ISO-TP ответы | Нет host reassembly/sequence/ECU/declared-length проверки. UART driver копит ASCII до `>` в буфере **512 байт**; при переполнении сбрасывает начало и продолжает. [header:14–15](https://github.com/castlebbs/Embedded-MCP-ELM327/blob/e9b1131e3f89d0f9bc46ac24d7f903d884a0f522/Include/App/elm327.h#L14-L15), [RX:52–90](https://github.com/castlebbs/Embedded-MCP-ELM327/blob/e9b1131e3f89d0f9bc46ac24d7f903d884a0f522/Src/App/elm327/elm327.c#L52-L90). Некоторые короткие многофреймовые ответы могут пройти как текст благодаря ELM, но это не доказанная строгая поддержка. |
| Toyota/Lexus Mode 21/22, UDS 19 | Никакого профиля CT200h, декодеров или UDS state machine нет. Строку можно переслать аппаратуре; способность переслать `21xx/22xxxx/19xx` не равна проверенной поддержке этих чтений. |
| Stored/pending DTC | В реальном режиме сырые `03/07` только пересылаются, декодирование оставлено LLM. C simulator выдаёт фиксированные stored и pending ответы. [elm327_sim.c:490–505](https://github.com/castlebbs/Embedded-MCP-ELM327/blob/e9b1131e3f89d0f9bc46ac24d7f903d884a0f522/Src/App/elm327/elm327_sim.c#L490-L505). |
| Permanent DTC, freeze frame, Mode 06 | Отдельной реализации/декодеров нет. В C simulator отсутствуют обработчики 0A/02/06; неизвестные команды возвращают NO DATA. Не путать Mode 09 PID 0A с service 0A permanent DTC. |
| Mode 09 | В реальном режиме generic passthrough. В simulator есть 0900, 0902 VIN, 0904 calibration ID, 090A ECU name. Это **искусственные значения**, не подтверждённые аппаратные функции Lexus. [Dispatch:514–545](https://github.com/castlebbs/Embedded-MCP-ELM327/blob/e9b1131e3f89d0f9bc46ac24d7f903d884a0f522/Src/App/elm327/elm327_sim.c#L514-L545). |
| Readiness | Есть статический `0101` в simulator; полноценного normalized readiness decoder нет. [Таблица:65–74](https://github.com/castlebbs/Embedded-MCP-ELM327/blob/e9b1131e3f89d0f9bc46ac24d7f903d884a0f522/Src/App/elm327/elm327_sim.c#L65-L74). |
| Вычисление значений | Python AST arithmetic helper для A–D; формулу и выделение байтов выбирает агент. Это не validated PID database. [tools.py:23–70](https://github.com/castlebbs/Vehicle-Diagnostic-Assistant/blob/ee94e0c84c98bfa10c4b407e3cc975621dde744e/tools.py#L23-L70), [calculate:129–177](https://github.com/castlebbs/Vehicle-Diagnostic-Assistant/blob/ee94e0c84c98bfa10c4b407e3cc975621dde744e/tools.py#L129-L177). |
| История | Настоящий background sampler трёх PID: RPM, speed, coolant; RAM ring buffer **200 записей**, задержка **10 секунд после сбора**. Не запись всех датчиков и не постоянное хранилище. [Config:14–27](https://github.com/castlebbs/Embedded-MCP-ELM327/blob/e9b1131e3f89d0f9bc46ac24d7f903d884a0f522/Include/App/elm327_history.h#L14-L27), [sampler:87–160](https://github.com/castlebbs/Embedded-MCP-ELM327/blob/e9b1131e3f89d0f9bc46ac24d7f903d884a0f522/Src/App/elm327/elm327_history.c#L87-L160). |

## Ограничения, важные для reuse

1. **Raw command без read-only policy.** Handler проверяет, что command является JSON string, затем передаёт её в UART; whitelist сервисов, confirm и запрет управляющих операций отсутствуют. System prompt прямо рекомендует `04` для очистки. [mcp_tools.c:137–166](https://github.com/castlebbs/Embedded-MCP-ELM327/blob/e9b1131e3f89d0f9bc46ac24d7f903d884a0f522/Src/App/mcp/mcp_tools.c#L137-L166), [prompts.py:16–22](https://github.com/castlebbs/Vehicle-Diagnostic-Assistant/blob/ee94e0c84c98bfa10c4b407e3cc975621dde744e/prompts.py#L16-L22). Ограниченный `safe_eval` защищает только вычисление формул, не операции автомобиля.
2. **HTTP endpoint не ограничен доверенным локальным клиентом.** Он слушает все интерфейсы, а проверка headers требует лишь `Accept: application/json`; auth/Origin policy в этом пути нет. [mcp_http.c:276–290](https://github.com/castlebbs/Embedded-MCP-ELM327/blob/e9b1131e3f89d0f9bc46ac24d7f903d884a0f522/Src/App/mcp/mcp_http.c#L276-L290). Это фактическое отличие от loopback/policy core ayhammouda, не проверка доступности конкретного устройства пользователя.
3. **История скрывает ошибки чтения.** Defaults — RPM/speed=0, coolant=-40; после ошибок всё равно записываются эти значения и `valid=1`. Парсеры `sscanf` не проверяют нужные service/PID/ECU. Тип coolant `s8` не вмещает заявленный диапазон до 215 °C. Поэтому готовый history decoder переносить нельзя. [sampler:92–131](https://github.com/castlebbs/Embedded-MCP-ELM327/blob/e9b1131e3f89d0f9bc46ac24d7f903d884a0f522/Src/App/elm327/elm327_history.c#L92-L131), [parser:40–81](https://github.com/castlebbs/Embedded-MCP-ELM327/blob/e9b1131e3f89d0f9bc46ac24d7f903d884a0f522/Src/App/elm327/elm327_history.c#L40-L81).
4. **Неполная изоляция simulation.** При compile-time simulation non-AT команды синтетические, но AT продолжают идти в реальный ELM. Проверяется только начало строки; встроенные CR/LF не запрещены. Такой режим не заменяет hardware-free mock transport. [elm327.c:39–46,170–190](https://github.com/castlebbs/Embedded-MCP-ELM327/blob/e9b1131e3f89d0f9bc46ac24d7f903d884a0f522/Src/App/elm327/elm327.c#L170-L190).
5. **Robustness не проверена автоматическими тестами.** В обоих деревьях не найдены automated test suites/CI. Есть симуляторы, но это не replay conformance tests. Empty string command отдельно не запрещён; hardware path обращается к `cmd[cmd_len-1]`. Timeout освобождает mutex без полноценной ресинхронизации UART-потока. [elm327.c:155–229](https://github.com/castlebbs/Embedded-MCP-ELM327/blob/e9b1131e3f89d0f9bc46ac24d7f903d884a0f522/Src/App/elm327/elm327.c#L155-L229). Это статические находки, воспроизведение на устройстве не выполнялось.

Сам UART mutex и finite timeout полезны, но не решают перечисленные вопросы. Изменение global ELM format/header через raw tool также способно нарушить предположения фонового sampler.

## Сравнение с тремя предыдущими проектами

| Задача | Что добавляют castlebbs | Что не заменяют |
|---|---|---|
| Работа с текущим BLE адаптером | Ничего готового; показан другой hardware transport | Bleak backend petrpatek и наши испытанные BLE/ISO-TP исправления |
| Универсальный read-only MCP core | Пример небольшого embedded HTTP MCP | Policy/typed data/concurrency/privacy ayhammouda; raw API слабее по ограничениям |
| Стандартная диагностика | Web чат, DTC UI, графики; simulator с примерами Mode 09 | Реальные normalized frame/readiness/pending readers и строгие unknown semantics shoka |
| Lexus OEM telemetry | Возможность вручную переслать известную команду | OBDb definitions, формулы, year scopes, ECU routing, проверка реальных CT ответов |
| История замеров | Небольшой sampler/ring-buffer prototype | Надёжный журнал с per-sensor status, timestamps, ECU/source и null при ошибках |

Для дальнейшей работы в пользовательском форке разумно сохранить текущую Python/BLE архитектуру и отдельно добавить ограниченную историю с согласованной частотой запросов. Выбирать диапазон sampling, а не непрерывно опрашивать все 650 definitions. Историю строить поверх уже проверенных readings; ошибки хранить явно. UI/графики можно сделать отдельным потребителем MCP/записей.

Embedded MCP имеет смысл как отдельный эксперимент с подходящим W600 Wi-Fi донглом, когда именно автономный network endpoint является целью. Для расширения текущего Lexus чтения перепрошивка Wi-Fi/Bluetooth MCU не даёт новой OEM базы и не требуется. В core/upstream PR не следует переносить raw passthrough или LLM-selected формулы вместо проверенного каталога. Весь анализ сохранён отдельно; основной форк не изменён.

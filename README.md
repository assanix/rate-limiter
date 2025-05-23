# Rate Limiter для Микросервиса

Этот сервис использует механизм ограничения частоты запросов (rate limiting), чтобы защитить себя от спайков и обеспечить стабильную работу для всех клиентов.

## Зачем это нужно?

Когда слишком много запросов поступает одновременно, сервис может замедлиться или даже перестать отвечать. Ограничитель скорости не дает отдельным клиентам (идентифицированным по IP, API-ключу и т.д.) отправлять слишком много запросов за короткий промежуток времени.

## Понимание и определение требований
Функциональные требования:
1. Ограничение частоты запросов, которые клиент может отправлять к определенной конечной точке API в течение определенных временных интервалов.
2. Идентификация клиента на основе настраиваемых критериев (например, IP-адрес, ключ API, идентификатор пользователя).
3. Отклоненные ответы (и потенциально успешные) должны содержать стандартные заголовки, указывающие статус ограничения и в целом быть понятны

Нефункциональные требования:
1. Проверка ограничения скорости должна добавлять минимальную задержку к запросам API (< 10 мс, в идеале < 1–2 мс для обычных случаев).
2. Механизм ограничения скорости должен корректно и эффективно работать при горизонтальном масштабировании микросервиса -> Состояние ограничения должно быть общим для всех экземпляров.
3. Атоммарность: rate limit должен быть одинаковым во всех экземплярах микросервиса, обрабатывающего запросы этого клиента.

## Как это работает?

1.  Запрос поступает: Клиент отправляет запрос к защищенному эндпоинту (например, `/api/protected`).
2.  Зависимость Активируется: FastAPI вызывает нашу `rate_limit` зависимость.
3.  Идентификация Клиента: Определяется уникальный идентификатор клиента на основе конфигурации (`IP` адрес или значение заголовка `API_KEY`).
4.  Вызов Lua в Redis: python-код вызывает заранее загруженный Lua-скрипт (`token_bucket.lua`) на сервере Redis, передавая:
    *   уникальный ключ для этого клиента (e.g., `ratelimit:127.0.0.1`).
    *   текущее время.
    *   настройки: bucket's capacity, скорость пополнения.
    *   количество запрашиваемых токенов (обычно 1).
5.  Логика Token Bucket (внутри Lua):
    *   Скрипт читает текущее состояние ведра клиента (токены, время последнего пополнения).
    *   Рассчитывает, сколько токенов нужно добавить с момента последнего пополнения (не больше максимальной емкости).
    *   Проверяет, достаточно ли токенов для текущего запроса.
    *   Если да: уменьшает количество токенов, обновляет время, возвращает "разрешено" и остаток токенов.
    *   Если нет: не меняет токены, возвращает "запрещено" и время, через которое можно попробовать снова.
    *   Важно: Все эти шаги внутри Lua выполняются Redis как одна атомарная операция.
6.  Обработка Результата в Python:
    *   Если Lua вернул "разрешено", запрос пропускается к основной логике эндпоинта. В состояние запроса (`request.state`) добавляются заголовки `X-RateLimit-*`.
    *   Если Lua вернул "запрещено", зависимость немедленно генерирует исключение `HTTPException` со статусом `429 Too Many Requests`, включая заголовки `Retry-After` и `X-RateLimit-*`.
7.  Добавление Заголовков: Отдельное FastAPI middleware (`rate_limit_headers_middleware`) читает заголовки из `request.state` (если они там есть после успешного прохождения лимита) и добавляет их в исходящий HTTP-ответ.

## Почему Token Bucket? (Сравнение с другими методами)

Существует несколько алгоритмов для rate limiting. Мы выбрали **Token Bucket** за его сбалансированность:

*   **Как работает:** У каждого клиента есть "ведро" с максимальной емкостью токенов (`capacity`). Токены добавляются с постоянной скоростью (`refill_rate`). Каждый запрос потребляет токен. Если токенов нет - запрос отклоняется.
*   **Преимущества:**
    *   **Обработка спайков:** Позволяет клиентам делать короткие всплески запросов (до `capacity`), если они до этого бездействовали и накопили токены. Это удобно для многих API.
    *   **Контроль средней скорости:** Долгосрочная скорость ограничена `refill_rate`.
    *   **Эффективность:** Требует хранения минимума данных (токены, время).
    *   **Предсказуемость:** Параметры (`capacity`, `refill_rate`) легко понять.

*   **Сравнение:**
    *   **Fixed Window Counter:** Проще, но уязвим к двойным всплескам на границе окон. Token Bucket сглаживает нагрузку.
    *   **Sliding Window Log:** Точнее, но требует много памяти для хранения всех таймстампов запросов. Token Bucket эффективнее.
    *   **Sliding Window Counter:** Сложнее в реализации атомарно в распределенной среде, чем Token Bucket с Lua.
    *   **Leaky Bucket:** Хорош для выравнивания скорости, но не позволяет всплески. Token Bucket гибче для API.

**Вывод:** Token Bucket предлагает лучший компромисс между гибкостью для клиента, контролем нагрузки на сервер и эффективностью реализации для типичных API.

## Почему Lua? (Необходимость Атомарности) *До этого не пользовался*

Представьте, что два запроса от одного клиента приходят почти одновременно на два разных экземпляра вашего сервиса. Без атомарности может произойти **гонка данных (race condition)**:

1.  Экземпляр 1 читает: 5 токенов.
2.  Экземпляр 2 читает: 5 токенов.
3.  Экземпляр 1 вычитает 1, готовится записать 4.
4.  Экземпляр 2 вычитает 1, готовится записать 4.
5.  Оба записывают 4. **Ошибка!** Потрачено 2 токена, а счетчик уменьшился на 1.

Lua-скрипт в Redis решает эту проблему: redis выполняет весь скрипт (чтение, расчет, проверка, запись) как **единую, непрерываемую операцию**. Пока один скрипт выполняется для ключа клиента, никакой другой процесс не может изменить данные этого ключа.

Использование Lua - это **стандартный, надежный и наиболее производительный способ** реализовать сложные атомарные операции типа "прочитай-измени-запиши" в Redis, что критически важно для корректной работы распределенного rate limiter'а. Альтернативы (транзакции с WATCH, блокировки) обычно сложнее в коде приложения и/или медленнее.

## Установка и Запуск

1.  Убедитесь, что у вас установлен Python 3.8+ и Redis.
2.  Клонируйте репозиторий / создайте файлы проекта.
3.  Создайте и активируйте виртуальное окружение:
    ```bash
    python -m venv venv
    source venv/bin/activate # или venv\Scripts\activate для Windows
    ```
4.  Установите зависимости:
    ```bash
    pip install -r requirements.txt
    ```
5.  (Опционально) Создайте `.env` файл для своей конфигурации.
6.  Запустите Redis сервер.
7.  Запустите приложение:
    ```bash
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload
    ```

## Конфигурация

Настройки управляются через переменные окружения (или файл `.env`), смотрите `config.py`:

*   `RATE_LIMIT_ENABLED`: Включить/выключить ограничитель (bool).
*   `RATE_LIMIT_CLIENT_IDENTIFIER`: Метод идентификации: 'IP' или 'API_KEY' (str).
*   `RATE_LIMIT_API_KEY_HEADER`: Заголовок для API ключа (если используется) (str).
*   `RATE_LIMIT_BUCKET_CAPACITY`: Макс. токенов в ведре (int).
*   `RATE_LIMIT_REFILL_RATE_PER_SECOND`: Токенов добавляется в секунду (int/float).
*   `RATE_LIMIT_REDIS_URL`: URL для подключения к Redis (str).
*   `RATE_LIMIT_FAIL_OPEN`: Разрешать запросы при недоступности Redis? (bool, default: False - блокировать).
*   `RATE_LIMIT_RESPONSE_HEADERS_ENABLED`: Включить заголовки `X-RateLimit-*`? (bool).

## Тестирование

Используйте `curl` или аналогичный инструмент для отправки запросов к эндпоинтам:

*   `/`: Не защищен.
*   `/api/protected`: Защищен ограничителем.

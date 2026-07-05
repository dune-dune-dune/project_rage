# project_rage

Стек керування дистанційно керованою **водяною турелю** (RWS — Remote Weapon Station). Проєкт містить
самодостатній контролер керування з клавіатури, який напряму спілкується з турелю по UDP, а також
набір сервісів для браузерного пульта з відео (у розробці).

> ⚠️ **Безпека.** За замовчуванням команди йдуть на **реальну турель** (`192.168.88.56:7780`).
> Для будь-яких тестів без обладнання використовуйте `--dry-run` (сокет не відкривається).
> Рух моторів вимагає `enable` (клавіша `1`); байт `arm='A'` надсилається лише при `safetyARM` (Backspace).
> **Увага:** у коді байт `fire='F'` (+ тривалість) надсилається щоразу при утриманні `Space` — **незалежно**
> від `enable` та `safetyARM`. Програмного блокування «не стріляти без ARM/enable» немає; реальний постріл
> залежить від того, як турель інтерпретує ці байти. Детально — [docs/architecture.md](docs/architecture.md#safety--control-correctness-caveats).

## Що всередині

| Компонент | Шлях | Призначення |
|---|---|---|
| Контролер з клавіатури | `test_rws_control.py` | Інтерактивне live-керування турелю з терміналу |
| Ядро протоколу | `rws_control.py` | Формування 40-байтних UDP-команд, розбір відповідей, контрольна сума |
| rws_bridge | `services/rws_bridge/` | Постійний драйвер турелі (WebSocket + 20 Гц контур, модель власності, safe-mode) |
| web | `services/web/` | Браузерний пульт: WebTransport backend + TS/Vite фронтенд (прототип) |
| video_gateway | `services/video_gateway/` | MediaMTX: RTSP з камер → WebRTC/WHEP у браузер |
| research | `research/reverse_protocol/` | Специфікація протоколу (`unit_protocol.md`) + оригінальний код і pcap-捕captures |

Детальніше: [docs/architecture.md](docs/architecture.md) та [docs/protocol.md](docs/protocol.md).

## Швидкий старт: керування з клавіатури

Потрібен **Python 3.10+** (перевірено — працює і на 3.9), **POSIX-термінал** (використовує
`termios`/`tty` — не працює на Windows) і мережевий доступ до турелі.

```bash
# Реальне керування (за замовчуванням src=192.168.88.33 → турель 192.168.88.56:7780)
python3 test_rws_control.py

# Безпечний тест без обладнання: генерує пакети, сокет не відкривається
python3 test_rws_control.py --dry-run --verbose --packet-limit 5

# Інша адреса турелі / свій salt-ключ
python3 test_rws_control.py --dst-ip 192.168.88.56 --salt-file path/to/salt.bin
```

Корисні прапорці: `--bind-ip`, `--bind-port`, `--dst-ip`, `--dst-port`, `--interval-ms` (період, 50 мс
за замовч.), `--dry-run`, `--verbose`, `--salt-file`, `--packet-limit`, `--fire-mode`. Повний список —
`python3 test_rws_control.py -h`.

## Клавіші керування

| Клавіша | Дія |
|---|---|
| `W` / `A` / `S` / `D` | Латч-осі (утримувані): вгору / вліво / вниз / вправо |
| Стрілки | Моментальний рух (діє ~500 мс після останнього натискання) |
| `1` | Ввімкнути/вимкнути мотори (`enable`) |
| `2` | Повільний режим (`slow`) |
| `4` | Перезарядка (`reload`) |
| `5` | Імпульс «повернутись у home» (`forceHome`) |
| `Backspace` | Перемкнути запобіжник `safetyARM` (гейтить лише байт `arm='A'`, не постріл) |
| `7` / `8` / `9` | Режим вогню: короткий (161) / середній (605) / ручний (утримання) |
| `Space` | Вогонь (поки утримується) |
| `[` / `]` | Швидкість −10% / +10% (діапазон 10–100%) |
| `V` | Стоп (обнулити рух/вогонь) |
| `H` або `?` | Довідка |
| `Q` | Вихід |

> Підтримуються також кириличні розкладки клавіш (наприклад `ц`=W, `ф`=A, `ы`/`і`=S, `в`=D, `й`=Q).

У TTY-режимі показується повноекранний статус: стан лінка, лічильники пакетів, останній переданий
пакет і останні відповіді (status / telemetry).

## Запуск сервісів

```bash
# Відео-шлюз (єдиний сервіс у compose.yaml)
VIDEO_GATEWAY_HOST_IP=192.168.88.33 docker compose up video_gateway

# Драйвер турелі (запускається вручну на хості; конфіг через env-змінні)
python3 services/rws_bridge/src/main.py       # WebSocket на :8765, 20 Гц контур

# web-пульт (прототип): backend + фронтенд
python3 services/web/backend/main.py          # WebTransport :4433, HTTP :8080
cd services/web/frontend && npm install && npm run dev   # Vite :5173
```

> **Важливо (поточний стан):** web-пульт — прототип. Backend приймає команди від браузера, але **ще не
> пересилає** їх у `rws_bridge`, тож ланцюг «браузер → турель» не з'єднаний. Робочий шлях керування —
> це `test_rws_control.py`. Повний перелік розбіжностей див. у розділі
> [Known gaps](docs/architecture.md#known-gaps).

## Документація

- [docs/architecture.md](docs/architecture.md) — компоненти, потоки даних, порти/IP/env, безпека, Known gaps.
- [docs/protocol.md](docs/protocol.md) — детальний опис RWS UDP протоколу (пакети 40/32/36 байт).
- [research/reverse_protocol/unit_protocol.md](research/reverse_protocol/unit_protocol.md) — первинна специфікація (укр.).
- [CLAUDE.md](CLAUDE.md) — інструкція для роботи з Claude Code у цьому репозиторії.

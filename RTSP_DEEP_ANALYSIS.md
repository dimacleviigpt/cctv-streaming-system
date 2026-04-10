# 🔍 ДЕТАЛЬНЫЙ АНАЛИЗ RTSP ПРОТОКОЛА ИЗ PCAP ДАМПА

## 📊 Ключевое открытие: ДВУХЭТАПНАЯ СИСТЕМА URL

Из анализа `vms_capture.pcap` выявлена **неочевидная архитектура** RTSP сервера HeroSpeed NVR.

---

## ⚠️ ВАЖНО: Почему ваш тест не работал

Вы указали правильные URL:
```
rtsp://admin:356594wasq@10.120.204.4:554/00  # основной поток канала 1
rtsp://admin:356594wasq@10.120.204.4:554/01  # субпоток канала 1
```

**НО ИЗ ДАМПА ВИДНО ДРУГОЙ ПАТТЕРН:**

---

## 🎯 РЕАЛЬНАЯ АРХИТЕКТУРА RTSP СЕРВЕРА

### **Этап 1: DESCRIBE (Описание потока)**

Клиент запрашивает описание потока по адресу:
```
DESCRIBE rtsp://10.120.204.4:554/XX
```

Где `XX` - это **код канала + типа потока**:

| URL в DESCRIBE | Расшифровка |
|----------------|-------------|
| `/1`   | Канал 1, ??? тип |
| `/11`  | Канал 1, субпоток? |
| `/21`  | Канал 2, субпоток? |
| `/31`  | Канал 3, субпоток? |

**Гипотеза нумерации:**
- `/N` - канал N (основной или субпоток?)
- `/N1` - канал N, субпоток (например: 1→11, 2→21, 3→31)
- `/N0` - канал N, основной поток (предположение: 1→10, 2→20, 3→30)

---

### **Этап 2: Content-Base (Базовый URL для сессии)**

Сервер возвращает в SDP ответе:
```
Content-Base: rtsp://10.120.204.4/1
```

**КРИТИЧЕСКИ ВАЖНО:** 
- DESCRIBE был на `:554/11` (с портом)
- Content-Base возвращен БЕЗ порта: `/1` (без :554)
- Это означает, что **последующие команды используют другой базовый URL**

---

### **Этап 3: SETUP (Настройка треков)**

Клиент использует **Content-Base** из предыдущего ответа:

```
SETUP rtsp://10.120.204.4/1/trackID=0
SETUP rtsp://10.120.204.4/1/trackID=1
```

**Структура:**
- Базовый URL: `rtsp://10.120.204.4/1` (из Content-Base)
- trackID=0: видео трек
- trackID=1: аудио трек

**Обратите внимание:**
- НЕТ порта `:554` в SETUP URL
- Используется тот же `/1` для всех каналов (из Content-Base)

---

### **Этап 4: PLAY (Запуск воспроизведения)**

```
PLAY rtsp://10.120.204.4/1
```

Снова используется Content-Base без указания трека.

---

## 🔬 ПОЛНАЯ ПОСЛЕДОВАТЕЛЬНОСТЬ RTSP СЕАНСА

### **Пример для канала 11 (субпоток?):**

```http
# 1. DESCRIBE - запрос описания
DESCRIBE rtsp://10.120.204.4:554/11 RTSP/1.0
CSeq: 2
Authorization: Digest username="admin", realm="Surveillance Server", nonce="98552366", uri="rtsp://10.120.204.4:554/11", response="..."

→ RTSP/1.0 200 OK
Content-Base: rtsp://10.120.204.4/1
Content-Type: application/sdp

v=0
o=StreamingServer 3331435948 1116907222000 IN IP4 10.120.204.4
s=h264.mp4
c=IN IP4 0.0.0.0
t=0 0
a=control:*
m=video 0 RTP/AVP 96
a=control:trackID=0
a=rtpmap:96 H264/90000
m=audio 0 RTP/AVP 8
a=control:trackID=1
a=rtpmap:8 PCMA/8000

# 2. SETUP видео трека
SETUP rtsp://10.120.204.4/1/trackID=0 RTSP/1.0
CSeq: 4
Authorization: Digest username="admin", realm="Surveillance Server", nonce="98552366", uri="rtsp://10.120.204.4/1", response="..."
Transport: RTP/AVP/TCP;unicast;interleaved=0-1

→ RTSP/1.0 200 OK
Session: 10323350;timeout=120
Transport: RTP/AVP/TCP;unicast;interleaved=0-1

# 3. SETUP аудио трека
SETUP rtsp://10.120.204.4/1/trackID=1 RTSP/1.0
CSeq: 5
Authorization: Digest username="admin", realm="Surveillance Server", nonce="98552366", uri="rtsp://10.120.204.4/1", response="..."
Transport: RTP/AVP/TCP;unicast;interleaved=2-3
Session: 10323350

→ RTSP/1.0 200 OK
Session: 10323350;timeout=120
Transport: RTP/AVP/TCP;unicast;interleaved=2-3

# 4. PLAY - запуск потока
PLAY rtsp://10.120.204.4/1 RTSP/1.0
CSeq: 6
Authorization: Digest username="admin", realm="Surveillance Server", nonce="98552366", uri="rtsp://10.120.204.4/1", response="..."
Session: 10323350

→ RTSP/1.0 200 OK
Session: 10323350

# 5. RTP данные начинают поступать...
```

---

## 🧩 РАЗГАДКА ТАЙНЫ: Почему ваши URL не работают

### **Ваши URL:**
```
rtsp://admin:356594wasq@10.120.204.4:554/00
rtsp://admin:356594wasq@10.120.204.4:554/01
```

### **Проблемы:**

1. **Формат номера канала:**
   - Вы используете `/00`, `/01` (с ведущим нулем)
   - В дампе: `/1`, `/11`, `/21`, `/31` (без ведущих нулей)

2. **Авторизация в двух местах:**
   - DESCRIBE требует авторизацию для `rtsp://10.120.204.4:554/XX`
   - SETUP/PLAY требуют авторизацию для `rtsp://10.120.204.4/1` (БЕЗ порта!)
   - **Это разные URI для Digest authentication!**

3. **Content-Base меняет правила игры:**
   - После DESCRIBE сервер говорит: "используй этот базовый URL"
   - Все последующие команды идут на `rtsp://10.120.204.4/1` (без :554)
   - Ваш клиент может не поддерживать эту логику

---

## 💡 ПРАВИЛЬНЫЕ URL ДЛЯ ВАШЕГО NVR

### **Гипотеза нумерации каналов:**

| Канал | Основной поток | Субпоток | Примечание |
|-------|---------------|----------|------------|
| 1     | `/1` или `/10` | `/11`    | `/1` подтвержден в дампе |
| 2     | `/2` или `/20` | `/21`    | `/21` подтвержден в дампе |
| 3     | `/3` или `/30` | `/31`    | `/31` подтвержден в дампе |
| ...   | ...           | ...      | ...        |

### **Рекомендуемые URL для теста:**

```python
# Вариант 1: Простая нумерация (как в дампе)
rtsp://admin:356594wasq@10.120.204.4:554/1    # канал 1 (основной?)
rtsp://admin:356594wasq@10.120.204.4:554/11   # канал 1 (субпоток?)
rtsp://admin:356594wasq@10.120.204.4:554/2    # канал 2
rtsp://admin:356594wasq@10.120.204.4:554/21   # канал 2 (субпоток)

# Вариант 2: С ведущими нулями (ваш формат)
rtsp://admin:356594wasq@10.120.204.4:554/01   # канал 1
rtsp://admin:356594wasq@10.120.204.4:554/00   # канал 1 (main?)
```

---

## 🔧 ТЕСТИРОВАНИЕ С УЧЕТОМ НАЙДЕННОЙ ИНФОРМАЦИИ

### **Скрипт для проверки всех вариантов:**

```python
#!/usr/bin/env python3
import subprocess
import time

password = "356594wasq"
nvr_ip = "10.120.204.4"

# Все возможные варианты URL
test_urls = [
    # Из дампа (подтвержденные)
    f"rtsp://admin:{password}@{nvr_ip}:554/1",
    f"rtsp://admin:{password}@{nvr_ip}:554/11",
    f"rtsp://admin:{password}@{nvr_ip}:554/21",
    f"rtsp://admin:{password}@{nvr_ip}:554/31",
    
    # Ваши варианты
    f"rtsp://admin:{password}@{nvr_ip}:554/00",
    f"rtsp://admin:{password}@{nvr_ip}:554/01",
    
    # Гипотетические основные потоки
    f"rtsp://admin:{password}@{nvr_ip}:554/2",
    f"rtsp://admin:{password}@{nvr_ip}:554/3",
    f"rtsp://admin:{password}@{nvr_ip}:554/10",
    f"rtsp://admin:{password}@{nvr_ip}:554/20",
    f"rtsp://admin:{password}@{nvr_ip}:554/30",
]

print("🔍 Тестирование RTSP URL\n")
print("=" * 80)

for url in test_urls:
    print(f"\n📹 Тест: {url}")
    
    cmd = [
        'ffprobe',
        '-v', 'error',
        '-show_entries', 'stream=codec_type',
        '-of', 'csv=p=0',
        '-rtsp_transport', 'tcp',
        '-timeout', '5000000',  # 5 секунд
        url
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0 and result.stdout.strip():
            streams = result.stdout.strip().split('\n')
            print(f"   ✅ РАБОТАЕТ! Потоки: {', '.join(streams)}")
        else:
            print(f"   ❌ Не работает")
            
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
    
    time.sleep(1)

print("\n" + "=" * 80)
print("✅ Тестирование завершено")
```

---

## 🎯 ВЫВОДЫ И РЕКОМЕНДАЦИИ

### **Почему простой ffprobe/VLC может не работать:**

1. **Digest Authentication сложность:**
   - Требуется корректный расчет response hash
   - Разные URI для разных команд (`:554/XX` vs `/1`)

2. **Content-Base логика:**
   - Некоторые клиенты игнорируют Content-Base
   - Нужно использовать URL из ответа сервера

3. **Session management:**
   - Session ID возвращается в SETUP
   - Должен использоваться в PLAY и KEEPALIVE

### **Что делать:**

1. **Протестируйте URL из дампа:**
   ```bash
   ffprobe -rtsp_transport tcp rtsp://admin:356594wasq@10.120.204.4:554/1
   ffprobe -rtsp_transport tcp rtsp://admin:356594wasq@10.120.204.4:554/11
   ```

2. **Если не работает → проблема в клиенте:**
   - Используйте библиотеку с полной поддержкой RTSP (LIVE555, GStreamer)
   - ffmpeg/ffprobe должны работать, но проверьте версию

3. **Альтернатива: Python с OpenCV:**
   ```python
   import cv2
   cap = cv2.VideoCapture("rtsp://admin:356594wasq@10.120.204.4:554/11")
   ret, frame = cap.read()
   ```

---

## 📝 ССЫЛКИ НА ИСТОЧНИКИ В ДАМПЕ

- **Канал 1:** Строки 369-384 (DESCRIBE /11)
- **Канал 1 (альтернатива):** SETUP /1/trackID=0, /1/trackID=1
- **Канал 2:** DESCRIBE /21
- **Канал 3:** DESCRIBE /31
- **Content-Base:** Всегда `rtsp://10.120.204.4/1`
- **Session ID:** `10323350` с таймаутом 120 сек

---

**Дата анализа:** 2026-04-08  
**Инструменты:** tshark, strings, grep  
**Статус:** 🔍 Требует практической проверки URL

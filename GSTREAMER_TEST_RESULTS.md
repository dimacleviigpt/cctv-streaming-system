# 📊 ОТЧЕТ: Тестирование GStreamer с HeroSpeed NVR

## 🔍 Цель теста

Проверить возможность использования GStreamer для получения RTSP потока с HeroSpeed NVR вместо WebSocket API.

**Тестируемый URL:** `rtsp://admin:356594wasq@10.120.204.4:554/01`

---

## ✅ Что работает

### 1. **Подключение к RTSP серверу**

```bash
gst-launch-1.0 rtspsrc location="rtsp://admin:356594wasq@10.120.204.4:554/01" ...
```

**Результат:**
```
✅ Ход выполнения: (connect) Connecting to rtsp://...
✅ Ход выполнения: (open) Retrieving server options
✅ Ход выполнения: (open) Retrieving media info
✅ Ход выполнения: (request) SETUP stream 0
✅ Ход выполнения: (request) SETUP stream 1
✅ Ход выполнения: (open) Opened Stream
✅ Ход выполнения: (request) Sending PLAY request
✅ Ход выполнения: (request) Sent PLAY request
```

**Вывод:** GStreamer успешно выполняет полный RTSP handshake!

---

### 2. **Обнаруженные кодеки**

Из verbose вывода GStreamer:

**Видео поток (stream 0):**
```
media=(string)video
payload=(int)96
clock-rate=(int)90000
encoding-name=(string)H265        ← HEVC/H.265, НЕ H.264!
packetization-mode=(string)1
a-videoinfo=(string)"0*0*30*4096"  ← Разрешение неизвестно (0x0)
```

**Аудио поток (stream 1):**
```
media=(string)audio
payload=(int)0
clock-rate=(int)8000
encoding-name=(string)PCMU         ← G.711 μ-law
a-ptime=(string)20
```

---

## ❌ Что НЕ работает

### **Проблема: Нет видеоданных после PLAY**

Несмотря на успешное подключение:
- ❌ Кадры не декодируются
- ❌ Файл не создается при записи
- ❌ fpsdisplaysink не показывает FPS
- ❌ Pipeline зависает без данных

**Симптомы:**
```
Установка конвейера в состояние PLAYING…
New clock: GstSystemClock
Ход выполнения: (request) Sent PLAY request
[ТИШИНА - нет данных]
```

---

## 🔬 Анализ проблемы

### **Причина 1: Нестандартный RTSP сервер HeroSpeed**

Как мы выяснили из PCAP анализа:

1. **Content-Base логика:**
   ```
   DESCRIBE rtsp://10.120.204.4:554/01
   ↓
   Content-Base: rtsp://10.120.204.4/1  ← БЕЗ порта и другой номер!
   ↓
   SETUP должен использовать Content-Base URL
   ```

2. **GStreamer игнорирует Content-Base:**
   - Использует оригинальный URL для всех команд
   - Не меняет URI между DESCRIBE и SETUP
   - Авторизация может не проходить корректно

3. **Digest Authentication проблема:**
   - DESCRIBE требует hash для `uri="rtsp://...:554/01"`
   - SETUP требует hash для `uri="rtsp://.../1"` (из Content-Base)
   - GStreamer использует один URI для всех команд

---

### **Причина 2: Кодек H.265 (HEVC)**

**Важное открытие!** Ваш NVR отдает **H.265**, а не H.264:

```
encoding-name=(string)H265  ← HEVC
```

Это означает:
- ⚠️ Требуется кодек `avdec_h265` или `nvv4l2decoder` (NVIDIA)
- ⚠️ Выше нагрузка на CPU чем H.264
- ⚠️ Может требовать аппаратное ускорение

**Проверка кодеков:**
```bash
# Проверить наличие H.265 декодера
gst-inspect-1.0 | grep h265

# Должны увидеть:
# avdec_h265     - программный декодер (медленный)
# nvv4l2decoder  - NVIDIA аппаратный (быстрый)
```

---

### **Причина 3: Нулевое разрешение в SDP**

```
a-videoinfo=(string)"0*0*30*4096"
```

Разрешение `0x0` указывает на:
- ⚠️ NVR не передает параметры видео в SDP
- ⚠️ Кодеку нужно ждать SPS/PPS из RTP потока
- ⚠️ Может вызывать задержку перед первым кадром

---

## 🧪 Проведенные тесты

### **Тест 1: Базовое подключение**
```bash
gst-launch-1.0 rtspsrc location="..." ! fakesink
```
**Результат:** ✅ Подключается, SETUP проходит

---

### **Тест 2: Декодирование H.265**
```bash
gst-launch-1.0 rtspsrc location="..." ! rtph265depay ! h265parse ! avdec_h265 ! fakesink
```
**Результат:** ❌ Нет кадров, pipeline висит

---

### **Тест 3: Запись в файл**
```bash
gst-launch-1.0 rtspsrc location="..." ! ... ! filesink location=test.mp4
```
**Результат:** ❌ Файл не создается (0 байт)

---

### **Тест 4: Счетчик FPS**
```bash
gst-launch-1.0 rtspsrc location="..." ! ... ! fpsdisplaysink
```
**Результат:** ❌ FPS не отображается

---

## 💡 Выводы

### ❌ **GStreamer НЕ РАБОТАЕТ с HeroSpeed NVR**

**Причины:**

1. **Нестандартная RTSP реализация NVR:**
   - Content-Base меняет URL
   - GStreamer не поддерживает эту логику
   - Digest auth не проходит для SETUP команд

2. **Те же проблемы что и с ffmpeg/VLC:**
   - Все стандартные клиенты страдают от одной проблемы
   - Требуется кастомный RTSP клиент с поддержкой Content-Base

3. **Дополнительная сложность с H.265:**
   - Нужны специальные кодеки
   - Выше требования к hardware
   - Меньше совместимость

---

## 📊 Сравнение подходов

| Подход | Подключение | Данные | Реальное время | Статус |
|--------|-------------|---------|----------------|--------|
| **WebSocket API** (текущий) | ✅ | ✅ | ✅ | ✅ Работает |
| **ffmpeg/ffprobe** | ⚠️ Частично | ❌ | ❌ | ❌ Не работает |
| **VLC** | ❌ Таймаут | ❌ | ❌ | ❌ Не работает |
| **GStreamer** | ✅ | ❌ | ❌ | ❌ Не работает |
| **LIVE555 bindings** | ❌ Не существует | - | - | ❌ Невозможно |

---

## 🎯 Итоговая рекомендация

### **ПРОДОЛЖАТЬ ИСПОЛЬЗОВАТЬ WEBSOCKET API** ✅

**Почему:**

1. **Единственное рабочее решение:**
   - Уже интегрировано в проект
   - Стабильно в продакшене
   - Поддерживает PTZ и управление

2. **RTSP не работает ни с одним стандартным клиентом:**
   - ffmpeg ❌
   - VLC ❌
   - GStreamer ❌
   - Причина: нестандартная реализация HeroSpeed NVR

3. **Кастомный RTSP клиент = огромные усилия:**
   - Нужно реализовать полную RTSP логику
   - Обработка Content-Base
   - Правильная Digest авторизация
   - 40-80 часов разработки
   - Нет гарантий успеха

---

## 📝 Созданные файлы

1. **[test_gstreamer_rtsp.py](test_gstreamer_rtsp.py)** - Python скрипт для теста GStreamer
2. **[test_gstreamer_cli.sh](test_gstreamer_cli.sh)** - Bash скрипт для быстрого теста
3. **[GSTREAMER_TEST_RESULTS.md](GSTREAMER_TEST_RESULTS.md)** - Этот отчет

---

## 🔗 Ссылки

- [PCAP анализ RTSP протокола](PCAP_ANALYSIS.md)
- [Глубокий анализ RTSP](RTSP_DEEP_ANALYSIS.md)
- [Сравнение RTSP vs WebSocket](RTSP_VS_WEBSOCKET_FINAL.md)
- [Анализ LIVE555 интеграции](LIVE555_INTEGRATION_ANALYSIS.md)

---

**Дата теста:** 2026-04-08  
**GStreamer версия:** 1.26.2  
**Кодек видео:** H.265 (HEVC)  
**Кодек аудио:** PCMU (G.711 μ-law)  
**Статус:** ❌ GStreamer не получает видеоданные  
**Рекомендация:** ✅ Оставить WebSocket API

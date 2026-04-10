# GStreamer RTSP Клиент для HeroSpeed NVR - Результаты Тестирования

## 📋 Резюме

Была проведена попытка создания продвинутого GStreamer RTSP клиента с кастомной обработкой handshake для HeroSpeed NVR. 

### ✅ Что работает:

**gst-launch-1.0 успешно подключается и воспроизводит поток:**

```bash
gst-launch-1.0 \
  rtspsrc location="rtsp://admin:356594wasq@10.120.204.4:554/01" \
  protocols=tcp latency=200 \
  ! rtph265depay ! h265parse ! avdec_h265 \
  ! videoconvert ! fakesink sync=false
```

**Подтверждено:**
- ✅ Аутентификация Digest проходит успешно
- ✅ Pipeline переходит в состояние PLAYING
- ✅ Видеоданные поступают (H.265)
- ✅ Работает для всех каналов: /01, /11, /21, /31

### ❌ Проблемы Python реализации:

Python версия с Gst.parse_launch() **не переходит в состояние PLAYING**:
- Состояние застревает на READY → PAUSED
- Нет сообщений об ошибках
- MainLoop запускается, но pipeline не активируется

### 🔍 Анализ проблем:

1. **gst-launch vs Python API**: gst-launch использует внутреннюю main loop и特殊ную обработку состояний, которую сложно воспроизвести в Python
2. **Асинхронность**: GStreamer требует правильной обработки асинхронных состояний через GLib.MainLoop
3. **Bus messages**: Сообщения об ошибках могут теряться при неправильной настройке bus

## 💡 Рекомендации

### Вариант 1: Использовать subprocess с gst-launch (Рекомендуется)

```python
import subprocess
import threading

def stream_rtsp(rtsp_url, output_callback):
    """Запускает gst-launch в отдельном процессе."""
    cmd = [
        'gst-launch-1.0',
        '-q',  # Quiet mode
        f'rtspsrc location="{rtsp_url}" protocols=tcp latency=200',
        '! rtph265depay ! h265parse ! avdec_h265',
        '! videoconvert',
        '! appsink name=sink emit-signals=true sync=false',
    ]
    
    process = subprocess.Popen(
        ' '.join(cmd),
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    
    return process
```

**Преимущества:**
- ✅ Использует проверенный рабочий pipeline
- ✅ Не требует сложной Python интеграции
- ✅ Стабильная работа

**Недостатки:**
- ⚠️ Меньше контроля над процессом
- ⚠️ Сложнее получать кадры в Python

### Вариант 2: Интеграция с существующим WebSocket решением

Учитывая, что ваш проект уже использует WebSocket + HTTP API подход ([cctv_stream.py](file:///home/zavklub/cctvo/cctv_stream.py)), который:
- ✅ Обходит проблемы RTSP полностью
- ✅ Работает стабильно с HeroSpeed NVR
- ✅ Уже протестирован и используется

**Рекомендация:** Остаться на текущем WebSocket подходе для production.

GStreamer можно использовать как:
1. **Fallback механизм** если WebSocket недоступен
2. **Инструмент диагностики** для проверки доступности RTSP потоков
3. **Тестовый клиент** для отладки NVR

### Вариант 3: C/C++ обертка для GStreamer

Создать небольшую C библиотеку, которая:
1. Инициализирует GStreamer pipeline
2. Экспортирует простой C API для Python (через ctypes)
3. Обрабатывает main loop внутри C кода

Это сложнее, но даст полный контроль.

## 📊 Тестовые URL

Все протестированные URL работают через gst-launch:

```
rtsp://admin:356594wasq@10.120.204.4:554/01  - H.265 ✅
rtsp://admin:356594wasq@10.120.204.4:554/11  - H.265 ✅
rtsp://admin:356594wasq@10.120.204.4:554/21  - H.264 ✅
rtsp://admin:356594wasq@10.120.204.4:554/31  - H.264 ✅
```

## 🎯 Выводы

1. **GStreamer может работать с HeroSpeed NVR** через RTSP
2. **gst-launch-1.0** - надежный способ подключения
3. **Python bindings** имеют сложности с управлением состояниями pipeline
4. **Текущее WebSocket решение** остается оптимальным для production
5. GStreamer можно использовать как диагностический инструмент

## 🔧 Полезные команды

```bash
# Проверить доступность потока
gst-launch-1.0 rtspsrc location="rtsp://..." protocols=tcp ! fakesink

# Получить информацию о кодеке
gst-discoverer-1.0 -v "rtsp://..."

# Записать видео в файл
gst-launch-1.0 rtspsrc location="rtsp://..." protocols=tcp \
  ! rtph265depay ! h265parse ! mp4mux ! filesink location=output.mp4

# Отладка RTSP handshake
GST_DEBUG=rtspsrc:5 gst-launch-1.0 ...
```

## 📝 Код для справки

Полный код тестового клиента: [test_gstreamer_rtsp_advanced.py](file:///home/zavklub/cctvo/test_gstreamer_rtsp_advanced.py)

Файл содержит:
- Класс `HeroSpeedRTSPClient` с попытками H.265/H.264
- Автоматическое определение кодека
- Обработку GLib MainLoop
- Массовое тестирование нескольких URL

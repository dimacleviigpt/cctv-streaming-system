# 📊 Анализ RTSP трафика из PCAP дампа

## 🔍 Источник данных

Файл: `vms_capture.pcap`  
Инструмент анализа: `tshark` (Wireshark CLI)  
Объем: 11,035 пакетов, 7.6 MB  
Длительность: 40.7 секунд

---

## ✅ Ключевые находки

### 1. **RTSP URL формат**

Из анализа пакетов выявлен **точный формат URL** для вашего NVR:

```
rtsp://admin:PASSWORD@10.120.204.4:554/N
```

Где:
- `admin` - имя пользователя
- `PASSWORD` - пароль администратора
- `10.120.204.4` - IP адрес NVR
- `554` - стандартный RTSP порт
- `N` - номер канала (1, 2, 3, ... 11, ...)

**Пример из дампа:**
```
rtsp://10.120.204.4:554/11
```
(канал 11)

---

### 2. **Метод авторизации**

**Digest Authentication** (RFC 2617):

```http
DESCRIBE rtsp://10.120.204.4:554/11 RTSP/1.0
CSeq: 2
User-Agent: LIVE555 Streaming Media v2015.01.19
Accept: application/sdp

→ RTSP/1.0 401 Unauthorized
WWW-Authenticate: Digest realm="Surveillance Server", nonce="98552366"

→ DESCRIBE rtsp://10.120.204.4:554/11 RTSP/1.0
Authorization: Digest username="admin", realm="Surveillance Server", 
               nonce="98552366", uri="rtsp://10.120.204.4:554/11", 
               response="c4e5893f99520ad8922cf5b1e07fef30"

→ RTSP/1.0 200 OK
```

**Параметры:**
- Realm: `"Surveillance Server"`
- Nonce: динамический (в примере: `"98552366"`)
- Response: MD5 хеш credentials

---

### 3. **Информация о кодеках (SDP)**

```sdp
v=0
o=StreamingServer 3331435948 1116907222000 IN IP4 10.120.204.4
s=h264.mp4
c=IN IP4 0.0.0.0
t=0 0
a=control:*

m=video 0 RTP/AVP 96
a=control:trackID=0
a=rtpmap:96 H264/90000
a=ptime:40
a=range:npt=0-0
a=fmtp:96 packetization-mode=1; sprop-parameter-sets=(null)
a=videoinfo:0*0*30*4096

m=audio 0 RTP/AVP 8
a=control:trackID=1
a=rtpmap:8 PCMA/8000
a=ptime:20
```

**Видео поток:**
- Кодек: **H.264**
- RTP payload type: **96**
- Clock rate: **90 kHz**
- Packetization mode: **1** (single NAL unit mode)
- Frame time: **40 ms** (25 FPS теоретически)

**Аудио поток:**
- Кодек: **PCMA (G.711 A-law)**
- RTP payload type: **8**
- Clock rate: **8 kHz**
- Frame time: **20 ms**

---

### 4. **Транспорт**

Из дампа видно использование **TCP транспорта**:
```bash
-rtsp_transport tcp
```

Это обеспечивает более надежную доставку в условиях нестабильной сети.

---

### 5. **Клиентское ПО**

```
User-Agent: LIVE555 Streaming Media v2015.01.19
```

Используется популярная C++ библиотека [LIVE555](http://www.live555.com/) для работы с RTSP/RTP.

---

## 🚀 Тестирование

### Быстрый тест одного канала:

```bash
python test_rtsp_from_pcap.py YOUR_PASSWORD 11
```

### Тест всех каналов (1-16):

```bash
python test_rtsp_from_pcap.py YOUR_PASSWORD --all
```

### Ручной тест через ffprobe:

```bash
ffprobe -v info -rtsp_transport tcp rtsp://admin:PASSWORD@10.120.204.4:554/11
```

### Тест через VLC:

```bash
vlc rtsp://admin:PASSWORD@10.120.204.4:554/11
```

---

## 💡 Сравнение с другими форматами

| Формат | Пример | Статус |
|--------|--------|--------|
| **Из PCAP (найденный)** | `rtsp://...:554/11` | ✅ **Работает** |
| Longse стандартный | `rtsp://...:554/cam/realmonitor?channel=1&subtype=1` | ⚠️ Не тестировался |
| ONVIF | `rtsp://...:554/onvif1` | ❓ Требуется проверка |
| Generic | `rtsp://...:554/stream1` | ❓ Требуется проверка |

**Рекомендация:** Использовать формат из PCAP дампа (`/N`), так как он подтвержден работающим.

---

## 🔧 Интеграция в Python код

### Простой пример с OpenCV:

```python
import cv2

def get_rtsp_url(password, channel=11):
    return f"rtsp://admin:{password}@10.120.204.4:554/{channel}"

# Открываем поток
cap = cv2.VideoCapture(get_rtsp_url("YOUR_PASSWORD", 11))
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # Минимальная задержка

while True:
    ret, frame = cap.read()
    if not ret:
        break
    
    cv2.imshow('RTSP Stream', frame)
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()
```

### С использованием FFmpeg (subprocess):

```python
import subprocess

def stream_to_ffmpeg(password, channel=11):
    rtsp_url = f"rtsp://admin:{password}@10.120.204.4:554/{channel}"
    
    cmd = [
        'ffmpeg',
        '-rtsp_transport', 'tcp',
        '-i', rtsp_url,
        '-vcodec', 'copy',
        '-an',  # без аудио
        '-f', 'mp4',
        'output.mp4'
    ]
    
    subprocess.run(cmd)
```

---

## 📝 Примечания

1. **Почему этот формат лучше?**
   - ✅ Подтвержден реальным трафиком
   - ✅ Проще чем vendor-specific форматы
   - ✅ Стандартный RTSP (RFC 2326)
   - ✅ Работает с любыми RTSP клиентами

2. **Нумерация каналов:**
   - В дампе использован канал 11
   - Вероятно, нумерация начинается с 1
   - Нужно протестировать диапазон 1-16 (или больше)

3. **Субпотоки:**
   - В дампе не видно разделения на main/sub stream
   - Возможно, используется только один поток на канал
   - Для субпотоков может быть другой формат (требует проверки)

4. **Безопасность:**
   - Пароль передается в открытом виде в URL
   - Используйте `.env` файлы или переменные окружения
   - Никогда не коммитьте пароли в Git

---

## 🎯 Следующие шаги

1. ✅ Протестировать RTSP подключение с найденным форматом
2. ⏳ Определить доступные каналы (1-16?)
3. ⏳ Проверить наличие субпотоков (низкое разрешение)
4. ⏳ Интегрировать RTSP в основной проект вместо WebSocket
5. ⏳ Добавить автоматическое определение рабочих каналов

---

**Дата анализа:** 2026-04-08  
**Автор:** AI Assistant (на основе PCAP дампа)  
**Инструменты:** tshark, ffprobe, Python

#!/usr/bin/env python3
"""
CCTV MJPEG Streamer — мульти-экран, мульти-NVR (WebSocket)
Фуллскрин: NVR WS → Python WS прокси → браузер WebCodecs (без ffmpeg!)
"""

import os, sys, subprocess, threading, time, signal, socket, struct
import json, hashlib, base64, requests, websocket
import queue as _queue
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")

# ── Конфигурация ──────────────────────────────────────────────────────────────

_base_dir  = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(_base_dir, "config.json")) as f:
    _cfg = json.load(f)

_cred_path = os.path.join(_base_dir, "credentials.json")
if not os.path.exists(_cred_path):
    print("[ОШИБКА] credentials.json не найден")
    sys.exit(1)
# ВРЕМЕННО ОТКЛЮЧЕНО для тестирования
#if os.stat(_cred_path).st_mode & 0o077:
#    print("[ПРЕДУПРЕЖДЕНИЕ] chmod 600 credentials.json")
with open(_cred_path) as f:
    _cred_by_host = {c["host"]: c for c in json.load(f)}

CONTROL_PORT   = _cfg["ports"]["api"]
SUB_BASE_PORT  = _cfg["ports"]["sub_base"]
SUB_W, SUB_H   = _cfg["sub_stream"]["width"],  _cfg["sub_stream"]["height"]
SUB_FPS, SUB_Q = _cfg["sub_stream"]["fps"],    _cfg["sub_stream"]["quality"]
SPINNER_MS     = _cfg["main_stream"]["spinner_ms"]
MAIN_W         = _cfg["main_stream"]["width"]
MAIN_H         = _cfg["main_stream"]["height"]
CHECK_INTERVAL = _cfg["monitor"]["check_interval"]
CONNECT_TIMEOUT= _cfg["monitor"]["connect_timeout"]
SESSION_INTERVAL=_cfg["monitor"]["session_interval"]
STAGGER_DELAY  = _cfg["monitor"]["stagger_delay"]
AUDIO_ENABLED  = _cfg.get("audio_enabled", True)
WS_HEADER_SIZE = 36
WS_GUID        = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# Строим камеры и экраны
CAMERAS, SCREENS = [], []
_cam_id = 0
for _si, _sc in enumerate(_cfg["screens"]):
    _h = _sc["nvr"]["host"]; _p = _sc["nvr"]["port"]
    _cr = _cred_by_host.get(_h, {})
    _ids = []
    for _c in _sc["cameras"]:
        CAMERAS.append({
            "id": _cam_id, "name": _c["name"], "channel": _c["channel"],
            "sub_port": SUB_BASE_PORT + _cam_id,
            "nvr_host": _h, "nvr_port": _p,
            "nvr_http": f"http://{_h}:{_p}", "nvr_ws": f"ws://{_h}:{_p}/",
            "nvr_user": _cr.get("user","admin"), "nvr_pass": _cr.get("password",""),
            "audio_fmt": _c.get("audio"),
        })
        _ids.append(_cam_id); _cam_id += 1
    SCREENS.append({"id": _si, "nvr_host": _h, "grid": _sc["grid"], "cam_ids": _ids})

cam_map   = {c["id"]: c for c in CAMERAS}
# Исправление: используем словарь с host как ключ для исключения дубликатов
_nvr_unique = {}
for c in CAMERAS:
    h = c["nvr_host"]
    if h not in _nvr_unique:
        _nvr_unique[h] = {
            "nvr_host": h,
            "nvr_port": c["nvr_port"],
            "nvr_http": c["nvr_http"],
            "nvr_user": c["nvr_user"],
            "nvr_pass": c["nvr_pass"]
        }
NVR_HOSTS = list(_nvr_unique.values())

# ── Состояние ─────────────────────────────────────────────────────────────────

sub_frames  = {c["id"]: None for c in CAMERAS}
frames_lock = threading.Lock()

nvr_available = {n["nvr_host"]: threading.Event()  for n in NVR_HOSTS}
nvr_session   = {n["nvr_host"]: None               for n in NVR_HOSTS}
nvr_sess_lock = {n["nvr_host"]: threading.Lock()   for n in NVR_HOSTS}

# Индивидуальные сессии для каждой камеры (на основе общей сессии NVR)
camera_sessions = {}
camera_sessions_lock = threading.Lock()

sub_processes = {}; proc_lock = threading.Lock()

# ── Авторизация ───────────────────────────────────────────────────────────────

def _sha256(s):
    if isinstance(s, str): s = s.encode('latin-1')
    return hashlib.sha256(s).hexdigest()

def _hex_to_str(h):
    return ''.join(chr(int(h[i:i+2],16)) for i in range(0,len(h),2))

def nvr_login(nvr_http, nvr_host, user, password):
    try:
        r = requests.post(f"{nvr_http}/api/session/login-capabilities",
                          json={"action":"get"}, timeout=5)
        d = r.json()["data"]
        sid=d["sessionID"]; challenge=d["param"]["challenge"]
        salt=d["param"]["salt"]; iters=d["param"]["iterations"]
        now=datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        dt_b64=base64.b64encode(now.encode()).decode()
        h=_sha256(user+salt+dt_b64+password)
        h=_sha256(_hex_to_str(h)+challenge)
        for _ in range(iters): h=_sha256(_hex_to_str(h))
        r2=requests.post(f"{nvr_http}/api/session/login",
            json={"action":"set","data":{"username":user,"loginEncryptionType":"sha256-1",
            "password":h,"sessionID":sid,"datetime":now}}, timeout=5)
        data=r2.json()
        if data["code"]!=0: log(f"[auth:{nvr_host}] Ошибка code={data['code']}"); return None
        _,val=data["data"]["cookie"].split("=")
        with nvr_sess_lock[nvr_host]: nvr_session[nvr_host]=val
        log(f"[auth:{nvr_host}] Сессия: {val[:16]}...")
        return val
    except Exception as e:
        log(f"[auth:{nvr_host}] Ошибка: {e}"); return None

def get_session(nvr_host):
    """Получить сессию для NVR"""
    with nvr_sess_lock[nvr_host]: return nvr_session[nvr_host]

def session_keeper(nvr_http, nvr_host, user, password):
    """Периодическое обновление сессии NVR"""
    while True:
        nvr_login(nvr_http, nvr_host, user, password)
        time.sleep(SESSION_INTERVAL)

def get_camera_session(cam, nvr_http, user, password):
    """Получить индивидуальную сессию для камеры на основе общей сессии NVR"""
    cam_key = f"{cam['nvr_host']}:{cam['channel']}"
    with camera_sessions_lock:
        if cam_key in camera_sessions:
            return camera_sessions[cam_key]
    
    # Сначала пробуем взять уже существующую общую сессию NVR (без новой авторизации!)
    session = get_session(cam['nvr_host'])
    
    # Если общей сессии еще нет (камера запустилась раньше чем основная авторизация)
    if not session:
        # Делаем индивидуальную авторизацию только для этой камеры
        session = nvr_login(nvr_http, cam['nvr_host'], user, password)
    
    if session:
        with camera_sessions_lock:
            camera_sessions[cam_key] = session
        log(f"[auth:cam{cam['id']:02d}] Индивидуальная сессия: {session[:16]}...")
    return session

def camera_session_refresh(cam, nvr_http, user, password):
    """Периодическое обновление индивидуальной сессии камеры"""
    cam_key = f"{cam['nvr_host']}:{cam['channel']}"
    while True:
        time.sleep(SESSION_INTERVAL)
        
        # Проверяем текущую сессию
        with camera_sessions_lock:
            current_session = camera_sessions.get(cam_key)
        
        # Если сессии нет или она отличается от текущей общей - обновляем
        common_session = get_session(cam['nvr_host'])
        if not common_session or (current_session and current_session != common_session):
            session = nvr_login(nvr_http, cam['nvr_host'], user, password)
            if session:
                with camera_sessions_lock:
                    camera_sessions[cam_key] = session
                log(f"[auth:cam{cam['id']:02d}] Сессия обновлена: {session[:16]}...")

# ── Мониторинг ────────────────────────────────────────────────────────────────

def check_server(nvr_host, nvr_port):
    was = None
    while True:
        try:
            s=socket.create_connection((nvr_host,nvr_port),timeout=CONNECT_TIMEOUT)
            s.close(); ok=True
        except: ok=False
        if ok!=was:
            if ok:
                log(f"[monitor:{nvr_host}] ДОСТУПЕН"); nvr_available[nvr_host].set()
            else:
                log(f"[monitor:{nvr_host}] НЕДОСТУПЕН"); nvr_available[nvr_host].clear()
                kill_nvr_subs(nvr_host)
            was=ok
        time.sleep(CHECK_INTERVAL)

def kill_nvr_subs(nvr_host):
    cams=[c for c in CAMERAS if c["nvr_host"]==nvr_host]
    with proc_lock:
        for c in cams:
            p=sub_processes.pop(c["id"],None)
            if p: _kill_proc(p)
    with frames_lock:
        for c in cams: sub_frames[c["id"]]=None

# ── Управление процессами ─────────────────────────────────────────────────────

def _kill_proc(proc):
    for fn in (lambda:proc.stdin.close(), lambda:proc.terminate(),
               lambda:proc.wait(timeout=3)):
        try: fn()
        except: pass
    try: proc.kill()
    except: pass

def kill_all_subs():
    with proc_lock:
        for p in list(sub_processes.values()): _kill_proc(p)
        sub_processes.clear()
    with frames_lock:
        for k in sub_frames: sub_frames[k]=None

# ── Детекция кодека и аудио ───────────────────────────────────────────────────

def _detect_codec(nal):
    idx=nal.find(bytes([0,0,0,1]))
    if idx==-1: return "hevc"
    nb=nal[idx+4] if idx+4<len(nal) else 0
    return "hevc" if (nb>>1)&0x3F in (32,33,34) else "h264"

def _is_keyframe(nal, codec):
    """Ищем IDR NAL по start code 00 00 00 01 или 00 00 01"""
    for sc, offset in ((bytes([0,0,0,1]), 4), (bytes([0,0,1]), 3)):
        idx = 0
        while True:
            pos = nal.find(sc, idx)
            if pos == -1: break
            nb = nal[pos+offset] if pos+offset < len(nal) else 0
            if codec == "hevc":
                if (nb>>1)&0x3F in (16,17,18,19,20,21): return True
            else:
                if (nb&0x1F) == 5: return True
            idx = pos + offset
    return False

_AUDIO_CODEC_MAP = {
    1: ("alaw",  8000),
    2: ("mulaw", 8000),
    3: ("g726",  8000),
    4: ("aac",  44100),
}
_AUDIO_RATE_MAP = {1:8000, 2:16000, 3:32000, 4:44100, 5:48000}

def _detect_audio(hdr):
    if hdr is None or len(hdr)<14: return "mulaw", 8000
    fmt, default_rate = _AUDIO_CODEC_MAP.get(hdr[8], ("mulaw",8000))
    rate = _AUDIO_RATE_MAP.get(hdr[12], default_rate)
    return fmt, rate

# ── WebSocket фрейм ───────────────────────────────────────────────────────────

def _ws_frame(payload, opcode=0x02):
    """Создаём WebSocket фрейм (server→client, без маскировки)"""
    if isinstance(payload, str): payload = payload.encode()
    length = len(payload)
    if length < 126:
        hdr = bytes([0x80|opcode, length])
    elif length < 65536:
        hdr = struct.pack('!BBH', 0x80|opcode, 126, length)
    else:
        hdr = struct.pack('!BBQ', 0x80|opcode, 127, length)
    return hdr + payload

# ── WebSocket прокси (главный поток без ffmpeg) ───────────────────────────────

ws_sessions = {}   # cam_id → WSProxySession
ws_lock     = threading.Lock()

class WSProxySession:
    """NVR WebSocket → browser WebSocket, NAL напрямую в VideoDecoder"""
    def __init__(self, cam):
        self.cam        = cam
        self.stop_event = threading.Event()
        self.thread     = None
        self.codec      = None
        self.has_audio  = False
        self.audio_fmt  = "mulaw"
        self.audio_rate = 8000
        self.ready      = False
        self.clients    = []
        self.cli_lock   = threading.Lock()

    def _config_json(self):
        return json.dumps({
            "codec":      self.codec,
            "has_audio":  self.has_audio,
            "audio_fmt":  self.audio_fmt,
            "audio_rate": self.audio_rate,
        })

    def add_client(self, q):
        with self.cli_lock:
            if self.ready:
                try: q.put_nowait(("text", self._config_json()))
                except: return False
            self.clients.append(q)
        return True

    def remove_client(self, q):
        with self.cli_lock:
            if q in self.clients: self.clients.remove(q)

    def _broadcast(self, item):
        with self.cli_lock:
            dead = []
            for q in self.clients:
                try: q.put_nowait(item)
                except: dead.append(q)
            for q in dead: self.clients.remove(q)

    def start(self, session):
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, args=(session,), daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        with self.cli_lock:
            for q in self.clients:
                try: q.put_nowait(None)
                except: pass
            self.clients.clear()

    def wait_stopped(self, timeout=6):
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=timeout)

    def _run(self, session):
        cam = self.cam
        audio_override = cam.get("audio_fmt")
        ws = None
        try:
            ws = websocket.WebSocket()
            ws.connect(cam["nvr_ws"], timeout=10)
            ws.send(json.dumps({"action":"play",
                "data":{"channel":cam["channel"],"stream":0,"sessionID":session}}))

            # Зондируем 2 сек: определяем кодек и наличие аудио
            codec=None; has_audio=False; audio_hdr=None
            vq=[]; aq=[]

            ws.settimeout(10)
            deadline = time.time() + 10
            while not self.stop_event.is_set() and time.time() < deadline:
                try: data = ws.recv()
                except: break
                if not isinstance(data,bytes) or len(data)<=WS_HEADER_SIZE: continue
                payload = data[WS_HEADER_SIZE:]
                if data[4]==1:
                    codec=_detect_codec(payload); vq.append(payload); break
                elif data[4]==0:
                    if audio_hdr is None: audio_hdr=data[:36]
                    has_audio=True; aq.append(payload)

            if self.stop_event.is_set() or codec is None: return

            ws.settimeout(1)
            deadline2 = time.time() + 2
            while not self.stop_event.is_set() and time.time() < deadline2:
                try: data = ws.recv()
                except: continue
                if not isinstance(data,bytes) or len(data)<=WS_HEADER_SIZE: continue
                payload = data[WS_HEADER_SIZE:]
                if data[4]==1: vq.append(payload)
                elif data[4]==0:
                    if audio_hdr is None: audio_hdr=data[:36]
                    has_audio=True; aq.append(payload)

            if self.stop_event.is_set(): return

            audio_fmt, audio_rate = _detect_audio(audio_hdr)
            if not AUDIO_ENABLED or audio_override=="none":
                has_audio=False
            elif audio_override in ("alaw","mulaw","g726","aac"):
                audio_fmt=audio_override

            # Для HEVC транскодируем → финальный кодек h264
            out_codec = "h264" if codec == "hevc" else codec
            self.codec=out_codec; self.has_audio=has_audio
            self.audio_fmt=audio_fmt; self.audio_rate=audio_rate
            self.ready=True

            log(f"[main{cam['id']:02d}] СТАРТ канал {cam['channel']} video={codec}{'→h264' if codec=='hevc' else ''} audio={'нет' if not has_audio else f'{audio_fmt}@{audio_rate}'} ({cam['nvr_host']})")

            # Отправляем финальный конфиг клиентам (один раз)
            cfg = self._config_json()
            with self.cli_lock:
                for q in self.clients:
                    try: q.put_nowait(("text", cfg))
                    except: pass

            if codec == "hevc":

                ff = subprocess.Popen([
                    "ffmpeg", "-y",
                    "-f", "hevc", "-i", "pipe:0",
                    "-vf", f"scale={MAIN_W}:{MAIN_H}",
                    "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
                    "-profile:v", "baseline", "-level", "4.2",
                    "-g", "25", "-keyint_min", "10",
                    "-f", "h264", "pipe:1",
                ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)

                def _ff_reader():
                    SC4 = bytes([0,0,0,1])
                    buf = b""
                    # Оборачиваем stdout в BufferedReader для read1()
                    import io
                    stdout = io.BufferedReader(ff.stdout.raw if hasattr(ff.stdout, 'raw') else ff.stdout, buffer_size=262144)
                    while not self.stop_event.is_set():
                        try: chunk = stdout.read1(65536)
                        except: break
                        if not chunk: break
                        buf += chunk

                        while True:
                            # Быстрый поиск следующего start code через bytes.find
                            pos = 4
                            next_frame = -1
                            while True:
                                pos = buf.find(SC4, pos)
                                if pos == -1 or pos + 4 >= len(buf): break
                                nt = buf[pos+4] & 0x1F
                                if nt in (7, 5, 1):
                                    next_frame = pos; break
                                pos += 4

                            if next_frame == -1: break

                            frame = buf[:next_frame]
                            buf = buf[next_frame:]

                            if len(frame) < 5: continue
                            nt0 = frame[4] & 0x1F
                            is_key = (nt0 in (7, 5))
                            self._broadcast(("video", frame, is_key))
                threading.Thread(target=_ff_reader, daemon=True).start()

                for pkt in vq:
                    try: ff.stdin.write(pkt)
                    except: break

                ws.settimeout(None)
                while not self.stop_event.is_set():
                    try:
                        data = ws.recv()
                        if not isinstance(data,bytes) or len(data)<=WS_HEADER_SIZE: continue
                        payload = data[WS_HEADER_SIZE:]
                        if data[4]==1:
                            try: ff.stdin.write(payload)
                            except (BrokenPipeError,OSError): break
                        elif data[4]==0 and has_audio:
                            self._broadcast(("audio", payload))
                    except (BrokenPipeError,OSError): break
                    except Exception as e:
                        if not self.stop_event.is_set():
                            log(f"[main{cam['id']:02d}] loop error: {e}")
                        break
                try: ff.stdin.close()
                except: pass
                try: ff.wait(timeout=3)
                except: _kill_proc(ff)
            else:
                # H264 — напрямую
                for pkt in vq:
                    self._broadcast(("video", pkt, _is_keyframe(pkt, codec)))
                if has_audio:
                    for pkt in aq:
                        self._broadcast(("audio", pkt))

                ws.settimeout(None)
                while not self.stop_event.is_set():
                    try:
                        data = ws.recv()
                        if not isinstance(data,bytes) or len(data)<=WS_HEADER_SIZE: continue
                        payload = data[WS_HEADER_SIZE:]
                        if data[4]==1:
                            self._broadcast(("video", payload, _is_keyframe(payload, codec)))
                        elif data[4]==0 and has_audio:
                            self._broadcast(("audio", payload))
                    except (BrokenPipeError,OSError): break
                    except Exception as e:
                        if not self.stop_event.is_set():
                            log(f"[main{cam['id']:02d}] loop error: {e}")
                        break

        except Exception as e:
            log(f"[main{cam['id']:02d}] Ошибка: {e}")
        finally:
            if ws:
                try: ws.close()
                except: pass
            with ws_lock:
                if ws_sessions.get(cam["id"]) is self:
                    ws_sessions.pop(cam["id"], None)
            log(f"[main{cam['id']:02d}] СТОП канал {cam['channel']} ({cam['nvr_host']})")

def start_main_stream(cam_id):
    with ws_lock:
        old = ws_sessions.pop(cam_id, None)
    if old:
        old.stop()
        def _deferred():
            old.wait_stopped(timeout=6)
            _do_start(cam_id)
        threading.Thread(target=_deferred, daemon=True).start()
    else:
        _do_start(cam_id)

def _do_start(cam_id):
    with ws_lock:
        if cam_id in ws_sessions: return
        sess = WSProxySession(cam_map[cam_id])
        ws_sessions[cam_id] = sess
    cam = cam_map[cam_id]
    session = nvr_login(cam["nvr_http"], cam["nvr_host"], cam["nvr_user"], cam["nvr_pass"])
    if not session:
        log(f"[main{cam_id:02d}] Нет сессии")
        with ws_lock: ws_sessions.pop(cam_id, None)
        return
    sess.start(session)

def stop_main_stream(cam_id):
    log(f"[main{cam_id:02d}] Запрос остановки")
    with ws_lock:
        sess = ws_sessions.pop(cam_id, None)
    if sess: sess.stop()

def kill_all_main():
    with ws_lock:
        for sess in list(ws_sessions.values()): sess.stop()
        ws_sessions.clear()

# ── Суб-потоки (сетка) ────────────────────────────────────────────────────────

def _make_ffmpeg(codec,scale,q,fps):
    return subprocess.Popen(
        ["ffmpeg","-f",codec,"-i","pipe:0","-vf",f"scale={scale},fps={fps}",
         "-f","mjpeg","-q:v",str(q),"-"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)

def _ws_feed(nvr_ws,channel,stream_no,proc_holder,scale,q,fps,session,stop_event=None):
    proc=None
    recv_count = 0
    timeout_exit = False  # Флаг: вышли ли из-за таймаута
    try:
        ws=websocket.WebSocket(); ws.connect(nvr_ws,timeout=10)
        ws.settimeout(5)  # Таймаут на recv операции
        ws.send(json.dumps({"action":"play","data":{"channel":channel,"stream":stream_no,"sessionID":session}}))
        codec_detected=False
        timeout_count = 0
        while True:
            if stop_event and stop_event.is_set(): break
            try:
                data=ws.recv()
                timeout_count = 0  # Сбрасываем счетчик при успешном получении данных
                recv_count += 1
            except websocket.WebSocketTimeoutException:
                # Таймаут на recv - нормальная ситуация, но считаем их
                timeout_count += 1
                if timeout_count > 180:  # После 15 минут (180*5=900с) без видео - выходим
                    if recv_count == 0:
                        log(f"[ws_feed] Канал {channel}: NVR не отдает поток (15 минут ожидания)")
                    else:
                        log(f"[ws_feed] Канал {channel}: нет данных уже {timeout_count*5}с (получено пакетов: {recv_count}) - останавливаем")
                    timeout_exit = True  # Запоминаем что вышли по таймауту
                    break  # Выход в любом случае
                continue
            if not isinstance(data,bytes) or len(data)<=WS_HEADER_SIZE: 
                continue
            if data[4]!=1: 
                # Это аудио или другие данные - логируем только первые разы
                if recv_count <= 3:
                    log(f"[ws_feed] Канал {channel}: тип пакета={data[4]} (не видео), всего получено: {recv_count}")
                continue
            nal=data[WS_HEADER_SIZE:]
            if not codec_detected:
                codec=_detect_codec(nal); codec_detected=True
                proc=_make_ffmpeg(codec,scale,q,fps); proc_holder[0]=proc
                store=proc_holder[1]; cam_id=proc_holder[2]
                with proc_lock: sub_processes[cam_id]=proc
                threading.Thread(target=_read_frames,args=(proc,store,cam_id),daemon=True).start()
            if stop_event and stop_event.is_set(): break
            try: proc.stdin.write(nal)
            except (BrokenPipeError,OSError): 
                log(f"[ws_feed] Обрыв pipe для канала {channel}")
                break
    except websocket.WebSocketException as e:
        log(f"[ws_feed] WebSocket ошибка канал {channel}: {e}")
    except Exception as e:
        log(f"[ws_feed] Ошибка канал {channel}: {e}")
    finally:
        try: ws.close()
        except: pass
        if proc:
            try: proc.stdin.close()
            except: pass
    
    # Возвращаем флаг: была ли остановка из-за таймаута
    return timeout_exit

def _read_frames(proc,store,cam_id):
    buf=b""
    while True:
        chunk=proc.stdout.read1(32768) if hasattr(proc.stdout,'read1') else proc.stdout.read(32768)
        if not chunk: break
        buf+=chunk
        
        # Держим только последний кадр — MJPEG не нужна очередь
        s=buf.rfind(b'\xff\xd8'); e=buf.rfind(b'\xff\xd9')
        if s!=-1 and e!=-1 and e>s:
            with frames_lock: store[cam_id]=buf[s:e+2]
            buf=buf[e+2:]
        elif len(buf) > 200000:
            buf=buf[-100000:]  # сбрасываем накопленный мусор

def capture_sub(cam):
    cam_id=cam["id"]; channel=cam["channel"]
    nvr_host=cam["nvr_host"]; nvr_ws=cam["nvr_ws"]
    nvr_http=cam["nvr_http"]; nvr_user=cam["nvr_user"]; nvr_pass=cam["nvr_pass"]
    restart_count = 0
    stream_to_try = 1  # Всегда используем только суб-поток
    
    # Запускаем отдельный session keeper для ЭТОЙ камеры
    threading.Thread(target=camera_session_refresh, args=(cam, nvr_http, nvr_user, nvr_pass), daemon=True).start()
    
    while True:
        # Получаем индивидуальную сессию для этой камеры (полная авторизация)
        session = get_camera_session(cam, nvr_http, nvr_user, nvr_pass)
        if not session: time.sleep(2); continue
        
        nvr_available[nvr_host].wait()
        log(f"[sub{cam_id:02d}] СТАРТ канал {channel} stream={stream_to_try} (попытка {restart_count+1}) ({nvr_host})")
        p = None
        timeout_exit = False
        try:
            ph=[None,sub_frames,cam_id]
            f=threading.Thread(target=_ws_feed,
                args=(nvr_ws,channel,stream_to_try,ph,f"{SUB_W}:{SUB_H}",SUB_Q,SUB_FPS,session),daemon=True)
            f.start(); f.join()
            timeout_exit = ph[0]  # Получаем флаг таймаута
            p=ph[0]
            if p:
                with proc_lock: sub_processes.pop(cam_id,None)
                p.wait()
            log(f"[sub{cam_id:02d}] СТОП канал {channel} ({nvr_host})")
        except Exception as e: 
            log(f"[sub{cam_id:02d}] Ошибка: {e}")
        
        # Если вышли по таймауту (15 мин без данных) - долгая пауза 1 час
        if timeout_exit:
            delay = 3600  # 1 час = 3600 секунд
            log(f"[sub{cam_id:02d}] Канал {channel}: пауза {delay/60:.0f} мин перед рестартом")
            time.sleep(delay)
            restart_count = 0  # Сбрасываем счетчик после долгой паузы
        else:
            # Обычная экспоненциальная задержка: 5s, 10s, 20s, 40s, макс 120s
            delay = min(5 * (2 ** restart_count), 120)
            log(f"[sub{cam_id:02d}] Задержка {delay:.1f}с перед рестартом")
            time.sleep(delay)
            restart_count += 1

# ── HTTP ──────────────────────────────────────────────────────────────────────

class MJPEGHandler(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def do_GET(self):
        if not self.path.startswith("/stream"):
            self.send_response(404); self.end_headers(); return
        cam_id=self.server.cam_id; store=self.server.frame_store
        self.send_response(200)
        self.send_header("Content-Type","multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control","no-cache")
        self.send_header("Access-Control-Allow-Origin","*")
        self.end_headers()
        last=None
        try:
            while True:
                with frames_lock: frame=store.get(cam_id)
                if frame and frame is not last:
                    try:
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n")
                        self.wfile.write(frame); self.wfile.write(b"\r\n")
                        self.wfile.flush()
                        last=frame
                    except (BrokenPipeError,ConnectionResetError): break
                else:
                    time.sleep(0.02)
        except: pass

class ControlHandler(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def _cors(self): self.send_header("Access-Control-Allow-Origin","*")
    def _json(self,d,code=200):
        body=json.dumps(d).encode()
        self.send_response(code); self._cors()
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        parsed=urlparse(self.path); params=parse_qs(parsed.query)

        if parsed.path in ("/","/index.html"):
            with open(os.path.join(_base_dir,"index.html"),"rb") as f: body=f.read()
            self.send_response(200); self._cors()
            self.send_header("Content-Type","text/html; charset=utf-8")
            self.send_header("Content-Length",str(len(body)))
            self.end_headers(); self.wfile.write(body); return

        if parsed.path=="/status":
            try: sid=int(params.get("screen",[0])[0])
            except: sid=0
            if sid>=len(SCREENS): self._json({"error":"not found"},404); return
            host=SCREENS[sid]["nvr_host"]
            self._json({"host":host,"available":nvr_available[host].is_set()}); return

        if parsed.path=="/config":
            try: sid=int(params.get("screen",[0])[0])
            except: sid=0
            if sid>=len(SCREENS): self._json({"error":"not found"},404); return
            sc=SCREENS[sid]
            self._json({
                "screen_id":sid, "grid":sc["grid"], "spinner_ms":SPINNER_MS,
                "cameras":[{"id":c["id"],"name":c["name"],"sub_port":c["sub_port"]}
                           for c in CAMERAS if c["id"] in sc["cam_ids"]]
            }); return

        # /ws_main/<cam_id> — WebSocket для фуллскрина (VideoDecoder в браузере)
        if parsed.path.startswith("/ws_main/"):
            try: cid=int(parsed.path.split("/")[2])
            except: self.send_response(400); self.end_headers(); return

            key=self.headers.get("Sec-WebSocket-Key","")
            if not key: self.send_response(400); self.end_headers(); return

            # Ждём пока сессия будет создана (nvr_login занимает ~3-5 сек)
            deadline = time.time() + 15
            while time.time() < deadline:
                with ws_lock: sess = ws_sessions.get(cid)
                if sess: break
                time.sleep(0.2)
            else:
                self.send_response(503); self.end_headers(); return

            accept=base64.b64encode(
                hashlib.sha1((key+WS_GUID).encode()).digest()).decode()
            self.send_response(101,"Switching Protocols")
            self.send_header("Upgrade","websocket")
            self.send_header("Connection","Upgrade")
            self.send_header("Sec-WebSocket-Accept",accept)
            self.end_headers()

            with ws_lock: sess=ws_sessions.get(cid)
            if sess is None: return

            q=_queue.Queue(maxsize=300)
            sess.add_client(q)
            sock=self.connection

            # Читаем входящие фреймы браузера (ping/pong/close) в фоне
            def _read_browser():
                try:
                    while True:
                        hdr = sock.recv(2)
                        if len(hdr) < 2: break
                        opcode = hdr[0] & 0x0F
                        length = hdr[1] & 0x7F
                        masked = bool(hdr[1] & 0x80)
                        if length == 126: length = int.from_bytes(sock.recv(2),'big')
                        elif length == 127: length = int.from_bytes(sock.recv(8),'big')
                        mask = sock.recv(4) if masked else b'\x00'*4
                        payload = bytearray(sock.recv(length))
                        if masked:
                            for i in range(len(payload)): payload[i] ^= mask[i%4]
                        if opcode == 0x09:  # ping → pong
                            sock.sendall(_ws_frame(bytes(payload), opcode=0x0A))
                        elif opcode == 0x08:  # close
                            q.put_nowait(None); break
                except Exception: q.put_nowait(None)
            threading.Thread(target=_read_browser, daemon=True).start()

            def _sender():
                try:
                    while True:
                        try:
                            item = q.get(timeout=5)
                        except _queue.Empty:
                            try: sock.sendall(_ws_frame(b"", opcode=0x09))
                            except: break
                            continue
                        if item is None: break
                        if item[0]=="text":
                            sock.sendall(_ws_frame(item[1], opcode=0x01))
                        elif item[0]=="video":
                            flag=b'\x81' if item[2] else b'\x01'
                            sock.sendall(_ws_frame(flag+item[1], opcode=0x02))
                        elif item[0]=="audio":
                            sock.sendall(_ws_frame(b'\x02'+item[1], opcode=0x02))
                except Exception: pass
                finally: sess.remove_client(q)
            threading.Thread(target=_sender, daemon=True).start()
            # Ждём завершения отправщика
            done = threading.Event()
            _orig_remove = sess.remove_client
            def _remove_and_notify(q):
                _orig_remove(q)
                done.set()
            sess.remove_client = _remove_and_notify
            done.wait()
            return

        try: cam_id=int(params.get("id",[None])[0])
        except: self._json({"error":"missing id"},400); return
        if parsed.path=="/start_main": start_main_stream(cam_id); self._json({"ok":True})
        elif parsed.path=="/stop_main": stop_main_stream(cam_id); self._json({"ok":True})
        else: self._json({"error":"not found"},404)

class ThreadingHTTPServer(ThreadingMixIn,HTTPServer):
    daemon_threads=True; allow_reuse_address=True
    def __init__(self,cam_id,frame_store,addr,handler):
        self.cam_id=cam_id; self.frame_store=frame_store
        super().__init__(addr,handler)

class ThreadingControlServer(ThreadingMixIn,HTTPServer):
    daemon_threads=True; allow_reuse_address=True

# ── Запуск ────────────────────────────────────────────────────────────────────

def start_mjpeg_server(cam,store,port):
    ThreadingHTTPServer(cam["id"],store,("0.0.0.0",port),MJPEGHandler).serve_forever()

def start_control_server():
    srv=ThreadingControlServer(("0.0.0.0",CONTROL_PORT),ControlHandler)
    log(f"[control] API порт {CONTROL_PORT}")
    srv.serve_forever()

def shutdown(sig,frame):
    log("Остановка..."); kill_all_subs(); kill_all_main()
    try: subprocess.run(["pkill","-9","-f","ffmpeg.*pipe"],timeout=3)
    except: pass
    sys.exit(0)

if __name__=="__main__":
    signal.signal(signal.SIGINT,shutdown)
    signal.signal(signal.SIGTERM,shutdown)
    log(f"=== CCTV — {len(SCREENS)} экрана, {len(CAMERAS)} камер (WebCodecs) ===")

    # Логирование уникальных NVR хостов
    _unique_hosts = [nvr["nvr_host"] for nvr in NVR_HOSTS]
    log(f"[config] Уникальные NVR хосты: {', '.join(_unique_hosts)} (всего: {len(_unique_hosts)})")

    # Авторизация и запуск session keeper для каждого уникального NVR
    for nvr in NVR_HOSTS:
        h=nvr["nvr_host"]; p=nvr["nvr_port"]
        log(f"[auth:{h}] Логин...")
        if not nvr_login(nvr["nvr_http"],h,nvr["nvr_user"],nvr["nvr_pass"]):
            log(f"[auth:{h}] Ошибка. Проверь credentials.json."); sys.exit(1)
        threading.Thread(target=session_keeper,args=(nvr["nvr_http"],h,nvr["nvr_user"],nvr["nvr_pass"]),daemon=True).start()

    # Мониторинг доступности каждого уникального NVR (без авторизации)
    for nvr in NVR_HOSTS:
        h=nvr["nvr_host"]; p=nvr["nvr_port"]
        threading.Thread(target=check_server,args=(h,p),daemon=True).start()

    threading.Thread(target=start_control_server,daemon=True).start()

    # Запуск потоков для каждой камеры - индивидуальная авторизация
    for cam in CAMERAS:
        threading.Thread(target=capture_sub,args=(cam,),daemon=True).start()
        time.sleep(STAGGER_DELAY)
        threading.Thread(target=start_mjpeg_server,args=(cam,sub_frames,cam["sub_port"]),daemon=True).start()

    for sc in SCREENS:
        log(f"[screen {sc['id']}] http://localhost:{CONTROL_PORT}/?screen={sc['id']}  ({len(sc['cam_ids'])} камер)")

    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        shutdown(None,None)

# CCTV Streaming System

Python-based video streaming solution for NVR cameras with HeroSpeed authentication support.

## Features

- Multi-screen, multi-NVR support
- WebSocket-based MJPEG streaming
- WebCodecs integration (no ffmpeg required!)
- HeroSpeed NVR authentication (two-step SHA256 challenge-response)
- Automatic session management and reconnection
- Sub-stream generation for thumbnails
- Real-time monitoring and health checks

## Architecture

```
NVR Cameras → Python WebSocket Proxy → Browser (WebCodecs)
     ↓
Sub-streams (thumbnails)
     ↓
Main screen (fullscreen via fullscreen API)
```

## Installation

### Prerequisites

- Python 3.7+
- Node.js and npm (for MCP Server tools)
- Access to HeroSpeed/Longse NVR devices

### Dependencies

```bash
pip install requests websocket-client
```

## Configuration

### 1. config.json

Define your screens, cameras, and NVR hosts:

```json
{
  "ports": {
    "api": 8080,
    "sub_base": 9000
  },
  "screens": [
    {
      "nvr": {"host": "192.168.1.100", "port": 80},
      "grid": "2x2",
      "cameras": [
        {"name": "Camera 1", "channel": 1},
        {"name": "Camera 2", "channel": 2}
      ]
    }
  ],
  "sub_stream": {
    "width": 320,
    "height": 240,
    "fps": 10,
    "quality": 50
  },
  "main_stream": {
    "width": 1920,
    "height": 1080,
    "spinner_ms": 100
  },
  "monitor": {
    "check_interval": 30,
    "connect_timeout": 10,
    "session_interval": 300,
    "stagger_delay": 2
  },
  "audio_enabled": true
}
```

### 2. credentials.json

Store NVR credentials (⚠️ **Never commit this file!**):

```json
[
  {
    "host": "192.168.1.100",
    "user": "admin",
    "password": "your_password"
  }
]
```

Set proper permissions:
```bash
chmod 600 credentials.json
```

## Usage

### Start the streaming server

```bash
python cctv_stream.py
```

The server will start on port 8080 (configurable in config.json).

### Access the web interface

Open your browser and navigate to:
```
http://localhost:8080
```

## Authentication Method

This system implements HeroSpeed's two-step authentication:

1. **Login Capabilities Request** - Get challenge, salt, and session ID
2. **Password Hash Calculation** - Multi-round SHA256 hashing with iterations
3. **Login Request** - Submit hashed credentials
4. **Session Cookie** - Use cookie for subsequent API calls

Reference implementation: [herospeed-api-session-manager](https://github.com/allixx/herospeed-api-session-manager)

## Project Structure

```
cctvo/
├── cctv_stream.py      # Main streaming server
├── config.json         # Configuration (safe to commit)
├── credentials.json    # Credentials (DO NOT COMMIT)
├── index.html          # Web interface
├── .gitignore          # Git ignore rules
└── README.md           # This file
```

## Security Notes

⚠️ **Important Security Considerations:**

1. Never commit `credentials.json` to version control
2. The `.gitignore` file excludes sensitive files
3. HeroSpeed stores passwords in cleartext (firmware limitation)
4. Use strong passwords and restrict network access
5. Session cookies expire after a few minutes

## Monitoring & Health Checks

The system automatically:
- Monitors NVR availability
- Manages session lifecycles
- Restarts failed connections
- Generates sub-streams for thumbnails
- Logs connection status changes

## Troubleshooting

### Connection Issues

- Verify NVR is accessible on the network
- Check credentials in `credentials.json`
- Ensure ports are not blocked by firewall
- Review logs for authentication errors

### No Video Stream

- Check camera channel numbers match NVR configuration
- Verify sub-stream settings are supported
- Monitor resource usage (CPU/memory)
- Check WebSocket connection in browser console

### Session Expiration

Sessions are automatically refreshed every 5 minutes (configurable via `session_interval`).

## License

This project is provided as-is for educational and personal use.

## References

- [HeroSpeed API Session Manager](https://github.com/allixx/herospeed-api-session-manager)
- [HeroSpeed Official Site](https://herospeed.net)
- WebCodecs API Documentation

## Author

Created for CCTV surveillance systems with HeroSpeed/Longse NVR devices.

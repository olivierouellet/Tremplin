# Native App Strategy

## Context

Tremplin currently serves its scoreboard and mobile views as HTML/CSS pages rendered in a browser. This document summarises a conversation about replacing those with native clients.

## Target Stack

| Surface | Technology | Replaces |
|---|---|---|
| iPhone | Swift (WKWebView → native UI) | Mobile web app |
| Android | Kotlin (WebView → native UI) | Mobile web app |
| Raspberry Pi / TV | Python + Qt (PySide6) | Chromium browser in kiosk mode |
| Server | FastAPI + plain WebSockets | Flask + Flask-SocketIO |

## Why Move Away From HTML/CSS

### Mobile (phones)
- Swimmer names truncate with CSS `text-overflow: ellipsis` — no way to shrink text to fit, only cut it
- Safe area handling (`env(safe-area-inset-*)`) is fragile and leaks into complex layout hacks
- The current `mobile.html` shell embeds 3 iframes inside a browser, adding a layer of complexity that causes its own rendering issues
- Native `UILabel` (iOS) and `TextView` (Android) support auto-shrink to fit (`adjustsFontSizeToFitWidth`, `autoSizeTextType`) — the name truncation problem disappears

### Raspberry Pi / TV
- Chromium uses significant RAM and CPU on RPi just to run a layout engine
- 4K rendering in a browser on RPi is sluggish — the GPU is not used efficiently
- Python + Qt (PySide6) renders natively, measures text precisely before drawing, and handles 4K/HiDPI as a first-class feature

## Why Drop Socket.IO

Socket.IO is a JavaScript-first library. Native clients exist (`socket.io-client-swift`, Paho Android) but are less maintained and add dependency risk. The actual usage in Tremplin is minimal:

1. Connect to a namespace
2. Emit `join_meet` once with the meet ID
3. Receive `update_scoreboard` and `reload` events

Plain WebSockets handle all three with zero third-party libraries on iOS (`URLSessionWebSocketTask`, built into the OS since iOS 13) and with OkHttp on Android (industry standard). Python uses the `websockets` library (excellent).

## Why FastAPI over Flask

Flask is WSGI (synchronous). Flask-SocketIO works around this with `eventlet`/`gevent` monkey-patching, which is fragile. FastAPI is ASGI (async-native) — WebSockets are built in, no extra library needed, and broadcasting to many concurrent clients (phones at a swim meet) is clean and efficient.

Server-side broadcast becomes straightforward:

```python
async def broadcast(meet_id: str, data: dict):
    for ws in connections[meet_id]:
        await ws.send_json(data)
```

Nothing changes on the client side — Swift, Kotlin, and Python all connect to a plain `ws://` endpoint regardless of server framework.

## WebSocket vs MQTT

MQTT was considered as an alternative (publish/subscribe, built-in reconnect, lightweight). Decision: stay with WebSockets because:

- iOS has **native** WebSocket support (no library); MQTT requires `CocoaMQTT` (smaller ecosystem)
- Python `paho-mqtt` is excellent, but the iOS side tips the balance
- Manual reconnect for WebSockets is ~50 lines of boilerplate (detect silent drops via ping, reconnect on app foreground) — manageable and written once
- For a scoreboard, missing 2–3 seconds during a reconnect is not critical — the next update catches up

## Raspberry Pi Hardware

### Display RPi (Qt scoreboard on TV)

| Resolution | Minimum | Comfortable |
| --- | --- | --- |
| 1080p | RPi 4 2GB | RPi 4 2GB |
| 4K | RPi 4 4GB | RPi 4 4GB |

RPi 3 (1GB) is ruled out for the display role:

- HDMI 1.3 caps at 1080p — no 4K regardless of software
- 1GB RAM is tight once the OS and Qt process are both running

RPi 4 2GB is the sweet spot for 1080p: Python + Qt sits around 80–150MB, leaving ample headroom. The 4GB/8GB models are unnecessary for a scoreboard.

Note on Qt version: **PyQt5** is safer than PySide6 on lower-RAM RPi 4 hardware. PySide6 (Qt 6) is fine on 4GB+.

### Server RPi (timing console + WebSocket relay)

The server process (FastAPI + uvicorn + serial port handling) uses ~100–200MB. Raspberry Pi OS Lite (no desktop) adds ~150MB. Total stays comfortably under 500MB.

- **RPi 3 (1GB) is sufficient** for a dedicated server RPi — it renders nothing, just parses serial data and pushes WebSocket updates
- Even 100 phones connected simultaneously adds only ~5MB of WebSocket state

| Configuration | Recommended hardware |
| --- | --- |
| Server only | RPi 3 (1GB) or any spare RPi |
| Display only (1080p) | RPi 4 2GB |
| Display only (4K) | RPi 4 4GB |
| Server + display on one box | RPi 4 2GB (1080p) or 4GB (4K) |

### Settings panel

The settings panel stays as a browser-based web page — it is used by the meet operator from a laptop on the local network, not by spectators. No native UI needed there.

## Why Not a PWA or WebView Wrapper

- **PWA**: already partially implemented (manifest, A2HS prompt), but iOS App Store does not accept PWAs; text rendering problems remain
- **Capacitor / WebView wrapper**: gets onto the App Store with minimal code change, but the inner pages still render as HTML — the swimmer name problem is unchanged

## Effort Estimate (with AI assistance)

- **Coding only**: 1–2 weeks of developer time across all three clients
- **Process overhead** (Apple Developer account, code signing, App Store review, Play Store review): adds days, cannot be compressed
- **Server migration** (Flask → FastAPI + plain WebSockets): moderate, well-contained, and worth doing at the same time to avoid a second migration later

## Open Questions

- Whether the RPi scoreboard server also migrates to FastAPI, or only the cloud server
- Whether the web browser scoreboard (for users who access via a laptop/desktop) is maintained in parallel with the native clients or deprecated

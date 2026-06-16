"""Outbound relay client — forwards scoreboard events to a cloud server."""
import base64
import os
import threading

import state

_client    = None
_connected = False
_lock      = threading.Lock()
_stop      = threading.Event()
_thread    = None


# ── Meet metadata ──────────────────────────────────────────────────────────────

def _get_metadata():
    flat_labels = dict(state.load_locale(style=state.settings.get('cloud_label_style', 'short')))
    # Add UI strings the cloud templates need
    for key in ('waiting_results', 'no_upcoming', 'no_schedule'):
        val = state._mobile_strings().get(key)
        if val:
            flat_labels[key] = val

    meta = {
        'name':     state.settings.get('cloud_meet_title') or state.settings.get('meet_title') or state.lenex_meet_info.get('name', ''),
        'location': state.lenex_meet_info.get('city')  or state.settings.get('meet_location', ''),
        'sport':    state.settings.get('meet_sport', ''),
        'app_window_title': state.settings.get('app_window_title', ''),
        'meet_date': _last_session_date(),
        'meet_uid':  state.meet_uid(),
        'settings': {
            'num_lanes':            int(state.settings.get('num_lanes', 8)),
            'show_podium':          state.settings.get('show_podium', True),
            'show_name':            state.settings.get('show_name', True),
            'show_club':            state.settings.get('show_club', True),
            'show_delta':           state.settings.get('show_delta', True),
            'show_position':        state.settings.get('show_position', True),
            'show_lane_header':     state.settings.get('show_lane_header', True),
            'show_name_header':     state.settings.get('show_name_header', True),
            'show_club_header':     state.settings.get('show_club_header', True),
            'show_time_header':     state.settings.get('show_time_header', True),
            'show_delta_header':    state.settings.get('show_delta_header', True),
            'show_position_header': state.settings.get('show_position_header', True),
            'theme_colors':         {**state.DEFAULT_THEME_COLORS,
                                     **state.settings.get('theme_colors', {})},
            'theme_fonts':          {**state.DEFAULT_THEME_FONTS,
                                     **state.settings.get('theme_fonts', {})},
            'locale':               state.settings.get('locale', 'en'),
            'labels':               flat_labels,
        },
    }
    icon = _icon_b64()
    if icon:
        meta['settings']['home_icon_b64'] = icon
    picker_img = _picker_image_b64()
    if picker_img:
        meta['settings']['picker_image_b64'] = picker_img
    return meta


def _last_session_date():
    """Latest session date from the loaded LENEX ('YYYY-MM-DD'), or '' if unknown.

    LENEX dates are ISO-formatted, so a lexical max is also the chronological max.
    The cloud uses this to expire a retained meet the day after its final session.
    """
    dates = [s.get('date', '') for s in state.lenex_meet_info.get('sessions', [])]
    dates = [d for d in dates if d]
    return max(dates) if dates else ''


def _icon_b64():
    try:
        if os.path.exists(state.HOME_ICON_PATH):
            with open(state.HOME_ICON_PATH, 'rb') as f:
                return base64.b64encode(f.read()).decode()
    except Exception:
        pass
    return None


def _picker_image_b64():
    try:
        active = state.settings.get('active_picker_image', '')
        if active:
            path = os.path.join(state.PICKER_DIR, active)
            if os.path.exists(path):
                with open(path, 'rb') as f:
                    return base64.b64encode(f.read()).decode()
    except Exception:
        pass
    return None


# ── Public API ─────────────────────────────────────────────────────────────────

def relay_emit(event, data):
    """Forward an event to the cloud relay. Non-blocking; silently drops if not connected."""
    with _lock:
        c, ok = _client, _connected
    if c and ok:
        try:
            c.emit(event, data, namespace='/relay')
        except Exception:
            pass


def update_metadata():
    """Re-send registration metadata to the cloud (call after settings change)."""
    with _lock:
        c, ok = _client, _connected
    if c and ok:
        key = state.settings.get('cloud_relay_key', '').strip()
        try:
            c.emit('register', {**_get_metadata(), 'key': key}, namespace='/relay')
        except Exception:
            pass


def send_schedule(client=None):
    """Send the current schedule snapshot to the cloud. Call after a meet file is loaded.

    Pass `client` directly when calling from inside a connect handler, because
    _client is not yet assigned at that point and relay_emit would silently drop.
    """
    from meet_data import _build_meet_data
    if not (state.lenex_start_list or state.event_info.events):
        return
    try:
        md = _build_meet_data()
        data = {
            'events': [[ev, sorted(heats)] for ev, heats in md['events_grouped']],
            'names':  {str(k): v for k, v in md['event_names'].items()},
            'times':  {str(k): {str(h): t for h, t in v.items()}
                       for k, v in md['heat_times'].items()},
            'start_list': _serialise_start_list(md['start_list']),
        }
        if client is not None:
            client.emit('schedule_snapshot', data, namespace='/relay')
        else:
            relay_emit('schedule_snapshot', data)
    except Exception:
        pass


def _serialise_start_list(sl):
    out = {}
    for ev, heats in sl.items():
        out[str(ev)] = {}
        for ht, lanes in heats.items():
            out[str(ev)][str(ht)] = {}
            for lane, entry in lanes.items():
                out[str(ev)][str(ht)][str(lane)] = {
                    'name':      entry.get('name', ''),
                    'club':      entry.get('club', ''),
                    'seed_time': entry.get('seed_time', ''),
                    'swimmers':  entry.get('swimmers', []),
                }
    return out


# ── Background thread ──────────────────────────────────────────────────────────

def _run():
    global _client, _connected
    import socketio as _sio

    while not _stop.is_set():
        url = state.settings.get('cloud_relay_url', '').strip().rstrip('/')
        key = state.settings.get('cloud_relay_key', '').strip()
        if not url or not key:
            _stop.wait(10)
            continue

        c = _sio.Client(reconnection=False, logger=False, engineio_logger=False)

        @c.event(namespace='/relay')
        def connect():
            global _connected
            meta = {**_get_metadata(), 'key': key}
            c.emit('register', meta, namespace='/relay')
            with _lock:
                _connected = True
            send_schedule(client=c)
            if state._last_results_snapshot:
                try:
                    c.emit('results_snapshot', state._last_results_snapshot, namespace='/relay')
                except Exception:
                    pass
            print('[relay] connected to cloud', flush=True)

        @c.event(namespace='/relay')
        def disconnect():
            global _connected
            with _lock:
                _connected = False
            print('[relay] disconnected from cloud', flush=True)

        @c.on('rejected', namespace='/relay')
        def rejected(data):
            print(f'[relay] rejected: {data.get("reason")}', flush=True)
            c.disconnect()

        try:
            c.connect(url, namespaces=['/relay'], transports=['websocket'])
            with _lock:
                _client = c
            c.wait()
        except Exception as e:
            print(f'[relay] error: {e}', flush=True)
        finally:
            with _lock:
                _connected = False
                if _client is c:
                    _client = None
            try:
                c.disconnect()
            except Exception:
                pass

        if not _stop.is_set():
            _stop.wait(5)


def status():
    with _lock:
        connected = _connected
    running = _thread is not None and _thread.is_alive()
    return {
        'connected': connected,
        'running':   running,
        'url':       state.settings.get('cloud_relay_url', '').strip(),
    }


def start():
    global _thread, _stop
    _stop = threading.Event()
    _thread = threading.Thread(target=_run, daemon=True, name='cloud-relay')
    _thread.start()


def stop():
    _stop.set()
    with _lock:
        c = _client
    if c:
        try:
            c.disconnect()
        except Exception:
            pass

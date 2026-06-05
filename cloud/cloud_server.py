"""Tremplin cloud relay server.

Receives scoreboard events from Pi relays and forwards them to attendees.
One instance handles all active meets; each meet is a SocketIO room.
"""
import base64
import datetime
import glob
import hashlib
import hmac
import json
import os
import secrets
import threading
import tomllib
import urllib.request

import flask
import flask_socketio

DATA_DIR    = os.environ.get('DATA_DIR', '/data')
KEYS_FILE   = os.path.join(DATA_DIR, 'keys.json')
CREDS_FILE  = os.path.join(DATA_DIR, 'credentials.json')
LOCALES_DIR = os.path.join(os.path.dirname(__file__), 'locales')

_locale_cache = {}

def _available_locales():
    locales = []
    for path in sorted(glob.glob(os.path.join(LOCALES_DIR, '*.toml'))):
        code = os.path.splitext(os.path.basename(path))[0]
        with open(path, 'rb') as f:
            name = tomllib.load(f).get('meta', {}).get('name', code)
        locales.append((code, name))
    return locales

def _strings(lang, section):
    available = {code for code, _ in _available_locales()}
    if lang not in available:
        lang = 'en'
    if lang not in _locale_cache:
        with open(os.path.join(LOCALES_DIR, f'{lang}.toml'), 'rb') as f:
            _locale_cache[lang] = tomllib.load(f)
    return _locale_cache[lang].get(section, {})

def _server_lang():
    available = {code for code, _ in _available_locales()}
    stored = _load_creds().get('locale', '')
    if stored and stored in available:
        return stored
    lang = 'en'
    accept = flask.request.headers.get('Accept-Language', 'en')
    for part in accept.replace('-', '_').split(','):
        code = part.split(';')[0].strip().split('_')[0].lower()
        if code in available:
            lang = code
            break
    return lang

def _load_cloud_strings():
    return _strings(_server_lang(), 'cloud')

def _meet_lang(meet):
    return meet.get('settings', {}).get('locale') or 'en'

def _locale_name(code):
    for c, name in _available_locales():
        if c == code:
            return name
    return code

app = flask.Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY') or secrets.token_hex(32)
socketio = flask_socketio.SocketIO(app, async_mode='gevent', cors_allowed_origins='*')

# ── Per-meet state ─────────────────────────────────────────────────────────────
# _meets: meet_id -> {
#   relay_key, relay_sid, organizer, name, location, sport,
#   settings, connected_at,
#   last_scoreboard, last_results, last_next_heats, schedule_data
# }
_meets      = {}
_relay_sids = {}   # relay_sid -> meet_id
_lock       = threading.Lock()


# ── Key management ─────────────────────────────────────────────────────────────

def _load_keys():
    try:
        with open(KEYS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_keys(keys):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(KEYS_FILE, 'w') as f:
        json.dump(keys, f, indent=2)


# ── Admin credentials ──────────────────────────────────────────────────────────

def _hash_password(password, salt=None):
    if salt is None:
        salt = os.urandom(16).hex()
    dk = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100_000)
    return base64.b64encode(dk).decode(), salt


def _load_creds():
    try:
        with open(CREDS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    # First run — migrate from env vars and persist
    user     = os.environ.get('ADMIN_USER', 'admin')
    password = os.environ.get('ADMIN_PASSWORD', '')
    pw_hash, salt = _hash_password(password)
    creds = {'user': user, 'password_hash': pw_hash, 'salt': salt}
    _save_creds(creds)
    return creds


def _save_creds(creds):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CREDS_FILE, 'w') as f:
        json.dump(creds, f, indent=2)


def _check_admin():
    auth = flask.request.authorization
    if not auth:
        return False
    creds = _load_creds()
    if auth.username != creds['user']:
        return False
    pw_hash, _ = _hash_password(auth.password, creds['salt'])
    return hmac.compare_digest(pw_hash, creds['password_hash'])


def _require_admin():
    if not _check_admin():
        return flask.Response('Authentication required', 401,
                              {'WWW-Authenticate': 'Basic realm="Tremplin Admin"'})
    return None


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route('/')
def route_index():
    with _lock:
        meets = [{'id': mid, 'name': m['name'], 'location': m['location'],
                  'sport': m['sport'], 'organizer': m['organizer']}
                 for mid, m in _meets.items()]
    if len(meets) == 1:
        return flask.redirect(flask.url_for('route_mobile', meet=meets[0]['id']))
    return flask.render_template('picker.html', meets=meets, t=_load_cloud_strings())


@app.route('/mobile')
def route_mobile():
    meet_id = flask.request.args.get('meet', '')
    with _lock:
        meet = _meets.get(meet_id)
    if not meet:
        return flask.redirect(flask.url_for('route_index'))
    return flask.render_template('mobile.html',
                                 meet_id=meet_id,
                                 name=meet['name'],
                                 location=meet['location'],
                                 sport=meet['sport'],
                                 t=_strings(_meet_lang(meet), 'mobile'))


@app.route('/mobile/live')
def route_live():
    meet_id = flask.request.args.get('meet', '')
    with _lock:
        meet = _meets.get(meet_id)
    if not meet:
        return flask.render_template('offline.html')
    s = meet.get('settings', {})
    return flask.render_template('live.html',
        meet_id=meet_id,
        meet_title=meet['name'],
        num_lanes=s.get('num_lanes', 8),
        show_lane_header=s.get('show_lane_header', True),
        show_name_header=s.get('show_name_header', True),
        show_club_header=s.get('show_club_header', True),
        show_time_header=s.get('show_time_header', True),
        show_delta_header=s.get('show_delta_header', True),
        show_position_header=s.get('show_position_header', True),
        show_name=s.get('show_name', True),
        show_club=s.get('show_club', True),
        show_delta=s.get('show_delta', True),
        show_position=s.get('show_position', True),
        theme_colors=s.get('theme_colors', _DEFAULT_COLORS),
        theme_fonts=s.get('theme_fonts', _DEFAULT_FONTS),
        labels=s.get('labels', {}),
    )


@app.route('/mobile/results')
def route_results():
    meet_id = flask.request.args.get('meet', '')
    with _lock:
        meet = _meets.get(meet_id)
    if not meet:
        return flask.render_template('offline.html')
    s = meet.get('settings', {})
    return flask.render_template('results.html',
        meet_id=meet_id,
        num_lanes=s.get('num_lanes', 8),
        show_lane_header=s.get('show_lane_header', True),
        show_name_header=s.get('show_name_header', True),
        show_club_header=s.get('show_club_header', True),
        show_time_header=s.get('show_time_header', True),
        show_delta_header=s.get('show_delta_header', True),
        show_position_header=s.get('show_position_header', True),
        show_name=s.get('show_name', True),
        show_club=s.get('show_club', True),
        show_delta=s.get('show_delta', True),
        show_position=s.get('show_position', True),
        theme_colors={**_DEFAULT_COLORS, **s.get('theme_colors', {})},
        theme_fonts=s.get('theme_fonts', _DEFAULT_FONTS),
        labels=s.get('labels', {}),
    )


def _build_heats_json(sched):
    if not sched or not sched.get('events'):
        return []
    names      = sched.get('names', {})
    times      = sched.get('times', {})
    start_list = sched.get('start_list', {})
    heats = []
    for ev, sorted_heats in sched['events']:
        ev_str = str(ev)
        for ht in sorted_heats:
            ht_str = str(ht)
            lanes_data = start_list.get(ev_str, {}).get(ht_str, {})
            lanes = []
            for lane_str in sorted(lanes_data, key=lambda x: int(x) if x.lstrip('-').isdigit() else 0):
                entry = lanes_data[lane_str]
                lanes.append({
                    'lane':      int(lane_str) if lane_str.lstrip('-').isdigit() else lane_str,
                    'name':      entry.get('name', ''),
                    'club':      entry.get('club', ''),
                    'seed_time': entry.get('seed_time', ''),
                    'swimmers':  entry.get('swimmers', []),
                })
            heats.append({
                'event':      ev,
                'heat':       ht,
                'event_name': names.get(ev_str, ''),
                'time':       times.get(ev_str, {}).get(ht_str, ''),
                'lanes':      lanes,
            })
    return heats


@app.route('/mobile/schedule')
def route_schedule():
    meet_id = flask.request.args.get('meet', '')
    with _lock:
        meet = _meets.get(meet_id)
    if not meet:
        return flask.render_template('offline.html')
    s     = meet.get('settings', {})
    sched = meet.get('schedule_data', {})
    heats = _build_heats_json(sched)
    return flask.render_template('schedule.html',
        meet_id=meet_id,
        heats_json=json.dumps(heats),
        has_meet=bool(heats),
        meet_name=meet['name'],
        t=_strings(_meet_lang(meet), 'mobile'),
        labels=s.get('labels', {}),
        theme_colors={**_DEFAULT_COLORS, **s.get('theme_colors', {})},
        theme_fonts={**_DEFAULT_FONTS,  **s.get('theme_fonts', {})},
    )


@app.route('/search_suggestions')
def route_search_suggestions():
    import unicodedata
    def fold(s):
        return unicodedata.normalize('NFD', s.lower()).encode('ascii', 'ignore').decode()

    meet_id = flask.request.args.get('meet_id', '')
    q       = fold(flask.request.args.get('q', '').strip())
    if not q:
        return flask.jsonify([])
    with _lock:
        meet = _meets.get(meet_id)
    if not meet:
        return flask.jsonify([])
    start_list = meet.get('schedule_data', {}).get('start_list', {})
    swimmers, clubs = {}, set()
    for ev, heats in start_list.items():
        for ht, lanes in heats.items():
            for lane, entry in lanes.items():
                if entry.get('club'):
                    clubs.add(entry['club'])
                if entry.get('name'):
                    swimmers[entry['name']] = entry.get('club', '')
                for sw in entry.get('swimmers', []):
                    if sw.get('name'):
                        swimmers[sw['name']] = entry.get('club', '')
    results = []
    for name, club in sorted(swimmers.items()):
        if q in fold(name):
            results.append({'type': 'swimmer', 'name': name, 'club': club})
    for club in sorted(clubs):
        if q in fold(club):
            results.append({'type': 'club', 'name': club})
    return flask.jsonify(results[:20])


@app.route('/logout')
def route_logout():
    return flask.Response(
        'Logged out — <a href="/admin">sign in again</a>', 401,
        {'WWW-Authenticate': 'Basic realm="Tremplin Admin"'}
    )


@app.route('/ping')
def route_ping():
    return 'ok'


@app.route('/manifest/<meet_id>')
def route_manifest(meet_id):
    with _lock:
        meet = _meets.get(meet_id)
    if not meet:
        flask.abort(404)
    has_icon = bool(meet.get('settings', {}).get('home_icon_b64'))
    icons = ([
        {'src': f'/icon/{meet_id}', 'sizes': '192x192', 'type': 'image/png'},
        {'src': f'/icon/{meet_id}', 'sizes': '512x512', 'type': 'image/png'},
    ] if has_icon else [
        {'src': '/static/img/default_mobile_icon.png', 'sizes': '1024x1024', 'type': 'image/png'},
    ])
    manifest = {
        'name':             meet.get('name') or 'Tremplin',
        'short_name':       'Tremplin',
        'start_url':        f'/mobile?meet={meet_id}',
        'display':          'standalone',
        'background_color': '#000000',
        'theme_color':      '#000000',
        'icons':            icons,
    }
    return flask.Response(json.dumps(manifest), mimetype='application/manifest+json')


@app.route('/icon/<meet_id>')
def route_icon(meet_id):
    with _lock:
        meet = _meets.get(meet_id)
    if not meet:
        flask.abort(404)
    icon_b64 = meet.get('settings', {}).get('home_icon_b64', '')
    if not icon_b64:
        flask.abort(404)
    data = base64.b64decode(icon_b64)
    return flask.Response(data, mimetype='image/png',
                          headers={'Cache-Control': 'public, max-age=3600'})


@app.route('/admin/backup/keys')
def route_backup_keys():
    denied = _require_admin()
    if denied:
        return denied
    try:
        with open(KEYS_FILE) as f:
            data = f.read()
    except FileNotFoundError:
        data = '{}'
    return flask.Response(
        data,
        mimetype='application/json',
        headers={'Content-Disposition': 'attachment; filename="tremplin-keys.json"'}
    )


@app.route('/admin/restore/keys', methods=['POST'])
def route_restore_keys():
    denied = _require_admin()
    if denied:
        return denied
    uploaded = flask.request.files.get('keys_file')
    if not uploaded:
        return flask.jsonify({'error': 'No file provided'}), 400
    try:
        data = json.loads(uploaded.read())
        if not isinstance(data, dict):
            raise ValueError('expected a JSON object')
        _save_keys(data)
        return flask.jsonify({'ok': True, 'count': len(data)})
    except (json.JSONDecodeError, ValueError) as e:
        return flask.jsonify({'error': f'Invalid file: {e}'}), 400
    except Exception as e:
        return flask.jsonify({'error': str(e)}), 500


@app.route('/admin/update', methods=['POST'])
def route_update():
    denied = _require_admin()
    if denied:
        return denied

    url    = os.environ.get('DEPLOY_WEBHOOK_URL', '')
    secret = os.environ.get('DEPLOY_WEBHOOK_SECRET', '')
    if not url or not secret:
        return flask.jsonify({'error': 'Deploy webhook not configured'}), 503

    version = flask.request.form.get('version', 'latest')
    try:
        body = json.dumps({'version': version}).encode()
        req = urllib.request.Request(url, data=body, method='POST')
        req.add_header('X-Deploy-Token', secret)
        req.add_header('Content-Type', 'application/json')
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                return flask.jsonify({'status': 'started'})
            return flask.jsonify({'error': f'webhook {resp.status}'}), 502
    except Exception as e:
        return flask.jsonify({'error': str(e)}), 502


@app.route('/admin/update_log')
def route_update_log():
    denied = _require_admin()
    if denied:
        return denied

    webhook_url = os.environ.get('DEPLOY_WEBHOOK_URL', '')
    secret      = os.environ.get('DEPLOY_WEBHOOK_SECRET', '')
    if not webhook_url or not secret:
        return flask.jsonify({'lines': [], 'done': None})

    log_url = webhook_url.rsplit('/', 1)[0] + '/log'
    try:
        req = urllib.request.Request(log_url, method='GET')
        req.add_header('X-Deploy-Token', secret)
        with urllib.request.urlopen(req, timeout=5) as resp:
            return flask.Response(resp.read(), content_type='application/json')
    except Exception:
        return flask.jsonify({'lines': [], 'done': None})


@app.route('/admin/logs')
def route_logs():
    denied = _require_admin()
    if denied:
        return denied

    webhook_url = os.environ.get('DEPLOY_WEBHOOK_URL', '')
    secret      = os.environ.get('DEPLOY_WEBHOOK_SECRET', '')
    if not webhook_url or not secret:
        return flask.jsonify({'ok': False, 'error': 'not configured'}), 503

    source = flask.request.args.get('source', 'app')
    tail   = flask.request.args.get('tail', '300')
    logs_url = webhook_url.rsplit('/', 1)[0] + f'/logs?source={source}&tail={tail}'
    try:
        req = urllib.request.Request(logs_url, method='GET')
        req.add_header('X-Deploy-Token', secret)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return flask.Response(resp.read(), content_type='application/json')
    except Exception as e:
        return flask.jsonify({'ok': False, 'error': str(e)}), 502


@app.route('/admin/versions')
def route_versions():
    denied = _require_admin()
    if denied:
        return denied

    webhook_url = os.environ.get('DEPLOY_WEBHOOK_URL', '')
    secret      = os.environ.get('DEPLOY_WEBHOOK_SECRET', '')
    if not webhook_url or not secret:
        return flask.jsonify({'ok': False, 'error': 'not configured'}), 503

    versions_url = webhook_url.rsplit('/', 1)[0] + '/versions'
    try:
        req = urllib.request.Request(versions_url, method='GET')
        req.add_header('X-Deploy-Token', secret)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return flask.Response(resp.read(), content_type='application/json')
    except Exception as e:
        return flask.jsonify({'ok': False, 'error': str(e)}), 502


@app.route('/admin', methods=['GET', 'POST'])
def route_admin():
    denied = _require_admin()
    if denied:
        return denied

    keys = _load_keys()

    if flask.request.method == 'POST':
        action = flask.request.form.get('action')
        if action == 'add':
            org = flask.request.form.get('organizer', '').strip()
            if org:
                new_key = secrets.token_urlsafe(32)
                keys[new_key] = {
                    'organizer': org,
                    'created':   datetime.date.today().isoformat(),
                    'active':    True,
                }
                _save_keys(keys)
        elif action == 'revoke':
            key = flask.request.form.get('key', '')
            if key in keys:
                keys[key]['active'] = False
                _save_keys(keys)
        elif action == 'delete':
            key = flask.request.form.get('key', '')
            if key in keys:
                del keys[key]
                _save_keys(keys)
        elif action == 'change_locale':
            locale = flask.request.form.get('locale', '')
            creds  = _load_creds()
            creds['locale'] = locale
            _save_creds(creds)
            _locale_cache.clear()
            return flask.redirect(flask.url_for('route_admin'))
        elif action == 'change_credentials':
            t         = _load_cloud_strings()
            creds     = _load_creds()
            cur_pw    = flask.request.form.get('current_password', '')
            new_user  = flask.request.form.get('new_user', '').strip()
            new_pw1   = flask.request.form.get('new_password', '')
            new_pw2   = flask.request.form.get('new_password2', '')
            cur_hash, _ = _hash_password(cur_pw, creds['salt'])
            if not hmac.compare_digest(cur_hash, creds['password_hash']):
                error = t.get('err_wrong_password', 'Incorrect current password.')
            elif new_pw1 != new_pw2:
                error = t.get('err_password_mismatch', 'New passwords do not match.')
            elif not new_pw1:
                error = t.get('err_empty_password', 'Password cannot be empty.')
            else:
                creds['user'] = new_user or creds['user']
                creds['password_hash'], creds['salt'] = _hash_password(new_pw1)
                _save_creds(creds)
                return flask.Response(
                    'Credentials updated — <a href="/admin">sign in with new credentials</a>',
                    401, {'WWW-Authenticate': 'Basic realm="Tremplin Admin"'}
                )
            with _lock:
                active = [{'id': mid, 'name': m['name'], 'location': m['location'],
                           'sport': m['sport'], 'organizer': m['organizer'],
                           'connected_at': m['connected_at'],
                           'language': _locale_name(_meet_lang(m))}
                          for mid, m in _meets.items()]
            return flask.render_template('admin.html', keys=keys, active_meets=active,
                                         t=t, creds_error=error,
                                         locales=_available_locales(),
                                         current_locale=_load_creds().get('locale', ''),
                                         has_deploy=bool(os.environ.get('DEPLOY_WEBHOOK_URL')))
        return flask.redirect(flask.url_for('route_admin'))

    with _lock:
        active = [{'id': mid, 'name': m['name'], 'location': m['location'],
                   'sport': m['sport'], 'organizer': m['organizer'],
                   'connected_at': m['connected_at'],
                   'language': _locale_name(_meet_lang(m))}
                  for mid, m in _meets.items()]

    return flask.render_template('admin.html', keys=keys, active_meets=active,
                                 t=_load_cloud_strings(), creds_error=None,
                                 locales=_available_locales(),
                                 current_locale=_load_creds().get('locale', ''),
                                 has_deploy=bool(os.environ.get('DEPLOY_WEBHOOK_URL')))


# ── SocketIO — /relay namespace (Pi connections) ───────────────────────────────

@socketio.on('connect', namespace='/relay')
def on_relay_connect():
    pass  # auth happens in 'register'


@socketio.on('register', namespace='/relay')
def on_relay_register(data):
    key  = data.get('key', '')
    keys = _load_keys()

    if key not in keys or not keys[key].get('active', False):
        socketio.emit('rejected', {'reason': 'invalid or inactive key'},
                      namespace='/relay', to=flask.request.sid)
        return

    sid = flask.request.sid
    with _lock:
        existing_id = _relay_sids.get(sid)
        if existing_id and existing_id in _meets:
            # Same socket re-registering (e.g. settings change) — update in place
            meet_id = existing_id
            _meets[meet_id].update({
                'name':     data.get('name', ''),
                'location': data.get('location', ''),
                'sport':    data.get('sport', ''),
                'settings': data.get('settings', {}),
            })
        else:
            meet_id = secrets.token_urlsafe(8)
            _meets[meet_id] = {
                'relay_key':       key,
                'relay_sid':       sid,
                'organizer':       keys[key]['organizer'],
                'name':            data.get('name', ''),
                'location':        data.get('location', ''),
                'sport':           data.get('sport', ''),
                'settings':        data.get('settings', {}),
                'connected_at':    datetime.datetime.now().strftime('%H:%M:%S'),
                'last_scoreboard': {},
                'last_results':    {},
                'last_next_heats': {},
                'schedule_data':   {},
            }
            _relay_sids[sid] = meet_id

    socketio.emit('registered', {'meet_id': meet_id},
                  namespace='/relay', to=sid)
    print(f'[cloud] {keys[key]["organizer"]} registered as meet {meet_id}', flush=True)


@socketio.on('disconnect', namespace='/relay')
def on_relay_disconnect():
    sid = flask.request.sid
    with _lock:
        meet_id = _relay_sids.pop(sid, None)
        if meet_id:
            _meets.pop(meet_id, None)
    if meet_id:
        print(f'[cloud] meet {meet_id} disconnected', flush=True)


def _forward(event, data):
    """Cache and broadcast a relay event to all attendees of the sending meet."""
    sid = flask.request.sid
    with _lock:
        meet_id = _relay_sids.get(sid)
        meet    = _meets.get(meet_id)
    if not meet_id or not meet:
        return

    if event == 'update_scoreboard':
        data.pop('running_time', None)
        meet['last_scoreboard'].update(data)
        socketio.emit(event, data, room=f'meet:{meet_id}', namespace='/scoreboard')
    elif event == 'results_snapshot':
        meet['last_results'] = data
        socketio.emit(event, data, room=f'meet:{meet_id}', namespace='/results')
    elif event == 'next_heats':
        meet['last_next_heats'] = data
        socketio.emit(event, data, room=f'meet:{meet_id}', namespace='/results')
    elif event == 'schedule_snapshot':
        meet['schedule_data'] = data
        socketio.emit('schedule_update', room=f'meet:{meet_id}', namespace='/schedule')


@socketio.on('update_scoreboard', namespace='/relay')
def on_relay_scoreboard(d):  _forward('update_scoreboard', d)

@socketio.on('results_snapshot',  namespace='/relay')
def on_relay_results(d):     _forward('results_snapshot', d)

@socketio.on('next_heats',        namespace='/relay')
def on_relay_next_heats(d):  _forward('next_heats', d)

@socketio.on('schedule_snapshot', namespace='/relay')
def on_relay_schedule(d):    _forward('schedule_snapshot', d)

@socketio.on('reload', namespace='/relay')
def on_relay_reload(d):
    sid = flask.request.sid
    with _lock:
        meet_id = _relay_sids.get(sid)
    if not meet_id:
        return
    socketio.emit('reload', room=f'meet:{meet_id}', namespace='/scoreboard')
    socketio.emit('reload', room=f'meet:{meet_id}', namespace='/results')


# ── SocketIO — /scoreboard namespace (attendees) ───────────────────────────────

@socketio.on('connect', namespace='/scoreboard')
def on_scoreboard_connect():
    pass


@socketio.on('join_meet', namespace='/scoreboard')
def on_scoreboard_join(data):
    meet_id = data.get('meet_id', '')
    with _lock:
        meet = _meets.get(meet_id)
    if not meet:
        return
    flask_socketio.join_room(f'meet:{meet_id}')
    if meet['last_scoreboard']:
        socketio.emit('update_scoreboard', meet['last_scoreboard'],
                      namespace='/scoreboard', to=flask.request.sid)


# ── SocketIO — /results namespace (attendees) ──────────────────────────────────

@socketio.on('connect', namespace='/results')
def on_results_connect():
    pass


@socketio.on('join_meet', namespace='/results')
def on_results_join(data):
    meet_id = data.get('meet_id', '')
    with _lock:
        meet = _meets.get(meet_id)
    if not meet:
        return
    flask_socketio.join_room(f'meet:{meet_id}')
    if meet['last_results']:
        socketio.emit('results_snapshot', meet['last_results'],
                      namespace='/results', to=flask.request.sid)
    if meet['last_next_heats']:
        socketio.emit('next_heats', meet['last_next_heats'],
                      namespace='/results', to=flask.request.sid)


# ── SocketIO — /schedule namespace (attendees) ────────────────────────────────

@socketio.on('connect', namespace='/schedule')
def on_schedule_connect():
    pass


@socketio.on('join_meet', namespace='/schedule')
def on_schedule_join(data):
    meet_id = data.get('meet_id', '')
    with _lock:
        meet = _meets.get(meet_id)
    if not meet:
        return
    flask_socketio.join_room(f'meet:{meet_id}')


# ── Theme defaults (fallback when Pi hasn't sent settings yet) ─────────────────

_DEFAULT_COLORS = {
    'bg': '#0d0d0d', 'header_bg': '#1a1a1a', 'header_border': '#2e2e2e',
    'header_label': '#ffffff', 'header_value': '#e0e0e0',
    'th_text': '#666666', 'th_bg': '#1a1a1a',
    'row_odd': '#141414', 'row_even': '#202020', 'row_text': '#e0e0e0',
    'time': '#FFD700', 'delta_better': '#4CAF50', 'delta_worse': '#808080',
    'podium_gold': '#545454', 'podium_silver': '#424242', 'podium_bronze': '#343434',
}
_DEFAULT_FONTS = {
    'family': 'Overpass Mono', 'digits': 'DSEG7Classic', 'timing': 'Overpass Mono',
}


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    os.makedirs(DATA_DIR, exist_ok=True)
    socketio.run(app, host='0.0.0.0', port=5000)

#! /usr/bin/python3
import glob
import os
import sys
import traceback

import flask
import flask_login

import state
from extensions import socketio
from meet_data import _get_next_heats, send_event_info
from worker import _restart_worker, main_thread_worker

from routes.scoreboard import bp as scoreboard_bp
from routes.meet       import bp as meet_bp
from routes.settings   import bp as settings_bp
from routes.debug      import bp as debug_bp
from routes.system     import bp as system_bp
from routes.network    import bp as network_bp
from routes.appearance import bp as appearance_bp

DEBUG = False

app = flask.Flask(__name__)
app.config.update(
    DEBUG=False,
    SECRET_KEY='rimnqiuqnewiornhf7nfwenjmqvliwynhtmlfnlsklrmqwe',
)
socketio.init_app(app, async_mode='gevent')

app.register_blueprint(scoreboard_bp)
app.register_blueprint(meet_bp)
app.register_blueprint(settings_bp)
app.register_blueprint(debug_bp)
app.register_blueprint(system_bp)
app.register_blueprint(network_bp)
app.register_blueprint(appearance_bp)

# ── Auth ───────────────────────────────────────────────────────────────────────

login_manager = flask_login.LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'route_login'


class User(flask_login.UserMixin):
    def __init__(self, id):
        self.id       = id
        self.name     = state.settings['username']
        self.password = state.settings['password']

    def __repr__(self):
        return '%d/%s' % (self.id, self.name)


user = User(0)


@login_manager.user_loader
def load_user(userid):
    return User(userid)


@app.route('/login', methods=['GET', 'POST'])
def route_login():
    if flask.request.method == 'POST':
        if (flask.request.form['username'] == state.settings['username'] and
                flask.request.form['password'] == state.settings['password']):
            flask_login.login_user(User(0))
            return flask.redirect(flask.request.args.get('next'))
        else:
            return flask.abort(401)
    return flask.render_template('login.html')


@app.route('/logout')
@flask_login.login_required
def route_logout():
    flask_login.logout_user()
    return flask.redirect('/')


@app.errorhandler(401)
def page_not_found(e):
    return flask.render_template('login.html', login_failed=True)


# ── SocketIO handlers ──────────────────────────────────────────────────────────

@socketio.on('connect', namespace='/scoreboard')
def ws_scoreboard_connect():
    import datetime
    state._scoreboard_clients[flask.request.sid] = {
        'ip': flask.request.remote_addr,
        'at': datetime.datetime.now().strftime('%H:%M:%S'),
    }
    if state.main_thread is None:
        state.main_thread = socketio.start_background_task(target=main_thread_worker)
    socketio.emit('test_mode',       {'active': state._test_session is not None}, namespace='/scoreboard')
    socketio.emit('display_overlay', {'active': state._overlay_active},           namespace='/scoreboard')
    socketio.emit('columns_state',   {'hidden': state._cols_hidden},               namespace='/scoreboard')
    send_event_info()


@socketio.on('disconnect', namespace='/scoreboard')
def ws_scoreboard_disconnect():
    state._scoreboard_clients.pop(flask.request.sid, None)


@socketio.on('set_overlay', namespace='/scoreboard')
def ws_set_overlay(d):
    state._overlay_active = bool(d.get('active', False))
    socketio.emit('display_overlay', {'active': state._overlay_active}, namespace='/scoreboard')


@socketio.on('set_columns', namespace='/scoreboard')
def ws_set_columns(d):
    state._cols_hidden = bool(d.get('hidden', False))
    socketio.emit('columns_state', {'hidden': state._cols_hidden}, namespace='/scoreboard')


@socketio.on('adjust_splits', namespace='/scoreboard')
def ws_adjust_splits(d):
    lane  = int(d.get('lane', 0))
    delta = int(d.get('delta', 0))
    if lane < 1 or lane > 12 or delta == 0:
        return
    new_val = state._decoder.adjust_splits(lane, delta)
    socketio.emit('update_scoreboard', {f'lane_splits{lane}': new_val}, namespace='/scoreboard')


@socketio.on('next_heat', namespace='/scoreboard')
def ws_next_heat(d):
    event_list = list(state.event_info.events.keys())
    event_list.sort()
    try:
        event_tuple = event_list[event_list.index(state._decoder.last_event_sent) + 1]
    except Exception:
        event_tuple = event_list[0]
    state._decoder.last_event_sent = event_tuple
    send_event_info()


@socketio.on('connect', namespace='/results')
def ws_results_connect():
    if state._last_results_snapshot:
        socketio.emit('results_snapshot', state._last_results_snapshot,
                      namespace='/results', to=flask.request.sid)
    ev, ht = state._decoder.last_event_sent if state._decoder.last_event_sent != (0, 0) else (0, 0)
    socketio.emit('next_heats',
                  {'heats': _get_next_heats(ev, ht,
                                            num_lanes=int(state.settings.get('num_lanes', 6)))},
                  namespace='/results', to=flask.request.sid)


@socketio.on('connect', namespace='/settings')
def ws_settings_connect():
    if state.main_thread is None and state._test_session is None:
        state.main_thread = socketio.start_background_task(target=main_thread_worker)


# ── Template context ───────────────────────────────────────────────────────────

@app.context_processor
def inject_globals():
    return dict(
        splash_url=state.settings.get('splash_url', ''),
        labels=state.load_locale(),
        show_lane_header=state.settings.get('show_lane_header', True),
        show_name_header=state.settings.get('show_name_header', True),
        show_club_header=state.settings.get('show_club_header', True),
        show_time_header=state.settings.get('show_time_header', True),
        show_delta_header=state.settings.get('show_delta_header', True),
        show_position_header=state.settings.get('show_position_header', True),
        show_name=state.settings.get('show_name', True),
        show_club=state.settings.get('show_club', True),
        show_delta=state.settings.get('show_delta', True),
        show_position=state.settings.get('show_position', True),
        show_podium=state.settings.get('show_podium', True),
        num_lanes=int(state.settings.get('num_lanes', 6)),
        intro_timeout=int(state.settings.get('intro_timeout', 300)),
        results_timeout=int(state.settings.get('results_timeout', 300)),
        server_update_timeout=int(state.settings.get('server_update_timeout', 300)),
        finish_debounce=float(state.settings.get('finish_debounce', 3.0)),
        split_min_duration=float(state.settings.get('split_min_duration', 1.0)),
        pool_length=int(state.settings.get('pool_length', 25)),
        touchpad_sides=int(state.settings.get('touchpad_sides', 1)),
        lenex_pool_length=int(state.lenex_meet_info.get('pool_length_lenex') or 0),
        theme_colors={**state.DEFAULT_THEME_COLORS, **state.settings.get('theme_colors', {})},
        theme_fonts={**state.DEFAULT_THEME_FONTS,  **state.settings.get('theme_fonts',  {})},
    )


# ── Locale URL aliases ─────────────────────────────────────────────────────────

def _register_locale_aliases():
    import tomllib
    seen = set()
    for path in glob.glob(os.path.join('locales', '*.toml')) + \
                glob.glob(os.path.join(state.CUSTOM_LOCALE_FOLDER, '*.toml')):
        try:
            with open(path, 'rb') as f:
                aliases = tomllib.load(f).get('aliases', {})
            for alias, target in aliases.items():
                if alias in seen:
                    continue
                seen.add(alias)
                def make_redirect(t):
                    def view():
                        return flask.redirect(t)
                    view.__name__ = 'alias_' + alias
                    return view
                app.add_url_rule('/' + alias, view_func=make_redirect(target))
        except Exception:
            pass


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    state.install_log_capture()
    import relay
    from console_decoders import load_custom_decoders
    from routes.settings import _load_meet_file
    state.load_settings()
    load_custom_decoders(state.CUSTOM_DECODERS_FOLDER)
    relay.start()
    _register_locale_aliases()
    _last = state.settings.get('last_meet_file', '')
    if _last:
        _path = os.path.join(state.MEET_FOLDER, _last)
        if os.path.isfile(_path):
            _load_meet_file(_path)
    try:
        socketio.run(app, host='0.0.0.0')
    except Exception:
        traceback.print_exc()

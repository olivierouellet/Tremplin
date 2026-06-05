import glob
import os
import re
import select
import shutil
import signal
import struct
import subprocess

import flask
import flask_login
from flask import Blueprint

import state
from extensions import socketio
from meet_data import send_event_info
from parsers.lenex_parser import load_lenex
from worker import _cleanup_test_meet, _list_sessions, _restart_worker

bp = Blueprint('debug', __name__)

_TERMINAL_ALLOWED_CMDS = {
    'bash':         ['bash'],
    'raspi-config': ['sudo', 'raspi-config'],
    'logs':         ['journalctl', '-u', 'scoreboard', '-f'],
    'dmesg-tty':    ['bash', '-c', 'dmesg | grep -i tty'],
    'serial-ports': ['python3', '-m', 'serial.tools.list_ports', '-v'],
}


@bp.route('/test_status')
@flask_login.login_required
def route_test_status():
    return flask.jsonify({
        'playing':        state._test_session is not None,
        'session':        os.path.basename(state._test_session) if state._test_session else '',
        'recording':      state._record_handle is not None,
        'sessions':       _list_sessions(),
        'speed':          state.in_speed,
        'has_meet':       bool(state._active_meet_file) and not state._test_meet_active,
        'test_meet':      state._test_meet_active,
        'test_meet_name': state._active_meet_file if state._test_meet_active else '',
    })


@bp.route('/test_play', methods=['POST'])
@flask_login.login_required
def route_test_play():
    name = flask.request.get_json(force=True).get('name', '')
    for s in _list_sessions():
        if s['name'] == name:
            socketio.emit('test_mode', {'active': True}, namespace='/scoreboard')
            socketio.start_background_task(_restart_worker, s['path'])
            companion = os.path.splitext(s['path'])[0] + '.lxf'
            if os.path.exists(companion):
                all_companions = {
                    os.path.basename(os.path.splitext(sess['path'])[0] + '.lxf')
                    for sess in _list_sessions()
                }
                for f in glob.glob(os.path.join(state.MEET_FOLDER, '*.lxf')):
                    if os.path.basename(f) in all_companions:
                        try:
                            os.remove(f)
                        except Exception:
                            pass
                if state._test_meet_active:
                    state.lenex_event_names.clear()
                    state.lenex_start_list.clear()
                    state.lenex_heat_times.clear()
                    state.lenex_meet_info.clear()
                    state.lenex_event_distances.clear()
                    state._test_meet_active = False
                if not state._active_meet_file:
                    try:
                        dest = os.path.join(state.MEET_FOLDER, os.path.basename(companion))
                        shutil.copy2(companion, dest)
                        data = load_lenex(dest)
                        state.lenex_event_names.update(data.event_names)
                        state.lenex_start_list.update(data.start_list)
                        state.lenex_heat_times.update(data.heat_times)
                        state.lenex_meet_info.update(data.meet_info)
                        state.lenex_event_distances.update(data.event_distances)
                        send_event_info()
                        state._test_meet_active = True
                    except Exception as e:
                        print(f'[test] Failed to load companion LXF: {e}')
            return flask.jsonify({'ok': True})
    return flask.jsonify({'error': 'Session not found'}), 404


@bp.route('/test_stop', methods=['POST'])
@flask_login.login_required
def route_test_stop():
    _cleanup_test_meet()
    socketio.emit('test_mode', {'active': False}, namespace='/scoreboard')
    socketio.start_background_task(_restart_worker, None)
    return flask.jsonify({'ok': True})


@bp.route('/test_meet_upload', methods=['POST'])
@flask_login.login_required
def route_test_meet_upload():
    if state._test_session is None:
        return flask.jsonify({'ok': False, 'error': 'No test session is running'})
    if state._test_meet_active:
        return flask.jsonify({'ok': False, 'error': 'Test meet already loaded'})
    meet_files = glob.glob(os.path.join(state.MEET_FOLDER, '*.lxf')) + \
                 glob.glob(os.path.join(state.MEET_FOLDER, '*.csv'))
    if meet_files:
        return flask.jsonify({'ok': False, 'error': 'A real meet file is already loaded'})
    file = flask.request.files.get('meet_file')
    if not file or not file.filename:
        return flask.jsonify({'ok': False, 'error': 'No file provided'})
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ('.csv', '.lxf'):
        return flask.jsonify({'ok': False, 'error': 'File must be .lxf or .csv'})
    dest = os.path.join(state.MEET_FOLDER, os.path.basename(file.filename))
    file.save(dest)
    try:
        if ext == '.csv':
            state.event_info.load(dest)
        else:
            data = load_lenex(dest)
            state.lenex_event_names.update(data.event_names)
            state.lenex_start_list.update(data.start_list)
            state.lenex_heat_times.update(data.heat_times)
            state.lenex_meet_info.update(data.meet_info)
            state.lenex_event_distances.update(data.event_distances)
        send_event_info()
        state._test_meet_active = True
        return flask.jsonify({'ok': True, 'name': os.path.basename(file.filename)})
    except Exception as e:
        try:
            os.remove(dest)
        except Exception:
            pass
        return flask.jsonify({'ok': False, 'error': str(e)})


@bp.route('/test_set_speed', methods=['POST'])
@flask_login.login_required
def route_test_set_speed():
    try:
        state.in_speed = float(flask.request.get_json(force=True).get('speed', 1.0))
        state.in_speed = max(0.1, min(state.in_speed, 100.0))
    except (TypeError, ValueError):
        pass
    return flask.jsonify({'speed': state.in_speed})


@bp.route('/test_record_start', methods=['POST'])
@flask_login.login_required
def route_test_record_start():
    if state._record_handle:
        state._record_handle.close()
    name = flask.request.get_json(force=True).get('name', 'recording').strip()
    code = re.sub(r'[^a-z0-9_-]', '_', name.lower()) or 'recording'
    path = os.path.join(state.CUSTOM_SESSIONS_FOLDER, code + '.cts')
    state._record_handle = open(path, 'wt')
    return flask.jsonify({'ok': True, 'file': code + '.cts'})


@bp.route('/test_record_stop', methods=['POST'])
@flask_login.login_required
def route_test_record_stop():
    if state._record_handle:
        state._record_handle.close()
        state._record_handle = None
    return flask.jsonify({'ok': True})


@bp.route('/test_session_delete', methods=['POST'])
@flask_login.login_required
def route_test_session_delete():
    name = flask.request.get_json(force=True).get('name', '')
    path = os.path.join(state.CUSTOM_SESSIONS_FOLDER, name)
    if os.path.isfile(path) and (path.endswith('.cts') or
                                  path.endswith('.raw') or
                                  path.endswith('.cap')):
        if state._test_session == path:
            socketio.start_background_task(_restart_worker, None)
        os.remove(path)
    return flask.redirect('/settings')


@bp.route('/test_session_upload', methods=['POST'])
@flask_login.login_required
def route_test_session_upload():
    file = flask.request.files.get('session_file')
    if file and (file.filename.endswith('.cts') or
                 file.filename.endswith('.raw') or
                 file.filename.endswith('.cap')):
        file.save(os.path.join(state.CUSTOM_SESSIONS_FOLDER,
                               os.path.basename(file.filename)))
    return flask.redirect('/settings')


@bp.route('/serial_status')
@flask_login.login_required
def route_serial_status():
    return flask.jsonify(state._serial_status)


@bp.route('/debug_status')
@flask_login.login_required
def route_debug_status():
    return flask.jsonify({'enabled': state._debug_serial})


@bp.route('/debug_toggle', methods=['POST'])
@flask_login.login_required
def route_debug_toggle():
    state._debug_serial = not state._debug_serial
    return flask.jsonify({'enabled': state._debug_serial})


@bp.route('/debug/seed_times')
def route_debug_seed_times():
    return flask.jsonify({
        'lane_seed_times': state._decoder.lane_seed_times,
        'last_event_sent': state._decoder.last_event_sent,
        'lenex_loaded':    bool(state.lenex_start_list),
    })


# ── Terminal (PTY) ─────────────────────────────────────────────────────────────

def _pty_reader():
    while state._pty_fd is not None:
        try:
            r, _, _ = select.select([state._pty_fd], [], [], 0.05)
            if r:
                data = os.read(state._pty_fd, 4096)
                if data:
                    socketio.emit('output', data.decode('utf-8', errors='replace'),
                                  namespace='/terminal')
                else:
                    break
        except OSError:
            break
        except Exception:
            break
        socketio.sleep(0)
    state._pty_fd = state._pty_pid = None
    socketio.emit('exit', {}, namespace='/terminal')


@bp.route('/terminal_start', methods=['POST'])
@flask_login.login_required
def route_terminal_start():
    if not state._PTY_AVAILABLE:
        return flask.jsonify({'ok': False, 'error': 'PTY not available on this platform'})
    if state._pty_fd is not None:
        return flask.jsonify({'ok': True})
    try:
        import fcntl
        import pty
        import termios
        data    = flask.request.get_json(silent=True) or {}
        cmd_key = data.get('cmd', 'bash')
        cmd     = _TERMINAL_ALLOWED_CMDS.get(cmd_key)
        if cmd is None:
            return flask.jsonify({'ok': False, 'error': f'Unknown command: {cmd_key}'})
        master_fd, slave_fd = pty.openpty()
        winsize = struct.pack('HHHH', 24, 80, 0, 0)
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)
        TIOCSCTTY = getattr(termios, 'TIOCSCTTY', 0x540E)

        def _preexec():
            os.setsid()
            fcntl.ioctl(0, TIOCSCTTY, 0)

        env  = {**os.environ, 'TERM': 'xterm-256color'}
        proc = subprocess.Popen(
            cmd,
            stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
            close_fds=True, preexec_fn=_preexec, env=env
        )
        os.close(slave_fd)
        state._pty_fd  = master_fd
        state._pty_pid = proc.pid
        socketio.start_background_task(_pty_reader)
        return flask.jsonify({'ok': True})
    except Exception as e:
        return flask.jsonify({'ok': False, 'error': str(e)})


@bp.route('/terminal_stop', methods=['POST'])
@flask_login.login_required
def route_terminal_stop():
    if state._pty_pid:
        try:
            os.kill(state._pty_pid, signal.SIGTERM)
        except Exception:
            pass
    if state._pty_fd:
        try:
            os.close(state._pty_fd)
        except Exception:
            pass
    state._pty_fd = state._pty_pid = None
    return flask.jsonify({'ok': True})


@socketio.on('input', namespace='/terminal')
def terminal_input(data):
    if state._pty_fd is not None:
        try:
            os.write(state._pty_fd, data.encode('utf-8'))
        except OSError:
            pass


@socketio.on('resize', namespace='/terminal')
def terminal_resize(data):
    if state._pty_fd is not None:
        try:
            import fcntl
            import termios
            winsize = struct.pack('HHHH', data['rows'], data['cols'], 0, 0)
            fcntl.ioctl(state._pty_fd, termios.TIOCSWINSZ, winsize)
        except Exception:
            pass

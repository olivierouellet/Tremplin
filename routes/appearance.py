import glob
import json
import os

import flask
import flask_login
from flask import Blueprint

import relay
import state
from extensions import socketio

bp = Blueprint('appearance', __name__)


@bp.route('/images/<path:filename>')
def serve_image(filename):
    return flask.send_from_directory(state.IMAGES_DIR, filename)


@bp.route('/splash_delete')
@flask_login.login_required
def route_splash_delete():
    filename = state.settings.get('splash_url', '')
    if filename:
        path = os.path.join(state.IMAGES_DIR, filename)
        if os.path.isfile(path):
            os.remove(path)
        state.settings['splash_url'] = ''
        with open(state.settings_file, 'wt') as f:
            json.dump(state.settings, f, sort_keys=True, indent=4)
    return flask.redirect('/settings#tab-display')


@bp.route('/splash_delete_all')
@flask_login.login_required
def route_splash_delete_all():
    for f in os.listdir(state.IMAGES_DIR):
        fp = os.path.join(state.IMAGES_DIR, f)
        if os.path.isfile(fp):
            os.remove(fp)
    state.settings['splash_url'] = ''
    with open(state.settings_file, 'wt') as f:
        json.dump(state.settings, f, sort_keys=True, indent=4)
    return flask.redirect('/settings#tab-display')


@bp.route('/icon_delete')
@flask_login.login_required
def route_icon_delete():
    filename = os.path.basename(flask.request.args.get('file', '').strip())
    if filename and filename not in ('home_icon.png', 'home_icon_512.png'):
        path = os.path.join(state.ICONS_DIR, filename)
        if os.path.isfile(path):
            os.remove(path)
        if state.settings.get('active_home_icon') == filename:
            for p in (state.HOME_ICON_PATH, state.HOME_ICON_512_PATH):
                if os.path.exists(p):
                    os.remove(p)
            state.settings['active_home_icon'] = ''
            state.save_meet_profile(state._active_meet_uid)
            with open(state.settings_file, 'wt') as f:
                json.dump(state.settings, f, sort_keys=True, indent=4)
            relay.update_metadata()
    return flask.redirect('/settings#tab-meet')


@bp.route('/icon_delete_all')
@flask_login.login_required
def route_icon_delete_all():
    if os.path.isdir(state.ICONS_DIR):
        for f in os.listdir(state.ICONS_DIR):
            fp = os.path.join(state.ICONS_DIR, f)
            if os.path.isfile(fp):
                os.remove(fp)
    state.settings['active_home_icon'] = ''
    state.save_meet_profile(state._active_meet_uid)
    with open(state.settings_file, 'wt') as f:
        json.dump(state.settings, f, sort_keys=True, indent=4)
    relay.update_metadata()
    return flask.redirect('/settings#tab-meet')


@bp.route('/locale_delete')
@flask_login.login_required
def route_locale_delete():
    code = state.settings.get('locale', '')
    path = os.path.join(state.CUSTOM_LOCALE_FOLDER, code + '.toml')
    if os.path.isfile(path):
        os.remove(path)
    state.settings['locale'] = 'fr'
    with open(state.settings_file, 'wt') as f:
        json.dump(state.settings, f, sort_keys=True, indent=4)
    return flask.redirect('/settings')


@bp.route('/locale_delete_all')
@flask_login.login_required
def route_locale_delete_all():
    for f in os.listdir(state.CUSTOM_LOCALE_FOLDER):
        fp = os.path.join(state.CUSTOM_LOCALE_FOLDER, f)
        if os.path.isfile(fp):
            os.remove(fp)
    builtin_codes = [os.path.splitext(os.path.basename(p))[0]
                     for p in glob.glob(os.path.join('locales', '*.toml'))]
    if state.settings.get('locale') not in builtin_codes:
        state.settings['locale'] = 'fr'
    with open(state.settings_file, 'wt') as f:
        json.dump(state.settings, f, sort_keys=True, indent=4)
    return flask.redirect('/settings')


@bp.route('/theme_delete')
@flask_login.login_required
def route_theme_delete():
    code = state.settings.get('active_theme', '')
    path = os.path.join(state.CUSTOM_THEME_FOLDER, code + '.toml')
    if os.path.exists(path):
        os.remove(path)
    state.settings['active_theme'] = 'default'
    colors, fonts = state.load_theme('default')
    state.settings['theme_colors'] = colors
    state.settings['theme_fonts']  = fonts
    with open(state.settings_file, 'wt') as f:
        json.dump(state.settings, f, sort_keys=True, indent=4)
    socketio.emit('reload', namespace='/scoreboard')
    socketio.emit('reload', namespace='/results')
    relay.relay_emit('reload', {})
    return flask.redirect('/settings')


@bp.route('/theme_delete_all')
@flask_login.login_required
def route_theme_delete_all():
    for f in glob.glob(os.path.join(state.CUSTOM_THEME_FOLDER, '*.toml')):
        os.remove(f)
    state.settings['active_theme'] = 'default'
    colors, fonts = state.load_theme('default')
    state.settings['theme_colors'] = colors
    state.settings['theme_fonts']  = fonts
    with open(state.settings_file, 'wt') as f:
        json.dump(state.settings, f, sort_keys=True, indent=4)
    socketio.emit('reload', namespace='/scoreboard')
    socketio.emit('reload', namespace='/results')
    relay.relay_emit('reload', {})
    return flask.redirect('/settings')


@bp.route('/picker_image_delete')
@flask_login.login_required
def route_picker_image_delete():
    filename = os.path.basename(flask.request.args.get('file', '').strip())
    if filename:
        path = os.path.join(state.PICKER_DIR, filename)
        if os.path.isfile(path):
            os.remove(path)
        if state.settings.get('active_picker_image') == filename:
            state.settings['active_picker_image'] = ''
            state.save_meet_profile(state._active_meet_uid)
            with open(state.settings_file, 'wt') as f:
                json.dump(state.settings, f, sort_keys=True, indent=4)
            relay.update_metadata()
    return flask.redirect('/settings#tab-meet')


@bp.route('/picker_image_delete_all')
@flask_login.login_required
def route_picker_image_delete_all():
    if os.path.isdir(state.PICKER_DIR):
        for f in os.listdir(state.PICKER_DIR):
            fp = os.path.join(state.PICKER_DIR, f)
            if os.path.isfile(fp):
                os.remove(fp)
    state.settings['active_picker_image'] = ''
    state.save_meet_profile(state._active_meet_uid)
    with open(state.settings_file, 'wt') as f:
        json.dump(state.settings, f, sort_keys=True, indent=4)
    relay.update_metadata()
    return flask.redirect('/settings#tab-meet')

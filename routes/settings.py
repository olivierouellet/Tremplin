import glob
import json
import os
import re
import traceback

import flask
import flask_login
import serial.tools.list_ports
from flask import Blueprint

import relay
import state
from console_decoders import CONSOLE_OPTIONS, load_custom_decoders, make_decoder
from extensions import socketio
from meet_data import send_event_info
from parsers.lenex_parser import load_lenex
from worker import _restart_worker

bp = Blueprint('settings_bp', __name__)


def _load_meet_file(path):
    """Clear current meet state and load *path* (Lenex or Hytek). Returns error string or None."""
    state.event_info.clear()
    state.lenex_event_names.clear()
    state.lenex_start_list.clear()
    state.lenex_heat_times.clear()
    state.lenex_meet_info.clear()
    state.lenex_event_distances.clear()
    state._last_results_snapshot      = {}
    state._results_prev_race_finished = False
    state._finish_timer_gen          += 1
    try:
        if path.endswith('.csv'):
            state.event_info.load(path)
        else:
            data = load_lenex(path)
            state.lenex_event_names.update(data.event_names)
            state.lenex_start_list.update(data.start_list)
            state.lenex_heat_times.update(data.heat_times)
            state.lenex_meet_info.update(data.meet_info)
            state.lenex_event_distances.update(data.event_distances)
    except Exception:
        return traceback.format_exc()
    state._active_meet_file = os.path.basename(path)
    state.settings['last_meet_file'] = state._active_meet_file
    with open(state.settings_file, 'wt') as f:
        json.dump(state.settings, f, sort_keys=True, indent=4)
    send_event_info()
    relay.send_schedule()
    return None


@bp.route('/settings', methods=['GET', 'POST'])
@flask_login.login_required
def route_settings():
    if flask.request.method == 'POST':
        modified = False
        icon_error = None

        if 'meet_file' in flask.request.files and state._test_session is None:
            file = flask.request.files['meet_file']
            if file and file.filename:
                ext = os.path.splitext(file.filename)[1].lower()
                if ext in ('.csv', '.lxf'):
                    for f in glob.glob(os.path.join(state.MEET_FOLDER, '*.csv')) + \
                             glob.glob(os.path.join(state.MEET_FOLDER, '*.lxf')):
                        os.remove(f)
                    dest = os.path.join(state.MEET_FOLDER, os.path.basename(file.filename))
                    file.save(dest)
                    err = _load_meet_file(dest)
                    if err:
                        return err
                    modified = True

        if 'meet_file_load_submit' in flask.request.form and state._test_session is None:
            selected = flask.request.form.get('meet_file_select', '').strip()
            if selected:
                dest = os.path.join(state.MEET_FOLDER, os.path.basename(selected))
                if os.path.isfile(dest):
                    err = _load_meet_file(dest)
                    if err:
                        return err
                    modified = True
            elif state._active_meet_file:
                state.event_info.clear()
                state.lenex_event_names.clear()
                state.lenex_start_list.clear()
                state.lenex_heat_times.clear()
                state.lenex_meet_info.clear()
                state.lenex_event_distances.clear()
                state._active_meet_file = ''
                state.settings['last_meet_file'] = ''
                send_event_info()
                modified = True

        if 'pool_setup_submit' in flask.request.form:
            for key, default in (('num_lanes', 8), ('pool_length', 25), ('touchpad_sides', 1)):
                try:
                    val = int(flask.request.form.get(key, default))
                    if val != int(state.settings.get(key, default)):
                        state.settings[key] = val
                        modified = True
                except (ValueError, TypeError):
                    pass

        if 'timing_settings_submit' in flask.request.form:
            changed = False
            for key in ('serial_port', 'console_type'):
                val = flask.request.form.get(key, '').strip()
                if val and val != state.settings.get(key):
                    state.settings[key] = val
                    changed = True
                    modified = True
            if changed:
                state._decoder = make_decoder(state.settings.get('console_type', 'cts_gen6'),
                                              state.settings)
                _restart_worker()

        if 'flow_settings_submit' in flask.request.form:
            for key, default in (('intro_timeout', 300), ('results_timeout', 300),
                                  ('server_update_timeout', 300)):
                try:
                    val = max(5, int(flask.request.form.get(key, default)))
                    if val != state.settings.get(key, default):
                        state.settings[key] = val
                        modified = True
                except (ValueError, TypeError):
                    pass
            try:
                val = round(max(0.5, float(flask.request.form.get('finish_debounce', 3.0))), 1)
                if val != state.settings.get('finish_debounce', 3.0):
                    state.settings['finish_debounce'] = val
                    modified = True
            except (ValueError, TypeError):
                pass
            try:
                val = round(max(0.5, float(flask.request.form.get('split_min_duration', 1.0))), 1)
                if val != state.settings.get('split_min_duration', 1.0):
                    state.settings['split_min_duration'] = val
                    modified = True
            except (ValueError, TypeError):
                pass

        if 'display_settings_submit' in flask.request.form:
            for key in ('show_lane_header', 'show_name_header', 'show_club_header',
                        'show_time_header', 'show_delta_header', 'show_position_header',
                        'show_name', 'show_club', 'show_delta', 'show_position', 'show_podium'):
                val = key in flask.request.form
                if val != state.settings.get(key, True):
                    state.settings[key] = val
                    modified = True
            sort_val = flask.request.form.get('results_sort', 'lane')
            if sort_val in ('lane', 'place') and sort_val != state.settings.get('results_sort', 'lane'):
                state.settings['results_sort'] = sort_val
                modified = True
            for key in ('meet_title', 'locale', 'label_style'):
                if key in flask.request.form and state.settings.get(key) != flask.request.form.get(key):
                    state.settings[key] = flask.request.form.get(key)
                    modified = True
            if 'locale_file' in flask.request.files:
                file = flask.request.files['locale_file']
                if file and file.filename and file.filename.endswith('.toml'):
                    filename = os.path.basename(file.filename)
                    file.save(os.path.join(state.CUSTOM_LOCALE_FOLDER, filename))
                    state.settings['locale'] = os.path.splitext(filename)[0]
                    modified = True

        if 'splash_settings_submit' in flask.request.form:
            splash_file = flask.request.files.get('splash_file')
            if splash_file and splash_file.filename:
                filename = os.path.basename(splash_file.filename)
                splash_file.save(os.path.join(state.IMAGES_DIR, filename))
                state.settings['splash_url'] = filename
                modified = True
            elif 'splash_url' in flask.request.form:
                val = flask.request.form.get('splash_url', '')
                if val != state.settings.get('splash_url', ''):
                    state.settings['splash_url'] = val
                    modified = True
            try:
                ci = max(3, int(flask.request.form.get('carousel_interval', 10)))
                if ci != int(state.settings.get('carousel_interval', 10)):
                    state.settings['carousel_interval'] = ci
                    modified = True
            except (ValueError, TypeError):
                pass

        elif 'theme_update_submit' in flask.request.form:
            file = flask.request.files.get('theme_file')
            if file and file.filename and file.filename.endswith('.toml'):
                filename = os.path.basename(file.filename)
                file.save(os.path.join(state.CUSTOM_THEME_FOLDER, filename))
                code = os.path.splitext(filename)[0]
                colors, fonts = state.load_theme(code)
                state.settings['active_theme'] = code
                state.settings['theme_colors'] = colors
                state.settings['theme_fonts']  = fonts
                modified = True
            else:
                code = flask.request.form.get('theme_select',
                                              state.settings.get('active_theme', 'default'))
                if code != state.settings.get('active_theme', 'default'):
                    colors, fonts = state.load_theme(code)
                    state.settings['active_theme'] = code
                    state.settings['theme_colors'] = colors
                    state.settings['theme_fonts']  = fonts
                    modified = True
                else:
                    colors = {}
                    for key in state.DEFAULT_THEME_COLORS:
                        form_key = 'color_' + key
                        if form_key in flask.request.form:
                            colors[key] = flask.request.form[form_key]
                    fonts = {}
                    val = flask.request.form.get('font_family', '').strip()
                    if val:
                        fonts['family'] = val
                    val = flask.request.form.get('font_digits', '').strip()
                    if val:
                        fonts['digits'] = val
                    val = flask.request.form.get('font_timing', '').strip()
                    if val:
                        fonts['timing'] = val
                    state.settings['theme_colors'] = {**state.DEFAULT_THEME_COLORS, **colors}
                    state.settings['theme_fonts']  = {**state.DEFAULT_THEME_FONTS,  **fonts}
                    modified = True

        elif 'theme_save_submit' in flask.request.form:
            name = flask.request.form.get('theme_name', '').strip()
            if name:
                code   = re.sub(r'[^a-z0-9_-]', '_', name.lower())
                colors = {**state.DEFAULT_THEME_COLORS, **state.settings.get('theme_colors', {})}
                fonts  = {**state.DEFAULT_THEME_FONTS,  **state.settings.get('theme_fonts',  {})}
                lines  = [f'name = "{name}"\n', '\n', '[colors]\n']
                for k, v in colors.items():
                    lines.append(f'{k} = "{v}"\n')
                lines.append('\n[fonts]\n')
                for k, v in fonts.items():
                    lines.append(f'{k} = "{v}"\n')
                with open(os.path.join(state.CUSTOM_THEME_FOLDER, code + '.toml'), 'w') as fh:
                    fh.writelines(lines)
                state.settings['active_theme'] = code
                modified = True

        if 'cloud_settings_submit' in flask.request.form:
            for key in ('cloud_relay_url', 'cloud_relay_key', 'meet_location', 'meet_sport', 'cloud_label_style', 'app_window_title'):
                val = flask.request.form.get(key, '').strip()
                if val != state.settings.get(key, ''):
                    state.settings[key] = val
                    modified = True
            # ── Picker image ──────────────────────────────────────────────────
            picker_image_error = None
            picker_image_file  = flask.request.files.get('picker_image')
            if picker_image_file and picker_image_file.filename:
                try:
                    from PIL import Image
                    img      = Image.open(picker_image_file.stream)
                    orig_name = os.path.basename(picker_image_file.filename)
                    img.save(os.path.join(state.PICKER_DIR, orig_name), optimize=True)
                    state.settings['active_picker_image'] = orig_name
                    modified = True
                except Exception as e:
                    picker_image_error = f'Could not process image: {e}'
            else:
                selected_pi = flask.request.form.get('selected_picker_image', '').strip()
                prev_pi     = state.settings.get('active_picker_image', '')
                if selected_pi != prev_pi:
                    state.settings['active_picker_image'] = selected_pi
                    modified = True

            # ── Home screen icon ──────────────────────────────────────────────
            if flask.request.form.get('remove_home_icon'):
                for p in (state.HOME_ICON_PATH, state.HOME_ICON_512_PATH):
                    if os.path.exists(p):
                        os.remove(p)
                state.settings['active_home_icon'] = ''
                modified = True
            icon_file = flask.request.files.get('home_icon')
            if icon_file and icon_file.filename:
                try:
                    from PIL import Image
                    img = Image.open(icon_file.stream)
                    if img.size not in [(512, 512), (1024, 1024)]:
                        icon_error = f'Icon must be 512×512 or 1024×1024 px (uploaded: {img.size[0]}×{img.size[1]}).'
                    else:
                        orig_name = os.path.basename(icon_file.filename)
                        img.save(os.path.join(state.ICONS_DIR, orig_name), 'PNG', optimize=True)
                        src = img.convert('RGBA')
                        src.resize((192, 192), Image.LANCZOS).save(state.HOME_ICON_PATH, 'PNG', optimize=True)
                        src.resize((512, 512), Image.LANCZOS).save(state.HOME_ICON_512_PATH, 'PNG', optimize=True)
                        state.settings['active_home_icon'] = orig_name
                        modified = True
                except Exception as e:
                    icon_error = f'Could not process image: {e}'
            else:
                selected = flask.request.form.get('selected_home_icon', '').strip()
                prev     = state.settings.get('active_home_icon', '')
                if selected != prev:
                    if selected:
                        src_path = os.path.join(state.ICONS_DIR, selected)
                        if os.path.isfile(src_path):
                            try:
                                from PIL import Image
                                img = Image.open(src_path).convert('RGBA')
                                img.resize((192, 192), Image.LANCZOS).save(state.HOME_ICON_PATH, 'PNG', optimize=True)
                                img.resize((512, 512), Image.LANCZOS).save(state.HOME_ICON_512_PATH, 'PNG', optimize=True)
                                state.settings['active_home_icon'] = selected
                                modified = True
                            except Exception as e:
                                icon_error = f'Could not apply icon: {e}'
                    else:
                        for p in (state.HOME_ICON_PATH, state.HOME_ICON_512_PATH):
                            if os.path.exists(p):
                                os.remove(p)
                        state.settings['active_home_icon'] = ''
                        modified = True
            if modified:
                import relay as _relay
                _relay.update_metadata()

        else:
            new_name = flask.request.form.get('user_name', '').strip()
            if new_name and new_name != state.settings.get('username'):
                state.settings['username'] = new_name
                modified = True
            new_pass = flask.request.form.get('password', '').strip()
            if new_pass and new_pass != state.settings.get('password'):
                state.settings['password'] = new_pass
                modified = True
            for k in state.settings.keys():
                if k in ('username', 'password'):
                    continue
                if k in flask.request.form and state.settings[k] != flask.request.form.get(k):
                    state.settings[k] = flask.request.form.get(k)
                    modified = True

        if modified:
            with open(state.settings_file, 'wt') as f:
                json.dump(state.settings, f, sort_keys=True, indent=4)
            if 'display_settings_submit' in flask.request.form or \
               'theme_update_submit' in flask.request.form:
                socketio.emit('reload', namespace='/scoreboard')
                socketio.emit('reload', namespace='/results')
                relay.relay_emit('reload', {})

    load_custom_decoders(state.CUSTOM_DECODERS_FOLDER)

    comm_port_list = [(port, '%s: %s' % (port, desc))
                      for port, desc, id in serial.tools.list_ports.comports()]
    if state.settings['serial_port'] not in [port for port, desc in comm_port_list]:
        comm_port_list.insert(0, (state.settings['serial_port'], state.settings['serial_port']))

    splash_url_list = sorted(
        f for f in os.listdir(state.IMAGES_DIR)
        if os.path.isfile(os.path.join(state.IMAGES_DIR, f))
    )

    meet_file_list = sorted(
        os.path.basename(f)
        for f in glob.glob(os.path.join(state.MEET_FOLDER, '*.csv')) +
                 glob.glob(os.path.join(state.MEET_FOLDER, '*.lxf'))
    )

    custom_locale_list  = state.list_custom_locales()
    custom_locale_codes = [c for c, _ in custom_locale_list]

    return flask.render_template(
        'settings.html',
        meet_file_list=meet_file_list,
        active_meet_file=state._active_meet_file,
        meet_title=state.settings['meet_title'],
        serial_port=state.settings['serial_port'],
        serial_port_list=comm_port_list,
        console_type=state.settings.get('console_type', 'cts_gen6'),
        console_options=[(key, label) for key, label, _ in CONSOLE_OPTIONS],
        user_name=state.settings['username'],
        splash_url_list=splash_url_list,
        splash_url=state.settings.get('splash_url', ''),
        carousel_interval=int(state.settings.get('carousel_interval', 10)),
        locale=state.settings.get('locale', 'fr'),
        locale_list=state.list_locales(),
        custom_locale_list=custom_locale_list,
        custom_locale_codes=custom_locale_codes,
        label_style=state.settings.get('label_style', 'short'),
        num_lanes=int(state.settings.get('num_lanes', 6)),
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
        results_sort=state.settings.get('results_sort', 'lane'),
        active_theme=state.settings.get('active_theme', 'default'),
        theme_list=state.list_builtin_themes(),
        custom_theme_list=state.list_custom_themes(),
        custom_theme_codes=[c for c, _ in state.list_custom_themes()],
        theme_colors={**state.DEFAULT_THEME_COLORS, **state.settings.get('theme_colors', {})},
        theme_fonts={**state.DEFAULT_THEME_FONTS,  **state.settings.get('theme_fonts', {})},
        cloud_relay_url=state.settings.get('cloud_relay_url', ''),
        cloud_relay_key=state.settings.get('cloud_relay_key', ''),
        meet_location=state.settings.get('meet_location', ''),
        meet_sport=state.settings.get('meet_sport', ''),
        cloud_label_style=state.settings.get('cloud_label_style', 'short'),
        app_window_title=state.settings.get('app_window_title', ''),
        has_home_icon=os.path.exists(state.HOME_ICON_PATH),
        icon_error=locals().get('icon_error'),
        active_home_icon=state.settings.get('active_home_icon', ''),
        icon_list=sorted(
            f for f in (os.listdir(state.ICONS_DIR) if os.path.isdir(state.ICONS_DIR) else [])
            if f.endswith('.png') and f not in ('home_icon.png', 'home_icon_512.png')
            and os.path.isfile(os.path.join(state.ICONS_DIR, f))
        ),
        active_picker_image=state.settings.get('active_picker_image', ''),
        picker_image_error=locals().get('picker_image_error'),
        picker_image_list=sorted(
            f for f in (os.listdir(state.PICKER_DIR) if os.path.isdir(state.PICKER_DIR) else [])
            if os.path.isfile(os.path.join(state.PICKER_DIR, f))
        ),
    )


@bp.route('/home_icon')
def route_home_icon():
    if not os.path.exists(state.HOME_ICON_PATH):
        flask.abort(404)
    return flask.send_file(state.HOME_ICON_PATH, mimetype='image/png',
                           max_age=0, conditional=True)


@bp.route('/home_icon_512')
def route_home_icon_512():
    path = state.HOME_ICON_512_PATH if os.path.exists(state.HOME_ICON_512_PATH) else state.HOME_ICON_PATH
    if not os.path.exists(path):
        flask.abort(404)
    return flask.send_file(path, mimetype='image/png', max_age=0, conditional=True)


@bp.route('/picker_image')
def route_picker_image():
    active = state.settings.get('active_picker_image', '')
    if not active:
        flask.abort(404)
    path = os.path.join(state.PICKER_DIR, active)
    if not os.path.isfile(path):
        flask.abort(404)
    return flask.send_file(path, max_age=0, conditional=True)


@bp.route('/manifest.json')
def route_manifest():
    import json as _json
    name = (state.lenex_meet_info.get('name') or
            state.settings.get('meet_title') or 'Tremplin')
    icons = ([
        {'src': '/home_icon',     'sizes': '192x192', 'type': 'image/png'},
        {'src': '/home_icon_512', 'sizes': '512x512', 'type': 'image/png'},
    ] if os.path.exists(state.HOME_ICON_PATH) else [
        {'src': '/static/img/default_mobile_icon.png', 'sizes': '1024x1024', 'type': 'image/png'},
    ])
    manifest = {
        'name':             name,
        'short_name':       'Tremplin',
        'start_url':        '/',
        'display':          'standalone',
        'background_color': '#000000',
        'theme_color':      '#000000',
        'icons':            icons,
    }
    return flask.Response(_json.dumps(manifest),
                          mimetype='application/manifest+json')




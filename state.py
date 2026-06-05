import glob
import json
import os
import os.path
import re
import sys

import tomllib

from parsers.hytek_parser import HytekParser
from parsers.lenex_parser import load_lenex
from console_decoders import make_decoder

try:
    import pty, fcntl, termios
    _PTY_AVAILABLE = True
except ImportError:
    _PTY_AVAILABLE = False

# ── Paths ──────────────────────────────────────────────────────────────────────

app_dir           = os.path.dirname(os.path.abspath(__file__))
SCOREBOARD_DIR    = os.path.expanduser('~/TremplinData')
settings_file     = os.path.join(SCOREBOARD_DIR, 'settings.json')
_settings_default = os.path.join(app_dir, 'settings.default.json')

SESSIONS_FOLDER        = os.path.join(app_dir, 'recorded')
CUSTOM_SESSIONS_FOLDER = os.path.join(SCOREBOARD_DIR, 'recorded')
IMAGES_DIR             = os.path.join(SCOREBOARD_DIR, 'images')
ICONS_DIR              = os.path.join(SCOREBOARD_DIR, 'icons')
HOME_ICON_PATH         = os.path.join(ICONS_DIR, 'home_icon.png')
HOME_ICON_512_PATH     = os.path.join(ICONS_DIR, 'home_icon_512.png')
MEET_FOLDER            = os.path.join(SCOREBOARD_DIR, 'meet')
CUSTOM_LOCALE_FOLDER          = os.path.join(SCOREBOARD_DIR, 'locale')
THEME_FOLDER           = os.path.join(app_dir, 'themes')
CUSTOM_THEME_FOLDER    = os.path.join(SCOREBOARD_DIR, 'themes')
CUSTOM_DECODERS_FOLDER = os.path.join(SCOREBOARD_DIR, 'console_decoders')

# ── Theme / locale defaults ────────────────────────────────────────────────────

DEFAULT_THEME_COLORS = {
    'bg': '#0d0d0d', 'header_bg': '#1a1a1a', 'header_border': '#2e2e2e',
    'header_label': '#ffffff', 'header_value': '#e0e0e0',
    'th_text': '#666666', 'th_bg': '#1a1a1a',
    'row_odd': '#141414', 'row_even': '#202020', 'row_text': '#e0e0e0',
    'time': '#FFD700', 'delta_better': '#4CAF50', 'delta_worse': '#808080',
    'podium_gold': '#545454', 'podium_silver': '#424242', 'podium_bronze': '#343434',
    'schedule_event': '#3b9eff', 'schedule_time': '#FFD700',
    'schedule_name': '#e0e0e0', 'schedule_club': '#666666',
}
DEFAULT_THEME_FONTS = {'family': 'Overpass Mono', 'digits': 'DSEG7Classic', 'timing': 'Overpass Mono'}

_FALLBACK_LABELS = {
    'event': 'EVENT', 'heat': 'HEAT', 'lane': 'LANE',
    'place': 'PLACE', 'time': 'TIME', 'name': 'NAME', 'club': 'CLUB',
    'chrono': 'CHRONO',
}

_STROKE_ALIASES = [
    ('individual medley', 'medley'),
    ('breaststroke',      'breaststroke'),
    ('backstroke',        'backstroke'),
    ('butterfly',         'butterfly'),
    ('freestyle',         'freestyle'),
    ('medley',            'medley'),
    ('breast',            'breaststroke'),
    ('back',              'backstroke'),
    ('free',              'freestyle'),
    ('fly',               'butterfly'),
    ('im',                'medley'),
]

_GENDER_PATTERNS = [
    (r"\bwomen(?:'s)?\b", 'women'),
    (r"\bgirls?(?:'s)?\b", 'girls'),
    (r"\bmen(?:'s)?\b", 'men'),
    (r"\bboys?(?:'s)?\b", 'boys'),
    (r"\bmixed\b", 'mixed'),
]

# ── Settings ───────────────────────────────────────────────────────────────────

settings = {
    'meet_title': '',
    'serial_port': 'COM1',
    'username': 'score',
    'password': 'swimming',
    'splash_url': '',
    'locale': 'en',
    'label_style': 'long',
    'num_lanes': 8,
    'show_lane_header': True,
    'show_name_header': True,
    'show_club_header': True,
    'show_time_header': True,
    'show_delta_header': True,
    'show_position_header': True,
    'show_name': True,
    'show_club': True,
    'show_delta': True,
    'show_position': True,
    'show_podium': True,
    'results_sort': 'lane',
    'active_theme': 'default',
    'theme_colors': {
        'bg': '#0d0d0d', 'header_bg': '#1a1a1a', 'header_border': '#2e2e2e',
        'header_label': '#ffffff', 'header_value': '#e0e0e0',
        'th_text': '#666666', 'th_bg': '#1a1a1a',
        'row_odd': '#141414', 'row_even': '#202020', 'row_text': '#e0e0e0',
        'time': '#FFD700', 'delta_better': '#4CAF50', 'delta_worse': '#808080',
        'podium_gold': '#545454', 'podium_silver': '#424242', 'podium_bronze': '#343434',
    },
    'theme_fonts': {'family': 'Overpass Mono', 'digits': 'DSEG7Classic', 'timing': 'Overpass Mono'},
    'intro_timeout': 300,
    'results_timeout': 300,
    'server_update_timeout': 300,
    'finish_debounce': 3.0,
    'split_min_duration': 1.0,
    'pool_length': 25,
    'touchpad_sides': 1,
    'carousel_interval': 10,
    'console_type': 'cts_gen6',
}

# ── Meet data ──────────────────────────────────────────────────────────────────

event_info            = HytekParser()
lenex_event_names     = {}
lenex_start_list      = {}
lenex_heat_times      = {}
lenex_meet_info       = {}
lenex_event_distances = {}

# ── Runtime state ──────────────────────────────────────────────────────────────

update      = {}

_last_results_snapshot      = {}
_results_prev_race_finished = False

_worker_stop        = False
_worker_gen         = 0
_test_session       = None
_record_handle      = None
_debug_serial       = False
_serial_status      = {'state': 'idle', 'msg': ''}
_finish_timer_gen   = 0
_scoreboard_clients = {}
_test_meet_active   = False
_overlay_active     = False
_cols_hidden        = False
_pty_fd             = None
_pty_pid            = None
main_thread         = None

in_speed = 1.0

_update_in_progress    = False
_active_meet_file      = ''   # basename of the currently loaded meet file
_os_update_in_progress = False
_update_log_lines      = []
_update_log_done       = None
_os_update_log_lines   = []
_os_update_log_done    = None

# ── Init ───────────────────────────────────────────────────────────────────────

def _ensure_data_dirs():
    for d in (SCOREBOARD_DIR, MEET_FOLDER, IMAGES_DIR, ICONS_DIR, CUSTOM_SESSIONS_FOLDER,
              CUSTOM_LOCALE_FOLDER, CUSTOM_THEME_FOLDER, CUSTOM_DECODERS_FOLDER):
        os.makedirs(d, exist_ok=True)
    if not os.path.exists(settings_file) and os.path.exists(_settings_default):
        import shutil
        shutil.copy2(_settings_default, settings_file)

_ensure_data_dirs()

# ── Locale / theme utilities ───────────────────────────────────────────────────

def _locale_path(code):
    custom = os.path.join(CUSTOM_LOCALE_FOLDER, code + '.toml')
    return custom if os.path.exists(custom) else os.path.join('locales', code + '.toml')

def load_locale(style=None):
    code  = settings.get('locale', 'fr')
    style = style or settings.get('label_style', 'long')
    try:
        with open(_locale_path(code), 'rb') as f:
            data = tomllib.load(f)
        return {k: v[style] for k, v in data['labels'].items()}
    except Exception:
        return dict(_FALLBACK_LABELS)

def load_preview_strings():
    code = settings.get('locale', 'fr')
    try:
        with open(_locale_path(code), 'rb') as f:
            return tomllib.load(f).get('preview', {})
    except Exception:
        return {}

def _mobile_strings():
    code = settings.get('locale', 'en')
    try:
        with open(_locale_path(code), 'rb') as f:
            return tomllib.load(f).get('mobile', {})
    except Exception:
        return {}

def _read_locale_name(path, fallback):
    try:
        with open(path, 'rb') as f:
            return tomllib.load(f).get('meta', {}).get('name', fallback)
    except Exception:
        return fallback

def list_locales():
    result = []
    for path in sorted(glob.glob(os.path.join('locales', '*.toml'))):
        code = os.path.splitext(os.path.basename(path))[0]
        result.append((code, _read_locale_name(path, code)))
    return result

def list_custom_locales():
    result = []
    for path in sorted(glob.glob(os.path.join(CUSTOM_LOCALE_FOLDER, '*.toml'))):
        code = os.path.splitext(os.path.basename(path))[0]
        result.append((code, _read_locale_name(path, code)))
    return result

def _read_theme_name(path, fallback):
    try:
        with open(path, 'rb') as f:
            return tomllib.load(f).get('name', fallback)
    except Exception:
        return fallback

def list_builtin_themes():
    return [(os.path.splitext(os.path.basename(p))[0],
             _read_theme_name(p, os.path.splitext(os.path.basename(p))[0]))
            for p in sorted(glob.glob(os.path.join(THEME_FOLDER, '*.toml')))]

def list_custom_themes():
    return [(os.path.splitext(os.path.basename(p))[0],
             _read_theme_name(p, os.path.splitext(os.path.basename(p))[0]))
            for p in sorted(glob.glob(os.path.join(CUSTOM_THEME_FOLDER, '*.toml')))]

def load_theme(code):
    path = os.path.join(CUSTOM_THEME_FOLDER, code + '.toml')
    if not os.path.exists(path):
        path = os.path.join(THEME_FOLDER, code + '.toml')
    try:
        with open(path, 'rb') as f:
            data = tomllib.load(f)
        colors = {**DEFAULT_THEME_COLORS, **data.get('colors', {})}
        fonts  = {**DEFAULT_THEME_FONTS,  **data.get('fonts',  {})}
        return colors, fonts
    except Exception:
        return dict(DEFAULT_THEME_COLORS), dict(DEFAULT_THEME_FONTS)

def load_event_translations():
    try:
        with open(_locale_path(settings.get('locale', 'en')), 'rb') as f:
            return tomllib.load(f).get('event_name', {})
    except Exception:
        return {}

def translate_event_name(raw, ev):
    if not ev or not raw:
        return raw
    s    = raw.strip()
    unit = ev.get('unit', 'm')
    sep  = ev.get('separator', '  —  ')

    gender = ''
    for pat, key in _GENDER_PATTERNS:
        if re.search(pat, s, re.IGNORECASE):
            gender = ev.get(key, key)
            break

    age   = ''
    s_rest = s
    age_m = re.search(
        r'\b(\d+)\s*(?:[Uu](?:nder)?|&\s*[Uu]nder|[Aa]nd\s+[Uu]nder)\b'
        r'|\b[Uu](\d+)\b', s)
    if age_m:
        num    = age_m.group(1) or age_m.group(2)
        age    = '< ' + num
        s_rest = s[:age_m.start()] + s[age_m.end():]
    else:
        range_m = re.search(r'\b(\d{1,2}-\d{1,2})\b', s)
        if range_m:
            age    = range_m.group(1)
            s_rest = s[:range_m.start()] + s[range_m.end():]
        elif re.search(r'\bopen\b', s, re.IGNORECASE):
            age    = ev.get('open', 'Open')
            s_rest = re.sub(r'\bopen\b', '', s, flags=re.IGNORECASE)
        elif re.search(r'\bsenior\b', s, re.IGNORECASE):
            age    = ev.get('senior', 'Senior')
            s_rest = re.sub(r'\bsenior\b', '', s, flags=re.IGNORECASE)

    is_relay = bool(re.search(r'\brelay\b', s_rest, re.IGNORECASE))

    dist   = ''
    dist_m = re.search(r'\b(\d+[xX]\d+|\d+)\b', s_rest)
    if dist_m:
        dist = dist_m.group(1)

    stroke = ''
    for alias, key in _STROKE_ALIASES:
        if re.search(r'\b' + re.escape(alias) + r'\b', s_rest, re.IGNORECASE):
            stroke = ev.get(key, alias)
            break

    left_parts = []
    if dist:
        left_parts.append(dist + ' ' + unit)
    if stroke:
        left_parts.append(stroke)
    if is_relay and ev.get('relay'):
        left_parts.append(ev['relay'])
    left = ' '.join(left_parts)

    right = ' '.join(p for p in [gender, age] if p)

    if left and right:
        return left + sep + right
    return left or right or raw

# ── Settings loader ────────────────────────────────────────────────────────────

def load_settings():
    try:
        with open(settings_file, 'rt') as f:
            settings.update(json.load(f))
    except Exception:
        pass
    csv_files = glob.glob(os.path.join(MEET_FOLDER, '*.csv'))
    if csv_files:
        try:
            event_info.load(max(csv_files, key=os.path.getmtime))
        except Exception:
            pass
    lxf_files = glob.glob(os.path.join(MEET_FOLDER, '*.lxf'))
    if lxf_files:
        try:
            data = load_lenex(max(lxf_files, key=os.path.getmtime))
            lenex_event_names.update(data.event_names)
            lenex_start_list.update(data.start_list)
            lenex_heat_times.update(data.heat_times)
            lenex_meet_info.update(data.meet_info)
            lenex_event_distances.update(data.event_distances)
        except Exception:
            pass
    _decoder.configure(settings)

# ── Decoder (initialized after settings dict is defined) ──────────────────────

_decoder = make_decoder(settings.get('console_type', 'cts_gen6'), settings)

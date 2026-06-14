import ipaddress
import subprocess

import flask
import flask_login
from flask import Blueprint

import state

bp = Blueprint('network', __name__)


def _nmcli(*args, timeout=8):
    return subprocess.run(['nmcli'] + list(args),
                          capture_output=True, text=True, timeout=timeout)


@bp.route('/wifi_status')
@flask_login.login_required
def route_wifi_status():
    try:
        r       = _nmcli('radio', 'wifi')
        enabled = r.returncode == 0 and 'enabled' in r.stdout.lower()

        r2       = _nmcli('-t', '-f', 'DEVICE,TYPE', '--escape', 'no', 'device')
        wifi_dev = eth_dev = ''
        for line in r2.stdout.splitlines():
            parts = line.split(':')
            if len(parts) < 2:
                continue
            dev, typ = parts[0], parts[1]
            if typ == 'wifi' and not wifi_dev:
                wifi_dev = dev
            elif typ == 'ethernet' and not eth_dev:
                eth_dev = dev

        def get_ip(device):
            if not device:
                return ''
            r = _nmcli('-t', '-f', 'IP4.ADDRESS', '--escape', 'no', 'dev', 'show', device)
            for line in r.stdout.splitlines():
                if line.startswith('IP4.ADDRESS'):
                    return line.split(':')[-1].split('/')[0]
            return ''

        # IN-USE is the correct field for dev wifi (not ACTIVE); active AP is marked with '*'
        r3   = _nmcli('-t', '-f', 'IN-USE,SSID', '--escape', 'no', 'dev', 'wifi')
        ssid = ''
        for line in r3.stdout.splitlines():
            if line.startswith('*:'):
                ssid = line.split(':', 1)[1]
                break

        return flask.jsonify({
            'enabled': enabled,
            'ssid':    ssid,
            'wifi_ip': get_ip(wifi_dev) if enabled else '',
            'eth_ip':  get_ip(eth_dev),
        })
    except FileNotFoundError:
        return flask.jsonify({'error': 'nmcli not found'}), 503
    except Exception as e:
        return flask.jsonify({'error': str(e)}), 500


@bp.route('/wifi_scan')
@flask_login.login_required
def route_wifi_scan():
    try:
        r     = _nmcli('--color', 'no', '-f', 'ACTIVE,SSID,SIGNAL,SECURITY',
                       'dev', 'wifi', 'list', timeout=20)
        lines = r.stdout.splitlines()
        if len(lines) < 2:
            return flask.jsonify({'networks': []})
        hdr = lines[0]
        col = {name: hdr.index(name) for name in ('ACTIVE', 'SSID', 'SIGNAL', 'SECURITY')}
        networks, seen = [], set()
        for line in lines[1:]:
            if len(line) <= col['SECURITY']:
                continue
            ssid     = line[col['SSID']   : col['SIGNAL']  ].strip()
            signal   = line[col['SIGNAL'] : col['SECURITY']].strip()
            security = line[col['SECURITY']:               ].strip()
            active   = line[col['ACTIVE'] : col['SSID']   ].strip()
            if not ssid or ssid in seen:
                continue
            seen.add(ssid)
            networks.append({
                'ssid':     ssid,
                'signal':   int(signal) if signal.isdigit() else 0,
                'security': '' if security in ('--', '') else security,
                'active':   '*' in active,
            })
        networks.sort(key=lambda n: n['signal'], reverse=True)
        return flask.jsonify({'networks': networks})
    except FileNotFoundError:
        return flask.jsonify({'error': 'nmcli not found'}), 503
    except Exception as e:
        return flask.jsonify({'error': str(e)}), 500


@bp.route('/wifi_toggle', methods=['POST'])
@flask_login.login_required
def route_wifi_toggle():
    try:
        r            = _nmcli('radio', 'wifi')
        currently_on = 'enabled' in r.stdout.lower()
        st           = 'off' if currently_on else 'on'
        subprocess.run(['sudo', 'nmcli', 'radio', 'wifi', st],
                       capture_output=True, timeout=8)
        return flask.jsonify({'enabled': not currently_on})
    except Exception as e:
        return flask.jsonify({'error': str(e)}), 500


@bp.route('/cloud_status')
@flask_login.login_required
def route_cloud_status():
    import relay
    return flask.jsonify(relay.status())


@bp.route('/cloud_toggle', methods=['POST'])
@flask_login.login_required
def route_cloud_toggle():
    import relay
    if relay.status()['running']:
        relay.stop()
    else:
        relay.start()
    return flask.jsonify(relay.status())


@bp.route('/wifi_connect', methods=['POST'])
@flask_login.login_required
def route_wifi_connect():
    data     = flask.request.get_json(force=True)
    ssid     = data.get('ssid', '')
    password = data.get('password', '')
    if not ssid:
        return flask.jsonify({'error': 'No SSID provided'}), 400
    try:
        # Remove any stale connection profile for this SSID first. nmcli
        # otherwise reuses an existing profile (e.g. from a previous attempt
        # on an open network) that lacks 802-11-wireless-security.key-mgmt,
        # which then fails validation once a password is supplied.
        subprocess.run(['sudo', 'nmcli', 'connection', 'delete', ssid],
                       capture_output=True, timeout=8)

        cmd = ['dev', 'wifi', 'connect', ssid]
        if password:
            cmd += ['password', password]
        r = _nmcli(*cmd, timeout=30)
        if r.returncode == 0:
            return flask.jsonify({'ok': True})
        return flask.jsonify({'error': (r.stderr or r.stdout).strip()}), 400
    except Exception as e:
        return flask.jsonify({'error': str(e)}), 500


@bp.route('/eth_dhcp_set', methods=['POST'])
@flask_login.login_required
def route_eth_dhcp_set():
    try:
        r = subprocess.run(
            ['sudo', 'nmcli', 'con', 'mod', 'tremplin-eth',
             'ipv4.method', 'auto', 'ipv4.addresses', '', 'ipv4.gateway', ''],
            capture_output=True, text=True, timeout=8)
        if r.returncode != 0:
            return flask.jsonify({'ok': False, 'error': r.stderr.strip() or 'nmcli error'})
        subprocess.run(['sudo', 'nmcli', 'con', 'up', 'tremplin-eth'],
                       capture_output=True, timeout=8)
        return flask.jsonify({'ok': True})
    except Exception as e:
        return flask.jsonify({'ok': False, 'error': str(e)})


@bp.route('/eth_ip_set', methods=['POST'])
@flask_login.login_required
def route_eth_ip_set():
    data       = flask.request.get_json()
    ip_str     = data.get('ip', '').strip()
    prefix_str = data.get('prefix', '24').strip()
    try:
        ipaddress.IPv4Address(ip_str)
        prefix = int(prefix_str)
        if not (1 <= prefix <= 32):
            raise ValueError
    except Exception:
        return flask.jsonify({'ok': False, 'error': 'Invalid IP address or prefix length'})
    cidr = f'{ip_str}/{prefix}'
    try:
        r = subprocess.run(
            ['sudo', 'nmcli', 'con', 'mod', 'tremplin-eth', 'ipv4.addresses', cidr],
            capture_output=True, text=True, timeout=8)
        if r.returncode != 0:
            return flask.jsonify({'ok': False, 'error': r.stderr.strip() or 'nmcli error'})
        subprocess.run(['sudo', 'nmcli', 'con', 'up', 'tremplin-eth'],
                       capture_output=True, timeout=8)
        return flask.jsonify({'ok': True})
    except Exception as e:
        return flask.jsonify({'ok': False, 'error': str(e)})


@bp.route('/clients')
@flask_login.login_required
def route_clients():
    # Browser tabs connected to the scoreboard WebSocket, shown in the Network tab
    return flask.jsonify({'clients': list(state._scoreboard_clients.values())})

import os

import flask
from flask import Blueprint

import state

bp = Blueprint('scoreboard', __name__)


@bp.route('/')
def route_index():
    return flask.redirect('/live')


@bp.route('/scoreboard')
def route_scoreboard_default():
    lanes = int(state.settings.get('num_lanes', 8))
    return flask.render_template('scoreboard.html',
                                 meet_title=state.settings['meet_title'],
                                 num_lanes=lanes,
                                 nosplash='nosplash' in flask.request.args,
                                 test_background='test' in flask.request.args)


@bp.route('/live')
def route_live():
    lanes = int(state.settings.get('num_lanes', 8))
    carousel_images = sorted(
        f for f in os.listdir(state.IMAGES_DIR)
        if os.path.isfile(os.path.join(state.IMAGES_DIR, f))
    )
    return flask.render_template('live.html',
                                 meet_title=state.settings['meet_title'],
                                 num_lanes=lanes,
                                 nosplash='nosplash' in flask.request.args,
                                 test_background='test' in flask.request.args,
                                 carousel_images=carousel_images,
                                 carousel_interval=int(state.settings.get('carousel_interval', 10)),
                                 theme_fonts={**state.DEFAULT_THEME_FONTS,
                                              **state.settings.get('theme_fonts', {})})


@bp.route('/live-mobile')
def route_live_mobile():
    lanes = int(state.settings.get('num_lanes', 8))
    carousel_images = sorted(
        f for f in os.listdir(state.IMAGES_DIR)
        if os.path.isfile(os.path.join(state.IMAGES_DIR, f))
    )
    return flask.render_template('live-mobile.html',
                                 meet_title=state.settings['meet_title'],
                                 num_lanes=lanes,
                                 carousel_images=carousel_images,
                                 carousel_interval=int(state.settings.get('carousel_interval', 10)),
                                 theme_fonts={**state.DEFAULT_THEME_FONTS,
                                              **state.settings.get('theme_fonts', {})})


@bp.route('/mobile')
def route_mobile():
    return flask.render_template('mobile.html', t=state._mobile_strings())


@bp.route('/results')
def route_results():
    return flask.render_template('results.html',
                                 num_lanes=int(state.settings.get('num_lanes', 8)),
                                 theme_colors={**state.DEFAULT_THEME_COLORS,
                                               **state.settings.get('theme_colors', {})},
                                 theme_fonts={**state.DEFAULT_THEME_FONTS,
                                              **state.settings.get('theme_fonts', {})},
                                 t=state._mobile_strings())


@bp.route('/operator')
def route_operator():
    sides      = int(state.settings.get('touchpad_sides', 1))
    split_step = 2 if sides == 1 else 1
    return flask.render_template('operator.html',
                                 num_lanes=int(state.settings.get('num_lanes', 8)),
                                 split_step=split_step)


@bp.route('/console')
def route_console():
    return flask.render_template('console.html',
                                 num_lanes=max(int(state.settings.get('num_lanes', 8)), 12))


@bp.route('/next_heats')
def route_next_heats():
    return flask.render_template('next_heats.html',
                                 theme_colors={**state.DEFAULT_THEME_COLORS,
                                               **state.settings.get('theme_colors', {})},
                                 theme_fonts={**state.DEFAULT_THEME_FONTS,
                                              **state.settings.get('theme_fonts', {})},
                                 num_lanes=int(state.settings.get('num_lanes', 8)),
                                 t=state._mobile_strings())


@bp.route('/info')
def route_info():
    return flask.render_template('info.html')


@bp.route('/help')
def route_help():
    return flask.render_template('help.html')


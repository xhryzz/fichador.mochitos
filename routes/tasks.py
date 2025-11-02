# routes/tasks.py
from flask import Blueprint, request, abort, current_app, jsonify
from tasks.notifications_jobs import run_tick, run_weekly_summaries

bp = Blueprint('tasks', url_prefix='/tasks')

def _check():
    token_req = request.args.get('token') or request.headers.get('X-CRON-TOKEN')
    token_cfg = current_app.config.get('CRON_TOKEN')
    if not token_cfg or token_req != token_cfg:
        abort(403)

@bp.get('/run-tick')
def run_tick_endpoint():
    _check()
    try:
        run_tick()
        return "ok"
    except Exception as e:
        current_app.logger.exception("run_tick failed")
        return jsonify(error="run_tick failed", detail=str(e)), 500

@bp.get('/run-weekly')
def run_weekly_endpoint():
    _check()
    try:
        run_weekly_summaries()
        return "ok"
    except Exception as e:
        current_app.logger.exception("run_weekly_summaries failed")
        return jsonify(error="run_weekly_summaries failed", detail=str(e)), 500

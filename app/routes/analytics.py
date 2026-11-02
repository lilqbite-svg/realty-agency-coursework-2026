from flask import Blueprint, abort, current_app, make_response, render_template, request
from flask_login import current_user
from sqlalchemy import select

from app.extensions import db
from app.models import Agent
from app.services.access import roles_required
from app.services.analytics import (AnalyticsFilterError, PRESETS, build_analytics,
                                    export_csv_bytes, export_pdf_bytes, parse_filters)

bp = Blueprint('analytics', __name__, url_prefix='/dashboard/analytics')


def _report():
    try:
        return build_analytics(parse_filters(request.args, current_user))
    except AnalyticsFilterError as error:
        return str(error)


@bp.get('')
@roles_required('ADMIN', 'REALTOR')
def index():
    result = _report()
    agents = db.session.scalars(select(Agent).order_by(Agent.full_name, Agent.id)).all() if current_user.role == 'ADMIN' else [current_user.agent]
    if isinstance(result, str):
        return render_template('dashboard/analytics.html', title='Аналитика', report=None, error=result, agents=agents, presets=PRESETS), 400
    return render_template('dashboard/analytics.html', title='Аналитика', report=result, error=None, agents=agents, presets=PRESETS)


@bp.get('/export.csv')
@roles_required('ADMIN', 'REALTOR')
def export_csv():
    report = _report()
    if isinstance(report, str):
        abort(400)
    response = make_response(export_csv_bytes(report))
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    response.headers['Content-Disposition'] = f'attachment; filename="analytics_{report["filters"].start}_{report["filters"].end}.csv"'
    return response


@bp.get('/export.pdf')
@roles_required('ADMIN', 'REALTOR')
def export_pdf():
    report = _report()
    if isinstance(report, str):
        abort(400)
    try:
        content = export_pdf_bytes(report)
    except (ImportError, RuntimeError):
        current_app.logger.warning('Analytics PDF unavailable: dependency or Unicode font missing')
        return render_template('dashboard/analytics.html', title='Аналитика', report=report,
                               error='PDF временно недоступен: отсутствует библиотека или шрифт с кириллицей. Экспорт CSV доступен.',
                               agents=db.session.scalars(select(Agent).order_by(Agent.full_name)).all() if current_user.role == 'ADMIN' else [current_user.agent], presets=PRESETS), 503
    response = make_response(content)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename="analytics_{report["filters"].start}_{report["filters"].end}.pdf"'
    return response

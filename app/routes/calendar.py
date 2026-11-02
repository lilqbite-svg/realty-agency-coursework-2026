from datetime import timedelta

from flask import Blueprint, abort, jsonify, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.forms import VIEWING_STATUSES
from app.models import Agent, Client, Property, Request, Viewing
from app.services.access import clients_query, roles_required, scoped
from app.services.scheduling import SchedulingError, parse_start, positive_id, save_viewing, to_moscow

bp = Blueprint('calendar', __name__, url_prefix='/dashboard/calendar')
staff = roles_required('REALTOR', 'ADMIN')


def serialize(record):
    start = record.viewing_date
    end = start + timedelta(minutes=record.duration_minutes) if start else None
    return {
        'id': record.id, 'title': record.property.title, 'property_id': record.property_id,
        'client_id': record.client_id, 'client_name': record.client.full_name,
        'agent_id': record.agent_id, 'agent_name': record.agent.full_name,
        'request_id': record.request_id, 'start': start.isoformat() + 'Z' if start else None,
        'end': end.isoformat() + 'Z' if end else None,
        'local_start': to_moscow(start).isoformat() if start else '',
        'duration_minutes': record.duration_minutes, 'status': record.status,
        'status_label': dict(VIEWING_STATUSES)[record.status], 'result': record.result,
        'comment': record.comment, 'client_message': record.client_message,
        'version': record.schedule_version,
        'url': url_for('dashboard.viewing_form', viewing_id=record.id),
    }


def accessible_record(viewing_id):
    record = db.get_or_404(Viewing, viewing_id)
    if current_user.role != 'ADMIN' and record.agent_id != current_user.agent.id:
        abort(403)
    return record


@bp.get('')
@staff
def index():
    properties = db.session.scalars(scoped(Property).order_by(Property.title)).all()
    clients = db.session.scalars(clients_query().order_by(Client.full_name)).all()
    agents = db.session.scalars(select(Agent).join(Agent.account).where(Agent.account.has(is_active=True)).order_by(Agent.full_name)).all() if current_user.role == 'ADMIN' else [current_user.agent]
    requests = db.session.scalars(scoped(Request).order_by(Request.created_at.desc())).all()
    # Prefill identifiers are checked against the exact visible choices. Query
    # strings cannot disclose or select records owned by another employee.
    prefill = {}
    for name, choices in (('property_id', properties), ('client_id', clients), ('agent_id', agents), ('request_id', requests)):
        if request.args.get(name):
            try:
                value = positive_id(request.args[name], name)
            except SchedulingError:
                abort(400)
            if value not in {item.id for item in choices}:
                abort(403)
            prefill[name] = value
    if prefill.get('request_id'):
        linked = next(item for item in requests if item.id == prefill['request_id'])
        if prefill.get('client_id', linked.client_id) != linked.client_id:
            abort(400)
        prefill['client_id'] = linked.client_id
    return render_template('dashboard/calendar.html', title='Календарь показов',
                           properties=properties, clients=clients, agents=agents,
                           requests=requests, statuses=VIEWING_STATUSES, prefill=prefill)


@bp.get('/events')
@staff
def events():
    try:
        start, end = parse_start(request.args.get('start')), parse_start(request.args.get('end'))
        if not start or not end or end <= start or end - start > timedelta(days=93):
            raise SchedulingError('Выберите диапазон от одного дня до 93 дней.')
        status = request.args.get('status', '')
        if status and status not in dict(VIEWING_STATUSES):
            raise SchedulingError('Неизвестный статус.')
        agent_id = positive_id(request.args.get('agent_id'), 'Риэлтор', optional=True)
        if agent_id and current_user.role != 'ADMIN' and agent_id != current_user.agent.id:
            raise SchedulingError('Недостаточно прав для просмотра чужого календаря.', 403)
        query = scoped(Viewing).options(joinedload(Viewing.property), joinedload(Viewing.client), joinedload(Viewing.agent))
        if status:
            query = query.where(Viewing.status == status)
        if agent_id:
            query = query.where(Viewing.agent_id == agent_id)
        records = db.session.scalars(query.where(Viewing.viewing_date >= start - timedelta(minutes=480), Viewing.viewing_date < end).order_by(Viewing.viewing_date).limit(1001)).all()
        unscheduled = db.session.scalars(query.where(Viewing.viewing_date.is_(None)).order_by(Viewing.created_at.desc()).limit(201)).all()
        if len(records) > 1000 or len(unscheduled) > 200:
            raise SchedulingError('Слишком много показов. Уточните период или фильтры.')
        records = [record for record in records if record.viewing_date + timedelta(minutes=record.duration_minutes) > start]
        return jsonify(events=[serialize(record) for record in records], unscheduled=[serialize(record) for record in unscheduled], timezone='Europe/Moscow')
    except SchedulingError as error:
        return jsonify(error=str(error)), error.status_code


@bp.get('/events/<int:viewing_id>')
@staff
def detail(viewing_id):
    return jsonify(serialize(accessible_record(viewing_id)))


def write_event(viewing_id=None, moving=False):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(error='Передайте поля показа в JSON.'), 400
    record = accessible_record(viewing_id) if viewing_id else Viewing()
    try:
        expected = positive_id(payload.get('version'), 'Версия записи') if viewing_id else None
        values = payload
        if moving:
            if not payload.get('viewing_date'):
                raise SchedulingError('Укажите новое время показа.')
            values = {key: payload[key] for key in ('viewing_date', 'duration_minutes') if key in payload}
        saved = save_viewing(record, values, current_user, expected_version=expected)
        return jsonify(event=serialize(saved)), 200 if viewing_id else 201
    except SchedulingError as error:
        return jsonify(error=str(error)), error.status_code


@bp.post('/events')
@staff
def create():
    return write_event()


@bp.post('/events/<int:viewing_id>')
@staff
def edit(viewing_id):
    return write_event(viewing_id)


@bp.post('/events/<int:viewing_id>/move')
@staff
def move(viewing_id):
    return write_event(viewing_id, moving=True)

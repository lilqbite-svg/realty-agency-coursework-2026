"""Staff-only property recommendations and persistent request matching history."""
from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from flask_wtf import FlaskForm
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from wtforms import DecimalField, IntegerField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, NumberRange, Optional, ValidationError

from app.extensions import db
from app.forms import DEALS, TYPES
from app.models import Property, Request, RequestMatch
from app.services.access import own_record, roles_required, scoped
from app.services.matching import (ALGORITHM_VERSION, configured_weights, decode, encode,
                                  evaluate_match, history_event, history_for_display, snapshot)

bp = Blueprint('matching', __name__, url_prefix='/dashboard/requests')
staff = roles_required('REALTOR', 'ADMIN')
MATCH_STATUSES = [('saved', 'Сохранен'), ('rejected', 'Отклонен'), ('viewing', 'Выбран для показа')]
SORTS = [('score', 'По соответствию'), ('price_asc', 'Сначала дешевле'), ('price_desc', 'Сначала дороже'), ('area_desc', 'Сначала просторнее')]


class MatchEditForm(FlaskForm):
    status = SelectField('Решение по варианту', choices=MATCH_STATUSES)
    agent_comment = TextAreaField('Комментарий риэлтора', validators=[Optional(), Length(max=5000)])


class RequirementsForm(FlaskForm):
    deal_type = SelectField('Тип сделки', choices=DEALS, validators=[DataRequired()])
    property_type = SelectField('Тип объекта', choices=TYPES, validators=[DataRequired()])
    min_price = DecimalField('Бюджет от, ₽', validators=[Optional(), NumberRange(min=0, max=999999999999.99)])
    max_price = DecimalField('Бюджет до, ₽', validators=[Optional(), NumberRange(min=0, max=999999999999.99)])
    rooms = IntegerField('Количество комнат', validators=[Optional(), NumberRange(min=0, max=100)])
    preferred_area = StringField('Предпочтительный район', validators=[Optional(), Length(max=120)])

    def validate_max_price(self, field):
        if field.data is not None and self.min_price.data is not None and field.data < self.min_price.data:
            raise ValidationError('Максимальный бюджет меньше минимального.')


def allowed_request(request_id):
    # A free request must be claimed before matching, so it cannot expose another agent's objects.
    return own_record(db.get_or_404(Request, request_id))


def allowed_match(record, match_id):
    match = db.get_or_404(RequestMatch, match_id)
    if match.request_id != record.id:
        abort(404)
    own_record(match.property)
    return match


def back(record):
    return redirect(url_for('matching.index', request_id=record.id))


def save_change(match, values, event):
    """Compare-and-swap preserves history if two browser tabs edit the same pair."""
    previous_history = match.history_json
    values['history_json'] = encode(decode(previous_history, []) + [event])
    values['updated_at'] = datetime.now()
    changed = db.session.execute(update(RequestMatch).where(RequestMatch.id == match.id,
                                RequestMatch.history_json == previous_history).values(**values))
    if changed.rowcount != 1:
        db.session.rollback()
        abort(409, description='Вариант уже изменен в другом окне. Обновите страницу.')
    db.session.commit()


@bp.get('/<int:request_id>/matches')
@staff
def index(request_id):
    record = allowed_request(request_id)
    sort = request.args.get('sort', 'score')
    if sort not in dict(SORTS):
        abort(400)
    weights = configured_weights()
    query = scoped(Property).where(Property.status == 'available', Property.deal_type == record.deal_type)
    if record.property_type:
        query = query.where(Property.property_type == record.property_type)
    if record.min_price is not None:
        query = query.where(Property.price >= record.min_price)
    if record.max_price is not None:
        query = query.where(Property.price <= record.max_price)
    candidates = [{'property': item, 'evaluation': evaluate_match(record, item, weights)}
                  for item in db.session.scalars(query).all()]
    candidates = [item for item in candidates if item['evaluation']['eligible']]
    ordering = {'score': lambda item: (-item['evaluation']['score'], item['property'].id),
                'price_asc': lambda item: (item['property'].price, item['property'].id),
                'price_desc': lambda item: (-item['property'].price, item['property'].id),
                'area_desc': lambda item: (-item['property'].area, item['property'].id)}
    candidates.sort(key=ordering[sort])
    saved_query = select(RequestMatch).join(Property).where(RequestMatch.request_id == record.id)
    if current_user.role != 'ADMIN':
        saved_query = saved_query.where(Property.agent_id == current_user.agent.id)
    saved = db.session.scalars(saved_query.order_by(RequestMatch.created_at.desc(), RequestMatch.id.desc())).all()
    saved_ids = {item.property_id for item in saved}
    saved_rows = []
    for match in saved:
        evaluation = evaluate_match(record, match.property, weights)
        saved_snapshot = decode(match.snapshot_json, {})
        saved_rows.append({'match': match, 'evaluation': evaluation, 'form': MatchEditForm(obj=match),
                           'saved_snapshot': saved_snapshot,
                           'changed': saved_snapshot != snapshot(evaluation),
                           'history': history_for_display(match.history_json)})
    return render_template('dashboard/matching.html', title=f'Подбор по заявке №{record.id}', record=record,
                           candidates=candidates, saved_rows=saved_rows, saved_ids=saved_ids,
                           sort=sort, sorts=SORTS, match_labels=dict(MATCH_STATUSES),
                           version=ALGORITHM_VERSION, weights=weights, requirements_form=RequirementsForm(obj=record))


@bp.post('/<int:request_id>/matches/requirements')
@staff
def requirements(request_id):
    record = allowed_request(request_id)
    form = RequirementsForm()
    if not form.validate_on_submit():
        errors = '; '.join(f'{getattr(form, key).label.text}: {", ".join(messages)}'
                           for key, messages in form.errors.items() if key != 'csrf_token')
        flash('Требования не изменены. ' + errors, 'danger')
        return back(record)
    for field in ('deal_type', 'property_type', 'min_price', 'max_price', 'rooms', 'preferred_area'):
        value = getattr(form, field).data
        setattr(record, field, value.strip() if isinstance(value, str) else value)
    db.session.commit()
    flash('Требования обновлены. Текущие оценки пересчитаны; сохраненные оценки и их история остались прежними до явного обновления.', 'success')
    return back(record)


@bp.post('/<int:request_id>/matches/save/<int:property_id>')
@staff
def save(request_id, property_id):
    record = allowed_request(request_id)
    property = own_record(db.get_or_404(Property, property_id))
    existing = db.session.scalar(select(RequestMatch).where(RequestMatch.request_id == record.id,
                                                           RequestMatch.property_id == property.id))
    if existing:
        flash('Вариант уже сохранен. Для пересчета используйте «Обновить сохраненную оценку».', 'info')
        return back(record)
    evaluation = evaluate_match(record, property)
    if not evaluation['eligible']:
        flash('Объект не соответствует обязательным условиям заявки и не сохранен.', 'warning')
        return back(record)
    evidence = snapshot(evaluation)
    db.session.add(RequestMatch(request_id=record.id, property_id=property.id, score=evaluation['score'],
                               algorithm_version=ALGORITHM_VERSION, status='saved', agent_comment='',
                               snapshot_json=encode(evidence), history_json=encode([
                                   history_event('saved', current_user.id, snapshot=evidence, status='saved', agent_comment='')
                               ])))
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        # Only the duplicate pair is recoverable; unrelated integrity failures must not be reported as success.
        if not db.session.scalar(select(RequestMatch).where(RequestMatch.request_id == record.id,
                                                            RequestMatch.property_id == property.id)):
            raise
        flash('Этот вариант уже сохранен другим запросом.', 'info')
    else:
        flash('Вариант сохранен в подборку с оценкой и параметрами расчета.', 'success')
    return back(record)


@bp.post('/<int:request_id>/matches/refresh/<int:match_id>')
@staff
def refresh(request_id, match_id):
    record = allowed_request(request_id)
    match = allowed_match(record, match_id)
    evaluation = evaluate_match(record, match.property)
    evidence = snapshot(evaluation)
    previous = decode(match.snapshot_json, {})
    if previous == evidence:
        flash('Оценка уже актуальна. История сохранена.', 'info')
        return back(record)
    save_change(match, {'score': evaluation['score'], 'algorithm_version': ALGORITHM_VERSION,
                       'snapshot_json': encode(evidence)},
                history_event('refreshed', current_user.id, previous_snapshot=previous, snapshot=evidence))
    flash('Сохраненная оценка обновлена; предыдущий расчет остался в истории.', 'success')
    return back(record)


@bp.post('/<int:request_id>/matches/edit/<int:match_id>')
@staff
def edit(request_id, match_id):
    record = allowed_request(request_id)
    match = allowed_match(record, match_id)
    form = MatchEditForm()
    if not form.validate_on_submit():
        flash('Проверьте статус и комментарий: допускается до 5000 символов.', 'danger')
        return back(record)
    comment = (form.agent_comment.data or '').strip()
    if match.status == form.status.data and match.agent_comment == comment:
        flash('Изменений нет.', 'info')
        return back(record)
    save_change(match, {'status': form.status.data, 'agent_comment': comment},
                history_event('edited', current_user.id,
                              previous_status=match.status, status=form.status.data,
                              previous_comment=match.agent_comment, agent_comment=comment))
    flash('Решение и комментарий сохранены в истории подборки.', 'success')
    return back(record)

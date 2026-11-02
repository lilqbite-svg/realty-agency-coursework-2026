from datetime import datetime, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.forms import (ClientForm, DealForm, PropertyForm, RequestEditForm,
                       ViewingForm)
from app.models import Agent, Client, Deal, Property, PropertyPhoto, Request, Viewing
from app.services.access import allowed_client, clients_query, own_record, roles_required, scoped
from app.services.uploads import attach_photos, photo_path, prepare_photos
from app.services.workflows import save_deal, viewing_key

bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')
staff = roles_required('REALTOR', 'ADMIN')


def form_page(title, form, **kwargs):
    return render_template('dashboard/form.html', title=title, form=form, **kwargs)


def records_page(title, items, kind):
    return render_template('dashboard/list.html', title=title, items=items, kind=kind)


def count_query(query):
    return db.session.scalar(select(func.count()).select_from(query.subquery()))


@bp.get('')
@staff
def index():
    now = datetime.now()
    kpis = [count_query(scoped(Property).where(Property.status == 'available')),
            count_query(scoped(Request, True).where(Request.status == 'new')),
            count_query(scoped(Viewing).where(Viewing.status.in_(['requested', 'confirmed']))),
            count_query(scoped(Deal).where(Deal.status == 'completed', Deal.deal_date >= now.date().replace(day=1)))]
    dates = [(now - timedelta(days=i)).date() for i in reversed(range(7))]
    recent = db.session.scalars(scoped(Request, True).where(Request.created_at >= datetime.combine(dates[0], datetime.min.time()))).all()
    counts = [sum(r.created_at.date() == day for r in recent) for day in dates]
    chart = [(day.strftime('%d.%m'), count, (count / max(max(counts), 1)) * 85) for day, count in zip(dates, counts)]
    return render_template('dashboard/index.html', title='Рабочая панель', kpis=kpis, chart=chart,
                           requests=db.session.scalars(scoped(Request, True).order_by(Request.created_at.desc()).limit(5)).all(),
                           clients=db.session.scalars(clients_query().order_by(Client.created_at.desc()).limit(5)).all(),
                           viewings=db.session.scalars(scoped(Viewing).where(Viewing.status.in_(['requested', 'confirmed'])).order_by(Viewing.viewing_date).limit(5)).all(),
                           deals=db.session.scalars(scoped(Deal).order_by(Deal.created_at.desc()).limit(5)).all())


@bp.get('/properties')
@staff
def properties():
    return records_page('Объекты недвижимости', db.session.scalars(scoped(Property).order_by(Property.created_at.desc())).all(), 'properties')


def property_choices(form):
    clients = db.session.scalars(clients_query().order_by(Client.full_name)).all()
    form.owner_id.choices = [(0, 'Не указан')] + [(c.id, c.full_name) for c in clients]
    agents = db.session.scalars(select(Agent).join(Agent.account).where(Agent.account.has(is_active=True))).all() if current_user.role == 'ADMIN' else [current_user.agent]
    form.agent_id.choices = [(a.id, a.full_name) for a in agents]


@bp.route('/properties/new', methods=['GET', 'POST'])
@bp.route('/properties/<int:property_id>/edit', methods=['GET', 'POST'])
@staff
def property_form(property_id=None):
    property = own_record(db.get_or_404(Property, property_id)) if property_id else Property(agent_id=current_user.agent.id)
    form = PropertyForm(obj=property)
    property_choices(form)
    if request.method == 'GET':
        form.owner_id.data = property.owner_id or 0
        form.agent_id.data = property.agent_id
    if form.validate_on_submit():
        try:
            prepared = prepare_photos(form.photos.data)
        except ValueError as error:
            form.photos.errors.append(str(error))
        else:
            completed = db.session.scalar(select(Deal).where(Deal.completed_key == property.id)) if property.id else None
            if completed and form.deal_type.data != property.deal_type:
                form.deal_type.errors.append('Тип объекта с заключенной сделкой нельзя изменить.')
            elif completed and form.status.data != property.status and form.status.data != 'archived':
                form.status.errors.append('Статус заключенной сделки можно изменить только на «В архиве».')
            else:
                for name in ('title', 'property_type', 'deal_type', 'address', 'district', 'area', 'rooms', 'floor', 'total_floors', 'price', 'description', 'status'):
                    setattr(property, name, getattr(form, name).data)
                property.owner_id = form.owner_id.data or None
                property.agent_id = form.agent_id.data if current_user.role == 'ADMIN' else current_user.agent.id
                # Keep employee ownership coherent when an admin reassigns an object.
                for viewing in property.viewings:
                    if viewing.status in ('requested', 'confirmed'):
                        viewing.agent_id = property.agent_id
                        if property.status in ('sold', 'rented', 'archived'):
                            viewing.status, viewing.active_key = 'cancelled', None
                db.session.add(property)
                paths = []
                try:
                    paths = attach_photos(property, prepared)
                    db.session.commit()
                except (IntegrityError, OSError):
                    db.session.rollback()
                    for path in paths:
                        path.unlink(missing_ok=True)
                    flash('Не удалось сохранить объект. Проверьте данные и повторите.', 'danger')
                else:
                    flash('Объект сохранен.', 'success')
                    return redirect(url_for('dashboard.property_form', property_id=property.id))
    return form_page('Редактирование объекта' if property_id else 'Добавить объект', form, property=property if property_id else None)


@bp.post('/properties/<int:property_id>/delete')
@staff
def archive_property(property_id):
    property = own_record(db.get_or_404(Property, property_id))
    property.status = 'archived'
    for viewing in property.viewings:
        if viewing.status in ('requested', 'confirmed'):
            viewing.status, viewing.active_key = 'cancelled', None
    db.session.commit()
    flash('Объект перенесен в архив; история сохранена.', 'success')
    return redirect(url_for('dashboard.properties'))


@bp.post('/photos/<int:photo_id>/main')
@staff
def main_photo(photo_id):
    photo = db.get_or_404(PropertyPhoto, photo_id)
    property = own_record(photo.property)
    for item in property.photos:
        item.is_main = item.id == photo.id
    db.session.commit()
    return redirect(url_for('dashboard.property_form', property_id=property.id))


@bp.post('/photos/<int:photo_id>/delete')
@staff
def delete_photo(photo_id):
    photo = db.get_or_404(PropertyPhoto, photo_id)
    property = own_record(photo.property)
    path = photo_path(photo)
    others = [p for p in property.photos if p.id != photo.id]
    if photo.is_main and others:
        others[0].is_main = True
    db.session.delete(photo)
    db.session.commit()
    try:
        path.unlink(missing_ok=True)
    except OSError:
        flash('Запись фото удалена, но файл занят. Повторите очистку файла позднее.', 'warning')
    flash('Фотография удалена.', 'success')
    return redirect(url_for('dashboard.property_form', property_id=property.id))


@bp.get('/clients')
@staff
def clients():
    query = clients_query()
    text = request.args.get('q', '').strip()
    if len(text) > 150:
        abort(400)
    if text:
        query = query.where(or_(Client.full_name.icontains(text, autoescape=True), Client.email.icontains(text, autoescape=True), Client.phone.icontains(text, autoescape=True)))
    return records_page('Клиенты', db.session.scalars(query.order_by(Client.full_name)).all(), 'clients')


@bp.route('/clients/new', methods=['GET', 'POST'])
@bp.route('/clients/<int:client_id>/edit', methods=['GET', 'POST'])
@staff
def client_form(client_id=None):
    person = allowed_client(client_id) if client_id else Client(created_by_agent_id=current_user.agent.id)
    form = ClientForm(obj=person)
    if form.validate_on_submit():
        for name in ('full_name', 'phone', 'email', 'client_type'):
            setattr(person, name, getattr(form, name).data)
        db.session.add(person)
        db.session.commit()
        flash('Клиент сохранен.', 'success')
        return redirect(url_for('dashboard.client_detail', client_id=person.id))
    return form_page('Карточка клиента' if client_id else 'Добавить клиента', form)


@bp.get('/clients/<int:client_id>')
@staff
def client_detail(client_id):
    person = allowed_client(client_id)
    return render_template('dashboard/client.html', title=person.full_name, person=person,
                           requests=db.session.scalars(scoped(Request).where(Request.client_id == client_id)).all(),
                           viewings=db.session.scalars(scoped(Viewing).where(Viewing.client_id == client_id)).all(),
                           deals=db.session.scalars(scoped(Deal).where(Deal.client_id == client_id)).all())


@bp.get('/requests')
@staff
def requests():
    return records_page('Заявки', db.session.scalars(scoped(Request, True).order_by(Request.created_at.desc())).all(), 'requests')


@bp.post('/requests/<int:request_id>/claim')
@staff
def claim_request(request_id):
    record = db.get_or_404(Request, request_id)
    if record.agent_id and record.agent_id != current_user.agent.id and current_user.role != 'ADMIN':
        abort(403)
    if record.agent_id is None:
        result = db.session.execute(update(Request).where(Request.id == request_id, Request.agent_id.is_(None)).values(agent_id=current_user.agent.id, status='in_progress'))
        if not result.rowcount:
            db.session.rollback()
            flash('Заявку уже взял другой сотрудник.', 'warning')
            return redirect(url_for('dashboard.requests'))
        db.session.commit()
    return redirect(url_for('dashboard.request_form', request_id=request_id))


@bp.route('/requests/<int:request_id>/edit', methods=['GET', 'POST'])
@staff
def request_form(request_id):
    record = db.get_or_404(Request, request_id)
    if record.agent_id is not None:
        own_record(record)
    form = RequestEditForm(obj=record)
    if form.validate_on_submit():
        if not record.agent_id:
            abort(403)
        record.status, record.internal_comment = form.status.data, form.internal_comment.data or ''
        db.session.commit()
        flash('Заявка обновлена.', 'success')
        return redirect(url_for('dashboard.requests'))
    filters = {'deal': record.deal_type, 'type': record.property_type, 'district': record.preferred_area or '', 'min_price': record.min_price or '', 'max_price': record.max_price or ''}
    if record.rooms is not None:
        filters['rooms'] = min(record.rooms, 4)
    return form_page(f'Заявка №{record.id}', form, record=record, matches_url=url_for('matching.index', request_id=record.id))


def relation_choices(form, record=None):
    form.property_id.choices = [(p.id, f'#{p.id} {p.title}') for p in db.session.scalars(scoped(Property).order_by(Property.title)).all()]
    form.client_id.choices = [(c.id, c.full_name) for c in db.session.scalars(clients_query().order_by(Client.full_name)).all()]
    if hasattr(form, 'request_id'):
        form.request_id.choices = [(0, 'Без связи с заявкой')] + [(r.id, f'№{r.id} — {r.client.full_name}') for r in db.session.scalars(scoped(Request).order_by(Request.id.desc())).all()]
        if not form.request_id.data:
            form.request_id.data = 0
    # Unassigned request clients become available after the employee claims the request.


@bp.get('/viewings')
@staff
def viewings():
    return records_page('Показы', db.session.scalars(scoped(Viewing).order_by(Viewing.created_at.desc())).all(), 'viewings')


@bp.route('/viewings/new', methods=['GET', 'POST'])
@bp.route('/viewings/<int:viewing_id>/edit', methods=['GET', 'POST'])
@staff
def viewing_form(viewing_id=None):
    from app.services.scheduling import SchedulingError, save_viewing, to_moscow
    record = own_record(db.get_or_404(Viewing, viewing_id)) if viewing_id else Viewing(agent_id=current_user.agent.id)
    form = ViewingForm(obj=record)
    relation_choices(form, record)
    if request.method == 'GET':
        form.duration_minutes.data = record.duration_minutes or 60
        form.schedule_version.data = str(record.schedule_version or 1)
        form.viewing_date.data = to_moscow(record.viewing_date).replace(tzinfo=None) if record.viewing_date else None
    if form.validate_on_submit():
        values = {name: getattr(form, name).data for name in ('property_id','client_id','request_id','viewing_date','status','result','comment')}
        values['duration_minutes'] = form.duration_minutes.data or 60
        try:
            version = int(form.schedule_version.data or 1)
            save_viewing(record, values, current_user, expected_version=version if viewing_id else None)
        except (SchedulingError, ValueError) as error:
            db.session.rollback()
            if getattr(error, 'status_code', None) == 403:
                abort(403)
            form.viewing_date.errors.append(str(error))
        else:
            flash('Показ сохранен.', 'success')
            return redirect(url_for('dashboard.viewings'))
    return form_page('Изменить показ' if viewing_id else 'Назначить показ', form, record=record if viewing_id else None)


@bp.get('/deals')
@staff
def deals():
    return records_page('Сделки', db.session.scalars(scoped(Deal).order_by(Deal.created_at.desc())).all(), 'deals')


@bp.route('/deals/new', methods=['GET', 'POST'])
@bp.route('/deals/<int:deal_id>/edit', methods=['GET', 'POST'])
@staff
def deal_form(deal_id=None):
    record = own_record(db.get_or_404(Deal, deal_id)) if deal_id else Deal(agent_id=current_user.agent.id)
    form = DealForm(obj=record)
    relation_choices(form, record)
    if not deal_id and request.method == 'GET':
        form.deal_date.data = datetime.now().date()
    if form.validate_on_submit():
        property = own_record(db.get_or_404(Property, form.property_id.data))
        allowed_client(form.client_id.data)
        try:
            save_deal(record, form, property)
        except ValueError as error:
            db.session.rollback()
            form.status.errors.append(str(error))
        else:
            record.agent_id = property.agent_id
            db.session.add(record)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                form.status.errors.append('Объект уже участвует в заключенной сделке.')
            else:
                flash('Сделка сохранена; статус объекта обновлен.', 'success')
                return redirect(url_for('dashboard.deals'))
    return form_page('Изменить сделку' if deal_id else 'Создать сделку', form)

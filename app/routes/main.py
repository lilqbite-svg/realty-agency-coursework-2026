from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func, select

from app.extensions import db
from app.forms import RequestForm
from app.models import Property, Request, Viewing
from app.routes.catalog import favorite_ids
from app.services.access import public_client
from app.services.workflows import get_contact_client

bp = Blueprint('main', __name__)


@bp.get('/')
def index():
    query = select(Property).where(Property.status == 'available')
    items = db.session.scalars(query.order_by(Property.id).limit(3)).all()
    count = db.session.scalar(select(func.count()).select_from(Property).where(Property.status == 'available'))
    return render_template('index.html', title='Агентство недвижимости', items=items, count=count, favorite_ids=favorite_ids())


@bp.route('/request', methods=['GET', 'POST'])
def new_request():
    form = RequestForm()
    person = public_client()
    if request.method == 'GET' and person:
        for name in ('full_name', 'phone', 'email'):
            getattr(form, name).data = getattr(person, name)
    if form.validate_on_submit():
        if current_user.is_authenticated and current_user.role != 'CLIENT':
            abort(403)
        person = get_contact_client(form, form.deal_type.data)
        values = {name: getattr(form, name).data for name in ('deal_type', 'property_type', 'min_price', 'max_price', 'rooms', 'preferred_area')}
        db.session.add(Request(client=person, message=form.message.data or '', **values))
        db.session.commit()
        flash('Заявка отправлена и сохранена.', 'success')
        return redirect(url_for('main.my_requests'))
    return render_template('public_form.html', title='Заявка на подбор недвижимости', form=form)


def my_records(model):
    person = public_client()
    return db.session.scalars(select(model).where(model.client_id == person.id).order_by(model.created_at.desc())).all() if person else []


@bp.get('/my-requests')
def my_requests():
    return render_template('my_records.html', title='Мои заявки', items=my_records(Request), kind='requests')


@bp.get('/my-viewings')
def my_viewings():
    return render_template('my_records.html', title='Мои показы', items=my_records(Viewing), kind='viewings')


@bp.get('/requests/<int:request_id>')
def request_detail(request_id):
    record = db.get_or_404(Request, request_id)
    person = public_client()
    if not person or record.client_id != person.id:
        abort(403)
    return render_template('my_records.html', title=f'Заявка №{record.id}', items=[record], kind='requests')

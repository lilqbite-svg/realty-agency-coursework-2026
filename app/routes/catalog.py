from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.forms import CatalogForm, PublicViewingForm
from app.models import Favorite, Property, Viewing
from app.services.access import public_client, roles_required
from app.services.workflows import get_contact_client, viewing_key

bp = Blueprint('catalog', __name__)


def favorite_ids():
    if current_user.is_authenticated:
        return set(db.session.scalars(select(Favorite.property_id).where(Favorite.user_id == current_user.id)))
    return set()


@bp.get('/catalog')
def index():
    data = MultiDict(request.args)
    data.setdefault('page', '1')
    data.setdefault('sort', 'newest')
    form = CatalogForm(data, meta={'csrf': False})
    valid = form.validate()
    query = select(Property).where(Property.status.in_(['available', 'reserved']))
    if valid:
        if form.q.data:
            text = form.q.data.strip()
            query = query.where(or_(Property.title.icontains(text, autoescape=True), Property.address.icontains(text, autoescape=True), Property.district.icontains(text, autoescape=True)))
        for name, column in [('deal', Property.deal_type), ('type', Property.property_type)]:
            value = getattr(form, name).data
            if value:
                query = query.where(column == value)
        if form.rooms.data:
            rooms = int(form.rooms.data)
            query = query.where(Property.rooms >= 4 if rooms == 4 else Property.rooms == rooms)
        if form.district.data:
            query = query.where(Property.district.icontains(form.district.data.strip(), autoescape=True))
        for name, column, minimum in [('min_price', Property.price, True), ('max_price', Property.price, False), ('min_area', Property.area, True), ('max_area', Property.area, False)]:
            value = getattr(form, name).data
            if value is not None:
                query = query.where(column >= value if minimum else column <= value)
        sorts = {'newest': Property.created_at.desc(), 'price_asc': Property.price.asc(), 'price_desc': Property.price.desc(), 'area_asc': Property.area.asc(), 'area_desc': Property.area.desc()}
        query = query.order_by(sorts[form.sort.data], Property.id.desc())
        page = db.paginate(query, page=form.page.data, per_page=12, error_out=False)
    else:
        page = db.paginate(query.where(Property.id < 0), page=1, per_page=12, error_out=False)
    params = request.args.to_dict()
    params.pop('page', None)
    return render_template('catalog.html', title='Каталог недвижимости', form=form, page=page, params=params, favorite_ids=favorite_ids()), 200 if valid else 400


@bp.route('/property/<int:property_id>', methods=['GET', 'POST'])
def detail(property_id):
    property = db.get_or_404(Property, property_id)
    if property.status == 'archived':
        abort(404)
    form = PublicViewingForm()
    person = public_client()
    if request.method == 'GET' and person:
        for name in ('full_name', 'phone', 'email'):
            getattr(form, name).data = getattr(person, name)
    if form.validate_on_submit():
        if current_user.is_authenticated and current_user.role != 'CLIENT':
            abort(403)
        if property.status != 'available':
            flash('Объект сейчас недоступен для новых показов.', 'warning')
        else:
            person = get_contact_client(form, property.deal_type)
            key = viewing_key(property.id, person.id, 'requested')
            if db.session.scalar(select(Viewing).where(Viewing.active_key == key)):
                flash('Вы уже записались на этот объект. Риэлтор согласует время.', 'info')
            else:
                db.session.add(Viewing(property=property, client=person, agent=property.agent, active_key=key, client_message=form.message.data or ''))
                try:
                    db.session.commit()
                except IntegrityError:
                    db.session.rollback()
                    flash('Активная запись на этот показ уже существует.', 'info')
                else:
                    flash('Запись на просмотр сохранена. Риэлтор свяжется с вами.', 'success')
            return redirect(url_for('main.my_viewings'))
    return render_template('property.html', title=property.title, property=property, form=form, favorite_ids=favorite_ids())


@bp.get('/favorites')
@roles_required('CLIENT')
def favorites():
    items = db.session.scalars(select(Property).join(Favorite).where(Favorite.user_id == current_user.id, Property.status != 'archived')).all()
    return render_template('favorites.html', title='Избранное', items=items, favorite_ids=favorite_ids())


@bp.post('/favorites/<int:property_id>/toggle')
@roles_required('CLIENT')
def toggle(property_id):
    property = db.get_or_404(Property, property_id)
    if property.status == 'archived':
        abort(404)
    favorite = db.session.scalar(select(Favorite).where(Favorite.user_id == current_user.id, Favorite.property_id == property_id))
    if favorite:
        db.session.delete(favorite)
    else:
        db.session.add(Favorite(user_id=current_user.id, property_id=property_id))
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify(error='Повторите действие.'), 409
    return jsonify(favorite=favorite is None)

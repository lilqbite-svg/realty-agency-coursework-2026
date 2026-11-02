from flask import session
from flask_login import current_user
from sqlalchemy import select

from app.extensions import db
from app.models import Client, Deal, Property, Request, Viewing
from app.services.access import public_client


def get_contact_client(form, deal_type):
    person = public_client()
    if person:
        return person
    # Never identify a stranger using a supplied phone/email alone.
    person = Client(full_name=form.full_name.data.strip(), phone=form.phone.data.strip(),
                    email=form.email.data or None, client_type='tenant' if deal_type == 'rent' else 'buyer')
    db.session.add(person)
    db.session.flush()
    session['guest_client_id'] = person.id
    return person


def viewing_key(property_id, client_id, status):
    return f'{property_id}:{client_id}' if status in ('requested', 'confirmed') else None


def save_deal(deal, form, property):
    # Lock on MySQL; a unique completed_key also protects concurrent commits on SQLite.
    property = db.session.scalar(select(Property).where(Property.id == property.id).with_for_update())
    if form.deal_type.data != property.deal_type:
        raise ValueError('Тип сделки должен соответствовать объекту.')
    request_id = form.request_id.data or None
    if request_id:
        source = db.session.get(Request, request_id)
        if not source or source.client_id != form.client_id.data or (current_user.role != 'ADMIN' and source.agent_id != current_user.agent.id):
            raise ValueError('Заявка недоступна или принадлежит другому клиенту.')
        linked = db.session.scalar(select(Viewing.id).where(Viewing.request_id == request_id,
            Viewing.property_id == property.id, Viewing.client_id == form.client_id.data,
            Viewing.status == 'completed').limit(1))
        if not linked:
            raise ValueError('Для связи сделки с заявкой нужен проведенный показ этого объекта и клиента.')
    if deal.id and deal.status == 'completed':
        immutable = ('property_id', 'client_id', 'deal_type', 'amount', 'deal_date', 'status')
        if deal.request_id != request_id:
            raise ValueError('Связь заключенной сделки с заявкой не изменяется.')
        if any(getattr(deal, name) != getattr(form, name).data for name in immutable):
            raise ValueError('Заключенная сделка сохраняется как история и не может быть изменена.')
        return
    if form.status.data == 'completed':
        existing = db.session.scalar(select(Deal).where(Deal.completed_key == property.id, Deal.id != (deal.id or 0)))
        if existing or property.status in ('sold', 'rented', 'archived'):
            raise ValueError('Для этого объекта нельзя заключить еще одну сделку.')
        property.status = 'sold' if form.deal_type.data == 'sale' else 'rented'
        deal.completed_key = property.id
        for viewing in property.viewings:
            if viewing.status in ('requested', 'confirmed'):
                viewing.status = 'cancelled'
                viewing.active_key = None
    for name in ('property_id', 'client_id', 'deal_type', 'amount', 'deal_date', 'status'):
        setattr(deal, name, getattr(form, name).data)
    deal.request_id = request_id

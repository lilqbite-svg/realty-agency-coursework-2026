from functools import wraps
from urllib.parse import urlsplit

from flask import abort, session
from flask_login import current_user, login_required
from sqlalchemy import or_, select

from app.extensions import db
from app.models import Client, Deal, Property, Request, Viewing


def roles_required(*roles):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if not current_user.is_active or current_user.role not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def safe_next(value):
    if not value or '\\' in value or any(ord(c) < 32 for c in value):
        return None
    parsed = urlsplit(value)
    return value if value.startswith('/') and not value.startswith('//') and not parsed.netloc and not parsed.scheme else None


def own_record(record):
    if current_user.role != 'ADMIN' and record.agent_id != current_user.agent.id:
        abort(403)
    return record


def scoped(model, include_unassigned=False):
    query = select(model)
    if current_user.role != 'ADMIN':
        condition = model.agent_id == current_user.agent.id
        if include_unassigned:
            condition = or_(condition, model.agent_id.is_(None))
        query = query.where(condition)
    return query


def clients_query():
    query = select(Client)
    if current_user.role == 'ADMIN':
        return query
    agent_id = current_user.agent.id
    return query.where(or_(
        Client.created_by_agent_id == agent_id,
        Client.properties.any(Property.agent_id == agent_id),
        Client.requests.any(Request.agent_id == agent_id),
        Client.viewings.any(Viewing.agent_id == agent_id),
        Client.deals.any(Deal.agent_id == agent_id),
    ))


def allowed_client(client_id):
    person = db.session.scalar(clients_query().where(Client.id == client_id))
    if not person:
        abort(403)
    return person


def public_client():
    if current_user.is_authenticated:
        return current_user.client if current_user.role == 'CLIENT' else None
    return db.session.get(Client, session.get('guest_client_id')) if session.get('guest_client_id') else None

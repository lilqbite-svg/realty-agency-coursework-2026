from datetime import datetime
import builtins

from flask_login import UserMixin
from sqlalchemy import CheckConstraint, UniqueConstraint
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db


def now():
    return datetime.now()  # Local agency time; see README.


class TimestampMixin:
    created_at = db.Column(db.DateTime, nullable=False, default=now)
    updated_at = db.Column(db.DateTime, nullable=False, default=now, onupdate=now)


class User(UserMixin, TimestampMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='CLIENT')
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    agent = db.relationship('Agent', back_populates='account', uselist=False)
    client = db.relationship('Client', back_populates='account', uselist=False)
    favorites = db.relationship('Favorite', back_populates='user', cascade='all, delete-orphan')
    __table_args__ = (CheckConstraint("role IN ('CLIENT','REALTOR','ADMIN')", name='role'),)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def full_name(self):
        person = self.agent or self.client
        return person.full_name if person else self.email


class Client(TimestampMixin, db.Model):
    __tablename__ = 'clients'
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(150), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(120), nullable=True)
    client_type = db.Column(db.String(30), nullable=False, default='buyer')
    account_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='RESTRICT'), unique=True)
    created_by_agent_id = db.Column(db.Integer, db.ForeignKey('agents.id', ondelete='RESTRICT'))
    account = db.relationship('User', back_populates='client')
    created_by = db.relationship('Agent', back_populates='created_clients', foreign_keys=[created_by_agent_id])
    properties = db.relationship('Property', back_populates='owner')
    requests = db.relationship('Request', back_populates='client')
    viewings = db.relationship('Viewing', back_populates='client')
    deals = db.relationship('Deal', back_populates='client')
    __table_args__ = (CheckConstraint("client_type IN ('buyer','seller','tenant','landlord')", name='type'),)


class Agent(TimestampMixin, db.Model):
    __tablename__ = 'agents'
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(150), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    position = db.Column(db.String(80), nullable=False, default='Риэлтор')
    account_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False, unique=True)
    account = db.relationship('User', back_populates='agent')
    properties = db.relationship('Property', back_populates='agent')
    requests = db.relationship('Request', back_populates='agent')
    viewings = db.relationship('Viewing', back_populates='agent')
    deals = db.relationship('Deal', back_populates='agent')
    created_clients = db.relationship('Client', back_populates='created_by', foreign_keys='Client.created_by_agent_id')


class Property(TimestampMixin, db.Model):
    __tablename__ = 'properties'
    matches = db.relationship('RequestMatch', back_populates='property')
    id = db.Column(db.Integer, primary_key=True)
    demo_key = db.Column(db.String(40), unique=True)
    title = db.Column(db.String(180), nullable=False)
    owner_id = db.Column(db.Integer, db.ForeignKey('clients.id', ondelete='RESTRICT'))
    agent_id = db.Column(db.Integer, db.ForeignKey('agents.id', ondelete='RESTRICT'), nullable=False, index=True)
    property_type = db.Column(db.String(50), nullable=False)
    deal_type = db.Column(db.String(20), nullable=False)
    address = db.Column(db.String(255), nullable=False)
    district = db.Column(db.String(120), nullable=False)
    area = db.Column(db.Numeric(8, 2), nullable=False)
    rooms = db.Column(db.Integer, nullable=False, default=0)
    floor = db.Column(db.Integer, nullable=False, default=0)
    total_floors = db.Column(db.Integer, nullable=False, default=0)
    price = db.Column(db.Numeric(14, 2), nullable=False)
    description = db.Column(db.Text, nullable=False, default='')
    status = db.Column(db.String(30), nullable=False, default='available', index=True)
    visual_style = db.Column(db.Integer, nullable=False, default=1)
    owner = db.relationship('Client', back_populates='properties')
    agent = db.relationship('Agent', back_populates='properties')
    photos = db.relationship('PropertyPhoto', back_populates='property', cascade='all, delete-orphan', order_by='PropertyPhoto.sort_order')
    viewings = db.relationship('Viewing', back_populates='property')
    deals = db.relationship('Deal', back_populates='property', foreign_keys='Deal.property_id')
    favorites = db.relationship('Favorite', back_populates='property', cascade='all, delete-orphan')
    __table_args__ = (
        CheckConstraint('price > 0 AND area > 0', name='positive'),
        CheckConstraint('rooms >= 0 AND floor >= 0 AND total_floors >= floor', name='floors'),
        CheckConstraint("property_type IN ('flat','house','land','commercial')", name='type'),
        CheckConstraint("deal_type IN ('sale','rent')", name='deal_type'),
        CheckConstraint("status IN ('available','reserved','sold','rented','archived')", name='status'),
    )

    @property
    def main_photo(self):
        return next((p for p in self.photos if p.is_main), self.photos[0] if self.photos else None)


class PropertyPhoto(db.Model):
    __tablename__ = 'property_photos'
    id = db.Column(db.Integer, primary_key=True)
    property_id = db.Column(db.Integer, db.ForeignKey('properties.id', ondelete='CASCADE'), nullable=False)
    image_url = db.Column(db.String(255), nullable=False)
    is_main = db.Column(db.Boolean, nullable=False, default=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=now)
    property = db.relationship('Property', back_populates='photos')


class Request(TimestampMixin, db.Model):
    __tablename__ = 'requests'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id', ondelete='RESTRICT'), nullable=False)
    agent_id = db.Column(db.Integer, db.ForeignKey('agents.id', ondelete='RESTRICT'), index=True)
    deal_type = db.Column(db.String(20), nullable=False)
    property_type = db.Column(db.String(50), nullable=False)
    min_price = db.Column(db.Numeric(14, 2))
    max_price = db.Column(db.Numeric(14, 2))
    rooms = db.Column(db.Integer)
    preferred_area = db.Column(db.String(120))
    message = db.Column(db.Text, nullable=False, default='')
    internal_comment = db.Column(db.Text, nullable=False, default='')
    status = db.Column(db.String(30), nullable=False, default='new', index=True)
    client = db.relationship('Client', back_populates='requests')
    agent = db.relationship('Agent', back_populates='requests')
    matches = db.relationship('RequestMatch', back_populates='request')
    viewings = db.relationship('Viewing', back_populates='request')
    deals = db.relationship('Deal', back_populates='request')
    __table_args__ = (
        CheckConstraint("status IN ('new','in_progress','completed','cancelled')", name='status'),
        CheckConstraint("deal_type IN ('sale','rent')", name='deal_type'),
        CheckConstraint("property_type IN ('flat','house','land','commercial')", name='type'),
        CheckConstraint('(min_price IS NULL OR min_price >= 0) AND (max_price IS NULL OR max_price >= 0) AND (min_price IS NULL OR max_price IS NULL OR max_price >= min_price)', name='budget'),
        CheckConstraint('rooms IS NULL OR rooms >= 0', name='rooms'),
    )


class Viewing(TimestampMixin, db.Model):
    __tablename__ = 'viewings'
    id = db.Column(db.Integer, primary_key=True)
    property_id = db.Column(db.Integer, db.ForeignKey('properties.id', ondelete='RESTRICT'), nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id', ondelete='RESTRICT'), nullable=False)
    agent_id = db.Column(db.Integer, db.ForeignKey('agents.id', ondelete='RESTRICT'), nullable=False, index=True)
    viewing_date = db.Column(db.DateTime)
    request_id = db.Column(db.Integer, db.ForeignKey('requests.id', ondelete='RESTRICT'), index=True)
    duration_minutes = db.Column(db.Integer, nullable=False, default=60)
    schedule_version = db.Column(db.Integer, nullable=False, default=1)
    result = db.Column(db.String(100), nullable=False, default='')
    comment = db.Column(db.Text, nullable=False, default='')
    client_message = db.Column(db.Text, nullable=False, default='')
    status = db.Column(db.String(30), nullable=False, default='requested')
    active_key = db.Column(db.String(80), unique=True)  # NULL for finished/cancelled viewings.
    property = db.relationship('Property', back_populates='viewings')
    client = db.relationship('Client', back_populates='viewings')
    agent = db.relationship('Agent', back_populates='viewings')
    request = db.relationship('Request', back_populates='viewings')

    @builtins.property
    def local_viewing_date(self):
        from datetime import timedelta
        return self.viewing_date + timedelta(hours=3) if self.viewing_date else None
    __table_args__ = (
        CheckConstraint("status IN ('requested','confirmed','completed','cancelled')", name='status'),
        CheckConstraint("status = 'requested' OR status = 'cancelled' OR viewing_date IS NOT NULL", name='date_required'),
        CheckConstraint('duration_minutes >= 15 AND duration_minutes <= 480', name='duration'),
        CheckConstraint('schedule_version >= 1', name='version'),
    )


class Deal(TimestampMixin, db.Model):
    __tablename__ = 'deals'
    id = db.Column(db.Integer, primary_key=True)
    property_id = db.Column(db.Integer, db.ForeignKey('properties.id', ondelete='RESTRICT'), nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id', ondelete='RESTRICT'), nullable=False)
    agent_id = db.Column(db.Integer, db.ForeignKey('agents.id', ondelete='RESTRICT'), nullable=False, index=True)
    deal_type = db.Column(db.String(20), nullable=False)
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    deal_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(30), nullable=False, default='preparation')
    completed_key = db.Column(db.Integer, db.ForeignKey('properties.id', ondelete='RESTRICT'), unique=True)
    request_id = db.Column(db.Integer, db.ForeignKey('requests.id', ondelete='RESTRICT'), index=True)
    property = db.relationship('Property', back_populates='deals', foreign_keys=[property_id])
    client = db.relationship('Client', back_populates='deals')
    agent = db.relationship('Agent', back_populates='deals')
    request = db.relationship('Request', back_populates='deals')
    __table_args__ = (
        CheckConstraint('amount > 0', name='amount'),
        CheckConstraint("deal_type IN ('sale','rent')", name='deal_type'),
        CheckConstraint("status IN ('preparation','completed','cancelled')", name='status'),
    )


class Favorite(db.Model):
    __tablename__ = 'favorites'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    property_id = db.Column(db.Integer, db.ForeignKey('properties.id', ondelete='CASCADE'), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=now)
    user = db.relationship('User', back_populates='favorites')
    property = db.relationship('Property', back_populates='favorites')
    __table_args__ = (UniqueConstraint('user_id', 'property_id'),)


class RequestMatch(TimestampMixin, db.Model):
    __tablename__ = 'request_matches'
    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey('requests.id', ondelete='RESTRICT'), nullable=False, index=True)
    property_id = db.Column(db.Integer, db.ForeignKey('properties.id', ondelete='RESTRICT'), nullable=False, index=True)
    score = db.Column(db.Numeric(5, 2), nullable=False)
    algorithm_version = db.Column(db.String(30), nullable=False)
    status = db.Column(db.String(20), nullable=False, default='saved')
    agent_comment = db.Column(db.Text, nullable=False, default='')
    snapshot_json = db.Column(db.Text, nullable=False, default='{}')
    history_json = db.Column(db.Text, nullable=False, default='[]')
    request = db.relationship('Request', back_populates='matches')
    property = db.relationship('Property', back_populates='matches')
    __table_args__ = (
        UniqueConstraint('request_id', 'property_id'),
        CheckConstraint('score >= 0 AND score <= 100', name='score'),
        CheckConstraint("status IN ('saved','rejected','viewing')", name='status'),
    )

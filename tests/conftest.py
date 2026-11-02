import pytest

from app import create_app
from app.cli import seed_demo
from app.extensions import db
from app.models import Agent, Client, Property, User
from config import TestConfig


@pytest.fixture
def app(tmp_path):
    class Testing(TestConfig):
        UPLOAD_FOLDER = tmp_path / 'uploads'
    application = create_app(Testing)
    with application.app_context():
        db.create_all()
        seed_demo()
        second = User(email='second@example.org', role='REALTOR')
        second.set_password('SecondPass123!')
        agent = Agent(account=second, full_name='Другой сотрудник', phone='+7 999 000-00-01', email=second.email)
        other = User(email='other@example.org', role='CLIENT')
        other.set_password('OtherPass123!')
        client = Client(account=other, full_name='Другой клиент', phone='+7 999 000-00-02', email=other.email, client_type='tenant')
        db.session.add_all([agent, client])
        db.session.flush()
        db.session.add(Property(title='Чужой объект', agent=agent, property_type='land', deal_type='sale', address='Тестовый участок', district='Другой район', area=800, rooms=0, floor=0, total_floors=0, price=1000000))
        db.session.commit()
    yield application
    with application.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def login(client):
    def do_login(role='realtor', **kwargs):
        return client.post('/login', data={'email': f'{role}@example.local', 'password': 'DemoPass123!', **kwargs})
    return do_login


@pytest.fixture
def contact():
    return {'full_name': 'Тестовый посетитель', 'phone': '+7 999 123-45-67', 'email': 'visitor@example.org'}


@pytest.fixture
def property_data(app):
    with app.app_context():
        agent_id = db.session.query(Agent).filter_by(email='realtor@example.local').one().id
    return {'title': 'Новая квартира', 'property_type': 'flat', 'deal_type': 'sale', 'owner_id': '0',
            'agent_id': str(agent_id), 'address': 'Москва, Тестовая улица, 1', 'district': 'Тестовый район',
            'area': '65', 'rooms': '2', 'floor': '3', 'total_floors': '9', 'price': '21000000',
            'description': 'Описание новой квартиры', 'status': 'available'}

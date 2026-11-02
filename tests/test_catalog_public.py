import re
from datetime import datetime

import pytest
from sqlalchemy import func, select

from app.extensions import db
from app.models import Client, Favorite, Property, Request, Viewing


def card_ids(response):
    return [int(x) for x in re.findall(r'data-property-id="(\d+)"', response.get_data(as_text=True))]


def test_home_and_catalog_use_database(app, client):
    assert card_ids(client.get('/')) == [1, 2, 3]
    assert len(card_ids(client.get('/catalog'))) == 7
    with app.app_context():
        db.session.get(Property, 1).title = 'Данные из БД'
        db.session.commit()
    assert 'Данные из БД' in client.get('/').get_data(as_text=True)
    assert client.get('/property/999').status_code == 404


@pytest.mark.parametrize('query,expected', [
    ('type=flat&deal=sale&rooms=2', {1, 5}),
    ('district=Хамовники&max_price=20000000', {1}),
    ('min_area=60&max_area=80', {1, 5}),
    ('q=Арбат', {3}), ('type=land', {7}), ('type=commercial&rooms=0', {6}),
    ('rooms=4', {4}), ('q=%25', set()),
])
def test_filters(client, query, expected):
    response = client.get('/catalog?' + query)
    assert response.status_code == 200
    assert set(card_ids(response)) == expected


@pytest.mark.parametrize('query', ['type=office', 'deal=bogus', 'rooms=-1', 'max_price=oops', 'min_area=-2', 'max_price=NaN', 'sort=drop', 'page=-1', 'page=abc', 'min_price=100&max_price=50', 'min_area=100&max_area=10'])
def test_invalid_filters(client, query):
    assert client.get('/catalog?' + query).status_code == 400


def test_sorting(client):
    assert card_ids(client.get('/catalog?sort=price_asc')) == [2, 6, 7, 1, 5, 4, 3]
    assert card_ids(client.get('/catalog?sort=area_desc')) == [7, 4, 6, 3, 5, 1, 2]


def test_pagination_preserves_filters(app, client):
    with app.app_context():
        original = db.session.get(Property, 1)
        for number in range(13):
            db.session.add(Property(title=f'Еще квартира {number}', agent_id=original.agent_id, property_type='flat', deal_type='sale', address='Москва', district='Хамовники', area=60, price=19000000, rooms=2, floor=1, total_floors=9))
        db.session.commit()
    response = client.get('/catalog?type=flat&district=Хамовники&sort=price_asc')
    assert len(card_ids(response)) == 12
    assert 'page=2' in response.get_data(as_text=True) and 'type=flat' in response.get_data(as_text=True)
    assert len(card_ids(client.get('/catalog?type=flat&district=Хамовники&sort=price_asc&page=2'))) == 2


def test_guest_request_privacy_and_contact_spoofing(app, client, contact):
    response = client.post('/request', data={**contact, 'deal_type': 'sale', 'property_type': 'flat', 'rooms': '2', 'max_price': '22000000', 'preferred_area': 'Хамовники', 'message': '<script>alert(1)</script>'})
    assert response.status_code == 302 and response.location == '/my-requests'
    with app.app_context():
        record = db.session.scalar(select(Request).order_by(Request.id.desc()))
        record_id = record.id
        client_id = record.client_id
        assert record.client.account_id is None
    assert client.get(f'/requests/{record_id}').status_code == 200
    assert '&lt;script&gt;' in client.get('/my-requests').get_data(as_text=True)
    stranger = app.test_client()
    assert stranger.get(f'/requests/{record_id}').status_code == 403
    # Same phone/email do not grant access to the existing visitor.
    stranger.post('/request', data={**contact, 'deal_type': 'rent', 'property_type': 'house'})
    with app.app_context():
        assert db.session.scalar(select(Request).order_by(Request.id.desc())).client_id != client_id
    assert stranger.get(f'/requests/{record_id}').status_code == 403


def test_guest_viewing_duplicate_and_status(app, client, contact):
    for _ in range(2):
        assert client.post('/property/1', data=contact).status_code == 302
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(Viewing)) == 1
        viewing = db.session.scalar(select(Viewing))
        assert viewing.status == 'requested' and viewing.agent_id == viewing.property.agent_id
        property = db.session.get(Property, 1)
        property.status = 'sold'
        db.session.commit()
    assert 'недоступен' in client.get('/property/1').get_data(as_text=True)
    stranger = app.test_client()
    stranger.post('/property/1', data=contact)
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(Viewing)) == 1


def test_client_request_and_foreign_request(client, login, contact):
    login('client')
    assert client.post('/request', data={**contact, 'deal_type': 'sale', 'property_type': 'flat'}).status_code == 302
    assert client.get('/requests/1').status_code == 200
    client.post('/logout')
    client.post('/login', data={'email': 'other@example.org', 'password': 'OtherPass123!'})
    assert client.get('/requests/1').status_code == 403


def test_favorites_add_remove(app, client, login):
    assert client.post('/favorites/1/toggle').status_code == 401
    login('client')
    assert client.post('/favorites/1/toggle').json == {'favorite': True}
    assert card_ids(client.get('/favorites')) == [1]
    assert client.post('/favorites/1/toggle').json == {'favorite': False}
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(Favorite)) == 0

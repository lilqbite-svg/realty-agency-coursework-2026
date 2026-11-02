from datetime import datetime
from io import BytesIO

import pytest
from PIL import Image
from sqlalchemy import func, select

from app.extensions import db
from app.models import Agent, Client, Deal, Property, PropertyPhoto, Request, Viewing


def image_file(color='blue'):
    buffer = BytesIO()
    Image.new('RGB', (40, 30), color).save(buffer, 'PNG')
    buffer.seek(0)
    return buffer


def test_property_create_edit_archive(app, client, login, property_data):
    login()
    assert client.post('/dashboard/properties/new', data=property_data).status_code == 302
    with app.app_context():
        property_id = db.session.scalar(select(Property).where(Property.title == property_data['title'])).id
    assert f'data-property-id="{property_id}"' in client.get('/catalog?q=Тестовая').get_data(as_text=True)
    assert client.post(f'/dashboard/properties/{property_id}/edit', data={**property_data, 'price': '23000000', 'title': 'Обновленная квартира'}).status_code == 302
    assert '23 000 000' in client.get(f'/property/{property_id}').get_data(as_text=True)
    assert client.get(f'/dashboard/properties/{property_id}/delete').status_code == 405
    assert client.post(f'/dashboard/properties/{property_id}/delete').status_code == 302
    assert client.get(f'/property/{property_id}').status_code == 404
    with app.app_context():
        assert db.session.get(Property, property_id).status == 'archived'


@pytest.mark.parametrize('changed', [{'price': '0'}, {'area': '-1'}, {'rooms': '-1'}, {'floor': '12', 'total_floors': '9'}, {'property_type': 'invalid'}, {'address': ''}, {'agent_id': '3'}])
def test_property_validation_and_forged_assignment(app, client, login, property_data, changed):
    login()
    assert client.post('/dashboard/properties/new', data={**property_data, **changed}).status_code == 200
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(Property)) == 7


def test_real_client_crud_search_and_scope(app, client, login, contact):
    login()
    assert client.post('/dashboard/clients/new', data={**contact, 'client_type': 'seller'}).status_code == 302
    with app.app_context():
        person = db.session.scalar(select(Client).where(Client.email == contact['email']))
        client_id = person.id
        assert person.client_type == 'seller' and person.created_by_agent_id
    assert 'Тестовый посетитель' in client.get('/dashboard/clients?q=visitor').get_data(as_text=True)
    assert client.post(f'/dashboard/clients/{client_id}/edit', data={**contact, 'full_name': 'Измененный клиент', 'client_type': 'landlord'}).status_code == 302
    assert 'Измененный клиент' in client.get(f'/dashboard/clients/{client_id}').get_data(as_text=True)
    client.post('/logout')
    client.post('/login', data={'email': 'second@example.org', 'password': 'SecondPass123!'})
    assert client.get(f'/dashboard/clients/{client_id}').status_code == 403


def test_guest_to_employee_pipeline(app, client, login, contact):
    # Public P0 functionality is explicitly available without registration.
    client.post('/request', data={**contact, 'deal_type': 'sale', 'property_type': 'flat', 'max_price': '22000000', 'rooms': '2', 'preferred_area': 'Хамовники'})
    client.post('/property/1', data={**contact, 'message': 'Удобно вечером'})
    with app.app_context():
        record = db.session.scalar(select(Request).order_by(Request.id.desc()))
        request_id, client_id = record.id, record.client_id
        viewing_id = db.session.scalar(select(Viewing)).id
    login()
    assert 'Удобно вечером' in client.get(f'/dashboard/viewings/{viewing_id}/edit').get_data(as_text=True)
    assert client.post(f'/dashboard/requests/{request_id}/edit', data={'status': 'completed'}).status_code == 403
    assert client.post(f'/dashboard/requests/{request_id}/claim').status_code == 302
    assert 'Подобрать объекты по заявке' in client.get(f'/dashboard/requests/{request_id}/edit').get_data(as_text=True)
    assert client.post(f'/dashboard/requests/{request_id}/edit', data={'status': 'in_progress', 'internal_comment': 'Связались с клиентом'}).status_code == 302
    assert client.post(f'/dashboard/viewings/{viewing_id}/edit', data={'property_id': '1', 'client_id': str(client_id), 'status': 'confirmed', 'viewing_date': '2026-10-01T17:30', 'comment': 'Встреча у дома', 'result': ''}).status_code == 302
    assert client.post(f'/dashboard/viewings/{viewing_id}/edit', data={'property_id': '1', 'client_id': str(client_id), 'status': 'completed', 'viewing_date': '2026-10-01T17:30', 'comment': 'Встреча у дома', 'result': 'Клиент согласен'}).status_code == 302
    assert client.post('/dashboard/deals/new', data={'property_id': '1', 'client_id': str(client_id), 'deal_type': 'sale', 'amount': '18500000', 'deal_date': datetime.now().date().isoformat(), 'status': 'completed'}).status_code == 302
    assert 'Сделки за месяц' in client.get('/dashboard').get_data(as_text=True)
    with app.app_context():
        deal = db.session.scalar(select(Deal))
        assert deal.amount == 18500000 and deal.client_id == client_id
        assert db.session.get(Property, 1).status == 'sold'
        db.session.get(Property, 1).price = 19500000
        db.session.commit()
        assert deal.amount == 18500000


def test_deal_preparation_edit_and_completion(app, client, login):
    login()
    with app.app_context():
        person_id = db.session.scalar(select(Client).where(Client.email == 'client@example.local')).id
    data = {'property_id': '2', 'client_id': str(person_id), 'deal_type': 'rent', 'amount': '115000', 'deal_date': '2026-09-28', 'status': 'preparation'}
    assert client.post('/dashboard/deals/new', data=data).status_code == 302
    with app.app_context():
        deal_id = db.session.scalar(select(Deal)).id
    assert client.post(f'/dashboard/deals/{deal_id}/edit', data={**data, 'amount': '117000', 'status': 'completed'}).status_code == 302
    with app.app_context():
        assert db.session.get(Property, 2).status == 'rented'
    response = client.post(f'/dashboard/deals/{deal_id}/edit', data={**data, 'status': 'cancelled'})
    assert 'не может быть изменена' in response.get_data(as_text=True)
    assert client.post('/dashboard/deals/new', data={**data, 'status': 'completed'}).status_code == 200


def test_photo_batch_main_delete_and_uuid(app, client, login, property_data):
    login()
    data = {**property_data, 'photos': [(image_file(), '../../unsafe.png'), (image_file('red'), 'second.png')]}
    assert client.post('/dashboard/properties/new', data=data, content_type='multipart/form-data').status_code == 302
    with app.app_context():
        property = db.session.scalar(select(Property).where(Property.title == property_data['title']))
        property_id = property.id
        photos = property.photos
        assert len(photos) == 2 and sum(p.is_main for p in photos) == 1
        first_id, second_id = photos[0].id, photos[1].id
        first_name = photos[0].image_url.rsplit('/', 1)[-1]
        assert len(first_name) == 36 and first_name.endswith('.jpg')
        assert (app.config['UPLOAD_FOLDER'] / first_name).exists()
    assert client.post(f'/dashboard/photos/{second_id}/main').status_code == 302
    with app.app_context():
        assert db.session.get(PropertyPhoto, second_id).is_main
        assert not db.session.get(PropertyPhoto, first_id).is_main
    assert client.post(f'/dashboard/photos/{first_id}/delete').status_code == 302
    with app.app_context():
        assert db.session.get(PropertyPhoto, first_id) is None
        assert not (app.config['UPLOAD_FOLDER'] / first_name).exists()
    assert client.get(f'/property/{property_id}').status_code == 200


@pytest.mark.parametrize('filename,content', [('script.svg', b'<svg/>'), ('fake.png', b'not a picture'), ('photo.jpg', None), ('large.png', b'X' * (5 * 1024 * 1024 + 1))], ids=['svg', 'fake', 'extension_mismatch', 'oversize'])
def test_upload_rejection_is_atomic(app, client, login, property_data, filename, content):
    login()
    invalid = image_file() if content is None else BytesIO(content)
    response = client.post('/dashboard/properties/new', data={**property_data, 'photos': [(image_file(), 'valid.png'), (invalid, filename)]}, content_type='multipart/form-data')
    assert response.status_code == 200
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(Property)) == 7
        assert db.session.scalar(select(func.count()).select_from(PropertyPhoto)) == 0
        assert not list(app.config['UPLOAD_FOLDER'].glob('*'))


def test_viewing_validation_duplicate_and_archive_history(app, client, login, contact):
    client.post('/property/1', data=contact)
    with app.app_context():
        viewing = db.session.scalar(select(Viewing))
        viewing_id, client_id = viewing.id, viewing.client_id
    login()
    data = {'property_id': '1', 'client_id': str(client_id), 'status': 'confirmed', 'viewing_date': ''}
    assert client.post(f'/dashboard/viewings/{viewing_id}/edit', data=data).status_code == 200
    data.update(viewing_date='2026-10-01T12:00')
    assert client.post(f'/dashboard/viewings/{viewing_id}/edit', data=data).status_code == 302
    assert client.post('/dashboard/viewings/new', data=data).status_code == 200
    assert client.post('/dashboard/properties/1/delete').status_code == 302
    with app.app_context():
        assert db.session.get(Viewing, viewing_id).status == 'cancelled'
        assert db.session.get(Viewing, viewing_id).active_key is None
        assert db.session.get(Property, 1)

from sqlalchemy import select

from app.extensions import db
from app.models import User


def test_registration_is_client_only(app, client, contact):
    response = client.post('/register', data={**contact, 'email': ' NEW@EXAMPLE.ORG ', 'password': 'Password123!', 'confirm': 'Password123!', 'role': 'ADMIN'})
    assert response.status_code == 302
    with app.app_context():
        user = db.session.scalar(select(User).where(User.email == 'new@example.org'))
        assert user.role == 'CLIENT' and user.client and not user.agent
        assert user.password_hash != 'Password123!' and user.check_password('Password123!')
    assert client.get('/dashboard').status_code == 403


def test_register_validates_duplicate_and_password(client, contact):
    response = client.post('/register', data={**contact, 'email': 'admin@example.local', 'password': 'Password123!', 'confirm': 'Password123!'})
    assert 'уже зарегистрирован' in response.get_data(as_text=True)
    response = client.post('/register', data={**contact, 'password': 'short', 'confirm': 'other'})
    assert response.status_code == 200 and client.get('/dashboard').status_code == 302


def test_login_wrong_password_session_logout(client, login):
    assert login(password='wrong').status_code == 200
    assert client.get('/dashboard').status_code == 302
    assert login().status_code == 302
    assert client.get('/dashboard').status_code == 200
    assert client.get('/logout').status_code == 405
    assert client.post('/logout').status_code == 302
    assert client.get('/dashboard').status_code == 302


def test_role_restrictions(client, login):
    login('client')
    assert client.get('/dashboard').status_code == 403
    assert client.get('/admin').status_code == 403
    client.post('/logout')
    login()
    assert client.get('/admin').status_code == 403
    assert client.post('/admin/users/1/toggle').status_code == 403
    assert client.get('/dashboard/properties/7/edit').status_code == 403
    assert client.post('/dashboard/properties/7/delete').status_code == 403
    assert client.get('/dashboard/clients/2').status_code == 403


def test_safe_next(client):
    for target in ['https://evil.example/', '//evil.example/', '/\\evil.example/', '/\n/evil.example/']:
        response = client.post('/login', query_string={'next': target}, data={'email': 'realtor@example.local', 'password': 'DemoPass123!'})
        assert response.location == '/dashboard'
        client.post('/logout')
    response = client.post('/login?next=/property/1', data={'email': 'client@example.local', 'password': 'DemoPass123!'})
    assert response.location == '/property/1'


def test_blocking_revokes_existing_session(app, client, login):
    login()
    with app.app_context():
        user = db.session.scalar(select(User).where(User.role == 'REALTOR', User.email == 'realtor@example.local'))
        user.is_active = False
        db.session.commit()
    assert client.get('/dashboard').status_code == 403
    assert client.get('/dashboard').status_code == 302
    assert login().status_code == 200


def test_profile_and_password(client, login):
    login('client')
    response = client.post('/profile', data={'full_name': 'Новое имя', 'phone': '+7 999 123-45-00', 'email': 'newmail@example.org'})
    assert response.status_code == 302
    assert 'Новое имя' in client.get('/profile').get_data(as_text=True)
    assert client.post('/profile/password', data={'current_password': 'wrong', 'password': 'NewPass123!', 'confirm': 'NewPass123!'}).status_code == 200
    assert client.post('/profile/password', data={'current_password': 'DemoPass123!', 'password': 'NewPass123!', 'confirm': 'NewPass123!'}).status_code == 302
    client.post('/logout')
    assert client.post('/login', data={'email': 'newmail@example.org', 'password': 'NewPass123!'}).status_code == 302


def test_admin_employee_management(app, client, login):
    login('admin')
    data = {'full_name': 'Новый сотрудник', 'phone': '+7 999 123-45-00', 'email': 'staff@example.org', 'position': 'Риэлтор', 'role': 'REALTOR', 'password': 'StaffPass123!', 'is_active': 'y'}
    assert client.post('/admin/users/new', data=data).status_code == 302
    with app.app_context():
        user = db.session.scalar(select(User).where(User.email == 'staff@example.org'))
        user_id = user.id
        assert user.agent.account_id == user.id
    assert client.post(f'/admin/users/{user_id}/edit', data={**data, 'role': 'ADMIN', 'password': ''}).status_code == 302
    assert client.post(f'/admin/users/{user_id}/toggle').status_code == 302
    assert client.post('/admin/users/1/toggle').status_code == 403
    assert client.post(f'/admin/users/{user_id}/toggle').status_code == 302

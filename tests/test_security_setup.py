import re
from pathlib import Path

from flask_migrate import upgrade
from sqlalchemy import create_mock_engine, func, select

from app.cli import seed_demo
from app.extensions import db
from app.models import Property, Request, User
from config import Config


def test_csrf_protects_public_staff_and_ajax(app, client, login, contact):
    app.config['WTF_CSRF_ENABLED'] = True
    assert client.post('/request', data={**contact, 'deal_type': 'sale', 'property_type': 'flat'}).status_code == 400
    page = client.get('/login').get_data(as_text=True)
    token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page).group(1)
    assert client.post('/login', data={'email': 'client@example.local', 'password': 'DemoPass123!', 'csrf_token': token}).status_code == 302
    assert client.post('/favorites/1/toggle').status_code == 400
    assert client.post('/favorites/1/toggle', headers={'X-CSRFToken': token}).json == {'favorite': True}
    assert client.post('/logout').status_code == 400
    assert client.post('/logout', data={'csrf_token': token}).status_code == 302
    assert Config.WTF_CSRF_ENABLED is True


def test_seed_idempotent_and_production_guard(app):
    with app.app_context():
        seed_demo()
        seed_demo()
        assert db.session.scalar(select(func.count()).select_from(Property)) == 7
        assert db.session.scalar(select(func.count()).select_from(User)) == 5
        assert db.session.scalar(select(func.count()).select_from(Request)) == 1
    app.config['APP_ENV'] = 'production'
    app.config['TESTING'] = False
    result = app.test_cli_runner().invoke(args=['seed-demo'])
    assert result.exit_code != 0 and 'development' in result.output


def test_migration_fresh_database(tmp_path):
    from app import create_app
    from config import TestConfig
    class MigrationConfig(TestConfig):
        SQLALCHEMY_DATABASE_URI = 'sqlite:///' + (tmp_path / 'fresh.db').as_posix()
        UPLOAD_FOLDER = tmp_path / 'uploads'
    app = create_app(MigrationConfig)
    with app.app_context():
        upgrade(directory=str(Path(__file__).resolve().parents[1] / 'migrations'))
        seed_demo()
        assert db.session.scalar(select(func.count()).select_from(Property)) == 6


def test_mysql_schema_compiles(app):
    statements = []
    engine = create_mock_engine('mysql+pymysql://', lambda sql, *args, **kwargs: statements.append(str(sql.compile(dialect=engine.dialect))))
    with app.app_context():
        db.metadata.create_all(engine)
    assert len(statements) >= 9
    assert any('FOREIGN KEY' in sql and 'RESTRICT' in sql for sql in statements)

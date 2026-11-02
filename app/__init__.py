import sqlite3
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, url_for
from flask_login import current_user, logout_user
from flask_wtf.csrf import CSRFError
from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.extensions import csrf, db, login_manager, migrate
from config import Config


@event.listens_for(Engine, 'connect')
def sqlite_foreign_keys(connection, record):
    if isinstance(connection, sqlite3.Connection):
        connection.execute('PRAGMA foreign_keys=ON')


def create_app(config=Config):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config)
    if not app.config['SECRET_KEY'] or app.config['SECRET_KEY'] == 'replace-with-a-random-secret':
        raise RuntimeError('Задайте случайный SECRET_KEY в .env перед запуском.')
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    Path(app.config['UPLOAD_FOLDER']).mkdir(parents=True, exist_ok=True)
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.refresh_view = 'auth.login'
    login_manager.login_message = 'Войдите, чтобы открыть кабинет.'
    login_manager.login_message_category = 'info'
    migrate.init_app(app, db, render_as_batch=True)
    csrf.init_app(app)

    from app.models import User
    from app.routes import admin, auth, catalog, dashboard, main, matching, calendar, analytics
    from app.cli import register_commands
    for blueprint in (main.bp, auth.bp, catalog.bp, dashboard.bp, admin.bp, matching.bp, calendar.bp, analytics.bp):
        app.register_blueprint(blueprint)
    register_commands(app)

    @login_manager.user_loader
    def load_user(user_id):
        try:
            return db.session.get(User, int(user_id))
        except (ValueError, TypeError):
            return None

    @login_manager.unauthorized_handler
    def unauthorized():
        from flask import flash, redirect
        if request.endpoint == 'catalog.toggle':
            target = url_for('catalog.detail', property_id=request.view_args['property_id'])
            return jsonify(error='Войдите, чтобы изменить избранное.', login_url=url_for('auth.login', next=target)), 401
        flash(login_manager.login_message, 'info')
        return redirect(url_for('auth.login', next=request.full_path.rstrip('?')))

    @app.before_request
    def reject_blocked_account():
        if not current_user.is_anonymous and not current_user.is_active:
            logout_user()
            abort(403)

    @app.context_processor
    def template_helpers():
        from app.forms import (CLIENT_TYPES, DEALS, DEAL_STATUSES, PROPERTY_STATUSES,
                               REQUEST_STATUSES, TYPES, VIEWING_STATUSES)
        labels = dict(TYPES + DEALS + CLIENT_TYPES + PROPERTY_STATUSES + REQUEST_STATUSES)
        # Status wording depends on the entity; pass status_labels for lists.
        return {'labels': labels, 'viewing_labels': dict(VIEWING_STATUSES), 'deal_labels': dict(DEAL_STATUSES)}

    @app.template_filter('money')
    def money(value):
        return f'{value or 0:,.0f}'.replace(',', ' ')

    @app.template_filter('date')
    def date(value):
        return value.strftime('%d.%m.%Y %H:%M') if value else 'Согласуется'

    @app.template_filter('objects_count')
    def objects_count(value):
        ending = value % 100
        word = 'объектов' if 11 <= ending <= 14 else ('объект' if value % 10 == 1 else 'объекта' if 2 <= value % 10 <= 4 else 'объектов')
        return f'{value} {word}'

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        if request.path.startswith(('/dashboard', '/admin', '/profile', '/my-', '/requests/', '/favorites', '/login')):
            response.headers['Cache-Control'] = 'no-store'
        return response

    def render_error(error):
        code = getattr(error, 'code', 500)
        if code == 500:
            db.session.rollback()
        messages = {400: 'Проверьте данные формы и повторите отправку.', 403: 'Недостаточно прав для просмотра этой страницы.',
                    404: 'Эта страница или запись не найдена.', 405: 'Этот способ обращения не поддерживается.',
                    413: 'Загрузка превышает допустимый размер 8 МБ.', 500: 'Не удалось выполнить действие. Попробуйте позже.'}
        if request.path.endswith('/toggle'):
            return jsonify(error=messages.get(code, 'Ошибка запроса')), code
        return render_template('errors/error.html', title=f'Ошибка {code}', code=code, message=messages.get(code, 'Ошибка запроса')), code

    for code in (400, 403, 404, 405, 413, 500):
        app.register_error_handler(code, render_error)
    app.register_error_handler(CSRFError, render_error)
    return app

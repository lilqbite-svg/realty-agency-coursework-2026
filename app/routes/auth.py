from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, fresh_login_required, login_required, login_user, logout_user
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.forms import LoginForm, PasswordForm, ProfileForm, RegisterForm
from app.models import Client, User
from app.services.access import public_client, safe_next

bp = Blueprint('auth', __name__)


def destination():
    if current_user.role == 'ADMIN':
        return url_for('admin.index')
    if current_user.role == 'REALTOR':
        return url_for('dashboard.index')
    return url_for('auth.profile')


@bp.route('/login', methods=['GET', 'POST'])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        user = db.session.scalar(select(User).where(User.email == form.email.data))
        if user and user.is_active and user.check_password(form.password.data):
            session.pop('guest_client_id', None)
            login_user(user, remember=form.remember.data, fresh=True)
            flash('Вы успешно вошли в систему.', 'success')
            return redirect(safe_next(request.args.get('next')) or destination())
        flash('Неверный email или пароль, либо учетная запись заблокирована.', 'danger')
    return render_template('auth/form.html', title='Вход в систему', form=form, mode='login')


@bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(destination())
    form = RegisterForm()
    if form.validate_on_submit():
        if db.session.scalar(select(User).where(User.email == form.email.data)):
            form.email.errors.append('Этот email уже зарегистрирован.')
        else:
            user = User(email=form.email.data, role='CLIENT')
            user.set_password(form.password.data)
            person = public_client() or Client(client_type='buyer')
            person.full_name, person.phone, person.email = form.full_name.data.strip(), form.phone.data.strip(), form.email.data
            person.account = user
            db.session.add_all([user, person])
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                form.email.errors.append('Этот email уже зарегистрирован.')
            else:
                session.pop('guest_client_id', None)
                login_user(user)
                flash('Регистрация завершена.', 'success')
                return redirect(safe_next(request.args.get('next')) or url_for('auth.profile'))
    return render_template('auth/form.html', title='Регистрация клиента', form=form, mode='register')


@bp.post('/logout')
@login_required
def logout():
    logout_user()
    session.pop('guest_client_id', None)
    flash('Вы вышли из системы.', 'info')
    return redirect(url_for('main.index'))


@bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    person = current_user.client if current_user.role == 'CLIENT' else current_user.agent
    form = ProfileForm(obj=person)
    if request.method == 'GET':
        form.email.data = current_user.email
    if form.validate_on_submit():
        duplicate = db.session.scalar(select(User).where(User.email == form.email.data, User.id != current_user.id))
        if duplicate:
            form.email.errors.append('Этот email уже используется.')
        else:
            person.full_name, person.phone, person.email = form.full_name.data.strip(), form.phone.data.strip(), form.email.data
            current_user.email = form.email.data
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                form.email.errors.append('Этот email уже используется.')
            else:
                flash('Профиль сохранен.', 'success')
                return redirect(url_for('auth.profile'))
    return render_template('auth/form.html', title='Профиль', form=form, mode='profile')


@bp.route('/profile/password', methods=['GET', 'POST'])
@fresh_login_required
def password():
    form = PasswordForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            form.current_password.errors.append('Текущий пароль неверный.')
        else:
            current_user.set_password(form.password.data)
            db.session.commit()
            flash('Пароль изменен.', 'success')
            return redirect(url_for('auth.profile'))
    return render_template('auth/form.html', title='Изменение пароля', form=form)

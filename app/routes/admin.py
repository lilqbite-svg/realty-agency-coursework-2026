from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.forms import EmployeeForm
from app.models import Agent, User
from app.services.access import roles_required

bp = Blueprint('admin', __name__, url_prefix='/admin')
admin_only = roles_required('ADMIN')


@bp.get('')
@admin_only
def index():
    return render_template('dashboard/users.html', title='Учетные записи', users=db.session.scalars(select(User).order_by(User.id)).all())


def last_admin(user):
    return user.role == 'ADMIN' and user.is_active and db.session.scalar(select(func.count()).select_from(User).where(User.role == 'ADMIN', User.is_active.is_(True))) <= 1


@bp.route('/users/new', methods=['GET', 'POST'])
@bp.route('/users/<int:user_id>/edit', methods=['GET', 'POST'])
@admin_only
def employee_form(user_id=None):
    user = db.get_or_404(User, user_id) if user_id else User()
    if user_id and user.role == 'CLIENT':
        abort(403)
    person = user.agent or Agent(account=user)
    form = EmployeeForm(obj=person)
    if request.method == 'GET':
        form.role.data = user.role or 'REALTOR'
        form.is_active.data = user.is_active if user_id else True
        form.email.data = user.email
    if form.validate_on_submit():
        duplicate = db.session.scalar(select(User).where(User.email == form.email.data, User.id != (user.id or 0)))
        if duplicate:
            form.email.errors.append('Этот email уже используется.')
        elif not user_id and not form.password.data:
            form.password.errors.append('Задайте пароль нового сотрудника.')
        elif user_id and user.id == current_user.id and (not form.is_active.data or form.role.data != 'ADMIN'):
            form.role.errors.append('Нельзя блокировать себя или снять собственную роль администратора.')
        elif user_id and last_admin(user) and (not form.is_active.data or form.role.data != 'ADMIN'):
            form.role.errors.append('В системе должен оставаться активный администратор.')
        else:
            user.email, user.role, user.is_active = form.email.data, form.role.data, form.is_active.data
            if form.password.data:
                user.set_password(form.password.data)
            for name in ('full_name', 'phone', 'email', 'position'):
                setattr(person, name, getattr(form, name).data)
            db.session.add_all([user, person])
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                form.email.errors.append('Этот email уже используется.')
            else:
                flash('Учетная запись сотрудника сохранена.', 'success')
                return redirect(url_for('admin.index'))
    return render_template('dashboard/form.html', title='Сотрудник', form=form)


@bp.post('/users/<int:user_id>/toggle')
@admin_only
def toggle_user(user_id):
    user = db.get_or_404(User, user_id)
    if user.id == current_user.id or last_admin(user):
        abort(403)
    user.is_active = not user.is_active
    db.session.commit()
    flash('Учетная запись разблокирована.' if user.is_active else 'Учетная запись заблокирована.', 'success')
    return redirect(url_for('admin.index'))

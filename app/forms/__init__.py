import re

from flask_wtf import FlaskForm
from flask_wtf.file import MultipleFileField
from wtforms import (BooleanField, DateField, DateTimeLocalField, DecimalField,
                     HiddenField, IntegerField, PasswordField, SelectField, StringField,
                     SubmitField, TextAreaField)
from wtforms.validators import (DataRequired, Email, EqualTo, InputRequired,
                                Length, NumberRange, Optional, ValidationError)

TYPES = [('flat', 'Квартира'), ('house', 'Дом'), ('land', 'Участок'), ('commercial', 'Коммерческая')]
DEALS = [('sale', 'Продажа / покупка'), ('rent', 'Аренда')]
CLIENT_TYPES = [('buyer', 'Покупатель'), ('seller', 'Продавец'), ('tenant', 'Арендатор'), ('landlord', 'Арендодатель')]
PROPERTY_STATUSES = [('available', 'Доступен'), ('reserved', 'Забронирован'), ('sold', 'Продан'), ('rented', 'Сдан'), ('archived', 'В архиве')]
REQUEST_STATUSES = [('new', 'Новая'), ('in_progress', 'В работе'), ('completed', 'Выполнена'), ('cancelled', 'Отменена')]
VIEWING_STATUSES = [('requested', 'Ожидает согласования'), ('confirmed', 'Подтвержден'), ('completed', 'Проведен'), ('cancelled', 'Отменен')]
DEAL_STATUSES = [('preparation', 'Подготовка'), ('completed', 'Заключена'), ('cancelled', 'Отменена')]
REQUIRED = 'Заполните поле.'
POSITIVE = [InputRequired(message=REQUIRED), NumberRange(min=0.01, max=999999999999.99, message='Укажите положительное число.')]
def email_validator(form, field):
    # The explicitly requested local demo addresses are reserved for development.
    if re.fullmatch(r'[a-z0-9._+-]+@example\.local', field.data or ''):
        return
    Email(check_deliverability=False, message='Укажите корректный email.')(form, field)


EMAIL = email_validator


def normalize_email(value):
    return value.strip().lower() if value else value


def phone_validator(form, field):
    if not re.fullmatch(r'[+\d()\s-]{7,20}', field.data or '') or len(re.sub(r'\D', '', field.data)) < 7:
        raise ValidationError('Укажите телефон от 7 до 15 цифр.')
    if len(re.sub(r'\D', '', field.data)) > 15:
        raise ValidationError('Телефон слишком длинный.')


class ContactForm(FlaskForm):
    full_name = StringField('ФИО', validators=[DataRequired(message=REQUIRED), Length(min=2, max=150)])
    phone = StringField('Телефон', validators=[DataRequired(message=REQUIRED), phone_validator])
    email = StringField('Email (необязательно)', filters=[normalize_email], validators=[Optional(), EMAIL, Length(max=120)])


class LoginForm(FlaskForm):
    email = StringField('Email', filters=[normalize_email], validators=[DataRequired(message=REQUIRED), EMAIL])
    password = PasswordField('Пароль', validators=[DataRequired(message=REQUIRED)])
    remember = BooleanField('Запомнить меня')
    submit = SubmitField('Войти')


class RegisterForm(ContactForm):
    email = StringField('Email', filters=[normalize_email], validators=[DataRequired(message=REQUIRED), EMAIL, Length(max=120)])
    password = PasswordField('Пароль', validators=[DataRequired(message=REQUIRED), Length(min=8, max=128, message='От 8 до 128 символов.')])
    confirm = PasswordField('Повтор пароля', validators=[DataRequired(message=REQUIRED), EqualTo('password', message='Пароли не совпадают.')])
    submit = SubmitField('Зарегистрироваться')


class ProfileForm(ContactForm):
    email = StringField('Email', filters=[normalize_email], validators=[DataRequired(message=REQUIRED), EMAIL, Length(max=120)])
    submit = SubmitField('Сохранить профиль')


class PasswordForm(FlaskForm):
    current_password = PasswordField('Текущий пароль', validators=[DataRequired(message=REQUIRED)])
    password = PasswordField('Новый пароль', validators=[DataRequired(message=REQUIRED), Length(min=8, max=128)])
    confirm = PasswordField('Повтор пароля', validators=[EqualTo('password', message='Пароли не совпадают.')])
    submit = SubmitField('Изменить пароль')


class RequestForm(ContactForm):
    deal_type = SelectField('Тип сделки', choices=DEALS, validators=[DataRequired()])
    property_type = SelectField('Тип объекта', choices=TYPES, validators=[DataRequired()])
    min_price = DecimalField('Бюджет от, ₽', validators=[Optional(), NumberRange(min=0, max=999999999999.99)])
    max_price = DecimalField('Бюджет до, ₽', validators=[Optional(), NumberRange(min=0, max=999999999999.99)])
    rooms = IntegerField('Количество комнат', validators=[Optional(), NumberRange(min=0, max=100)])
    preferred_area = StringField('Район', validators=[Optional(), Length(max=120)])
    message = TextAreaField('Комментарий', validators=[Optional(), Length(max=5000)])
    submit = SubmitField('Отправить заявку')

    def validate_max_price(self, field):
        if field.data is not None and self.min_price.data is not None and field.data < self.min_price.data:
            raise ValidationError('Максимальный бюджет меньше минимального.')


class PublicViewingForm(ContactForm):
    message = TextAreaField('Когда удобно посмотреть объект?', validators=[Optional(), Length(max=5000)])
    submit = SubmitField('Записаться на просмотр')


class PropertyForm(FlaskForm):
    title = StringField('Название', validators=[DataRequired(message=REQUIRED), Length(max=180)])
    property_type = SelectField('Тип объекта', choices=TYPES)
    deal_type = SelectField('Тип сделки', choices=DEALS)
    owner_id = SelectField('Собственник', coerce=int, choices=[])
    agent_id = SelectField('Ответственный риэлтор', coerce=int, choices=[])
    address = StringField('Адрес', validators=[DataRequired(message=REQUIRED), Length(max=255)])
    district = StringField('Район', validators=[DataRequired(message=REQUIRED), Length(max=120)])
    area = DecimalField('Площадь, м²', validators=[InputRequired(message=REQUIRED), NumberRange(min=0.01, max=999999.99)])
    rooms = IntegerField('Комнаты', default=0, validators=[InputRequired(), NumberRange(min=0, max=100)])
    floor = IntegerField('Этаж', default=0, validators=[InputRequired(), NumberRange(min=0, max=200)])
    total_floors = IntegerField('Всего этажей', default=0, validators=[InputRequired(), NumberRange(min=0, max=200)])
    price = DecimalField('Цена, ₽', validators=POSITIVE)
    description = TextAreaField('Описание', validators=[Optional(), Length(max=10000)])
    status = SelectField('Статус', choices=PROPERTY_STATUSES)
    photos = MultipleFileField('Фотографии (JPEG, PNG, WebP, до 5 МБ каждая)')
    submit = SubmitField('Сохранить объект')

    def validate_total_floors(self, field):
        if field.data is not None and self.floor.data is not None and field.data < self.floor.data:
            raise ValidationError('Этажность не может быть меньше этажа.')


class ClientForm(ContactForm):
    client_type = SelectField('Тип клиента', choices=CLIENT_TYPES)
    submit = SubmitField('Сохранить клиента')


class RequestEditForm(FlaskForm):
    status = SelectField('Статус', choices=REQUEST_STATUSES)
    internal_comment = TextAreaField('Внутренний комментарий', validators=[Optional(), Length(max=5000)])
    submit = SubmitField('Сохранить')


class ViewingForm(FlaskForm):
    schedule_version = HiddenField(default='1', validators=[Optional()])
    property_id = SelectField('Объект', coerce=int, choices=[])
    client_id = SelectField('Клиент', coerce=int, choices=[])
    viewing_date = DateTimeLocalField('Дата и время (Москва)', format='%Y-%m-%dT%H:%M', validators=[Optional()])
    duration_minutes = IntegerField('Длительность, мин', default=60, validators=[Optional(), NumberRange(min=15, max=480)])
    request_id = SelectField('Исходная заявка', coerce=int, choices=[], default=0)
    status = SelectField('Статус', choices=VIEWING_STATUSES)
    result = StringField('Результат показа', validators=[Optional(), Length(max=100)])
    comment = TextAreaField('Внутренний комментарий', validators=[Optional(), Length(max=5000)])
    submit = SubmitField('Сохранить показ')

    def validate_viewing_date(self, field):
        if self.status.data in ('confirmed', 'completed') and field.data is None:
            raise ValidationError('Для подтвержденного или проведенного показа нужна дата.')


class DealForm(FlaskForm):
    property_id = SelectField('Объект', coerce=int, choices=[])
    client_id = SelectField('Клиент', coerce=int, choices=[])
    request_id = SelectField('Исходная заявка', coerce=int, choices=[], default=0)
    deal_type = SelectField('Тип сделки', choices=DEALS)
    amount = DecimalField('Сумма сделки, ₽', validators=POSITIVE)
    deal_date = DateField('Дата сделки', validators=[InputRequired()])
    status = SelectField('Статус', choices=DEAL_STATUSES)
    submit = SubmitField('Сохранить сделку')


class EmployeeForm(ContactForm):
    email = StringField('Email для входа', filters=[normalize_email], validators=[DataRequired(message=REQUIRED), EMAIL, Length(max=120)])
    position = StringField('Должность', validators=[DataRequired(), Length(max=80)])
    role = SelectField('Роль', choices=[('REALTOR', 'Риэлтор'), ('ADMIN', 'Администратор')])
    is_active = BooleanField('Учетная запись активна', default=True)
    password = PasswordField('Пароль (при редактировании оставить пустым)', validators=[Optional(), Length(min=8, max=128)])
    submit = SubmitField('Сохранить сотрудника')


class CatalogForm(FlaskForm):
    q = StringField('Поиск', validators=[Optional(), Length(max=180)])
    deal = SelectField('Сделка', default='', choices=[('', 'Все')] + DEALS)
    type = SelectField('Тип объекта', default='', choices=[('', 'Все')] + TYPES)
    rooms = SelectField('Комнаты', default='', choices=[('', 'Любое'), ('0', 'Без комнат'), ('1', '1'), ('2', '2'), ('3', '3'), ('4', '4 и более')])
    district = StringField('Район', validators=[Optional(), Length(max=120)])
    min_price = DecimalField('Цена от, ₽', validators=[Optional(), NumberRange(min=0, max=999999999999.99)])
    max_price = DecimalField('Цена до, ₽', validators=[Optional(), NumberRange(min=0, max=999999999999.99)])
    min_area = DecimalField('Площадь от, м²', validators=[Optional(), NumberRange(min=0, max=999999.99)])
    max_area = DecimalField('Площадь до, м²', validators=[Optional(), NumberRange(min=0, max=999999.99)])
    sort = SelectField('Сортировка', default='newest', choices=[('newest', 'Сначала новые'), ('price_asc', 'Сначала дешевле'), ('price_desc', 'Сначала дороже'), ('area_asc', 'По возрастанию площади'), ('area_desc', 'По убыванию площади')])
    page = IntegerField('Страница', default=1, validators=[InputRequired(), NumberRange(min=1, max=100000)])

    def validate_max_price(self, field):
        if field.data is not None and self.min_price.data is not None and field.data < self.min_price.data:
            raise ValidationError('Цена до меньше цены от.')

    def validate_max_area(self, field):
        if field.data is not None and self.min_area.data is not None and field.data < self.min_area.data:
            raise ValidationError('Площадь до меньше площади от.')

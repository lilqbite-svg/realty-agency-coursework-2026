import click
from flask import current_app
from sqlalchemy import select

from app.extensions import db
from app.models import Agent, Client, Property, Request, User
from app.forms import normalize_email

DEMO_PASSWORD = 'DemoPass123!'
DEMO_PROPERTIES = [
    ('2-комнатная квартира, 64 м²', 'flat', 'sale', 'Москва, Хамовники · 8 мин. до метро', 'Хамовники', 64, 2, 7, 14, 18900000),
    ('1-комнатная квартира, 42 м²', 'flat', 'rent', 'Москва, Пресненский район · 5 мин. до метро', 'Пресненский район', 42, 1, 18, 24, 120000),
    ('3-комнатные апартаменты, 91 м²', 'flat', 'sale', 'Москва, Арбат · 6 мин. до метро', 'Арбат', 91, 3, 6, 9, 34500000),
    ('Дом, 186 м² · участок 8 сот.', 'house', 'sale', 'Новая Москва · 21 км от МКАД', 'Новая Москва', 186, 4, 0, 2, 27900000),
    ('2-комнатная квартира, 70 м²', 'flat', 'sale', 'Москва, Даниловский район · 7 мин. до метро', 'Даниловский район', 70, 2, 12, 19, 22400000),
    ('Офис класса A, 145 м²', 'commercial', 'rent', 'Москва-Сити · 3 мин. до метро', 'Москва-Сити', 145, 0, 39, 60, 490000),
]


def seed_demo():
    if current_app.config['APP_ENV'] != 'development' and not current_app.testing:
        raise click.ClickException('Демо-данные разрешены только в development.')
    people = {}
    for role, name in [('ADMIN', 'Администратор агентства'), ('REALTOR', 'Александр Ченцов'), ('CLIENT', 'Анна Смирнова')]:
        email = role.lower() + '@example.local'
        user = db.session.scalar(select(User).where(User.email == email))
        if not user:
            user = User(email=email, role=role)
            user.set_password(DEMO_PASSWORD)
            db.session.add(user)
            if role == 'CLIENT':
                db.session.add(Client(account=user, full_name=name, phone='+7 999 111-22-33', email=email, client_type='buyer'))
            else:
                db.session.add(Agent(account=user, full_name=name, phone='+7 495 000-00-00', email=email, position='Администратор' if role == 'ADMIN' else 'Риэлтор'))
            db.session.flush()
        people[role] = user
    agent, client = people['REALTOR'].agent, people['CLIENT'].client
    if not agent or not client:
        raise click.ClickException('Демо-учетные записи были изменены; восстановите их вручную.')
    for index, row in enumerate(DEMO_PROPERTIES, 1):
        key = f'original-{index}'
        if db.session.scalar(select(Property).where(Property.demo_key == key)):
            continue
        title, kind, deal, address, district, area, rooms, floor, floors, price = row
        db.session.add(Property(demo_key=key, title=title, property_type=kind, deal_type=deal, address=address, district=district,
                                area=area, rooms=rooms, floor=floor, total_floors=floors, price=price, agent=agent,
                                visual_style=index, description='Светлый объект с функциональной планировкой. Рядом магазины, транспорт и прогулочные зоны. Подробности и время просмотра можно согласовать с риэлтором.'))
    if not db.session.scalar(select(Request).where(Request.client_id == client.id)):
        db.session.add(Request(client=client, agent=agent, deal_type='sale', property_type='flat', max_price=22000000,
                               rooms=2, preferred_area='Хамовники', message='Подберите квартиру рядом с метро.'))
    db.session.commit()


def register_commands(app):
    @app.cli.command('seed-demo')
    def seed_command():
        """Load the six original cards and development accounts, idempotently."""
        seed_demo()
        click.echo('Демо-данные готовы. Повторный запуск не дублирует объекты и аккаунты.')

    @app.cli.command('create-admin')
    @click.option('--email', prompt='Email')
    @click.option('--name', prompt='ФИО')
    @click.option('--phone', prompt='Телефон')
    @click.password_option(confirmation_prompt=True)
    def create_admin(email, name, phone, password):
        """Create an administrator; password is entered without echo."""
        from app.forms import EmployeeForm
        email = normalize_email(email)
        form = EmployeeForm(data={'email': email, 'full_name': name, 'phone': phone, 'password': password,
                                  'position': 'Администратор', 'role': 'ADMIN', 'is_active': True}, meta={'csrf': False})
        if not form.validate() or len(password) < 8:
            raise click.ClickException(f'Некорректные данные: {form.errors}')
        if db.session.scalar(select(User).where(User.email == email)):
            raise click.ClickException('Этот email уже используется.')
        user = User(email=email, role='ADMIN')
        user.set_password(password)
        db.session.add(Agent(account=user, full_name=name, phone=phone, email=email, position='Администратор'))
        db.session.commit()
        click.echo('Администратор создан.')

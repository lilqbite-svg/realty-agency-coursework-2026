"""One transactional schedule writer for the calendar and the existing form.

Viewing.viewing_date is UTC without tzinfo in storage. Form values without an
offset belong to Europe/Moscow. Active intervals are half-open [start, end).
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError, OperationalError

from app.extensions import db
from app.models import Agent, Client, Property, Request, Viewing

try:
    MOSCOW = ZoneInfo('Europe/Moscow')
except ZoneInfoNotFoundError:  # Windows installations without the IANA package.
    MOSCOW = timezone(timedelta(hours=3), 'Europe/Moscow')

ACTIVE = ('requested', 'confirmed')
STATUSES = ('requested', 'confirmed', 'completed', 'cancelled')


class SchedulingError(ValueError):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_moscow(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(MOSCOW)


def parse_start(value):
    """Parse an offset ISO date or a Moscow datetime-local value as UTC-naive."""
    if value in (None, ''):
        return None
    if isinstance(value, str):
        if len(value) > 40:
            raise SchedulingError('Некорректная дата и время.')
        try:
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError as error:
            raise SchedulingError('Некорректная дата и время.') from error
    if not isinstance(value, datetime):
        raise SchedulingError('Некорректная дата и время.')
    if value.tzinfo is None:
        value = value.replace(tzinfo=MOSCOW)
    try:
        return value.astimezone(timezone.utc).replace(tzinfo=None, microsecond=0)
    except (ValueError, OverflowError) as error:
        raise SchedulingError('Некорректная дата и время.') from error


def positive_id(value, label='Идентификатор', optional=False):
    if optional and value in (None, '', 0, '0'):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise SchedulingError(f'{label}: выберите запись из списка.')
    try:
        result = int(value)
    except (ValueError, TypeError) as error:
        raise SchedulingError(f'{label}: выберите запись из списка.') from error
    if result < 1 or result > 2147483647:
        raise SchedulingError(f'{label}: выберите запись из списка.')
    return result


def actor_client_query(actor):
    query = select(Client)
    if actor.role == 'ADMIN':
        return query
    agent_id = actor.agent.id
    return query.where(or_(
        Client.created_by_agent_id == agent_id,
        Client.properties.any(Property.agent_id == agent_id),
        Client.requests.any(Request.agent_id == agent_id),
        Client.viewings.any(Viewing.agent_id == agent_id),
        Client.deals.any(agent_id=agent_id),
    ))


def _staff(actor):
    if not actor or not actor.is_authenticated or not actor.is_active or actor.role not in ('ADMIN', 'REALTOR') or not actor.agent:
        raise SchedulingError('Недостаточно прав для изменения показов.', 403)


def _write_lock(actor):
    """Lock in the database, not in a Python process, before conflict reads.

    SQLite serializes writers: the first no-op UPDATE acquires its write lock.
    Existing read-snapshot/busy failures are rolled back and reported for retry.
    MySQL locks the ordered staff rows, serializing this small agency schedule.
    Thus two independent workers cannot both validate a free interval and save.
    """
    if db.engine.dialect.name == 'sqlite':
        db.session.execute(
            update(Agent).where(Agent.id == actor.agent.id)
            .values(id=Agent.id, updated_at=Agent.updated_at),
            execution_options={'autoflush': False, 'synchronize_session': False},
        )
    else:
        db.session.scalars(select(Agent).order_by(Agent.id).with_for_update()).all()


def save_viewing(record, values, actor, expected_version=None):
    """Validate, save and commit a Viewing; roll back on every rejected write.

    Callers must not mutate persistent record fields before calling. For an edit,
    submit the version displayed in the form/API as expected_version. Legacy
    programmatic callers may omit it and use the record's loaded version.
    """
    _staff(actor)
    if not isinstance(values, dict):
        raise SchedulingError('Ожидались поля показа.')
    record_id = record.id
    loaded_version = record.schedule_version if record_id else None
    if record_id:
        expected_version = positive_id(expected_version if expected_version is not None else loaded_version, 'Версия записи')
    try:
        with db.session.no_autoflush:
            _write_lock(actor)
            if record_id:
                db.session.refresh(record, with_for_update=True)
                if actor.role != 'ADMIN' and record.agent_id != actor.agent.id:
                    raise SchedulingError('Недостаточно прав для изменения показа.', 403)
                if record.schedule_version != expected_version:
                    raise SchedulingError('Показ уже изменен другим сотрудником. Обновите календарь и повторите действие.', 409)
            property_id = positive_id(values.get('property_id', record.property_id), 'Объект')
            client_id = positive_id(values.get('client_id', record.client_id), 'Клиент')
            property = db.session.scalar(select(Property).where(Property.id == property_id).with_for_update())
            if not property or (actor.role != 'ADMIN' and property.agent_id != actor.agent.id):
                raise SchedulingError('Выбранный объект недоступен.', 403)
            person = db.session.scalar(actor_client_query(actor).where(Client.id == client_id))
            if not person:
                raise SchedulingError('Выбранный клиент недоступен.', 403)
            default_agent = record.agent_id if record_id else property.agent_id
            agent_id = positive_id(values.get('agent_id') or default_agent, 'Риэлтор')
            agent = db.session.get(Agent, agent_id)
            if not agent or not agent.account.is_active or agent.account.role not in ('REALTOR', 'ADMIN'):
                raise SchedulingError('Выберите действующего сотрудника.')
            if actor.role != 'ADMIN' and agent_id != actor.agent.id:
                raise SchedulingError('Нельзя назначить показ другому риэлтору.', 403)
            request_id = positive_id(values.get('request_id', record.request_id), 'Заявка', optional=True)
            if request_id:
                linked_request = db.session.get(Request, request_id)
                if not linked_request or (actor.role != 'ADMIN' and linked_request.agent_id != actor.agent.id):
                    raise SchedulingError('Выбранная заявка недоступна.', 403)
                if linked_request.client_id != client_id:
                    raise SchedulingError('Клиент показа должен совпадать с клиентом заявки.')
            status = values.get('status', record.status or 'requested')
            if status not in STATUSES:
                raise SchedulingError('Неизвестный статус показа.')
            duration = positive_id(values.get('duration_minutes', record.duration_minutes or 60), 'Длительность')
            if not 15 <= duration <= 480:
                raise SchedulingError('Длительность должна составлять от 15 до 480 минут.')
            start = parse_start(values['viewing_date']) if 'viewing_date' in values else record.viewing_date
            if status in ('confirmed', 'completed') and start is None:
                raise SchedulingError('Для подтвержденного или завершенного показа нужна дата.')
            if status in ACTIVE and property.status != 'available':
                raise SchedulingError('Объект недоступен для активного показа.')
            changed_schedule = (not record_id or start != record.viewing_date or duration != record.duration_minutes
                                or agent_id != record.agent_id or property_id != record.property_id
                                or record.status not in ACTIVE)
            if status in ACTIVE and start is not None and start < utc_now() and (changed_schedule or (status == 'confirmed' and record.status != 'confirmed')):
                raise SchedulingError('Нельзя назначить или перенести активный показ в прошлое.')
            if start is not None:
                try:
                    end = start + timedelta(minutes=duration)
                except OverflowError as error:
                    raise SchedulingError('Некорректная дата окончания показа.') from error
            if status in ACTIVE and start is not None:
                # Duration is bounded by 480 minutes, so the SQL candidate window
                # is finite; compare the precise half-open intervals below.
                overlaps = db.session.scalars(select(Viewing).where(
                    Viewing.id != (record_id or 0), Viewing.status.in_(ACTIVE),
                    Viewing.viewing_date.is_not(None), Viewing.viewing_date < end,
                    Viewing.viewing_date > start - timedelta(minutes=480),
                    or_(Viewing.agent_id == agent_id, Viewing.property_id == property_id),
                ).with_for_update()).all()
                for other in overlaps:
                    if other.viewing_date + timedelta(minutes=other.duration_minutes) > start:
                        occupied = f'{to_moscow(other.viewing_date):%d.%m.%Y %H:%M}–{to_moscow(other.viewing_date + timedelta(minutes=other.duration_minutes)):%H:%M}'
                        raise SchedulingError(f'Интервал {occupied} (Москва) занят: пересекается время риэлтора или объекта. Выберите другое время.', 409)
            text_values = {}
            for field, maximum in (('result', 100), ('comment', 5000), ('client_message', 5000)):
                value = values.get(field, getattr(record, field, None) or '')
                if not isinstance(value, str) or len(value) > maximum:
                    raise SchedulingError(f'Недопустимая длина поля «{field}» (до {maximum} символов).')
                text_values[field] = value.strip()
            record.property_id, record.client_id, record.agent_id = property_id, client_id, agent_id
            record.request_id, record.viewing_date, record.duration_minutes = request_id, start, duration
            record.status = status
            record.active_key = f'{property_id}:{client_id}' if status in ACTIVE else None
            record.schedule_version = (record.schedule_version + 1) if record_id else 1
            for field, value in text_values.items():
                setattr(record, field, value)
            db.session.add(record)
        db.session.commit()
        return record
    except SchedulingError:
        db.session.rollback()
        raise
    except IntegrityError as error:
        db.session.rollback()
        raise SchedulingError('Не удалось сохранить показ: у клиента уже есть активная запись на этот объект либо данные изменились. Обновите календарь.', 409) from error
    except OperationalError as error:
        db.session.rollback()
        raise SchedulingError('Расписание сейчас изменяется другим сотрудником. Обновите календарь и повторите действие.', 409) from error

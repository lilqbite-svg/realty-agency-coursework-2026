"""Deterministic, explainable matching; all scores are rule scores, not probabilities."""
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from flask import current_app, has_app_context

ALGORITHM_VERSION = 'rules-1.0'
DEFAULT_WEIGHTS = {'district': 45, 'rooms': 30, 'budget': 25}
CENT = Decimal('0.01')


def number(value):
    return Decimal(str(value)) if value is not None else None


def normalized_text(value):
    return re.sub(r'\s+', ' ', (value or '').strip().casefold().replace('ё', 'е'))


def configured_weights(weights=None):
    if weights is None:
        weights = current_app.config.get('MATCHING_WEIGHTS', DEFAULT_WEIGHTS) if has_app_context() else DEFAULT_WEIGHTS
    result = {}
    for key in DEFAULT_WEIGHTS:
        value = number(weights.get(key, 0))
        if value is None or not value.is_finite() or value < 0:
            raise ValueError('Веса подбора должны быть конечными неотрицательными числами.')
        result[key] = value
    return result


def money_text(value):
    return f'{number(value):,.2f}'.replace(',', ' ').replace('.', ',') + ' ₽'


def evaluate_match(record, property, weights=None):
    """Return JSON-safe evidence and Decimal score for the current request/object pair.

    District is an exact normalized match. Rooms use 1-|actual-target|/max(target,1).
    Budget uses 1-|price-target|/target; target is midpoint or the only positive bound.
    Components are clipped to [0,1], and only specified, positively weighted criteria count.
    """
    weights = configured_weights(weights)
    price = number(property.price)
    lower, upper = number(record.min_price), number(record.max_price)
    required = [
        {'label': 'Доступность', 'passed': property.status == 'available',
         'reason': 'Объект доступен' if property.status == 'available' else 'Объект больше не доступен для нового подбора'},
        {'label': 'Тип сделки', 'passed': bool(record.deal_type) and property.deal_type == record.deal_type,
         'reason': 'Тип сделки совпадает' if property.deal_type == record.deal_type else 'Тип сделки отличается от заявки'},
        {'label': 'Тип недвижимости', 'passed': not record.property_type or property.property_type == record.property_type,
         'reason': 'Тип объекта не задан' if not record.property_type else ('Тип объекта совпадает' if property.property_type == record.property_type else 'Тип объекта отличается от заявки')},
        {'label': 'Минимальная цена', 'passed': lower is None or price >= lower,
         'reason': 'Нижняя граница не задана' if lower is None else f'Цена не ниже {money_text(lower)}' if price >= lower else f'Цена ниже {money_text(lower)}'},
        {'label': 'Максимальная цена', 'passed': upper is None or price <= upper,
         'reason': 'Верхняя граница не задана' if upper is None else f'Цена не выше {money_text(upper)}' if price <= upper else f'Цена выше {money_text(upper)}'},
    ]
    eligible = all(item['passed'] for item in required)
    raw = []
    if normalized_text(record.preferred_area):
        matches = normalized_text(record.preferred_area) == normalized_text(property.district)
        raw.append(('district', 'Район', Decimal(int(matches)),
                    f'Район совпадает: {property.district}' if matches else f'Указан «{record.preferred_area}», у объекта — «{property.district}»'))
    if record.rooms is not None:
        wanted, actual = Decimal(record.rooms), Decimal(property.rooms)
        quality = max(Decimal(0), Decimal(1) - abs(actual - wanted) / max(wanted, Decimal(1)))
        raw.append(('rooms', 'Комнаты', quality, f'В заявке: {record.rooms}; у объекта: {property.rooms}. Совпадение: {quality * 100:.2f} из 100'))
    target = (lower + upper) / 2 if lower is not None and upper is not None else upper if upper is not None else lower
    if target is not None and target > 0:
        quality = max(Decimal(0), Decimal(1) - abs(price - target) / target)
        raw.append(('budget', 'Близость к бюджету', quality,
                    f'Целевой бюджет: {money_text(target)}; отклонение цены: {money_text(abs(price - target))}'))
    active = [(key, label, quality, reason) for key, label, quality, reason in raw if weights[key] > 0]
    total = sum((weights[key] for key, _, _, _ in active), Decimal(0))
    criteria = []
    calculated = Decimal(0)
    for key, label, quality, reason in active:
        normalized_weight = weights[key] / total
        contribution = 100 * normalized_weight * quality
        calculated += contribution
        criteria.append({'key': key, 'label': label, 'reason': reason,
                         'weight': float((100 * normalized_weight).quantize(CENT, rounding=ROUND_HALF_UP)),
                         'quality': float((100 * quality).quantize(CENT, rounding=ROUND_HALF_UP)),
                         'points': float(contribution.quantize(CENT, rounding=ROUND_HALF_UP))})
    score = (calculated if total else Decimal(100)) if eligible else Decimal(0)
    return {'score': score.quantize(CENT, rounding=ROUND_HALF_UP), 'eligible': eligible,
            'ranking_available': bool(total), 'required': required, 'criteria': criteria,
            'weights': {key: str(value) for key, value in weights.items()}, 'version': ALGORITHM_VERSION,
            'request_parameters': {'deal_type': record.deal_type, 'property_type': record.property_type,
                                   'min_price': str(lower) if lower is not None else None,
                                   'max_price': str(upper) if upper is not None else None,
                                   'rooms': record.rooms, 'preferred_area': record.preferred_area},
            'property_parameters': {'deal_type': property.deal_type, 'property_type': property.property_type,
                                    'price': str(price), 'district': property.district, 'rooms': property.rooms,
                                    'status': property.status, 'area': str(property.area)}}


def snapshot(evaluation):
    result = dict(evaluation)
    result['score'] = str(evaluation['score'])
    return result


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def decode(value, default):
    try:
        result = json.loads(value)
        return result if isinstance(result, type(default)) else default
    except (TypeError, ValueError):
        return default


def history_event(action, actor_id, **details):
    return {'action': action, 'actor_id': actor_id, 'at': datetime.now(timezone.utc).isoformat(), **details}


def history_for_display(value):
    events = decode(value, [])
    labels = {'saved': 'Вариант сохранен', 'refreshed': 'Оценка обновлена', 'edited': 'Статус или комментарий изменен'}
    result = []
    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        item = dict(event)
        item['label'] = labels.get(event.get('action'), 'Действие с подборкой')
        try:
            item['when'] = datetime.fromisoformat(event['at']).astimezone(timezone(timedelta(hours=3))).strftime('%d.%m.%Y %H:%M')
        except (KeyError, TypeError, ValueError):
            item['when'] = 'Дата не указана'
        result.append(item)
    return result

"""Database-backed metrics shared by the screen and both export formats."""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO, StringIO
from pathlib import Path
import csv
from xml.sax.saxutils import escape

from flask import abort, current_app
from sqlalchemy import distinct, extract, func, select

from app.extensions import db
from app.models import Agent, Deal, Property, Request, Viewing

MOSCOW = timezone(timedelta(hours=3))
PRESETS = {'current_month': 'Текущий месяц', 'previous_month': 'Предыдущий месяц',
           'last_30_days': 'Последние 30 дней', 'custom': 'Произвольный период'}
PROPERTY_TYPES = {'flat': 'Квартиры', 'house': 'Дома', 'land': 'Участки', 'commercial': 'Коммерческие'}
REQUEST_STATUSES = {'new': 'Новые', 'in_progress': 'В работе', 'completed': 'Завершённые', 'cancelled': 'Отменённые'}


class AnalyticsFilterError(ValueError):
    pass


@dataclass(frozen=True)
class AnalyticsFilters:
    start: date
    end: date
    agent_id: int | None
    preset: str = 'custom'

    @property
    def query(self):
        return {'preset': self.preset, 'start': self.start.isoformat(), 'end': self.end.isoformat(),
                'agent_id': str(self.agent_id) if self.agent_id else 'all'}

    @property
    def local_bounds(self):
        return datetime.combine(self.start, time.min), datetime.combine(self.end + timedelta(days=1), time.min)

    @property
    def utc_bounds(self):
        return tuple(value.replace(tzinfo=MOSCOW).astimezone(timezone.utc).replace(tzinfo=None)
                     for value in self.local_bounds)


def parse_filters(args, user, today=None):
    """Validate the query and derive scope from authenticated server-side identity."""
    if user.role not in ('ADMIN', 'REALTOR'):
        abort(403)
    today = today or datetime.now(MOSCOW).date()
    preset = args.get('preset', 'current_month')
    if preset not in PRESETS:
        raise AnalyticsFilterError('Неизвестный вариант периода.')
    first = today.replace(day=1)
    if preset == 'current_month':
        start, end = first, today
    elif preset == 'previous_month':
        end = first - timedelta(days=1)
        start = end.replace(day=1)
    elif preset == 'last_30_days':
        start, end = today - timedelta(days=29), today
    else:
        try:
            start, end = date.fromisoformat(args.get('start', '')), date.fromisoformat(args.get('end', ''))
        except (ValueError, TypeError):
            raise AnalyticsFilterError('Укажите начальную и конечную даты в формате ГГГГ-ММ-ДД.') from None
    if start > end:
        raise AnalyticsFilterError('Начальная дата не может быть позже конечной.')
    if start.year < 2000 or end.year > 2100:
        raise AnalyticsFilterError('Выберите даты с 2000 по 2100 год.')
    if (end - start).days > 1095:
        raise AnalyticsFilterError('Период не должен превышать 1096 календарных дней.')
    if end >= date.max:
        raise AnalyticsFilterError('Конечная дата находится за пределами допустимого диапазона.')
    raw_agent = args.get('agent_id', 'all')
    if user.role == 'REALTOR':
        if not user.agent:
            abort(403)
        if raw_agent not in ('all', '', str(user.agent.id)):
            abort(403)
        agent_id = user.agent.id
    elif raw_agent in ('all', ''):
        agent_id = None
    else:
        try:
            agent_id = int(raw_agent)
        except (ValueError, TypeError):
            raise AnalyticsFilterError('Некорректный идентификатор риэлтора.') from None
        if agent_id <= 0 or db.session.get(Agent, agent_id) is None:
            raise AnalyticsFilterError('Выбранный риэлтор не найден.')
    return AnalyticsFilters(start, end, agent_id, preset)


def _scope(model, filters):
    return [model.agent_id == filters.agent_id] if filters.agent_id is not None else []


def _count(model, conditions):
    return db.session.scalar(select(func.count()).select_from(model).where(*conditions)) or 0


def _money(value):
    return Decimal(str(value or 0)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _display(value, unit=''):
    if value is None:
        return 'Недостаточно данных'
    if unit in ('₽', '%'):
        digits = 2 if unit == '₽' else 1
        return f'{value:,.{digits}f}'.replace(',', ' ').replace('.', ',') + ' ' + unit
    return str(value)


def _month_series(column, conditions, aggregate, filters):
    year, month = extract('year', column), extract('month', column)
    rows = db.session.execute(select(year, month, aggregate).where(*conditions).group_by(year, month))
    found = {(int(y), int(m)): value for y, m, value in rows}
    cursor = filters.start.replace(day=1)
    result = []
    while cursor <= filters.end:
        result.append((cursor.strftime('%m.%Y'), found.get((cursor.year, cursor.month), 0)))
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    return result


def build_analytics(filters):
    """All totals use SQL aggregation; zero-filled month buckets contain no invented records."""
    local_start, local_end = filters.local_bounds
    utc_start, utc_end = filters.utc_bounds
    properties = _scope(Property, filters)
    requests = _scope(Request, filters) + [Request.created_at >= local_start, Request.created_at < local_end]
    viewings = _scope(Viewing, filters) + [Viewing.viewing_date >= utc_start, Viewing.viewing_date < utc_end]
    deals = _scope(Deal, filters) + [Deal.deal_date >= filters.start, Deal.deal_date <= filters.end, Deal.status == 'completed']
    request_counts = dict(db.session.execute(select(Request.status, func.count()).where(*requests).group_by(Request.status)).all())
    property_counts = dict(db.session.execute(select(Property.property_type, func.count()).where(*properties).group_by(Property.property_type)).all())
    deal_count, total, average = db.session.execute(select(func.count(Deal.id), func.sum(Deal.amount), func.avg(Deal.amount)).where(*deals)).one()
    total, average = _money(total), _money(average) if deal_count else None
    cohort = select(Request.id).where(*requests)
    cohort_count = sum(request_counts.values())
    # Explicit request_id plus matching client: no attribution by contact identity.
    attributed_viewings = _scope(Viewing, filters) + [Viewing.request_id.in_(cohort), Viewing.status != 'cancelled',
                            Viewing.client_id == Request.client_id, Viewing.request_id == Request.id]
    with_viewing = db.session.scalar(select(func.count(distinct(Viewing.request_id))).select_from(Viewing).join(Request, Request.id == Viewing.request_id).where(*attributed_viewings)) or 0
    # Require an explicit viewing of the same object/client/request. This also excludes corrupt legacy links.
    evidence = select(Viewing.id).where(Viewing.request_id == Deal.request_id, Viewing.property_id == Deal.property_id,
                    Viewing.client_id == Deal.client_id, Viewing.status.in_(('confirmed', 'completed')),
                    *_scope(Viewing, filters)).exists()
    with_deal = db.session.scalar(select(func.count(distinct(Deal.request_id))).select_from(Deal).join(Request, Request.id == Deal.request_id).where(
        Deal.request_id.in_(cohort), Deal.status == 'completed', Deal.client_id == Request.client_id,
        *_scope(Deal, filters), evidence)) or 0
    conversion_viewing = round(with_viewing * 100 / cohort_count, 1) if cohort_count else None
    conversion_deal = round(with_deal * 100 / cohort_count, 1) if cohort_count else None
    missing_viewings = _count(Viewing, viewings + [Viewing.request_id.is_(None), Viewing.status != 'cancelled'])
    missing_deals = _count(Deal, deals + [Deal.request_id.is_(None)])
    snapshot = 'Текущее состояние объектов; период не применяется.'
    cohort_definition = 'Заявки, созданные в периоде; текущий статус. Все статусы входят в знаменатель воронки.'
    values = [
        ('properties_total', 'Объекты всего', sum(property_counts.values()), '', snapshot),
        ('properties_available', 'Доступные объекты', _count(Property, properties + [Property.status == 'available']), '', snapshot),
        ('requests_new', 'Новые заявки', request_counts.get('new', 0), '', cohort_definition),
        ('requests_in_progress', 'Заявки в работе', request_counts.get('in_progress', 0), '', cohort_definition),
        ('requests_completed', 'Завершённые заявки', request_counts.get('completed', 0), '', cohort_definition),
        ('viewings_planned', 'Запланированные показы', _count(Viewing, viewings + [Viewing.status == 'confirmed']), '', 'Статус «Подтверждён», дата начала показа внутри периода по Москве.'),
        ('viewings_completed', 'Состоявшиеся показы', _count(Viewing, viewings + [Viewing.status == 'completed']), '', 'Статус «Завершён», дата начала показа внутри периода по Москве.'),
        ('deals_completed', 'Заключённые сделки', deal_count, '', 'Статус «Завершена», дата сделки внутри периода.'),
        ('deals_total', 'Сумма завершённых сделок', total, '₽', 'Стоимость сделок продажи и аренды по данным договора. Не доход агентства.'),
        ('deals_average', 'Средняя сумма сделки', average, '₽', 'Сумма завершённых сделок / их число. При отсутствии сделок данных недостаточно.'),
        ('conversion_viewing', 'Заявки → показы', conversion_viewing, '%', 'Доля заявок когорты с хотя бы одним явно связанным неотменённым показом за всё время.'),
        ('conversion_deal', 'Заявки → сделки', conversion_deal, '%', 'Доля заявок когорты с завершённой сделкой и связанным подтверждённым/завершённым показом того же объекта за всё время.'),
    ]
    metrics = [{'key': key, 'label': label, 'value': str(value) if isinstance(value, Decimal) else value,
                'unit': unit, 'display': _display(value, unit), 'definition': definition}
               for key, label, value, unit, definition in values]
    request_months = _month_series(Request.created_at, requests, func.count(Request.id), filters)
    deal_months = _month_series(Deal.deal_date, deals, func.count(Deal.id), filters)
    amount_months = _month_series(Deal.deal_date, deals, func.sum(Deal.amount), filters)
    agent_query = select(Agent).order_by(Agent.full_name, Agent.id)
    if filters.agent_id is not None:
        agent_query = agent_query.where(Agent.id == filters.agent_id)
    agents = db.session.scalars(agent_query).all()
    agent_requests = dict(db.session.execute(select(Request.agent_id, func.count()).where(*requests).group_by(Request.agent_id)).all())
    agent_viewings = dict(db.session.execute(select(Viewing.agent_id, func.count()).where(*viewings, Viewing.status.in_(('confirmed', 'completed'))).group_by(Viewing.agent_id)).all())
    agent_deals = {agent_id: (count, _money(amount)) for agent_id, count, amount in db.session.execute(
        select(Deal.agent_id, func.count(), func.sum(Deal.amount)).where(*deals).group_by(Deal.agent_id))}
    performance = [{'id': agent.id, 'name': agent.full_name, 'requests': agent_requests.get(agent.id, 0),
                    'viewings': agent_viewings.get(agent.id, 0), 'deals': agent_deals.get(agent.id, (0, 0))[0],
                    'amount': str(agent_deals.get(agent.id, (0, Decimal('0.00')))[1])} for agent in agents]
    def graph(key, title, unit, rows, kind, note):
        return {'key': key, 'title': title, 'unit': unit, 'labels': [r[0] for r in rows],
                'values': [float(r[1]) if unit == '₽' else int(r[1]) for r in rows],
                'display_values': [_display(_money(r[1]) if unit == '₽' else r[1], unit if unit == '₽' else '') for r in rows],
                'kind': kind, 'note': note}
    charts = [
        graph('request_dynamics', 'Динамика заявок', 'заявок', request_months, 'line', 'По месяцу создания; крайние месяцы ограничены выбранными датами.'),
        graph('deal_dynamics', 'Динамика завершённых сделок', 'сделок', deal_months, 'line', 'По дате сделки в выбранном периоде.'),
        graph('deal_amounts', 'Сумма сделок по месяцам', '₽', amount_months, 'bar', 'Стоимость завершённых сделок; не доход агентства.'),
        graph('property_types', 'Объекты по типам', 'объектов', [(label, property_counts.get(key, 0)) for key, label in PROPERTY_TYPES.items()], 'donut', snapshot),
        graph('request_statuses', 'Заявки по статусам', 'заявок', [(label, request_counts.get(key, 0)) for key, label in REQUEST_STATUSES.items()], 'bar', cohort_definition),
        graph('agent_performance', 'Результаты риэлторов', 'сделок', [(f"{row['name']} (#{row['id']})", row['deals']) for row in performance], 'bar', 'Завершённые сделки в периоде; дополнительные показатели приведены ниже.'),
        graph('funnel', 'Воронка заявок', 'заявок', [('Заявка', cohort_count), ('Показ', with_viewing), ('Сделка', with_deal)], 'bar', 'Одна заявка учитывается один раз на этапе. Связанные результаты — за всё время, а не только за период создания заявки.'),
    ]
    scope_label = 'Все риэлторы (включая неназначенные заявки)' if filters.agent_id is None else f"{agents[0].full_name} (#{filters.agent_id})"
    return {'filters': filters, 'scope_label': scope_label, 'period_label': f'{filters.start:%d.%m.%Y} — {filters.end:%d.%m.%Y}',
            'metrics': metrics, 'charts': charts, 'performance': performance, 'cohort_count': cohort_count,
            'missing_viewings': missing_viewings, 'missing_deals': missing_deals,
            'unassigned_requests': agent_requests.get(None, 0), 'generated_at': datetime.now(MOSCOW).strftime('%d.%m.%Y %H:%M МСК')}


def _csv_cell(value):
    text = str(value if value is not None else 'Недостаточно данных')
    # Names are user-entered. Prevent spreadsheet formula interpretation on export.
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r', '\n')) else text


def export_csv_bytes(report):
    output = StringIO(newline='')
    writer = csv.writer(output, delimiter=';', lineterminator='\r\n')
    def row(*values):
        writer.writerow([_csv_cell(value) for value in values])
    row('Ключевой выбор — аналитика', report['period_label'], report['scope_label'])
    row('Сформировано', report['generated_at'])
    row('Показатель', 'Значение', 'Единица', 'Определение')
    for item in report['metrics']:
        row(item['label'], item['value'], item['unit'], item['definition'])
    row('Показы без связи с заявкой', report['missing_viewings'])
    row('Завершённые сделки без связи с заявкой', report['missing_deals'])
    row('Неназначенные заявки', report['unassigned_requests'])
    for chart in report['charts']:
        row()
        row(chart['title'], chart['unit'], chart['note'])
        row('Категория', 'Значение')
        for label, value in zip(chart['labels'], chart['values']):
            row(label, format(_money(value), 'f') if chart['unit'] == '₽' else value)
    row()
    row('Риэлтор', 'Заявки', 'Подтверждённые и состоявшиеся показы', 'Завершённые сделки', 'Сумма сделок, ₽')
    for item in report['performance']:
        row(f"{item['name']} (#{item['id']})", item['requests'], item['viewings'], item['deals'], item['amount'])
    return output.getvalue().encode('utf-8-sig')


def export_pdf_bytes(report):
    """A self-contained Unicode PDF with vector charts, no browser/CDN required."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.graphics.shapes import Drawing, Rect, String
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

    configured = current_app.config.get('ANALYTICS_PDF_FONT')
    candidates = [Path(configured)] if configured else []
    candidates += [Path(current_app.root_path) / 'static' / 'fonts' / 'DejaVuSans.ttf',
                   Path('C:/Windows/Fonts/arial.ttf'), Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')]
    font_path = next((path for path in candidates if path.is_file()), None)
    if font_path is None:
        raise RuntimeError('Для PDF необходим шрифт с кириллицей: задайте ANALYTICS_PDF_FONT.')
    font = 'AnalyticsUnicode'
    if font not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(font, str(font_path)))
    stream = BytesIO()
    document = SimpleDocTemplate(stream, pagesize=A4, rightMargin=38, leftMargin=38, topMargin=38, bottomMargin=38,
                                title='Аналитика — Ключевой выбор', author='Ключевой выбор')
    normal = ParagraphStyle('AnalyticsNormal', fontName=font, fontSize=9, leading=13, spaceAfter=7)
    heading = ParagraphStyle('AnalyticsHeading', parent=normal, fontSize=14, leading=18, spaceBefore=10, spaceAfter=9, keepWithNext=True)
    def paragraph(text, style=normal):
        return Paragraph(escape(str(text)), style)
    elements = [paragraph('Аналитика агентства «Ключевой выбор»', heading), paragraph(report['period_label']),
                paragraph(report['scope_label']), paragraph('Сформировано: ' + report['generated_at'])]
    table_data = [[paragraph('Показатель'), paragraph('Значение')]]
    table_data.extend([[paragraph(item['label']), paragraph(item['display'])] for item in report['metrics']])
    table = Table(table_data, colWidths=[340, 179], repeatRows=1)
    table.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e5e7eb')), ('GRID', (0, 0), (-1, -1), .4, colors.HexColor('#d9dfe8')),
                              ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 3)]))
    elements += [table, Spacer(1, 10), paragraph('Сумма сделок — стоимость недвижимости или аренды по договору, не доход агентства. Объекты показаны в текущем состоянии; остальные KPI используют определения ниже.'),
                 paragraph(f"Без связи с заявкой в периоде: показов — {report['missing_viewings']}, завершённых сделок — {report['missing_deals']}. Они не включены в конверсию."),
                 paragraph('Воронка основана на заявках, созданных в периоде, и явно связанных результатах за всё время. При нескольких заявках клиенту атрибуция задаётся выбранным request_id, а не совпадением клиента.')]
    for chart in report['charts']:
        elements += [paragraph(chart['title'], heading), paragraph(chart['note'] + ' Единица: ' + chart['unit'] + '.')]
        if not chart['values'] or not any(chart['values']):
            elements.append(paragraph('Нет записей для построения графика. Значения равны нулю.'))
        maximum = max(chart['values'], default=0) or 1
        for offset in range(0, len(chart['labels']), 10):
            entries = list(zip(chart['labels'], chart['values'], chart['display_values']))[offset:offset + 10]
            drawing = Drawing(519, max(40, len(entries) * 35 + 12))
            for i, (label, value, display) in enumerate(entries):
                y = drawing.height - 25 - i * 35
                drawing.add(String(0, y + 8, label[:77], fontName=font, fontSize=8, fillColor=colors.HexColor('#334155')))
                drawing.add(Rect(0, y - 7, 395 * value / maximum, 9, fillColor=colors.HexColor('#2563eb'), strokeColor=None))
                drawing.add(String(405, y - 7, display, fontName=font, fontSize=8, fillColor=colors.HexColor('#0f172a')))
            elements.append(drawing)
    elements.append(paragraph('Определения показателей', heading))
    for item in report['metrics']:
        elements.append(paragraph(item['label'] + ': ' + item['definition']))
    def page_number(canvas, doc):
        canvas.setFont(font, 8)
        canvas.drawCentredString(A4[0] / 2, 20, str(doc.page))
    document.build(elements, onFirstPage=page_number, onLaterPages=page_number)
    return stream.getvalue()

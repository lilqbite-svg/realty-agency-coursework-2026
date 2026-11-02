from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
import json
import re

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import Agent, Client, Deal, Request, User, Viewing
from app.services.analytics import AnalyticsFilterError, AnalyticsFilters, build_analytics, export_csv_bytes, parse_filters


@pytest.fixture
def analytics_data(app):
    with app.app_context():
        agent = db.session.scalar(select(Agent).where(Agent.email == 'realtor@example.local'))
        other = db.session.scalar(select(Agent).where(Agent.email == 'second@example.org'))
        person = db.session.scalar(select(Client).where(Client.email == 'client@example.local'))
        records = []
        for day, status, employee in [(5, 'completed', agent), (6, 'in_progress', agent), (7, 'new', agent), (10, 'new', other)]:
            record = Request(client=person, agent=employee, deal_type='sale', property_type='flat',
                             status=status, created_at=datetime(2026, 1, day, 10))
            db.session.add(record)
            records.append(record)
        db.session.flush()
        a, b, c, foreign = records
        db.session.add_all([
            Viewing(property_id=1, client=person, agent=agent, request_id=a.id, viewing_date=datetime(2026, 1, 15, 10), status='completed'),
            Viewing(property_id=2, client=person, agent=agent, request_id=a.id, viewing_date=datetime(2026, 1, 16, 10), status='confirmed'),
            Viewing(property_id=3, client=person, agent=agent, request_id=b.id, viewing_date=datetime(2026, 1, 17, 10), status='cancelled'),
            Viewing(property_id=4, client=person, agent=agent, request_id=b.id, status='requested'),
            Viewing(property_id=5, client=person, agent=agent, viewing_date=datetime(2026, 1, 18, 10), status='confirmed'),
            Viewing(property_id=7, client=person, agent=other, request_id=foreign.id, viewing_date=datetime(2026, 1, 18, 10), status='completed'),
            Deal(property_id=1, client=person, agent=agent, request_id=a.id, deal_type='sale', amount=Decimal('100.10'), deal_date=date(2026, 1, 20), status='completed'),
            Deal(property_id=2, client=person, agent=agent, deal_type='rent', amount=Decimal('200.20'), deal_date=date(2026, 1, 21), status='completed'),
            Deal(property_id=3, client=person, agent=agent, request_id=b.id, deal_type='sale', amount=Decimal('999'), deal_date=date(2026, 1, 22), status='preparation'),
            Deal(property_id=4, client=person, agent=agent, request_id=a.id, deal_type='sale', amount=Decimal('500'), deal_date=date(2026, 2, 3), status='completed'),
            Deal(property_id=7, client=person, agent=other, request_id=foreign.id, deal_type='sale', amount=Decimal('900'), deal_date=date(2026, 1, 22), status='completed'),
        ])
        db.session.commit()
        return {'agent_id': agent.id, 'other_id': other.id, 'client_id': person.id, 'request_ids': [row.id for row in records]}


def metric_map(report):
    return {metric['key']: metric['value'] for metric in report['metrics']}


def test_analytics_counts_money_distinct_cohort_and_missing_links(app, analytics_data):
    with app.app_context():
        report = build_analytics(AnalyticsFilters(date(2026, 1, 1), date(2026, 1, 31), analytics_data['agent_id']))
        metrics = metric_map(report)
        assert metrics['properties_total'] == metrics['properties_available'] == 6
        assert (metrics['requests_new'], metrics['requests_in_progress'], metrics['requests_completed']) == (1, 1, 1)
        assert (metrics['viewings_planned'], metrics['viewings_completed'], metrics['deals_completed']) == (2, 1, 2)
        assert Decimal(metrics['deals_total']) == Decimal('300.30')
        assert Decimal(metrics['deals_average']) == Decimal('150.15')
        assert metrics['conversion_viewing'] == 66.7  # Two requests, not four viewing records.
        assert metrics['conversion_deal'] == 33.3
        assert report['charts'][-1]['values'] == [3, 2, 1]
        assert report['missing_viewings'] == report['missing_deals'] == 1
        assert report['charts'][2]['values'] == [300.30]
        assert len(report['performance']) == 1
        assert report['performance'][0]['deals'] == 2


def test_analytics_no_data_denominator_and_zero_months(app):
    with app.app_context():
        report = build_analytics(AnalyticsFilters(date(2030, 1, 1), date(2030, 3, 31), None))
        metrics = metric_map(report)
        assert metrics['deals_completed'] == 0 and metrics['deals_total'] == '0.00'
        assert metrics['deals_average'] is None
        assert metrics['conversion_viewing'] is metrics['conversion_deal'] is None
        assert report['charts'][0]['labels'] == ['01.2030', '02.2030', '03.2030']
        assert report['charts'][0]['values'] == [0, 0, 0]
        assert next(item for item in report['metrics'] if item['key'] == 'conversion_deal')['display'] == 'Недостаточно данных'
        assert metrics['properties_total'] == 7  # Explicitly current stock, independent of period.


def test_analytics_moscow_midnight_boundaries(app, analytics_data):
    with app.app_context():
        db.session.add_all([
            Viewing(property_id=1, client_id=analytics_data['client_id'], agent_id=analytics_data['agent_id'], status='confirmed', viewing_date=datetime(2025, 12, 31, 22)),
            Viewing(property_id=1, client_id=analytics_data['client_id'], agent_id=analytics_data['agent_id'], status='confirmed', viewing_date=datetime(2026, 1, 31, 22)),
        ])
        db.session.commit()
        report = build_analytics(AnalyticsFilters(date(2026, 1, 1), date(2026, 1, 31), analytics_data['agent_id']))
        assert metric_map(report)['viewings_planned'] == 3  # Jan 1 01:00 MSK included; Feb 1 01:00 excluded.


def test_analytics_explicit_attribution_no_guessing_and_same_object_evidence(app, analytics_data):
    with app.app_context():
        db.session.add(Deal(property_id=6, client_id=analytics_data['client_id'], agent_id=analytics_data['agent_id'],
                            request_id=analytics_data['request_ids'][2], deal_type='sale', amount=10, deal_date=date(2026, 1, 25), status='completed'))
        db.session.commit()
        report = build_analytics(AnalyticsFilters(date(2026, 1, 1), date(2026, 1, 31), analytics_data['agent_id']))
        assert metric_map(report)['deals_completed'] == 3
        assert report['charts'][-1]['values'] == [3, 2, 1]  # Same client has multiple requests; no matching viewing for this link.


@pytest.mark.parametrize('query', [
    {'preset': 'unknown'}, {'preset': 'custom', 'start': 'bad', 'end': '2026-01-31'},
    {'preset': 'custom', 'start': '2026-02-01', 'end': '2026-01-01'},
    {'preset': 'custom', 'start': '2020-01-01', 'end': '2026-01-01'},
    {'preset': 'custom', 'start': '0001-01-01', 'end': '0001-01-02'},
    {'agent_id': 'invalid'}, {'agent_id': '9999'},
])
def test_analytics_filter_validation(app, query):
    with app.app_context():
        admin = db.session.scalar(select(User).where(User.email == 'admin@example.local'))
        with pytest.raises(AnalyticsFilterError):
            parse_filters(query, admin, today=date(2026, 3, 5))


def test_analytics_presets(app):
    with app.app_context():
        admin = db.session.scalar(select(User).where(User.email == 'admin@example.local'))
        current = parse_filters({}, admin, today=date(2026, 3, 5))
        previous = parse_filters({'preset': 'previous_month'}, admin, today=date(2026, 3, 5))
        recent = parse_filters({'preset': 'last_30_days'}, admin, today=date(2026, 3, 5))
        assert (current.start, current.end) == (date(2026, 3, 1), date(2026, 3, 5))
        assert (previous.start, previous.end) == (date(2026, 2, 1), date(2026, 2, 28))
        assert (recent.end - recent.start).days == 29


def test_analytics_roles_filters_and_export_agree(app, client, login, analytics_data):
    query = 'preset=custom&start=2026-01-01&end=2026-01-31'
    for suffix in ['', '/export.csv', '/export.pdf']:
        assert client.get('/dashboard/analytics' + suffix).status_code == 302
    login('client')
    for suffix in ['', '/export.csv', '/export.pdf']:
        assert client.get('/dashboard/analytics' + suffix).status_code == 403
    client.post('/logout')
    login()
    page = client.get('/dashboard/analytics?' + query)
    assert page.status_code == 200
    assert 'Другой сотрудник' not in page.get_data(as_text=True)
    graph_match = re.search(r'<script type="application/json" id="analyticsData">(.*?)</script>', page.get_data(as_text=True), re.S)
    graphs = json.loads(graph_match.group(1))
    assert graphs[-1]['values'] == [3, 2, 1] and len(graphs) == 7
    for suffix in ['', '/export.csv', '/export.pdf']:
        assert client.get(f'/dashboard/analytics{suffix}?{query}&agent_id={analytics_data["other_id"]}').status_code == 403
    csv = client.get('/dashboard/analytics/export.csv?' + query)
    assert csv.status_code == 200 and csv.data.startswith(b'\xef\xbb\xbf')
    decoded = csv.data.decode('utf-8-sig')
    assert 'Сумма завершённых сделок;300.30;₽' in decoded
    assert 'Другой сотрудник' not in decoded
    assert 'no-store' in csv.headers['Cache-Control']
    pdf = client.get('/dashboard/analytics/export.pdf?' + query)
    assert pdf.status_code == 200 and pdf.data.startswith(b'%PDF-')
    assert pdf.headers['Content-Type'] == 'application/pdf'
    client.post('/logout')
    login('admin')
    all_report = client.get('/dashboard/analytics/export.csv?' + query).data.decode('utf-8-sig')
    assert 'Сумма завершённых сделок;1200.30;₽' in all_report
    selected = client.get(f'/dashboard/analytics/export.csv?{query}&agent_id={analytics_data["other_id"]}').data.decode('utf-8-sig')
    assert 'Сумма завершённых сделок;900.00;₽' in selected
    assert client.get('/dashboard/analytics?preset=custom&start=bad').status_code == 400
    assert client.get('/dashboard/analytics/export.csv?preset=custom&start=bad').status_code == 400


def test_analytics_csv_formula_injection(app, analytics_data):
    with app.app_context():
        agent = db.session.get(Agent, analytics_data['agent_id'])
        agent.full_name = '=1+1'
        db.session.commit()
        report = build_analytics(AnalyticsFilters(date(2026, 1, 1), date(2026, 1, 31), agent.id))
        export = export_csv_bytes(report).decode('utf-8-sig')
        assert "'=1+1" in export
        assert '\r\n=1+1' not in export

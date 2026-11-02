import json
import re
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.extensions import db
from app.models import Agent, Property, Request, RequestMatch
from app.services.matching import evaluate_match


def parameters(**changes):
    values = dict(deal_type='sale', property_type='flat', min_price=None,
                  max_price=Decimal('22000000'), rooms=2, preferred_area='Хамовники')
    return SimpleNamespace(**(values | changes))


def property_values(**changes):
    values = dict(deal_type='sale', property_type='flat', price=Decimal('18900000'),
                  rooms=2, district='Хамовники', status='available', area=Decimal('64'))
    return SimpleNamespace(**(values | changes))


def test_score_is_explainable_and_repeatable():
    result = evaluate_match(parameters(), property_values())
    assert result['score'] == Decimal('96.48')
    assert result == evaluate_match(parameters(), property_values())
    assert all(check['passed'] for check in result['required'])
    assert [part['points'] for part in result['criteria']] == [45.0, 30.0, 21.48]
    assert result['ranking_available'] is True
    lower = evaluate_match(parameters(), property_values(district='Арбат', rooms=3, price=Decimal('22000000')))
    assert lower['score'] == Decimal('40.00')


@pytest.mark.parametrize('changes', [
    {'status': 'reserved'}, {'status': 'sold'}, {'status': 'rented'}, {'status': 'archived'},
    {'deal_type': 'rent'}, {'property_type': 'house'}, {'price': Decimal('22000000.01')},
])
def test_hard_limits_exclude_ineligible_objects(changes):
    result = evaluate_match(parameters(), property_values(**changes))
    assert not result['eligible'] and result['score'] == 0
    assert any(not check['passed'] for check in result['required'])


def test_minimum_price_is_hard_limit_and_boundaries_are_inclusive():
    request = parameters(min_price=Decimal('19000000'), max_price=Decimal('22000000'))
    assert not evaluate_match(request, property_values())['eligible']
    assert evaluate_match(request, property_values(price=Decimal('19000000')))['eligible']
    assert evaluate_match(request, property_values(price=Decimal('22000000')))['eligible']
    assert evaluate_match(request, property_values(price=Decimal('20500000')))['score'] == 100


def test_optional_criteria_normalize_weights_and_empty_requirements_work():
    only_budget = evaluate_match(parameters(rooms=None, preferred_area=None), property_values(price=Decimal('11000000')))
    assert only_budget['score'] == 50
    assert only_budget['criteria'][0]['weight'] == 100
    no_preferences = evaluate_match(parameters(rooms=None, preferred_area=' ', min_price=None, max_price=None, property_type=None), property_values())
    assert no_preferences['eligible'] and no_preferences['score'] == 100
    assert not no_preferences['ranking_available'] and no_preferences['criteria'] == []
    assert evaluate_match(parameters(rooms=0, max_price=None, preferred_area=None), property_values(rooms=0))['score'] == 100
    assert evaluate_match(parameters(rooms=None, preferred_area=None, min_price=0, max_price=None), property_values())['ranking_available'] is False


def test_weights_and_district_normalization():
    result = evaluate_match(parameters(preferred_area='  ХАМОвники  '), property_values(), {'district': 1, 'rooms': 0, 'budget': 0})
    assert result['score'] == 100 and len(result['criteria']) == 1
    assert evaluate_match(parameters(preferred_area='Алёшкино'), property_values(district='Алешкино'), {'district': 1})['score'] == 100
    with pytest.raises(ValueError):
        evaluate_match(parameters(), property_values(), {'rooms': -1})


def test_matching_page_lists_scoped_objects_and_calendar_context(client, login):
    login()
    response = client.get('/dashboard/requests/1/matches')
    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert '96.48 / 100' in page and 'data-property-id="1"' in page
    assert 'data-property-id="5"' not in page  # The 22.4M object exceeds 22M.
    assert 'data-property-id="7"' not in page
    assert '/dashboard/calendar?' in page and 'request_id=1' in page and 'property_id=1' in page and 'client_id=1' in page
    assert 'вероятность покупки' in page
    assert client.get('/dashboard/requests/1/matches?sort=unknown').status_code == 400


def test_saved_pair_persists_and_repeated_save_does_not_overwrite_history(app, client, login):
    login()
    assert client.post('/dashboard/requests/1/matches/save/1').status_code == 302
    with app.app_context():
        match = db.session.scalar(select(RequestMatch))
        first_snapshot = match.snapshot_json
        first_history = match.history_json
        assert match.score == Decimal('96.48') and match.status == 'saved'
        assert json.loads(first_history)[0]['actor_id'] is not None
        assert json.loads(first_snapshot)['request_parameters']['max_price'] == '22000000.00'
    assert client.post('/dashboard/requests/1/matches/save/1').status_code == 302
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(RequestMatch)) == 1
        match = db.session.scalar(select(RequestMatch))
        assert match.snapshot_json == first_snapshot and match.history_json == first_history
    assert 'data-saved-match-id=' in client.get('/dashboard/requests/1/matches').get_data(as_text=True)


def test_refresh_keeps_previous_score_and_snapshot(app, client, login):
    login()
    client.post('/dashboard/requests/1/matches/save/1')
    with app.app_context():
        match_id = db.session.scalar(select(RequestMatch.id))
        db.session.get(Property, 1).price = Decimal('20000000')
        db.session.commit()
    page = client.get('/dashboard/requests/1/matches').get_data(as_text=True)
    assert '96.48 / 100' in page and '97.73 / 100' in page
    assert 'Сохраненная оценка не перезаписана' in page
    with app.app_context():
        assert db.session.get(RequestMatch, match_id).score == Decimal('96.48')
    assert client.post(f'/dashboard/requests/1/matches/refresh/{match_id}').status_code == 302
    with app.app_context():
        match = db.session.get(RequestMatch, match_id)
        assert match.score == Decimal('97.73')
        history = json.loads(match.history_json)
        assert len(history) == 2 and history[1]['action'] == 'refreshed'
        assert history[1]['previous_snapshot']['score'] == '96.48'
    client.post(f'/dashboard/requests/1/matches/refresh/{match_id}')
    with app.app_context():
        assert len(json.loads(db.session.get(RequestMatch, match_id).history_json)) == 2


def test_ineligible_saved_variant_remains_and_can_be_explicitly_refreshed(app, client, login):
    login()
    client.post('/dashboard/requests/1/matches/save/1')
    with app.app_context():
        match_id = db.session.scalar(select(RequestMatch.id))
        db.session.get(Property, 1).status = 'sold'
        db.session.commit()
    page = client.get('/dashboard/requests/1/matches').get_data(as_text=True)
    assert 'data-property-id="1"' not in page and f'data-saved-match-id="{match_id}"' in page
    assert '0.00 / 100' in page
    client.post(f'/dashboard/requests/1/matches/refresh/{match_id}')
    with app.app_context():
        match = db.session.get(RequestMatch, match_id)
        assert match.score == 0 and match.status == 'saved'
        assert len(json.loads(match.history_json)) == 2


def test_change_requirements_recalculates_current_not_saved_scores(app, client, login):
    login()
    client.post('/dashboard/requests/1/matches/save/1')
    data = {'deal_type': 'sale', 'property_type': 'flat', 'min_price': '', 'max_price': '23000000', 'rooms': '3', 'preferred_area': 'Арбат'}
    assert client.post('/dashboard/requests/1/matches/requirements', data=data).status_code == 302
    with app.app_context():
        record = db.session.get(Request, 1)
        assert record.rooms == 3 and record.max_price == Decimal('23000000') and record.client_id == 1
        match = db.session.scalar(select(RequestMatch))
        assert match.score == Decimal('96.48') and len(json.loads(match.history_json)) == 1
    page = client.get('/dashboard/requests/1/matches').get_data(as_text=True)
    assert 'data-property-id="5"' in page and 'Сохраненная оценка не перезаписана' in page
    client.post('/dashboard/requests/1/matches/requirements', data=data | {'min_price': '30000000'})
    with app.app_context():
        assert db.session.get(Request, 1).min_price is None


def test_decision_comment_is_validated_escaped_and_recorded(app, client, login):
    login()
    client.post('/dashboard/requests/1/matches/save/1')
    with app.app_context():
        match_id = db.session.scalar(select(RequestMatch.id))
    route = f'/dashboard/requests/1/matches/edit/{match_id}'
    assert client.post(route, data={'status': 'rejected', 'agent_comment': '<script>no()</script>'}).status_code == 302
    with app.app_context():
        match = db.session.get(RequestMatch, match_id)
        assert match.status == 'rejected' and len(json.loads(match.history_json)) == 2
    assert '&lt;script&gt;' in client.get('/dashboard/requests/1/matches').get_data(as_text=True)
    client.post(route, data={'status': 'invalid', 'agent_comment': 'bad'})
    with app.app_context():
        assert db.session.get(RequestMatch, match_id).status == 'rejected'


def test_matching_access_denies_clients_foreign_requests_and_properties(app, client, login):
    assert client.get('/dashboard/requests/1/matches').status_code == 302
    login('client')
    assert client.get('/dashboard/requests/1/matches').status_code == 403
    client.post('/logout')
    login()
    assert client.post('/dashboard/requests/1/matches/save/7').status_code == 403
    with app.app_context():
        second = db.session.scalar(select(Agent).where(Agent.email == 'second@example.org'))
        foreign = Request(client_id=1, agent_id=second.id, deal_type='sale', property_type='land')
        unassigned = Request(client_id=1, deal_type='sale', property_type='flat')
        db.session.add_all([foreign, unassigned])
        db.session.commit()
        foreign_id, free_id = foreign.id, unassigned.id
    assert client.get(f'/dashboard/requests/{foreign_id}/matches').status_code == 403
    assert client.get(f'/dashboard/requests/{free_id}/matches').status_code == 403
    assert client.post(f'/dashboard/requests/{foreign_id}/matches/requirements', data={}).status_code == 403
    client.post('/logout')
    login('admin')
    assert 'data-property-id="7"' in client.get(f'/dashboard/requests/{foreign_id}/matches').get_data(as_text=True)


def test_saved_property_reassignment_does_not_leak_details(app, client, login):
    login()
    client.post('/dashboard/requests/1/matches/save/1')
    with app.app_context():
        match_id = db.session.scalar(select(RequestMatch.id))
        db.session.get(Property, 1).agent_id = db.session.scalar(select(Agent.id).where(Agent.email == 'second@example.org'))
        db.session.commit()
    page = client.get('/dashboard/requests/1/matches').get_data(as_text=True)
    assert 'data-property-id="1"' not in page and 'data-saved-match-id=' not in page
    assert client.post(f'/dashboard/requests/1/matches/refresh/{match_id}').status_code == 403
    assert client.post(f'/dashboard/requests/1/matches/edit/{match_id}', data={'status': 'saved'}).status_code == 403


def test_no_results_and_ineligible_save_does_not_create_pair(app, client, login):
    login()
    assert client.post('/dashboard/requests/1/matches/save/5').status_code == 302
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(RequestMatch)) == 0
        db.session.get(Request, 1).max_price = Decimal('1')
        db.session.commit()
    assert 'Подходящих объектов нет' in client.get('/dashboard/requests/1/matches').get_data(as_text=True)


def test_matching_mutations_require_csrf(app, client, login):
    login()
    app.config['WTF_CSRF_ENABLED'] = True
    assert client.post('/dashboard/requests/1/matches/save/1').status_code == 400
    page = client.get('/dashboard/requests/1/matches').get_data(as_text=True)
    token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page).group(1)
    assert client.post('/dashboard/requests/1/matches/save/1', data={'csrf_token': token}).status_code == 302

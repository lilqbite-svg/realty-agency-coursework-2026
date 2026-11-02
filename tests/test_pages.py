import re
from urllib.parse import urlsplit

import pytest


@pytest.mark.parametrize('role,paths', [
    (None, ['/', '/catalog', '/catalog?type=land', '/property/1', '/request', '/my-requests', '/my-viewings', '/register', '/login']),
    ('realtor', ['/dashboard', '/dashboard/properties', '/dashboard/properties/new', '/dashboard/properties/1/edit', '/dashboard/clients', '/dashboard/clients/new', '/dashboard/clients/1', '/dashboard/clients/1/edit', '/dashboard/requests', '/dashboard/requests/1/edit', '/dashboard/viewings', '/dashboard/viewings/new', '/dashboard/deals', '/dashboard/deals/new', '/profile', '/profile/password']),
    ('admin', ['/admin', '/admin/users/new', '/admin/users/2/edit', '/dashboard']),
    ('client', ['/profile', '/favorites', '/my-requests', '/requests/1']),
])
def test_pages_and_internal_links(client, login, role, paths):
    if role:
        login(role)
    for path in paths:
        response = client.get(path)
        assert response.status_code == 200, path
        for target in re.findall(r'<(?:a|link)[^>]*href="([^"]+)"', response.get_data(as_text=True)):
            parsed = urlsplit(target.replace('&amp;', '&'))
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            linked = client.get(parsed.path + ('?' + parsed.query if parsed.query else ''))
            assert linked.status_code in (200, 302), target


def test_error_pages_do_not_expose_traceback(app, client):
    def fail():
        raise RuntimeError('private-debug-information')
    app.add_url_rule('/test-error', view_func=fail)
    app.config.update(TESTING=False, PROPAGATE_EXCEPTIONS=False)
    assert 'Ошибка 404' in client.get('/does-not-exist').get_data(as_text=True)
    response = client.get('/test-error')
    assert response.status_code == 500
    assert 'Ошибка 500' in response.get_data(as_text=True)
    assert 'private-debug-information' not in response.get_data(as_text=True)

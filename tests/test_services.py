import re
import urllib.request

import pytest

from jaraco import services


class TestHTTPStatus:
    def http_error(self, url):
        raise urllib.error.HTTPError(
            url, code=400, msg='Bad Request', hdrs=dict(), fp=None
        )

    def test_HTTPError(self, monkeypatch):
        monkeypatch.setattr(urllib.request, 'urlopen', self.http_error)
        monkeypatch.setattr('portend.occupied', lambda *a, **kw: None)
        status = services.HTTPStatus()
        status.port = 80
        with pytest.raises(services.ServiceNotRunningError) as trap:
            status.wait_for_http(timeout=0)
        msg = str(trap.value)
        assert "Received status 400 from " in msg
        assert re.search('<jaraco.services.HTTPStatus object .*>', msg)
        assert ' on localhost:80' in msg

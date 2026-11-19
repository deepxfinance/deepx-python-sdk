# Modified by DeepX to redact transport failure details.
import json

import requests

from ..._rpc_transport import _safe_http_endpoint
from .base import TransportBase
from ..exceptions import SubstrateRequestException, ConfigurationError


class HttpTransport(TransportBase):
    def __init__(self, url, headers=None, debug_fn=None):
        super().__init__(debug_fn=debug_fn)
        self.url = url
        self.headers = headers or {
            'content-type': "application/json",
            'cache-control': "no-cache"
        }
        self.session = requests.Session()

    def rpc_request(self, payload, result_handler=None):
        if result_handler:
            raise ConfigurationError("Result handlers only available for websockets (ws://) connections")

        try:
            response = self.session.request("POST", self.url, data=json.dumps(payload), headers=self.headers)
        except (requests.RequestException, OSError, ValueError) as exc:
            raise SubstrateRequestException(
                f"RPC request failed: POST {_safe_http_endpoint(self.url)}: {type(exc).__name__}"
            ) from None

        if response.status_code != 200:
            raise SubstrateRequestException(
                "RPC request failed with HTTP status code {}".format(response.status_code))

        json_body = response.json()

        if 'error' in json_body:
            raise SubstrateRequestException(json_body['error'])

        return json_body

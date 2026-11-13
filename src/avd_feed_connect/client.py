""":class:`AvdClient` — the single object the GUI and CLI talk to.

It wires together token storage, the OAuth flows, feed discovery and the RDP
launcher, and exposes the per-session state (``upn``, ``last_refresh_error``)
that used to be module globals. Callers hold one instance and read/update its
``upn`` in one place.
"""

from . import clouds
from .auth import OAuthClient, TokenStore
from .feed import FeedClient
from .rdp import RdpLauncher, download_rdp


class AvdClient:
    """Feed + connect operations for one signed-in session."""

    def __init__(self, token_store=None, output_dir=None, cloud=None):
        self.tokens = token_store or TokenStore()
        self.output_dir = output_dir
        # One client serves one account, so it serves one Azure cloud: the
        # authority, the AVD scope, the feed endpoint and the token-trust host
        # list all follow from it.
        self._cloud = cloud or clouds.resolve()
        self.oauth = OAuthClient(self.tokens, cloud=self._cloud)
        self.feed = FeedClient(cloud=self._cloud)
        self.rdp = RdpLauncher(cloud=self._cloud)

    # -- session state (formerly module globals) ----------------------------

    @property
    def cloud(self):
        return self._cloud

    @cloud.setter
    def cloud(self, value):
        """Re-point this account at another cloud.

        Assigning here has to reach the sub-objects too: each holds its own
        reference so it can answer without a back-pointer, so setting only the
        attribute would leave the OAuth authority and the feed endpoint on the
        old cloud — a silent, confusing half-switch.
        """
        self._cloud = value or clouds.resolve()
        self.oauth.cloud = self._cloud
        self.feed.cloud = self._cloud
        self.rdp.cloud = self._cloud

    @property
    def upn(self):
        return self.oauth.upn

    @upn.setter
    def upn(self, value):
        self.oauth.upn = value

    @property
    def last_refresh_error(self):
        return self.oauth.last_refresh_error

    # -- auth ---------------------------------------------------------------

    def get_token(self, verbose=True, use_device_code=False):
        return self.oauth.get_token(verbose=verbose, use_device_code=use_device_code)

    def set_upn_from_token(self, tok):
        return self.oauth.set_upn_from_token(tok)

    def has_token(self):
        return self.tokens.has()

    def load_token_record(self):
        return self.tokens.load()

    def save_refresh_token(self, refresh_token):
        return self.tokens.save(refresh_token)

    def clear_token_cache(self):
        return self.tokens.clear()

    # -- feed / connect -----------------------------------------------------

    def enumerate_feed(self, token):
        return self.feed.enumerate(token)

    def download_rdp(self, token, res):
        return download_rdp(token, res, output_dir=self.output_dir,
                            cloud=self._cloud)

    def launch(self, path):
        return self.rdp.launch(path, self.upn)

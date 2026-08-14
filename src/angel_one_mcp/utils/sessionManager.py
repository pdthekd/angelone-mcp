from datetime import datetime, timedelta
from typing import Optional
from SmartApi import SmartConnect
import pyotp
from dotenv import load_dotenv
import os
from pathlib import Path
from .retry_helper_decorator import retry_with_backoff

# Force the working directory to the project root to prevent SmartApi's hardcoded
# relative "logs" folder from attempting to write to protected Windows directories.
# sessionManager.py lives at src/angel_one_mcp/utils/, so the project root is 4
# parents up (utils -> angel_one_mcp -> src -> root), not 3.
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
os.chdir(str(BASE_DIR))

class SessionManager:
    def __init__(self):
        load_dotenv()
        self.smart_api: Optional[SmartConnect] = None
        self.refresh_token: Optional[str] = None
        self.session_expiry: Optional[datetime] = None
        self.api_key = os.environ.get('api_key')
        self.username = os.environ.get('username')
        self.pwd = os.environ.get('pwd')
        self.token = os.environ.get('token')
        self.threshold = os.environ.get('threshold')

    @retry_with_backoff(max_retries=3, base_delay=2)
    def _api_call(self, method_name, *args, **kwargs):
        method = getattr(self.smart_api, method_name)
        return method(*args, **kwargs)
    
    def create_session(self):
        # Re-anchor the working directory immediately before instantiating
        # SmartConnect, since it writes to a "logs" folder relative to the
        # process cwd at construction time, and cwd is mutable global state
        # that other code could have changed since the module was imported.
        os.chdir(str(BASE_DIR))
        self.smart_api = SmartConnect(self.api_key)
        totp = pyotp.TOTP(self.token).now()
        data = self._api_call('generateSession', self.username, self.pwd, totp)
        
        if not data.get('status'):
            raise Exception("Session generation failed")
        
        self.refresh_token = data['data']['refreshToken']
        self.smart_api.generateToken(self.refresh_token)
        self.session_expiry = datetime.now() + timedelta(hours=6)
        return self.smart_api
    
    def refresh_session(self):
        if not self.refresh_token or not self.smart_api:
            return self.create_session()
        
        try:
            self.smart_api.generateToken(self.refresh_token)
            self.session_expiry = datetime.now() + timedelta(hours=6)
            return self.smart_api
        except:
            return self.create_session()
        
    def get_api(self):
        if not self.smart_api or not self.session_expiry or datetime.now() >= self.session_expiry:
            return self.create_session()
        return self.smart_api
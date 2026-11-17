from __future__ import annotations

import os
import logging
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

try:
    from google.cloud import secretmanager
    SECRET_MANAGER_AVAILABLE = True
except ImportError:
    SECRET_MANAGER_AVAILABLE = False

logger = logging.getLogger(__name__)

class Config(BaseSettings):
    gemini_api_key: Optional[str] = None
    project_id: Optional[str] = None

    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8')

def get_secret(secret_id: str, project_id: Optional[str] = None) -> str:
    # 1. Check environment variable / .env first
    config = Config()
    val = getattr(config, secret_id.lower(), None)
    if val:
        return val

    # 2. Fetch from Google Cloud Secret Manager
    if SECRET_MANAGER_AVAILABLE and project_id:
        try:
            client = secretmanager.SecretManagerServiceClient()
            name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
            response = client.access_secret_version(request={"name": name})
            return response.payload.data.decode("UTF-8")
        except Exception as e:
            logger.warning(f"Failed to access secret {secret_id} from Secret Manager: {e}")
            
    raise ValueError(f"Secret {secret_id} not found in environment or Secret Manager.")

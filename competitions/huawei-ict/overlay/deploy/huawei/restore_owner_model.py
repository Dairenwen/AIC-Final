#!/usr/bin/env python3
"""Re-encrypt only the explicitly selected owner's account model; no API calls."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(args.env_file, override=True)
    import os
    from flask import Flask
    from sqlalchemy import select
    from app.config import Config
    from app.extensions import db
    from app.models import User, ModelConfig
    from app.api.auth import normalize_phone
    from app.services.model_credentials import encrypt_api_key
    phone = normalize_phone(os.environ.get('MIGRATION_OWNER_PHONE'))
    if not phone or not Config.USER_API_MODEL_API_KEY or not Config.MODEL_CONFIG_ENCRYPTION_KEY:
        parser.error('Specify MIGRATION_OWNER_PHONE and model credentials in the private env file.')
    if args.apply and sys.platform != 'linux':
        parser.error('Apply on the restored Linux target only.')
    app = Flask('owner-model-migration')
    app.config.from_object(Config)
    db.init_app(app)
    with app.app_context():
        user = db.session.scalar(select(User).where(User.phone == phone))
        if user is None:
            parser.error('Specified owner does not exist in the restored database.')
        items = db.session.scalars(select(ModelConfig).where(
            ModelConfig.owner_user_id == user.id, ModelConfig.config_scope == 'USER',
            ModelConfig.is_account_api.is_(True), ModelConfig.deleted_at.is_(None),
        )).all()
        if len(items) != 1:
            parser.error('Expected exactly one existing account API model; inspect owner configuration.')
        item = items[0]
        if args.apply:
            item.encrypted_api_key, item.key_nonce, item.key_version = encrypt_api_key(Config.USER_API_MODEL_API_KEY)
            item.key_last_four = Config.USER_API_MODEL_API_KEY[-4:]
            item.base_url = Config.USER_API_MODEL_BASE_URL.rstrip('/')
            item.model_name = Config.USER_API_MODEL_NAME
            item.provider_code = 'openai_compatible'
            item.settings = {**(item.settings or {}), 'timeout_seconds': Config.USER_API_MODEL_TIMEOUT_SECONDS,
                             'max_output_tokens': Config.USER_API_MODEL_MAX_OUTPUT_TOKENS}
            item.status = 'DRAFT'
            item.last_verified_at = None
            item.last_error_code = None
            db.session.commit()
            print('Owner account model saved as DRAFT. Original UI verification is still required before use.')
        else:
            db.session.rollback()
            print('Exactly one owner account model found. Read-only inspection; no changes or API calls.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        # SQL/driver errors can contain connection details or bound credentials.
        print(f'Owner model migration failed: {type(exc).__name__}. No credentials printed.', file=sys.stderr)
        raise SystemExit(1)

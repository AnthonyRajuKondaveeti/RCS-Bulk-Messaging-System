import secrets
import hashlib
import sys
import os
from uuid import uuid4
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine, Table, MetaData, insert, JSON
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

# Add project root to sys.path
sys.path.append(os.getcwd())

from apps.core.config import get_settings

def seed():
    print("--- RCS Platform Development Seed (Sync/Psycopg2) ---")
    
    settings = get_settings()
    db_config = settings.database
    
    # Force port 5433 if it's currently 5432 which seems to be the default failing one
    port = db_config.port if db_config.port != 5432 else 5433
    
    sync_url = f"postgresql://{db_config.username}:{db_config.password}@{db_config.host}:{port}/{db_config.database}"
    
    print(f"Connecting to: {db_config.host}:{port}/{db_config.database}")
    
    try:
        engine = create_engine(sync_url)
        Session = sessionmaker(bind=engine)
        session = Session()
        
        # Test connection
        session.execute(sa.text("SELECT 1"))
        print("Connected successfully!")
        
        # Check if seed data already exists (by looking for the dev template)
        existing = session.execute(
            sa.text("SELECT COUNT(*) FROM templates WHERE external_template_id = 'mock-template-001'")
        ).scalar()
        
        if existing and existing > 0:
            print("\n⚠️  Seed data already exists!")
            print("The database already contains a template with external_template_id='mock-template-001'.")
            print("To re-seed, first clear the database or use a fresh database.")
            print("\nExiting without making changes (idempotent).")
            sys.exit(0)
        
        metadata = MetaData()
        # Define minimal tables
        api_keys = Table("api_keys", metadata, sa.Column("id", PG_UUID), sa.Column("key_hash", sa.Text), sa.Column("user_id", PG_UUID), sa.Column("tenant_id", PG_UUID), sa.Column("is_active", sa.Boolean), sa.Column("created_at", sa.DateTime))
        templates = Table("templates", metadata, sa.Column("id", PG_UUID), sa.Column("tenant_id", PG_UUID), sa.Column("name", sa.Text), sa.Column("status", sa.Text), sa.Column("content", sa.Text), sa.Column("external_template_id", sa.Text), sa.Column("rcs_type", sa.Text), sa.Column("created_at", sa.DateTime), sa.Column("updated_at", sa.DateTime))
        audiences = Table("audiences", metadata, sa.Column("id", PG_UUID), sa.Column("tenant_id", PG_UUID), sa.Column("name", sa.Text), sa.Column("audience_type", sa.Text), sa.Column("status", sa.Text), sa.Column("total_contacts", sa.Integer), sa.Column("created_at", sa.DateTime), sa.Column("updated_at", sa.DateTime))
        audience_contacts = Table("audience_contacts", metadata, sa.Column("id", PG_UUID), sa.Column("audience_id", PG_UUID), sa.Column("phone_number", sa.Text), sa.Column("variables", JSON), sa.Column("created_at", sa.DateTime))

        # Generate Credentials
        tenant_id = uuid4()
        raw_key = f"rcs_dev_{secrets.token_hex(16)}"
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        user_id = uuid4()
        
        # 1. Create API Key
        print(f"Creating API key for tenant {tenant_id}...")
        session.execute(
            api_keys.insert().values(
                id=uuid4(),
                key_hash=key_hash,
                user_id=user_id,
                tenant_id=tenant_id,
                is_active=True,
                created_at=sa.func.now()
            )
        )
        
        # 2. Create Template
        template_id = uuid4()
        print(f"Creating template {template_id}...")
        session.execute(
            templates.insert().values(
                id=template_id,
                tenant_id=tenant_id,
                name="Dev Welcome Template",
                status="approved",
                content="Hello {{1}}!",
                external_template_id="mock-template-001",
                rcs_type="BASIC",
                created_at=sa.func.now(),
                updated_at=sa.func.now()
            )
        )
        
        # 3. Create Audience
        audience_id = uuid4()
        print(f"Creating audience {audience_id}...")
        session.execute(
            audiences.insert().values(
                id=audience_id,
                tenant_id=tenant_id,
                name="Dev Audience",
                audience_type="static",
                status="ready",
                total_contacts=2,
                created_at=sa.func.now(),
                updated_at=sa.func.now()
            )
        )
        
        # 4. Create Audience Contacts
        print("Adding contacts to audience...")
        contacts = [
            ("+919876543210", ["Test User"]),
            ("+919876543211", ["Test User 2"])
        ]
        
        for phone, variables in contacts:
            session.execute(
                audience_contacts.insert().values(
                    id=uuid4(),
                    audience_id=audience_id,
                    phone_number=phone,
                    variables=variables,
                    created_at=sa.func.now()
                )
            )
        
        session.commit()
            
        print("\n--- Seed Summary ---")
        print(f"API Key:      {raw_key}")
        print(f"Tenant ID:    {tenant_id}")
        print(f"Template ID:  {template_id}")
        print(f"Audience ID:  {audience_id}")
        print("-" * 30)
        
    except Exception as e:
        print(f"\nSEED FAILED: {e}")
        try:
            session.rollback()
        except:
            pass
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        try:
            session.close()
        except:
            pass
        try:
            engine.dispose()
        except:
            pass

if __name__ == "__main__":
    seed()

#!/usr/bin/env python3
"""
End-to-End Test with 20+ Phone Numbers

Tests the complete RCS platform flow:
1. Creates template, audience with 20+ contacts
2. Creates and activates campaign
3. Monitors worker processing
4. Shows delivery results

Usage:
    # With mock (no real sending):
    USE_MOCK_AGGREGATOR=true python tests/test_e2e_20_numbers.py
    
    # With real RCS (requires credentials):
    python tests/test_e2e_20_numbers.py
"""

import asyncio
import sys
import time
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from apps.core.config import get_settings
from apps.adapters.db.postgres import Database
from apps.adapters.db.unit_of_work import SQLAlchemyUnitOfWork
from apps.adapters.queue.rabbitmq import RabbitMQAdapter
from apps.adapters.aggregators.mock_adapter import MockAdapter
from apps.adapters.aggregators.rcssms_adapter import RcsSmsAdapter
from apps.core.aggregators.factory import AggregatorFactory
from apps.core.domain.template import Template, TemplateVariable
from apps.core.domain.audience import Audience, Contact
from apps.core.domain.campaign import CampaignType, Priority
from apps.core.services.campaign_service import CampaignService
from apps.core.observability.logging import setup_logging


def print_header(text: str, char: str = "="):
    """Print formatted header"""
    width = 80
    print("\n" + char * width)
    print(f"{text:^{width}}")
    print(char * width)


def print_section(text: str):
    """Print section header"""
    print(f"\n{'─' * 80}")
    print(f"▶ {text}")
    print(f"{'─' * 80}")


async def wait_for_processing(
    db: Database,
    campaign_id,
    expected_messages: int,
    timeout_seconds: int = 120
) -> bool:
    """
    Wait for all campaign messages to be processed.
    
    Returns True if all processed, False if timeout.
    """
    print(f"\n⏳ Waiting for {expected_messages} messages to be processed...")
    print(f"   Timeout: {timeout_seconds}s")
    
    start_time = time.time()
    last_status = {}
    
    while time.time() - start_time < timeout_seconds:
        async with db.session() as session:
            uow = SQLAlchemyUnitOfWork(session)
            
            # Get campaign
            campaign = await uow.campaigns.get_by_id(campaign_id)
            if not campaign:
                print(f"\n❌ Campaign not found!")
                return False
            
            # Count message statuses
            from sqlalchemy import text, select
            result = await session.execute(
                text("""
                    SELECT status, COUNT(*) as count
                    FROM messages
                    WHERE campaign_id = :cid
                    GROUP BY status
                """),
                {"cid": str(campaign_id)}
            )
            
            status_counts = {row[0]: row[1] for row in result.fetchall()}
            
            # Check if different from last status
            if status_counts != last_status:
                last_status = status_counts
                print(f"\n   Status update:")
                for status_name, count in sorted(status_counts.items()):
                    icon = {
                        'PENDING': '⏸️ ',
                        'SENT': '✅',
                        'DELIVERED': '✅',
                        'FAILED': '❌',
                        'READ': '👁️ '
                    }.get(status_name, '  ')
                    print(f"      {icon} {status_name}: {count}")
            
            # Check if all processed (no pending)
            pending = status_counts.get('PENDING', 0)
            total = sum(status_counts.values())
            
            if pending == 0 and total >= expected_messages:
                print(f"\n✅ All {total} messages processed!")
                return True
            
            # Progress indicator
            elapsed = int(time.time() - start_time)
            sys.stdout.write(f"\r   [{elapsed}s] Pending: {pending}/{total}   ")
            sys.stdout.flush()
            
            await asyncio.sleep(2)
    
    print(f"\n⚠️  Timeout after {timeout_seconds}s")
    return False


async def main():
    """Run end-to-end test with 20+ numbers"""
    
    print_header("🧪 RCS PLATFORM - END-TO-END TEST (20+ NUMBERS)", "═")
    
    settings = get_settings()
    
    # Check if using mock
    use_mock = settings.use_mock_aggregator
    if use_mock:
        print("\n✅ Using MOCK AGGREGATOR (no real messages sent)")
    else:
        print("\n⚠️  Using REAL RCS AGGREGATOR (messages will be sent!)")
        print(f"   RCS Username: {settings.aggregator.username}")
        confirm = input("\n   Continue? (yes/no): ")
        if confirm.lower() != 'yes':
            print("   Aborted.")
            return 1
    
    # Setup logging
    setup_logging(
        log_level="INFO",
        log_file="logs/test_e2e_20_numbers.log"
    )
    print(f"\n📝 Logging to: logs/test_e2e_20_numbers.log")
    
    # Connect to infrastructure
    print_section("STEP 1: Connecting to Infrastructure")
    
    db = Database()
    await db.connect()
    print("   ✅ Database connected")
    
    queue = RabbitMQAdapter(url=settings.rabbitmq.url)
    await queue.connect()
    print("   ✅ RabbitMQ connected")
    
    # Get aggregator

    aggregator = AggregatorFactory.create_aggregator()
    await aggregator.connect()
    agg_type = "Mock" if use_mock else "RCS"
    print(f"   ✅ {agg_type} aggregator initialized")
    
    try:
        tenant_id = uuid4()
        
        # ──────────────────────────────────────────────────────────────────
        # STEP 2: Create Template
        # ──────────────────────────────────────────────────────────────────
        print_section("STEP 2: Creating Template")
        
        async with db.session() as session:
            uow = SQLAlchemyUnitOfWork(session)
            
            template = Template.create(
                tenant_id=tenant_id,
                name="E2E Test Template - 20 Numbers",
                content="Hello {{name}}, this is test message #{{test_id}}!",
                variables=["name", "test_id"],
            )
            
            # Mock: auto-approve
            if use_mock:
                template.approve_template("mock-template-e2e-20")
            
            await uow.templates.save(template)
            await uow.commit()  # Commit changes before session closes
            template_id = template.id
            
            print(f"   ✅ Template created: {template_id}")
            print(f"      Name: {template.name}")
            print(f"      Status: {template.status}")
            print(f"      External ID: {template.external_template_id or 'Not set'}")
        
        # ──────────────────────────────────────────────────────────────────
        # STEP 3: Create Audience with 25 contacts
        # ──────────────────────────────────────────────────────────────────
        print_section("STEP 3: Creating Audience with 25 Test Contacts")
        
        # Generate 25 test phone numbers
        test_contacts = []
        for i in range(25):
            phone = f"+91987654{i:04d}"  # +919876540000 to +919876540024
            variables = {
                "name": f"Test User {i+1}",
                "test_id": str(i+1)
            }
            test_contacts.append(Contact(
                phone_number=phone,
                metadata=variables
            ))
        
        async with db.session() as session:
            uow = SQLAlchemyUnitOfWork(session)
            
            audience = Audience.create(
                tenant_id=tenant_id,
                name="E2E Test Audience - 25 Numbers",
            )
            audience.add_contacts(test_contacts)
            await uow.audiences.save(audience)
            await uow.commit()  # Commit changes before session closes
            audience_id = audience.id
            
            print(f"   ✅ Audience created: {audience_id}")
            print(f"      Name: {audience.name}")
            print(f"      Contacts: {len(test_contacts)}")
            print(f"      Status: {audience.status}")
            
            # Show sample contacts
            print(f"\n   📋 Sample contacts:")
            for i, contact in enumerate(test_contacts[:5], 1):
                print(f"      {i}. {contact.phone_number} - {contact.metadata}")
            print(f"      ... and {len(test_contacts) - 5} more")
        
        # ──────────────────────────────────────────────────────────────────
        # STEP 4: Create Campaign
        # ──────────────────────────────────────────────────────────────────
        print_section("STEP 4: Creating Campaign")
        
        async with db.session() as session:
            uow = SQLAlchemyUnitOfWork(session)
            campaign_service = CampaignService(uow, queue)
            
            campaign = await campaign_service.create_campaign(
                tenant_id=tenant_id,
                name="E2E Test Campaign - 25 Numbers",
                template_id=template_id,
                campaign_type=CampaignType.PROMOTIONAL,
                priority=Priority.HIGH,
            )
            
            # Add audience to campaign
            campaign.add_audience(audience_id, len(test_contacts))
            await uow.campaigns.save(campaign)
            await uow.commit()  # Commit changes before session closes
            
            campaign_id = campaign.id
            
            print(f"   ✅ Campaign created: {campaign_id}")
            print(f"      Name: {campaign.name}")
            print(f"      Status: {campaign.status}")
            print(f"      Recipients: {campaign.recipient_count}")
        
        # ──────────────────────────────────────────────────────────────────
        # STEP 5: Activate Campaign
        # ──────────────────────────────────────────────────────────────────
        print_section("STEP 5: Activating Campaign")
        
        print("   ⚡ Activating campaign...")
        print("   This will queue messages for sending by workers...")
        
        async with db.session() as session:
            uow = SQLAlchemyUnitOfWork(session)
            campaign_service = CampaignService(uow, queue)
            
            campaign = await campaign_service.activate_campaign(campaign_id)
            
            print(f"   ✅ Campaign activated!")
            print(f"      Status: {campaign.status}")
        
        # ──────────────────────────────────────────────────────────────────
        # STEP 6: Wait for Processing
        # ──────────────────────────────────────────────────────────────────
        print_section("STEP 6: Monitoring Message Processing")
        
        print("\n   💡 NOTE: This requires workers to be running!")
        print("      Terminal 1: python -m apps.workers.entrypoints.orchestrator")
        print("      Terminal 2: python -m apps.workers.entrypoints.dispatcher")
        print("      Or: python -m apps.workers.manager (runs all workers)")
        
        success = await wait_for_processing(
            db=db,
            campaign_id=campaign_id,
            expected_messages=len(test_contacts),
            timeout_seconds=180  # 3 minutes
        )
        
        # ──────────────────────────────────────────────────────────────────
        # STEP 7: Show Results
        # ──────────────────────────────────────────────────────────────────
        print_section("STEP 7: Final Results")
        
        async with db.session() as session:
            uow = SQLAlchemyUnitOfWork(session)
            
            # Get updated campaign
            campaign = await uow.campaigns.get_by_id(campaign_id)
            
            print(f"\n📊 Campaign Statistics:")
            print(f"   Campaign ID: {campaign.id}")
            print(f"   Status: {campaign.status}")
            print(f"   Recipients: {campaign.recipient_count}")
            print(f"   Messages sent: {campaign.stats.messages_sent}")
            print(f"   Messages delivered: {campaign.stats.messages_delivered}")
            print(f"   Messages failed: {campaign.stats.messages_failed}")
            print(f"   Delivery rate: {campaign.stats.delivery_rate:.1%}")
            
            # Get message details
            from sqlalchemy import text
            result = await session.execute(
                text("""
                    SELECT 
                        status,
                        COUNT(*) as count,
                        COUNT(*) * 100.0 / SUM(COUNT(*)) OVER () as percentage
                    FROM messages
                    WHERE campaign_id = :cid
                    GROUP BY status
                    ORDER BY count DESC
                """),
                {"cid": str(campaign_id)}
            )
            
            print(f"\n📈 Message Status Breakdown:")
            for row in result.fetchall():
                status_name, count, pct = row
                icon = {
                    'PENDING': '⏸️ ',
                    'SENT': '✅',
                    'DELIVERED': '✅',
                    'FAILED': '❌',
                    'READ': '👁️ '
                }.get(status_name, '  ')
                bar = '█' * int(pct / 5)  # 20 chars max
                print(f"   {icon} {status_name:12s}: {count:3d} ({pct:5.1f}%) {bar}")
            
            # Show sample messages
            result = await session.execute(
                text("""
                    SELECT recipient_phone, status, failure_reason
                    FROM messages
                    WHERE campaign_id = :cid
                    ORDER BY created_at
                    LIMIT 10
                """),
                {"cid": str(campaign_id)}
            )
            
            print(f"\n📱 Sample Messages:")
            for i, row in enumerate(result.fetchall(), 1):
                phone, status, reason = row
                status_icon = '✅' if status in ['SENT', 'DELIVERED'] else '❌' if status == 'FAILED' else '⏸️'
                reason_text = f" ({reason})" if reason else ""
                print(f"   {i:2d}. {phone} - {status_icon} {status}{reason_text}")
        
        # ──────────────────────────────────────────────────────────────────
        # Summary
        # ──────────────────────────────────────────────────────────────────
        print_header("✅ END-TO-END TEST COMPLETE", "═")
        
        if success:
            print("\n✅ All messages processed successfully!")
            print("\n📝 Next Steps:")
            if use_mock:
                print("   1. Test with real RCS credentials:")
                print("      - Remove USE_MOCK_AGGREGATOR from .env")
                print("      - Add RCS_USERNAME, RCS_PASSWORD, RCS_ID")
                print("      - Run this script again")
            else:
                print("   1. ✅ Ready for production!")
                print("   2. Check delivery reports in /api/v1/campaigns/{id}")
                print("   3. Monitor webhook deliveries")
            print(f"\n   Campaign ID: {campaign_id}")
            print(f"   View logs: logs/test_e2e_20_numbers.log")
            return 0
        else:
            print("\n⚠️  Processing incomplete or timed out")
            print("   Check:")
            print("   1. Are workers running?")
            print("   2. Check logs/test_e2e_20_numbers.log")
            print("   3. Check RabbitMQ queues: http://localhost:15672")
            return 1
    
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    finally:
        print("\n🧹 Cleaning up...")
        await queue.close()
        await db.disconnect()
        await aggregator.close()
        print("   ✅ Connections closed")


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)

#!/usr/bin/env python3
"""
Local Testing Script

Tests the RCS platform with mock adapter (no real messages sent).
This is safe to run anytime - no Gupshup account needed!

Usage:
    python tests/local/test_local.py
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from uuid import uuid4
from apps.core.domain.campaign import Campaign, CampaignType, Priority
from apps.core.domain.message import Message, MessageContent, RichCard, SuggestedAction
from apps.core.domain.template import Template
from apps.core.domain.audience import Audience, Contact
from apps.adapters.db.postgres import Database
from apps.adapters.db.unit_of_work import SQLAlchemyUnitOfWork
from apps.adapters.queue.rabbitmq import RabbitMQAdapter
from apps.adapters.aggregators.mock_adapter import MockAdapter
from apps.core.services.campaign_service import CampaignService
from apps.core.services.delivery_service import DeliveryService
from apps.core.observability.logging import setup_logging
from apps.core.config import get_settings


def print_header(title):
    """Print section header"""
    print("\n" + "="*70)
    print(f"  {title}")
    print("="*70)


async def test_domain_models():
    """Test 1: Domain models (no infrastructure needed)"""
    print_header("TEST 1: Domain Models")
    
    # Test Campaign
    campaign = Campaign.create(
        name="Test Campaign",
        tenant_id=uuid4(),
        template_id=uuid4(),
        campaign_type=CampaignType.PROMOTIONAL,
    )
    print(f"✅ Campaign created: {campaign.id}")
    print(f"   Status: {campaign.status}")
    
    campaign.add_audience(uuid4(), 100)
    
    # Test state transitions
    from datetime import datetime, timedelta, timezone
    scheduled_time = datetime.now(timezone.utc) + timedelta(hours=1)
    campaign.schedule(scheduled_time)
    print(f"✅ Campaign scheduled for: {scheduled_time}")
    
    campaign.activate()
    print(f"✅ Campaign activated: {campaign.status}")
    
    # Test Message with rich content
    content = MessageContent(
        text="Your order has shipped! 🚚",
        rich_card=RichCard(
            title="Track Package",
            description="Order #12345",
            media_url="https://example.com/package.jpg",
        ),
        suggestions=[
            SuggestedAction(
                type="url",
                text="Track",
                url="https://track.example.com/12345"
            )
        ]
    )
    
    message = Message.create(
        campaign_id=campaign.id,
        tenant_id=campaign.tenant_id,
        recipient_phone="+919876543210",
        content=content,
    )
    print(f"✅ RCS Message created: {message.id}")
    print(f"   Text: {message.content.text}")
    print(f"   Rich Card: {message.content.rich_card.title}")
    
    # Test SMS fallback conversion
    sms_text = message.content.to_sms_text()
    print(f"✅ SMS Fallback: {sms_text[:50]}...")
    
    print("\n✅ Domain model tests PASSED!")


async def test_with_mock_adapter():
    """Test 2: Services with mock adapter"""
    print_header("TEST 2: Services with Mock Adapter")
    
    settings = get_settings()
    
    # Setup
    db = Database()
    await db.connect()
    print("✅ Database connected")
    
    queue = RabbitMQAdapter(url=settings.rabbitmq.url)
    await queue.connect()
    print("✅ Queue connected")
    
    # Use MOCK adapter (no real sending!)
    mock_adapter = MockAdapter(success_rate=0.9)
    print("✅ Mock adapter initialized (90% success rate)")
    
    try:
        # Create campaign
        async with db.session() as session:
            uow = SQLAlchemyUnitOfWork(session)
            campaign_service = CampaignService(uow, queue)
            
            tenant_id = uuid4()
            template_id = uuid4()
            
            campaign = await campaign_service.create_campaign(
                tenant_id=tenant_id,
                name="Mock Test Campaign",
                template_id=template_id,
                campaign_type=CampaignType.PROMOTIONAL,
                priority=Priority.HIGH,
            )
            
            print(f"✅ Campaign created in database: {campaign.id}")
        
        # Send test messages
        test_phones = [
            "+919876543210",
            "+919876543211",
            "+919876543212",
            "+919876543213",
            "+919876543214",
        ]
        
        print(f"\n📤 Sending {len(test_phones)} test messages...")
        
        for i, phone in enumerate(test_phones, 1):
            async with db.session() as session:
                uow = SQLAlchemyUnitOfWork(session)
                delivery_service = DeliveryService(uow, mock_adapter, queue)
                
                content = MessageContent(text=f"Test message #{i}")
                
                message = await delivery_service.send_message(
                    campaign_id=campaign.id,
                    tenant_id=tenant_id,
                    recipient_phone=phone,
                    content=content,
                )
                
                print(f"   {i}. Message {message.id} -> {phone}")
        
        # Check capability
        print(f"\n🔍 Checking RCS capability...")
        capabilities = await mock_adapter.check_rcs_capability(test_phones)
        for cap in capabilities:
            status = "✓ RCS" if cap.rcs_enabled else "✗ SMS"
            print(f"   {status}: {cap.phone_number}")
        
        # Print mock adapter stats
        mock_adapter.print_stats()
        
        print("✅ Service tests PASSED!")
        
    finally:
        await queue.close()
        await db.disconnect()
        await mock_adapter.close()


async def test_end_to_end():
    """Test 3: Complete end-to-end flow"""
    print_header("TEST 3: End-to-End Flow")
    
    settings = get_settings()
    
    # Setup
    db = Database()
    await db.connect()
    
    queue = RabbitMQAdapter(url=settings.rabbitmq.url)
    await queue.connect()
    
    mock_adapter = MockAdapter(success_rate=0.95, delay=0.001)
    
    try:
        tenant_id = uuid4()
        template_id = uuid4()
        
        # Step 1: Create Campaign with real Template
        print("📋 Step 1: Creating campaign with Template & Audience...")
        async with db.session() as session:
            uow = SQLAlchemyUnitOfWork(session)
            campaign_service = CampaignService(uow, queue)
            
            # 1a. Create & Save Template
            template = Template.create(
                tenant_id=tenant_id,
                name="E2E Test Template",
                content="Hello {{name}}, here is your test message!",
                variables=["name"],
            )
            await uow.templates.save(template)
            print(f"   ✅ Template created: {template.id}")

            # 1b. Create & Save Audience (Contact List)
            test_contacts = []
            for i in range(5):
                test_contacts.append(Contact(phone_number=f"+919876543{str(i).zfill(3)}"))
            
            audience = Audience.create(
                tenant_id=tenant_id,
                name="E2E Test Audience",
            )
            audience.add_contacts(test_contacts)
            await uow.audiences.save(audience)
            print(f"   ✅ Audience created: {audience.id} ({len(test_contacts)} contacts)")
            
            # 1c. Create Campaign linked to Template
            campaign = await campaign_service.create_campaign(
                tenant_id=tenant_id,
                name="E2E Test Campaign",
                template_id=template.id,
                campaign_type=CampaignType.PROMOTIONAL,
            )
            
            # 1d. Attach Audience to Campaign
            campaign.add_audience(audience.id, len(test_contacts))
            await uow.campaigns.save(campaign)
            
            campaign_id = campaign.id
            print(f"   ✅ Campaign created: {campaign_id}")
        
        # Step 2: Send Messages & Record Opt-ins
        print("\n📨 Step 2: Sending messages...")
        messages = []
        # We'll use the audience contacts we just created
        for i in range(5):
            async with db.session() as session:
                uow = SQLAlchemyUnitOfWork(session)
                delivery_service = DeliveryService(uow, mock_adapter, queue)
                
                phone = f"+919876543{str(i).zfill(3)}"
                
                # IMPORTANT: Record Opt-In first!
                await uow.opt_outs.opt_in(phone, tenant_id)
                
                content = MessageContent(text=f"E2E test message #{i+1}")
                
                message = await delivery_service.send_message(
                    campaign_id=campaign_id,
                    tenant_id=tenant_id,
                    recipient_phone=phone,
                    content=content,
                )
                messages.append(message.id)
        
        print(f"   ✅ Sent {len(messages)} messages (with Opt-Ins)")
        
        # Step 3: Wait for Worker Processing (Real E2E)
        print("\n⏳ Step 3: Waiting for Worker to process messages...")
        print("   (Worker container should be picking up tasks from RabbitMQ)")
        
        max_retries = 30
        for attempt in range(max_retries):
            pending_count = 0
            async with db.session() as session:
                uow = SQLAlchemyUnitOfWork(session)
                
                # Check status of all messages
                all_processed = True
                current_statuses = []
                
                for msg_id in messages:
                    msg = await uow.messages.get_by_id(msg_id)
                    current_statuses.append(msg.status.value)
                    if msg.status.value == 'PENDING':
                        all_processed = False
                        pending_count += 1
                    elif msg.status.value == 'FAILED':
                        print(f"   ❌ Message {msg_id} FAILED! Reason: {msg.failure_reason}")
                        # Consider this processed but failed - maybe we should error out?
                        # For now, let's allow it to break the pending loop but verify success later
                        pass 
                
                if all_processed:
                    print(f"   ✅ All messages processed! Statuses: {set(current_statuses)}")
                    
                    # Verify they are actually SENT or DELIVERED
                    if any(s == 'FAILED' for s in current_statuses):
                        print("   ❌ Some messages FAILED!")
                        raise RuntimeError("Messages failed processing")
                        
                    break
                
                sys.stdout.write(f"\r   Waiting... ({attempt+1}/{max_retries}) Pending: {pending_count}   ")
                sys.stdout.flush()
                await asyncio.sleep(1)
        else:
            print("\n   ❌ Timeout waiting for workers!")
            raise RuntimeError("Workers did not process messages in time")
        
        # Step 4: Check Results
        print("\n📊 Step 4: Verifying results...")
        async with db.session() as session:
            uow = SQLAlchemyUnitOfWork(session)
            
            # Get campaign stats
            updated_campaign = await uow.campaigns.get_by_id(campaign_id)
            
            print(f"   Campaign Stats:")
            print(f"      Messages sent: {updated_campaign.stats.messages_sent}")
            print(f"      Messages delivered: {updated_campaign.stats.messages_delivered}")
            print(f"      Messages failed: {updated_campaign.stats.messages_failed}")
        
        # Mock adapter stats
        stats = mock_adapter.get_stats()
        print(f"\n   Mock Adapter:")
        print(f"      Total sent: {stats['total_sent']}")
        print(f"      Success rate: {stats['success_rate']*100:.1f}%")
        
        print("\n✅ End-to-end test PASSED!")
        
    finally:
        await queue.close()
        await db.disconnect()
        await mock_adapter.close()


async def main():
    """Run all tests"""
    print("\n" + "╔" + "="*68 + "╗")
    print("║" + " "*20 + "🧪 RCS PLATFORM LOCAL TESTS" + " "*20 + "║")
    print("╚" + "="*68 + "╝")
    
    print("\nℹ️  These tests use MOCK adapter - no real messages sent!")
    print("ℹ️  Safe to run anytime - no Gupshup account needed\n")
    
    # Setup logging to file
    setup_logging(
        log_level="INFO",
        log_file="logs/test_local.log"
    )
    print("📝 Logging initialized to logs/test_local.log")
    
    try:
        # Test 1: Domain models (pure business logic)
        await test_domain_models()
        
        # Test 2: Services with mock adapter
        await test_with_mock_adapter()
        
        # Test 3: Complete flow
        await test_end_to_end()
        
        # Summary
        print("\n" + "╔" + "="*68 + "╗")
        print("║" + " "*22 + "✅ ALL TESTS PASSED! ✅" + " "*22 + "║")
        print("╚" + "="*68 + "╝\n")
        
        print("📝 Next steps:")
        print("   1. Try Postman tests (see TESTING.md)")
        print("   2. Run with real Gupshup (add credentials to .env)")
        print("   3. Deploy to production!\n")
        
        return 0
        
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
